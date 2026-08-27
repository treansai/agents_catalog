"""Pure tool contracts and trace DTOs owned by the domain."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from domain._validation import require_non_blank
from domain.dossier import Dossier

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | Mapping[str, JsonValue]
type JsonObject = Mapping[str, JsonValue]
type DossierLookup = Callable[[str], Dossier | None]
type InternalListLookup = Callable[[str], str | None]

TOOLS_VERSION = "1.0.0"
TOOL_NAMES = ("fetch_dossier", "compute_debt_ratio", "check_internal_list")


@dataclass(frozen=True, slots=True)
class ListStatus:
    dossier_id: str
    is_listed: bool
    reason: str | None

    def __post_init__(self) -> None:
        _validate_list_status(self)


@dataclass(frozen=True, slots=True)
class ToolCallTrace:
    name: str
    input: JsonObject
    output: JsonObject | None
    duration_ms: int
    error: str | None

    def __post_init__(self) -> None:
        _validate_tool_call(self)


def _validate_list_status(status: ListStatus) -> None:
    require_non_blank(status.dossier_id, "dossier_id")
    if status.is_listed != (status.reason is not None):
        raise ValueError("is_listed and reason must be consistent")
    if status.reason is not None:
        require_non_blank(status.reason, "reason")


def _validate_tool_call(call: ToolCallTrace) -> None:
    require_non_blank(call.name, "name")
    if call.duration_ms < 0:
        raise ValueError("duration_ms must not be negative")
    if (call.output is None) != (call.error is not None):
        raise ValueError("exactly one of output or error must be present")
    if call.error is not None:
        require_non_blank(call.error, "error")
