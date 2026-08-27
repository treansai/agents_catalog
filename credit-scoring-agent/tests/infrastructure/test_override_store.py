from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DataError

from domain.configuration import AgentConfig, config_hash
from domain.dossier import Dossier
from domain.events import DecisionEmitted, GuardrailOverride, HumanOverride
from domain.overrides import HumanOverridePort, OverrideCommand, effective_decision
from domain.run import LlmCallTrace, RunTrace
from domain.tools import ToolCallTrace
from infrastructure.api.database_app import create_database_app
from infrastructure.database.override_store import make_human_override_port
from infrastructure.database.records import AgentConfigRecord
from infrastructure.database.repositories import (
    insert_agent_config,
    insert_dossiers,
    list_trace_events,
)
from infrastructure.database.run_reader import load_complete_run
from infrastructure.database.run_store import persist_complete_run
from infrastructure.database.schema import (
    agent_config_table,
    dossier_table,
    human_override_table,
    run_trace_table,
    trace_event_table,
)

NOW = datetime(2026, 8, 28, 13, tzinfo=UTC)
OVERRIDE_AT = NOW + timedelta(seconds=2)
RUN_ID = UUID("40000000-0000-0000-0000-000000000147")
MISSING_RUN_ID = UUID("40000000-0000-0000-0000-000000000404")
EVENT_ID_1 = UUID("41000000-0000-0000-0000-000000000001")
EVENT_ID_2 = UUID("41000000-0000-0000-0000-000000000002")
DOSSIER_ID = "override-store-147"
CONFIG = AgentConfig("{dossier}", "override-model", 0.0, 1.0, 256, "1.0.0", ("age",))
CONFIG_RECORD = AgentConfigRecord(config_hash(CONFIG), "override-store-v1", CONFIG, NOW)
DOSSIER = Dossier(
    DOSSIER_ID,
    Decimal("3100.00"),
    Decimal("850.00"),
    Decimal("9000.00"),
    36,
    60,
    "CDI",
    41,
    "69003",
    0,
)
TOOL_CALLS = (
    ToolCallTrace(
        "fetch_dossier",
        {"dossier_id": DOSSIER_ID},
        {"dossier_id": DOSSIER_ID},
        2,
        None,
    ),
    ToolCallTrace("compute_debt_ratio", {}, {"ratio": "27.42"}, 1, None),
    ToolCallTrace("check_internal_list", {}, {"is_listed": False}, 1, None),
)
LLM_CALL = LlmCallTrace("prompt override", '{"decision":"REFUS"}', 40, 8, 12)
DECISION = DecisionEmitted(RUN_ID, "REFUS", "Ratio 27.42 %.", 0.8, NOW)
GUARDRAIL = GuardrailOverride(
    RUN_ID,
    "manual-review",
    "REFUS",
    "INSTRUCTION_MANUELLE",
    "Revue imposée.",
    NOW,
)
INITIAL_EVENTS = (DECISION, GUARDRAIL)
TRACE = RunTrace(
    RUN_ID,
    CONFIG_RECORD.config_hash,
    DOSSIER_ID,
    NOW,
    NOW + timedelta(seconds=1),
    TOOL_CALLS,
    (LLM_CALL,),
    "REFUS",
    DECISION.justification,
    DECISION.confidence,
    INITIAL_EVENTS,
)
COMMAND_1 = OverrideCommand(
    RUN_ID, "analyst-1", "ACCORD", "Pièces justificatives reçues.", OVERRIDE_AT
)
COMMAND_2 = OverrideCommand(
    RUN_ID, "analyst-2", "REFUS", "Nouvel incident confirmé.", OVERRIDE_AT
)


@pytest.fixture
def seeded_run(migrated_engine: Engine) -> Iterator[Engine]:
    _cleanup(migrated_engine)
    _seed_parents(migrated_engine)
    persist_complete_run(migrated_engine, TRACE)
    yield migrated_engine
    _cleanup(migrated_engine)


def _seed_parents(engine: Engine) -> None:
    with engine.begin() as connection:
        insert_agent_config(connection, CONFIG_RECORD)
        insert_dossiers(connection, [DOSSIER])


def _cleanup(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.delete(human_override_table).where(_human_filter()))
        connection.execute(sa.delete(trace_event_table).where(_event_filter()))
        connection.execute(sa.delete(run_trace_table).where(_run_filter()))
        connection.execute(sa.delete(dossier_table).where(_dossier_filter()))
        connection.execute(sa.delete(agent_config_table).where(_config_filter()))


def _human_filter() -> sa.ColumnElement[bool]:
    return human_override_table.c.run_id == RUN_ID


def _event_filter() -> sa.ColumnElement[bool]:
    return trace_event_table.c.run_id == RUN_ID


def _run_filter() -> sa.ColumnElement[bool]:
    return run_trace_table.c.run_id == RUN_ID


def _dossier_filter() -> sa.ColumnElement[bool]:
    return dossier_table.c.dossier_id == DOSSIER_ID


