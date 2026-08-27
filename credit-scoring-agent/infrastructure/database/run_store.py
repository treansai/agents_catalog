"""Atomic persistence mapping for a complete, already executed run."""

from collections.abc import Mapping
from datetime import datetime
from functools import partial
from uuid import UUID, uuid5

from sqlalchemy import Connection, Engine

from domain.decisions import Decision
from domain.events import HumanOverride, TraceEvent
from domain.run import LlmCallTrace, PersistRunPort, RunTrace
from domain.tools import JsonValue as DomainJsonValue
from domain.tools import ToolCallTrace
from infrastructure.database.records import JsonObject, JsonValue, RunTraceRecord
from infrastructure.database.repositories import insert_run_trace, insert_trace_event

type RecordArgs = tuple[
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


def make_persist_run_port(engine: Engine) -> PersistRunPort:
    return partial(persist_complete_run, engine)


def persist_complete_run(engine: Engine, trace: RunTrace) -> None:
    _require_initial_events(trace.events)
    record = _run_record(trace)
    with engine.begin() as connection:
        _insert_graph(connection, record, trace)


def _insert_graph(
    connection: Connection, record: RunTraceRecord, trace: RunTrace
) -> None:
    insert_run_trace(connection, record)
    for sequence_no, event in enumerate(trace.events):
        event_id = uuid5(trace.run_id, f"{sequence_no}:{event.event_type}")
        insert_trace_event(connection, event_id, event, sequence_no)


def _require_initial_events(events: tuple[TraceEvent, ...]) -> None:
    if any(isinstance(event, HumanOverride) for event in events):
        raise ValueError("HUMAN_OVERRIDE must be persisted by the override workflow")


def _run_record(trace: RunTrace) -> RunTraceRecord:
    return RunTraceRecord(*_record_args(trace))


def _record_args(trace: RunTrace) -> RecordArgs:
    identity = trace.run_id, trace.config_hash, trace.dossier_id
    timing = trace.started_at, trace.ended_at
    tools = tuple(_tool_payload(call) for call in trace.tool_calls)
    llms = tuple(_llm_payload(call) for call in trace.llm_calls)
    outcome = trace.decision, trace.justification, trace.confidence
    return (*identity, *timing, tools, llms, *outcome)


def _tool_payload(call: ToolCallTrace) -> JsonObject:
    output = None if call.output is None else _json_object(call.output)
    return {
        "name": call.name,
        "input": _json_object(call.input),
        "output": output,
        "duration_ms": call.duration_ms,
        "error": call.error,
    }


def _llm_payload(call: LlmCallTrace) -> JsonObject:
    return {
        "prompt_rendered": call.prompt_rendered,
        "raw_response": call.raw_response,
        "tokens_in": call.tokens_in,
        "tokens_out": call.tokens_out,
        "latency_ms": call.latency_ms,
    }


def _json_object(value: Mapping[str, DomainJsonValue]) -> dict[str, JsonValue]:
    return {key: _json_value(item) for key, item in value.items()}


def _json_value(value: DomainJsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return _json_object(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value
