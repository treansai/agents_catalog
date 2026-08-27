from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from domain.configuration import AgentConfig, config_hash
from domain.decisions import Decision
from domain.events import TraceEvent

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class AgentConfigRecord:
    config_hash: str
    label: str
    config: AgentConfig
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_agent_config_record(self)


@dataclass(frozen=True, slots=True)
class InternalListEntry:
    dossier_id: str
    reason: str
    listed_at: datetime

    def __post_init__(self) -> None:
        _require_non_blank(self.dossier_id, "dossier_id")
        _require_non_blank(self.reason, "reason")
        _require_utc(self.listed_at, "listed_at")


@dataclass(frozen=True, slots=True)
class RunTraceRecord:
    run_id: UUID
    config_hash: str
    dossier_id: str
    started_at: datetime
    ended_at: datetime
    tool_calls: tuple[JsonObject, ...]
    llm_calls: tuple[JsonObject, ...]
    decision: Decision
    justification: str
    confidence: float


@dataclass(frozen=True, slots=True)
class StoredTraceEvent:
    event_id: UUID
    event: TraceEvent


def _validate_agent_config_record(record: AgentConfigRecord) -> None:
    if record.config_hash != config_hash(record.config):
        raise ValueError("config_hash does not match config")
    _require_non_blank(record.label, "label")
    _require_utc(record.created_at, "created_at")


def _require_non_blank(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
