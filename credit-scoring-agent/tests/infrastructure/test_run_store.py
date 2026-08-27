from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import IntegrityError

from domain.configuration import AgentConfig, config_hash
from domain.dossier import Dossier
from domain.events import DecisionEmitted, GuardrailOverride, HumanOverride, ToolFailure
from domain.run import LlmCallTrace, RunTrace
from domain.tools import JsonObject as DomainJsonObject
from domain.tools import ToolCallTrace
from infrastructure.database import run_store
from infrastructure.database.records import (
    AgentConfigRecord,
    JsonObject,
    RunTraceRecord,
)
from infrastructure.database.repositories import (
    get_run_trace,
    insert_agent_config,
    insert_dossiers,
    list_trace_events,
)
from infrastructure.database.schema import (
    agent_config_table,
    dossier_table,
    human_override_table,
    run_trace_table,
    trace_event_table,
)

NOW = datetime(2026, 8, 28, 9, 30, tzinfo=UTC)
RUN_ID = UUID("30000000-0000-0000-0000-000000000147")
DOSSIER_ID = "run-store-147"
PROMPT = "Dossier 147\nTaux : 28,13 %\nRépondre en JSON."
RAW_RESPONSE = '{\n  "decision": "REFUS",\n  "justification": "28.13 %"\n}'
CONFIG = AgentConfig("{dossier}", "model-run-store", 0.0, 1.0, 512, "1.0.0", ("age",))
CONFIG_RECORD = AgentConfigRecord(config_hash(CONFIG), "run-store-v1", CONFIG, NOW)
DOSSIER = Dossier(
    DOSSIER_ID,
    Decimal("3200.00"),
    Decimal("900.00"),
    Decimal("12000.00"),
    48,
    72,
    "CDI",
    38,
    "75011",
    1,
)

FETCH_INPUT: DomainJsonObject = MappingProxyType({"dossier_id": DOSSIER_ID})
FETCH_OUTPUT: DomainJsonObject = MappingProxyType(
    {
        "dossier_id": DOSSIER_ID,
        "revenu_mensuel": "3200.00",
        "profile": MappingProxyType({"age": 38, "code_postal": "75011"}),
    }
)
RATIO_OUTPUT: DomainJsonObject = MappingProxyType(
    {"taux_endettement": "28.13", "reste_a_vivre": "2300.00"}
)
TOOL_CALLS = (
    ToolCallTrace("fetch_dossier", FETCH_INPUT, FETCH_OUTPUT, 4, None),
    ToolCallTrace("compute_debt_ratio", FETCH_OUTPUT, RATIO_OUTPUT, 1, None),
    ToolCallTrace("check_internal_list", FETCH_INPUT, None, 7, "timeout interne"),
)
LLM_CALLS = (LlmCallTrace(PROMPT, RAW_RESPONSE, 143, 27, 31),)

TOOL_FAILURE = ToolFailure(RUN_ID, "check_internal_list", "timeout interne", NOW)
DECISION = DecisionEmitted(RUN_ID, "REFUS", "Ratio 28.13 %, un incident.", 0.87, NOW)
GUARDRAIL = GuardrailOverride(
    RUN_ID,
    "manual-on-tool-failure",
    "REFUS",
    "INSTRUCTION_MANUELLE",
    "Échec outil : revue requise.",
    NOW,
)
EVENTS = (TOOL_FAILURE, DECISION, GUARDRAIL)
TRACE = RunTrace(
    RUN_ID,
    CONFIG_RECORD.config_hash,
    DOSSIER_ID,
    NOW,
    NOW + timedelta(seconds=1),
    TOOL_CALLS,
    LLM_CALLS,
    "REFUS",
    DECISION.justification,
    DECISION.confidence,
    EVENTS,
)

EXPECTED_TOOLS: tuple[JsonObject, ...] = (
    {
        "name": "fetch_dossier",
        "input": {"dossier_id": DOSSIER_ID},
        "output": {
            "dossier_id": DOSSIER_ID,
            "revenu_mensuel": "3200.00",
            "profile": {"age": 38, "code_postal": "75011"},
        },
        "duration_ms": 4,
        "error": None,
    },
    {
        "name": "compute_debt_ratio",
        "input": {
            "dossier_id": DOSSIER_ID,
            "revenu_mensuel": "3200.00",
            "profile": {"age": 38, "code_postal": "75011"},
        },
        "output": {"taux_endettement": "28.13", "reste_a_vivre": "2300.00"},
        "duration_ms": 1,
        "error": None,
    },
    {
        "name": "check_internal_list",
        "input": {"dossier_id": DOSSIER_ID},
        "output": None,
        "duration_ms": 7,
        "error": "timeout interne",
    },
)
EXPECTED_LLMS: tuple[JsonObject, ...] = (
    {
        "prompt_rendered": PROMPT,
        "raw_response": RAW_RESPONSE,
        "tokens_in": 143,
        "tokens_out": 27,
        "latency_ms": 31,
    },
)
EXPECTED_RECORD = RunTraceRecord(
    RUN_ID,
    CONFIG_RECORD.config_hash,
    DOSSIER_ID,
    TRACE.started_at,
    TRACE.ended_at,
    EXPECTED_TOOLS,
    EXPECTED_LLMS,
    TRACE.decision,
    TRACE.justification,
    TRACE.confidence,
)
HUMAN_OVERRIDE = HumanOverride(
    RUN_ID,
    "analyst-8",
    "INSTRUCTION_MANUELLE",
    "ACCORD",
    "Revue manuelle",
    TRACE.ended_at,
)


