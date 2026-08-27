"""Reconstruct complete domain traces from their persisted audit graph."""

from collections.abc import Callable, Mapping
from datetime import datetime
from functools import partial
from typing import cast
from uuid import UUID

from sqlalchemy import Engine

from domain.decisions import Decision
from domain.events import TraceEvent
from domain.run import LlmCallTrace, RunTrace
from domain.tools import JsonValue as DomainJsonValue
from domain.tools import ToolCallTrace
from infrastructure.database.records import JsonObject, JsonValue, RunTraceRecord
from infrastructure.database.repositories import get_run_trace, list_trace_events

type RunTraceLookup = Callable[[UUID], RunTrace | None]
type TraceArgs = tuple[
    UUID,
    str,
    str,
    datetime,
    datetime,
    tuple[ToolCallTrace, ...],
    tuple[LlmCallTrace, ...],
    Decision,
    str,
    float,
    tuple[TraceEvent, ...],
]


def make_run_trace_lookup(engine: Engine) -> RunTraceLookup:
    return partial(load_complete_run, engine)


def load_complete_run(engine: Engine, run_id: UUID) -> RunTrace | None:
    with engine.connect() as connection:
        record = get_run_trace(connection, run_id)
        stored = list_trace_events(connection, run_id) if record else ()
    events = tuple(item.event for item in stored)
    return None if record is None else _run_trace(record, events)


def _run_trace(record: RunTraceRecord, events: tuple[TraceEvent, ...]) -> RunTrace:
    return RunTrace(*_trace_args(record, events))


def _trace_args(record: RunTraceRecord, events: tuple[TraceEvent, ...]) -> TraceArgs:
    identity = record.run_id, record.config_hash, record.dossier_id
    timing = record.started_at, record.ended_at
    calls = _tool_calls(record), _llm_calls(record)
    outcome = record.decision, record.justification, record.confidence
    return (*identity, *timing, *calls, *outcome, events)


def _tool_calls(record: RunTraceRecord) -> tuple[ToolCallTrace, ...]:
    return tuple(_tool_call(payload) for payload in record.tool_calls)


def _llm_calls(record: RunTraceRecord) -> tuple[LlmCallTrace, ...]:
    return tuple(_llm_call(payload) for payload in record.llm_calls)


def _tool_call(payload: JsonObject) -> ToolCallTrace:
    return ToolCallTrace(
        _string(payload, "name"),
        _object(payload, "input"),
        _optional_object(payload, "output"),
        _integer(payload, "duration_ms"),
        _optional_string(payload, "error"),
    )


def _llm_call(payload: JsonObject) -> LlmCallTrace:
    return LlmCallTrace(
        _string(payload, "prompt_rendered"),
        _string(payload, "raw_response"),
        _integer(payload, "tokens_in"),
        _integer(payload, "tokens_out"),
        _integer(payload, "latency_ms"),
    )


def _string(payload: JsonObject, name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ValueError(f"stored {name} must be a string")
    return value


def _optional_string(payload: JsonObject, name: str) -> str | None:
    value = payload.get(name)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"stored {name} must be a string or null")


def _integer(payload: JsonObject, name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"stored {name} must be an integer")
    return value


def _object(payload: JsonObject, name: str) -> Mapping[str, DomainJsonValue]:
    value = payload.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"stored {name} must be an object")
    return cast(Mapping[str, DomainJsonValue], value)


def _optional_object(
    payload: JsonObject, name: str
) -> Mapping[str, DomainJsonValue] | None:
    value: JsonValue = payload.get(name)
    if value is None:
        return None
    return _object(payload, name)
