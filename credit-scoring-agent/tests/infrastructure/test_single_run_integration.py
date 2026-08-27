from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from adapters.run_agent import ConfigLookup, RunPorts, RunRequest, execute_run
from domain.configuration import AgentConfig, config_hash
from domain.dataset import generate_dossiers
from domain.events import DecisionEmitted, TraceEvent
from domain.run import RunTrace
from domain.tools import DossierLookup, InternalListLookup
from infrastructure.database.records import AgentConfigRecord, RunTraceRecord
from infrastructure.database.repositories import (
    get_run_trace,
    insert_agent_config,
    insert_dossiers,
    list_trace_events,
)
from infrastructure.database.run_ports import (
    lookup_agent_config,
    lookup_dossier,
    lookup_internal_reason,
)
from infrastructure.database.run_reader import load_complete_run
from infrastructure.database.run_store import make_persist_run_port
from infrastructure.database.schema import (
    agent_config_table,
    dossier_table,
    run_trace_table,
    trace_event_table,
)
from infrastructure.llm.scaleway import build_scaleway_llm

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures/llm/scaleway_refus.json"
FIXTURE_BODY = FIXTURE_PATH.read_text(encoding="utf-8")
NOW = datetime(2026, 8, 28, 14, tzinfo=UTC)
RUN_ID = UUID("40000000-0000-0000-0000-000000000147")
DOSSIER = replace(generate_dossiers()[146], dossier_id="stage3-147")
CONFIG = AgentConfig(
    "Analyse et cite les valeurs numériques utilisées.",
    "mistral-nemo-instruct-2407",
    0.0,
    1.0,
    512,
    "1.0.0",
    ("revenu_mensuel", "charges_mensuelles", "nb_incidents_passes"),
)
CONFIG_RECORD = AgentConfigRecord(config_hash(CONFIG), "stage3-single-run", CONFIG, NOW)
type LookupPorts = tuple[ConfigLookup, DossierLookup, InternalListLookup]


@pytest.fixture
def single_run_engine(migrated_engine: Engine) -> Iterator[Engine]:
    _cleanup(migrated_engine)
    with migrated_engine.begin() as connection:
        insert_agent_config(connection, CONFIG_RECORD)
        insert_dossiers(connection, [DOSSIER])
    yield migrated_engine
    _cleanup(migrated_engine)


def _cleanup(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.delete(trace_event_table).where(_run_event()))
        connection.execute(sa.delete(run_trace_table).where(_run_trace()))
        connection.execute(sa.delete(dossier_table).where(_dossier()))
        connection.execute(sa.delete(agent_config_table).where(_config()))


def _run_event() -> sa.ColumnElement[bool]:
    return trace_event_table.c.run_id == RUN_ID


def _run_trace() -> sa.ColumnElement[bool]:
    return run_trace_table.c.run_id == RUN_ID


def _dossier() -> sa.ColumnElement[bool]:
    return dossier_table.c.dossier_id == DOSSIER.dossier_id


def _config() -> sa.ColumnElement[bool]:
    return agent_config_table.c.config_hash == CONFIG_RECORD.config_hash


def _fixture_response(request: httpx.Request) -> httpx.Response:
    del request
    headers = {"Content-Type": "application/json"}
    return httpx.Response(200, content=FIXTURE_BODY.encode(), headers=headers)


def _tool_clock() -> Callable[[], int]:
    ticks = (0, 1_000_000, 2_000_000, 4_000_000, 5_000_000, 8_000_000)
    return iter(ticks).__next__


def _utc_clock() -> Callable[[], datetime]:
    return iter((NOW, NOW + timedelta(seconds=1))).__next__


def _ports(engine: Engine, client: httpx.Client) -> RunPorts:
    llm = build_scaleway_llm(client, "fixture-key", clock_ns=_llm_clock())
    factory = partial(RunPorts, *_lookups(engine), llm)
    return factory(make_persist_run_port(engine), _utc_clock(), _tool_clock(), _uuid)


def _lookups(engine: Engine) -> LookupPorts:
    return (
        partial(lookup_agent_config, engine),
        partial(lookup_dossier, engine),
        partial(lookup_internal_reason, engine),
    )


def _llm_clock() -> Callable[[], int]:
    return iter((10_000_000, 22_000_000)).__next__


def _uuid() -> UUID:
    return RUN_ID


def _stored_graph(engine: Engine) -> tuple[RunTraceRecord, tuple[object, ...]]:
    with engine.connect() as connection:
        record = get_run_trace(connection, RUN_ID)
        events = tuple(item.event for item in list_trace_events(connection, RUN_ID))
    if record is None:
        raise AssertionError("run trace was not persisted")
    return record, events


def test_single_run_executes_and_persists_complete_evidence(
    single_run_engine: Engine,
) -> None:
    transport = httpx.MockTransport(_fixture_response)
    with httpx.Client(transport=transport) as client:
        trace = execute_run(_request(), _ports(single_run_engine, client))
    _assert_persistence(single_run_engine, trace)


def _assert_persistence(engine: Engine, trace: RunTrace) -> None:
    record, events = _stored_graph(engine)
    _assert_trace(record)
    assert events == _expected_events(trace)
    assert load_complete_run(engine, RUN_ID) == trace


def _request() -> RunRequest:
    return RunRequest(CONFIG_RECORD.config_hash, DOSSIER.dossier_id)


def _expected_events(trace: RunTrace) -> tuple[TraceEvent, ...]:
    emitted = DecisionEmitted(
        RUN_ID, "REFUS", trace.justification, 0.87, trace.ended_at
    )
    return (emitted,)


def _assert_trace(record: RunTraceRecord) -> None:
    assert record.run_id == RUN_ID
    assert tuple(call["name"] for call in record.tool_calls) == _tool_names()
    assert record.llm_calls[0]["raw_response"] == FIXTURE_BODY
    prompt = str(record.llm_calls[0]["prompt_rendered"])
    assert DOSSIER.code_postal not in prompt
    assert record.decision == "REFUS"
    assert "29.00 %" in record.justification
    assert "2407.61 EUR" in record.justification


def _tool_names() -> tuple[str, ...]:
    return "fetch_dossier", "compute_debt_ratio", "check_internal_list"
