from dataclasses import FrozenInstanceError

import pytest

from domain.tools import JsonObject, ListStatus, ToolCallTrace


def test_list_status_represents_listed_and_clear_dossiers() -> None:
    assert ListStatus("147", True, "Incident synthétique").is_listed
    assert ListStatus("148", False, None).reason is None


@pytest.mark.parametrize(
    ("dossier_id", "is_listed", "reason", "message"),
    [
        (" ", False, None, "dossier_id"),
        ("147", True, None, "consistent"),
        ("147", False, "Incident", "consistent"),
        ("147", True, "\t", "reason"),
    ],
)
def test_list_status_rejects_inconsistent_values(
    dossier_id: str, is_listed: bool, reason: str | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ListStatus(dossier_id, is_listed, reason)


def test_tool_call_trace_accepts_success_and_failure() -> None:
    success = ToolCallTrace("fetch_dossier", {"dossier_id": "147"}, {}, 4, None)
    failure = ToolCallTrace("fetch_dossier", {}, None, 9, "timeout")
    assert success.output == {}
    assert failure.error == "timeout"


@pytest.mark.parametrize(
    ("name", "output", "duration_ms", "error", "message"),
    [
        (" ", None, 0, None, "name"),
        ("fetch_dossier", None, -1, None, "duration_ms"),
        ("fetch_dossier", None, 0, None, "exactly one"),
        ("fetch_dossier", {}, 0, "failure", "exactly one"),
        ("fetch_dossier", None, 0, " ", "error"),
    ],
)
def test_tool_call_trace_rejects_invalid_metadata(
    name: str,
    output: JsonObject | None,
    duration_ms: int,
    error: str | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ToolCallTrace(name, {}, output, duration_ms, error)


@pytest.mark.parametrize(
    ("value", "field_name"),
    [
        (ListStatus("147", False, None), "reason"),
        (ToolCallTrace("tool", {}, {}, 0, None), "error"),
    ],
)
def test_tool_dtos_are_immutable(value: object, field_name: str) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(value, field_name, "changed")
