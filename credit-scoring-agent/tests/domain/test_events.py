from collections.abc import Callable
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from domain.decisions import Decision, parse_decision
from domain.events import (
    DecisionEmitted,
    GuardrailOverride,
    HumanOverride,
    ToolFailure,
    TraceEvent,
)

RUN_ID = UUID("6f80d97b-3abc-4100-8f2d-cbb827c246e5")
OCCURRED_AT = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
DECISION_EVENT = DecisionEmitted(
    run_id=RUN_ID,
    decision="REFUS",
    justification="Taux d'endettement de 47,20 %.",
    confidence=0.82,
    occurred_at=OCCURRED_AT,
)
TOOL_EVENT = ToolFailure(
    run_id=RUN_ID,
    tool_name="fetch_dossier",
    error="dossier not found",
    occurred_at=OCCURRED_AT,
)
GUARDRAIL_EVENT = GuardrailOverride(
    run_id=RUN_ID,
    guardrail_id="manual-review-on-tool-failure",
    original_decision="ACCORD",
    corrected_decision="INSTRUCTION_MANUELLE",
    reason="A tool failed.",
    occurred_at=OCCURRED_AT,
)
HUMAN_EVENT = HumanOverride(
    run_id=RUN_ID,
    operator_id="analyst-42",
    original_decision="REFUS",
    corrected_decision="ACCORD",
    reason="Supporting documents were verified.",
    occurred_at=OCCURRED_AT,
)
EVENTS: tuple[TraceEvent, ...] = (
    DECISION_EVENT,
    TOOL_EVENT,
    GUARDRAIL_EVENT,
    HUMAN_EVENT,
)


@pytest.mark.parametrize("value", ["ACCORD", "REFUS", "INSTRUCTION_MANUELLE"])
def test_parse_decision_accepts_wire_values(value: Decision) -> None:
    assert parse_decision(value) == value


def test_parse_decision_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="unknown decision"):
        parse_decision("REVIEW")


def test_events_have_explicit_serialized_discriminants() -> None:
    expected = (
        "DECISION_EMITTED",
        "TOOL_FAILURE",
        "GUARDRAIL_OVERRIDE",
        "HUMAN_OVERRIDE",
    )
    assert tuple(asdict(event)["event_type"] for event in EVENTS) == expected


def test_human_override_carries_required_audit_fields() -> None:
    assert HUMAN_EVENT.run_id == RUN_ID
    assert HUMAN_EVENT.operator_id == "analyst-42"
    assert HUMAN_EVENT.original_decision == "REFUS"
    assert HUMAN_EVENT.corrected_decision == "ACCORD"
    assert HUMAN_EVENT.reason == "Supporting documents were verified."
    assert HUMAN_EVENT.occurred_at == OCCURRED_AT


@pytest.mark.parametrize("reason", ["", " ", "\t\n"])
def test_human_override_rejects_blank_reason(reason: str) -> None:
    with pytest.raises(ValueError, match="reason"):
        replace(HUMAN_EVENT, reason=reason)


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda: replace(DECISION_EVENT, justification=" "), "justification"),
        (lambda: replace(TOOL_EVENT, tool_name=" "), "tool_name"),
        (lambda: replace(TOOL_EVENT, error=" "), "error"),
        (lambda: replace(GUARDRAIL_EVENT, guardrail_id=" "), "guardrail_id"),
        (lambda: replace(GUARDRAIL_EVENT, reason=" "), "reason"),
        (lambda: replace(HUMAN_EVENT, operator_id=" "), "operator_id"),
    ],
)
def test_events_reject_other_blank_audit_fields(
    factory: Callable[[], TraceEvent], field_name: str
) -> None:
    with pytest.raises(ValueError, match=field_name):
        factory()


@pytest.mark.parametrize("confidence", [float("nan"), -0.01, 1.01])
def test_decision_event_rejects_invalid_confidence(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        replace(DECISION_EVENT, confidence=confidence)


@pytest.mark.parametrize(
    "occurred_at",
    [
        datetime(2026, 8, 27, 12, 0),
        datetime(2026, 8, 27, 12, 0, tzinfo=timezone(timedelta(hours=1))),
    ],
)
def test_events_reject_non_utc_timestamps(occurred_at: datetime) -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        replace(HUMAN_EVENT, occurred_at=occurred_at)


@pytest.mark.parametrize("event", EVENTS)
def test_events_are_immutable(event: TraceEvent) -> None:
    field_name = "event_type"
    with pytest.raises(FrozenInstanceError):
        setattr(event, field_name, "changed")
