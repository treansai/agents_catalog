from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection, RowMapping

from domain.configuration import AgentConfig, config_to_payload
from domain.decisions import Decision, parse_decision
from domain.dossier import Dossier
from domain.events import (
    DecisionEmitted,
    GuardrailOverride,
    HumanOverride,
    ToolFailure,
    TraceEvent,
)
from infrastructure.database.records import (
    AgentConfigRecord,
    InternalListEntry,
    JsonObject,
    JsonValue,
    RunTraceRecord,
    StoredTraceEvent,
)
from infrastructure.database.schema import (
    agent_config_table,
    dossier_table,
    human_override_table,
    internal_list_table,
    run_trace_table,
    trace_event_table,
)

type RunTraceArgs = tuple[
    UUID,
    str,
    str,
    datetime,
    datetime,
    tuple[JsonObject, ...],
    tuple[JsonObject, ...],
    Decision,
    str,
    float,
]
type Values = dict[str, object]

_EVENT_QUERY = sa.select(
    trace_event_table,
    human_override_table.c.operator_id,
    human_override_table.c.original_decision,
    human_override_table.c.corrected_decision,
    human_override_table.c.reason,
).outerjoin(human_override_table)


def insert_agent_config(connection: Connection, record: AgentConfigRecord) -> None:
    connection.execute(sa.insert(agent_config_table), _config_values(record))


def get_agent_config(
    connection: Connection, config_hash: str
) -> AgentConfigRecord | None:
    query = sa.select(agent_config_table).where(
        agent_config_table.c.config_hash == config_hash
    )
    row = connection.execute(query).mappings().one_or_none()
    return None if row is None else _config_record(row)


def insert_dossiers(connection: Connection, dossiers: Sequence[Dossier]) -> None:
    if dossiers:
        values = list(map(_dossier_values, dossiers))
        connection.execute(sa.insert(dossier_table), values)


def get_dossier(connection: Connection, dossier_id: str) -> Dossier | None:
    query = sa.select(dossier_table).where(dossier_table.c.dossier_id == dossier_id)
    row = connection.execute(query).mappings().one_or_none()
    return None if row is None else _dossier_from_row(row)


def insert_internal_list_entries(
    connection: Connection, entries: Sequence[InternalListEntry]
) -> None:
    if entries:
        values = list(map(_internal_list_values, entries))
        connection.execute(sa.insert(internal_list_table), values)


def get_internal_list_entry(
    connection: Connection, dossier_id: str
) -> InternalListEntry | None:
    query = sa.select(internal_list_table).where(
        internal_list_table.c.dossier_id == dossier_id
    )
    row = connection.execute(query).mappings().one_or_none()
    return None if row is None else _internal_list_entry(row)


def is_on_internal_list(connection: Connection, dossier_id: str) -> bool:
    query = sa.select(sa.literal(True)).where(
        sa.exists().where(internal_list_table.c.dossier_id == dossier_id)
    )
    return bool(connection.scalar(query))


def insert_run_trace(connection: Connection, trace: RunTraceRecord) -> None:
    connection.execute(sa.insert(run_trace_table), _run_trace_values(trace))


def get_run_trace(connection: Connection, run_id: UUID) -> RunTraceRecord | None:
    query = sa.select(run_trace_table).where(run_trace_table.c.run_id == run_id)
    row = connection.execute(query).mappings().one_or_none()
    return None if row is None else _run_trace_record(row)


def insert_trace_event(
    connection: Connection, event_id: UUID, event: TraceEvent, sequence_no: int
) -> None:
    values = _trace_event_values(event_id, event, sequence_no)
    connection.execute(sa.insert(trace_event_table), values)
    if isinstance(event, HumanOverride):
        _insert_human_override(connection, event_id, event)


def list_trace_events(
    connection: Connection, run_id: UUID
) -> tuple[StoredTraceEvent, ...]:
    query = _EVENT_QUERY.where(trace_event_table.c.run_id == run_id)
    ordered = query.order_by(trace_event_table.c.sequence_no)
    rows = connection.execute(ordered).mappings()
    return tuple(_stored_trace_event(row) for row in rows)


def _config_values(record: AgentConfigRecord) -> dict[str, object]:
    payload = config_to_payload(record.config)
    return {
        "config_hash": record.config_hash,
        "label": record.label,
        "created_at": record.created_at,
        **payload,
    }


