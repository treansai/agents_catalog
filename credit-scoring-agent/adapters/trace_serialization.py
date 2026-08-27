"""Pure JSON serialization for complete run traces and their events."""

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from typing import cast
from uuid import UUID

from domain.events import TraceEvent
from domain.run import RunTrace

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


def serialize_run_trace(trace: RunTrace) -> JsonObject:
    return cast(JsonObject, _json_value(asdict(trace)))


def serialize_trace_event(event: TraceEvent) -> JsonObject:
    return cast(JsonObject, _json_value(asdict(event)))


def _json_value(value: object) -> JsonValue:
    if isinstance(value, UUID | datetime):
        return _formatted(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported trace value: {type(value).__name__}")


def _formatted(value: UUID | datetime) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)