def _config_filter() -> sa.ColumnElement[bool]:
    return agent_config_table.c.config_hash == CONFIG_RECORD.config_hash


def _port(engine: Engine, *event_ids: UUID) -> HumanOverridePort:
    return make_human_override_port(engine, iter(event_ids).__next__)


def _events(engine: Engine, run_id: UUID = RUN_ID) -> tuple[object, ...]:
    with engine.connect() as connection:
        stored = list_trace_events(connection, run_id)
    return tuple(item.event for item in stored)


def _human_rows(engine: Engine) -> tuple[RowMapping, ...]:
    query = sa.select(human_override_table).where(_human_filter())
    with engine.connect() as connection:
        return tuple(connection.execute(query).mappings())


def _sequences(engine: Engine) -> tuple[int, ...]:
    query = sa.select(trace_event_table.c.sequence_no).where(_event_filter())
    ordered = query.order_by(trace_event_table.c.sequence_no)
    with engine.connect() as connection:
        return tuple(connection.scalars(ordered))


def _assert_dedicated_row(row: RowMapping, event: HumanOverride) -> None:
    assert row["event_id"] == EVENT_ID_1
    assert row["run_id"] == RUN_ID
    assert row["operator_id"] == event.operator_id
    assert row["original_decision"] == event.original_decision
    assert row["corrected_decision"] == event.corrected_decision
    assert row["reason"] == event.reason
    assert row["occurred_at"] == event.occurred_at


def test_override_uses_server_original_and_dedicated_table(
    seeded_run: Engine,
) -> None:
    event = _port(seeded_run, EVENT_ID_1)(COMMAND_1)
    assert event.original_decision == "INSTRUCTION_MANUELLE"
    rows = _human_rows(seeded_run)
    assert len(rows) == 1
    _assert_dedicated_row(rows[0], event)
    assert _events(seeded_run) == (*INITIAL_EVENTS, event)


def test_successive_overrides_chain_effective_decision(seeded_run: Engine) -> None:
    port = _port(seeded_run, EVENT_ID_1, EVENT_ID_2)
    first = port(COMMAND_1)
    second = port(COMMAND_2)
    assert first.original_decision == "INSTRUCTION_MANUELLE"
    assert second.original_decision == "ACCORD"
    assert _events(seeded_run) == (*INITIAL_EVENTS, first, second)
    assert _sequences(seeded_run) == (0, 1, 2, 3)


def test_reader_reconstructs_successive_overrides(seeded_run: Engine) -> None:
    port = _port(seeded_run, EVENT_ID_1, EVENT_ID_2)
    first, second = port(COMMAND_1), port(COMMAND_2)
    trace = _complete_trace(seeded_run)
    assert trace.events == (*INITIAL_EVENTS, first, second)
    assert trace.llm_calls == TRACE.llm_calls
    assert effective_decision(trace.decision, trace.events) == "REFUS"


def test_override_endpoint_persists_and_returns_complete_trace(
    seeded_run: Engine,
) -> None:
    with _api_client(seeded_run) as client:
        response = client.post(f"/runs/{RUN_ID}/override", json=_override_json())
        trace_response = client.get(f"/runs/{RUN_ID}")
    _assert_http_override(response.status_code, response.json())
    assert trace_response.json()["events"][-1]["event_type"] == "HUMAN_OVERRIDE"
    assert len(_human_rows(seeded_run)) == 1


def test_missing_run_raises_without_writing(migrated_engine: Engine) -> None:
    command = replace(COMMAND_1, run_id=MISSING_RUN_ID)
    with pytest.raises(LookupError, match="run not found"):
        _port(migrated_engine, EVENT_ID_1)(command)
    assert _events(migrated_engine, MISSING_RUN_ID) == ()


def test_child_insert_failure_rolls_back_base_event(seeded_run: Engine) -> None:
    invalid = replace(COMMAND_1, operator_id="x" * 256)
    with pytest.raises(DataError):
        _port(seeded_run, EVENT_ID_1)(invalid)
    assert _events(seeded_run) == INITIAL_EVENTS
    assert _human_rows(seeded_run) == ()
    assert _sequences(seeded_run) == (0, 1)


def _complete_trace(engine: Engine) -> RunTrace:
    trace = load_complete_run(engine, RUN_ID)
    if trace is None:
        raise AssertionError("complete trace was not found")
    return trace


def _api_client(engine: Engine) -> TestClient:
    app = create_database_app(engine, lambda: OVERRIDE_AT, lambda: EVENT_ID_1)
    return TestClient(app)


def _override_json() -> dict[str, str]:
    return {
        "operator_id": COMMAND_1.operator_id,
        "corrected_decision": COMMAND_1.corrected_decision,
        "reason": COMMAND_1.reason,
    }


def _assert_http_override(status_code: int, payload: object) -> None:
    assert status_code == 201
    assert isinstance(payload, dict)
    assert payload["event_type"] == "HUMAN_OVERRIDE"
    assert payload["original_decision"] == "INSTRUCTION_MANUELLE"