def _config_record(row: RowMapping) -> AgentConfigRecord:
    return AgentConfigRecord(
        config_hash=cast(str, row["config_hash"]),
        label=cast(str, row["label"]),
        config=_agent_config(row),
        created_at=_utc_datetime(row, "created_at"),
    )


def _agent_config(row: RowMapping) -> AgentConfig:
    return AgentConfig(
        prompt_template=cast(str, row["prompt_template"]),
        model_id=cast(str, row["model_id"]),
        temperature=cast(float, row["temperature"]),
        top_p=cast(float, row["top_p"]),
        max_tokens=cast(int, row["max_tokens"]),
        tools_version=cast(str, row["tools_version"]),
        context_fields=tuple(cast(list[str], row["context_fields"])),
    )


def _dossier_values(dossier: Dossier) -> dict[str, object]:
    return cast(dict[str, object], asdict(dossier))


def _dossier_from_row(row: RowMapping) -> Dossier:
    dossier_id = cast(str, row["dossier_id"])
    return Dossier(dossier_id, *_amounts(row), *_employment(row), *_demographics(row))


def _amounts(row: RowMapping) -> tuple[Decimal, Decimal, Decimal]:
    return (
        cast(Decimal, row["revenu_mensuel"]),
        cast(Decimal, row["charges_mensuelles"]),
        cast(Decimal, row["montant_demande"]),
    )


def _employment(row: RowMapping) -> tuple[int, int, str]:
    return (
        cast(int, row["duree_mois"]),
        cast(int, row["anciennete_emploi_mois"]),
        cast(str, row["type_contrat"]),
    )


def _demographics(row: RowMapping) -> tuple[int, str, int]:
    return (
        cast(int, row["age"]),
        cast(str, row["code_postal"]),
        cast(int, row["nb_incidents_passes"]),
    )


def _internal_list_values(entry: InternalListEntry) -> dict[str, object]:
    return {
        "dossier_id": entry.dossier_id,
        "reason": entry.reason,
        "listed_at": entry.listed_at,
    }


def _internal_list_entry(row: RowMapping) -> InternalListEntry:
    return InternalListEntry(
        dossier_id=cast(str, row["dossier_id"]),
        reason=cast(str, row["reason"]),
        listed_at=_utc_datetime(row, "listed_at"),
    )


def _run_trace_values(trace: RunTraceRecord) -> dict[str, object]:
    return {
        **_run_identity_values(trace),
        **_run_evidence_values(trace),
        **_run_outcome_values(trace),
    }


def _run_identity_values(trace: RunTraceRecord) -> dict[str, object]:
    return {
        "run_id": trace.run_id,
        "config_hash": trace.config_hash,
        "dossier_id": trace.dossier_id,
        "started_at": trace.started_at,
    }


def _run_evidence_values(trace: RunTraceRecord) -> dict[str, object]:
    return {
        "ended_at": trace.ended_at,
        "tool_calls": list(trace.tool_calls),
        "llm_calls": list(trace.llm_calls),
    }


def _run_outcome_values(trace: RunTraceRecord) -> dict[str, object]:
    return {
        "decision": trace.decision,
        "justification": trace.justification,
        "confidence": trace.confidence,
    }


def _run_trace_record(row: RowMapping) -> RunTraceRecord:
    return RunTraceRecord(*_run_trace_args(row))


def _run_trace_args(row: RowMapping) -> RunTraceArgs:
    identity = _run_identity(row)
    timing = _run_timing(row)
    calls = _run_calls(row)
    outcome = _run_outcome(row)
    return (*identity, *timing, *calls, *outcome)


def _run_identity(row: RowMapping) -> tuple[UUID, str, str]:
    return (
        cast(UUID, row["run_id"]),
        cast(str, row["config_hash"]),
        cast(str, row["dossier_id"]),
    )


def _run_timing(row: RowMapping) -> tuple[datetime, datetime]:
    return _utc_datetime(row, "started_at"), _utc_datetime(row, "ended_at")


def _run_calls(
    row: RowMapping,
) -> tuple[tuple[JsonObject, ...], tuple[JsonObject, ...]]:
    return _json_objects(row["tool_calls"]), _json_objects(row["llm_calls"])


def _run_outcome(row: RowMapping) -> tuple[Decision, str, float]:
    return (
        parse_decision(cast(str, row["decision"])),
        cast(str, row["justification"]),
        cast(float, row["confidence"]),
    )


def _trace_event_values(event_id: UUID, event: TraceEvent, sequence_no: int) -> Values:
    return {
        "event_id": event_id,
        "run_id": event.run_id,
        "sequence_no": sequence_no,
        "event_type": event.event_type,
        "payload": _event_payload(event),
        "occurred_at": event.occurred_at,
    }


