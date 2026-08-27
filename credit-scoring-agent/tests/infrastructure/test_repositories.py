from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from domain.configuration import AgentConfig, config_hash
from domain.dataset import generate_dossiers
from domain.dossier import Dossier
from domain.events import DecisionEmitted, GuardrailOverride, HumanOverride, ToolFailure
from infrastructure.database.records import (
    AgentConfigRecord,
    InternalListEntry,
    JsonObject,
    RunTraceRecord,
)
from infrastructure.database.repositories import (
    get_agent_config,
    get_dossier,
    get_internal_list_entry,
    get_run_trace,
    insert_agent_config,
    insert_dossiers,
    insert_internal_list_entries,
    insert_run_trace,
    insert_trace_event,
    is_on_internal_list,
    list_trace_events,
)
from infrastructure.database.schema import dossier_table, human_override_table

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)
RUN_ID = UUID("10000000-0000-0000-0000-000000000001")
CONFIG = AgentConfig("Décider: {dossier}", "model-v1", 0.0, 1.0, 512, "1.0.0", ("age",))
CONFIG_RECORD = AgentConfigRecord(config_hash(CONFIG), "v1", CONFIG, NOW)
DOSSIER = Dossier(
    "147",
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
TOOLS: tuple[JsonObject, ...] = ({"name": "fetch_dossier", "duration_ms": 4},)
LLM: tuple[JsonObject, ...] = (
    {"raw_response": '{"decision":"REFUS"}', "tokens_out": 10},
)
TRACE = RunTraceRecord(
    RUN_ID,
    CONFIG_RECORD.config_hash,
    "147",
    NOW,
    NOW + timedelta(seconds=1),
    TOOLS,
    LLM,
    "REFUS",
    "Ratio 28.13 %, un incident.",
    0.81,
)
EMITTED = DecisionEmitted(RUN_ID, "REFUS", TRACE.justification, 0.81, NOW)
TOOL_FAILURE = ToolFailure(RUN_ID, "fetch_dossier", "timeout", NOW)
GUARDRAIL = GuardrailOverride(
    RUN_ID,
    "manual-on-tool-failure",
    "REFUS",
    "INSTRUCTION_MANUELLE",
    "Tool failure requires review.",
    NOW,
)
OVERRIDE = HumanOverride(RUN_ID, "analyst-7", "REFUS", "ACCORD", "Revue", NOW)


def _insert_run_parents(connection: Connection) -> AgentConfigRecord:
    insert_agent_config(connection, CONFIG_RECORD)
    insert_dossiers(connection, [DOSSIER])
    return CONFIG_RECORD


def _insert_trace_graph(connection: Connection) -> None:
    _insert_run_parents(connection)
    insert_run_trace(connection, TRACE)
    insert_trace_event(connection, UUID(int=1), EMITTED, 0)
    insert_trace_event(connection, UUID(int=2), TOOL_FAILURE, 1)
    insert_trace_event(connection, UUID(int=3), GUARDRAIL, 2)
    insert_trace_event(connection, UUID(int=4), OVERRIDE, 3)


def test_config_dossier_and_internal_list_round_trip(connection: Connection) -> None:
    record = _insert_run_parents(connection)
    entry = InternalListEntry("147", "Incident synthétique", NOW)
    insert_internal_list_entries(connection, [entry])
    assert get_agent_config(connection, record.config_hash) == record
    assert get_dossier(connection, "147") == DOSSIER
    assert get_internal_list_entry(connection, "147") == entry
    assert is_on_internal_list(connection, "147")


def test_missing_rows_are_reported_without_sentinels(connection: Connection) -> None:
    assert get_agent_config(connection, "0" * 64) is None
    assert get_dossier(connection, "missing") is None
    assert get_internal_list_entry(connection, "missing") is None
    assert not is_on_internal_list(connection, "missing")


def test_config_record_rejects_a_hash_mismatch() -> None:
    with pytest.raises(ValueError, match="does not match"):
        AgentConfigRecord("0" * 64, "v1", CONFIG, NOW)


def test_duplicate_config_does_not_overwrite(connection: Connection) -> None:
    insert_agent_config(connection, CONFIG_RECORD)
    with pytest.raises(IntegrityError), connection.begin_nested():
        insert_agent_config(connection, CONFIG_RECORD)
    assert get_agent_config(connection, CONFIG_RECORD.config_hash) == CONFIG_RECORD


def test_generated_dataset_round_trip_includes_dossier_147(
    connection: Connection,
) -> None:
    dossiers = generate_dossiers()
    insert_dossiers(connection, dossiers)
    assert (
        connection.scalar(sa.select(sa.func.count()).select_from(dossier_table)) == 200
    )
    assert get_dossier(connection, "147") == dossiers[146]


def test_trace_and_human_override_round_trip(connection: Connection) -> None:
    _insert_trace_graph(connection)
    assert get_run_trace(connection, RUN_ID) == TRACE
    stored = tuple(item.event for item in list_trace_events(connection, RUN_ID))
    assert stored == (EMITTED, TOOL_FAILURE, GUARDRAIL, OVERRIDE)
    assert _human_override_count(connection) == 1


def _human_override_count(connection: Connection) -> int:
    query = sa.select(sa.func.count()).select_from(human_override_table)
    return int(connection.scalar(query) or 0)