@pytest.fixture
def seeded_engine(migrated_engine: Engine) -> Iterator[Engine]:
    _cleanup(migrated_engine)
    with migrated_engine.begin() as connection:
        insert_agent_config(connection, CONFIG_RECORD)
        insert_dossiers(connection, [DOSSIER])
    yield migrated_engine
    _cleanup(migrated_engine)


def _cleanup(engine: Engine) -> None:
    with engine.begin() as connection:
        overrides = sa.delete(human_override_table).where(_override_run_filter())
        connection.execute(overrides)
        connection.execute(sa.delete(trace_event_table).where(_event_run_filter()))
        connection.execute(sa.delete(run_trace_table).where(_run_filter()))
        connection.execute(sa.delete(dossier_table).where(_dossier_filter()))
        connection.execute(sa.delete(agent_config_table).where(_config_filter()))


def _event_run_filter() -> sa.ColumnElement[bool]:
    return trace_event_table.c.run_id == RUN_ID


def _override_run_filter() -> sa.ColumnElement[bool]:
    return human_override_table.c.run_id == RUN_ID


def _run_filter() -> sa.ColumnElement[bool]:
    return run_trace_table.c.run_id == RUN_ID


def _dossier_filter() -> sa.ColumnElement[bool]:
    return dossier_table.c.dossier_id == DOSSIER_ID


def _config_filter() -> sa.ColumnElement[bool]:
    return agent_config_table.c.config_hash == CONFIG_RECORD.config_hash


def _persist(engine: Engine, trace: RunTrace = TRACE) -> None:
    run_store.make_persist_run_port(engine)(trace)


def _load_record(engine: Engine) -> RunTraceRecord:
    with engine.connect() as connection:
        record = get_run_trace(connection, RUN_ID)
    if record is None:
        raise AssertionError("run trace was not persisted")
    return record


def _load_events(engine: Engine) -> tuple[object, ...]:
    with engine.connect() as connection:
        stored = list_trace_events(connection, RUN_ID)
    return tuple(item.event for item in stored)


def _sequence_numbers(connection: Connection) -> tuple[int, ...]:
    query = sa.select(trace_event_table.c.sequence_no)
    filtered = query.where(trace_event_table.c.run_id == RUN_ID)
    return tuple(connection.scalars(filtered.order_by(trace_event_table.c.sequence_no)))


def _assert_graph_absent(engine: Engine) -> None:
    with engine.connect() as connection:
        assert get_run_trace(connection, RUN_ID) is None
        assert list_trace_events(connection, RUN_ID) == ()


def _constant_event_id(_namespace: UUID, _name: str) -> UUID:
    return UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")


def test_complete_graph_round_trip(seeded_engine: Engine) -> None:
    _persist(seeded_engine)
    assert _load_record(seeded_engine) == EXPECTED_RECORD
    assert _load_events(seeded_engine) == EVENTS


def test_prompt_and_raw_response_are_exact(seeded_engine: Engine) -> None:
    _persist(seeded_engine)
    llm_call = _load_record(seeded_engine).llm_calls[0]
    assert llm_call["prompt_rendered"] == PROMPT
    assert llm_call["raw_response"] == RAW_RESPONSE


def test_equal_timestamps_keep_domain_sequence(seeded_engine: Engine) -> None:
    _persist(seeded_engine)
    assert _load_events(seeded_engine) == EVENTS
    with seeded_engine.connect() as connection:
        assert _sequence_numbers(connection) == (0, 1, 2)


def test_event_failure_rolls_back_complete_graph(
    seeded_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_store, "uuid5", _constant_event_id)
    with pytest.raises(IntegrityError):
        _persist(seeded_engine)
    _assert_graph_absent(seeded_engine)


def test_human_override_is_rejected_before_any_write(seeded_engine: Engine) -> None:
    invalid = replace(TRACE, events=(*TRACE.events, HUMAN_OVERRIDE))
    with pytest.raises(ValueError, match="override workflow"):
        _persist(seeded_engine, invalid)
    _assert_graph_absent(seeded_engine)