def _event_payload(event: TraceEvent) -> dict[str, JsonValue]:
    if isinstance(event, DecisionEmitted):
        return _decision_payload(event)
    if isinstance(event, ToolFailure):
        return {"tool_name": event.tool_name, "error": event.error}
    if isinstance(event, GuardrailOverride):
        return _guardrail_payload(event)
    return {}


def _decision_payload(event: DecisionEmitted) -> dict[str, JsonValue]:
    return {
        "decision": event.decision,
        "justification": event.justification,
        "confidence": event.confidence,
    }


def _guardrail_payload(event: GuardrailOverride) -> dict[str, JsonValue]:
    return {
        "guardrail_id": event.guardrail_id,
        "original_decision": event.original_decision,
        "corrected_decision": event.corrected_decision,
        "reason": event.reason,
    }


def _insert_human_override(
    connection: Connection, event_id: UUID, event: HumanOverride
) -> None:
    values = _human_override_values(event_id, event)
    connection.execute(sa.insert(human_override_table), values)


def _human_override_values(event_id: UUID, event: HumanOverride) -> dict[str, object]:
    return {
        "event_id": event_id,
        "run_id": event.run_id,
        "operator_id": event.operator_id,
        "original_decision": event.original_decision,
        "corrected_decision": event.corrected_decision,
        "reason": event.reason,
        "occurred_at": event.occurred_at,
    }


def _stored_trace_event(row: RowMapping) -> StoredTraceEvent:
    event_id = cast(UUID, row["event_id"])
    return StoredTraceEvent(event_id=event_id, event=_event_from_row(row))


def _event_from_row(row: RowMapping) -> TraceEvent:
    event_type = cast(str, row["event_type"])
    reader = _EVENT_READERS.get(event_type)
    if reader is None:
        raise ValueError(f"unknown event type: {event_type}")
    return reader(row)


def _decision_event(row: RowMapping) -> DecisionEmitted:
    payload = _payload(row)
    return DecisionEmitted(
        run_id=cast(UUID, row["run_id"]),
        decision=parse_decision(cast(str, payload["decision"])),
        justification=cast(str, payload["justification"]),
        confidence=cast(float, payload["confidence"]),
        occurred_at=_utc_datetime(row, "occurred_at"),
    )


def _tool_failure_event(row: RowMapping) -> ToolFailure:
    payload = _payload(row)
    return ToolFailure(
        run_id=cast(UUID, row["run_id"]),
        tool_name=cast(str, payload["tool_name"]),
        error=cast(str, payload["error"]),
        occurred_at=_utc_datetime(row, "occurred_at"),
    )


def _guardrail_event(row: RowMapping) -> GuardrailOverride:
    payload = _payload(row)
    return GuardrailOverride(
        run_id=cast(UUID, row["run_id"]),
        guardrail_id=cast(str, payload["guardrail_id"]),
        original_decision=parse_decision(cast(str, payload["original_decision"])),
        corrected_decision=parse_decision(cast(str, payload["corrected_decision"])),
        reason=cast(str, payload["reason"]),
        occurred_at=_utc_datetime(row, "occurred_at"),
    )


def _human_override_event(row: RowMapping) -> HumanOverride:
    return HumanOverride(
        run_id=cast(UUID, row["run_id"]),
        operator_id=cast(str, row["operator_id"]),
        original_decision=parse_decision(cast(str, row["original_decision"])),
        corrected_decision=parse_decision(cast(str, row["corrected_decision"])),
        reason=cast(str, row["reason"]),
        occurred_at=_utc_datetime(row, "occurred_at"),
    )


def _payload(row: RowMapping) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], row["payload"])


def _json_objects(value: object) -> tuple[JsonObject, ...]:
    return tuple(cast(list[dict[str, JsonValue]], value))


def _utc_datetime(row: RowMapping, key: str) -> datetime:
    value = cast(datetime, row[key])
    if value.tzinfo is None:
        raise ValueError(f"{key} must be timezone-aware")
    return value.astimezone(UTC)


_EVENT_READERS: dict[str, Callable[[RowMapping], TraceEvent]] = {
    "DECISION_EMITTED": _decision_event,
    "TOOL_FAILURE": _tool_failure_event,
    "GUARDRAIL_OVERRIDE": _guardrail_event,
    "HUMAN_OVERRIDE": _human_override_event,
}
