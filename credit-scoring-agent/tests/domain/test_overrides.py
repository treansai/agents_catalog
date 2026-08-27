from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from domain.events import DecisionEmitted, GuardrailOverride, HumanOverride, ToolFailure
from domain.overrides import (
    HumanOverridePort,
    OverrideCommand,
    build_human_override,
    effective_decision,
)

RUN_ID = UUID("40000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)
COMMAND = OverrideCommand(RUN_ID, "analyst-7", "REFUS", "Revue humaine", NOW)
GUARDRAIL = GuardrailOverride(
    RUN_ID, "manual-review", "ACCORD", "INSTRUCTION_MANUELLE", "Contrôle", NOW
)
HUMAN = HumanOverride(
    RUN_ID, "analyst-7", "INSTRUCTION_MANUELLE", "REFUS", "Revue humaine", NOW
)


def test_build_human_override_preserves_every_audit_field() -> None:
    event = build_human_override(COMMAND, "INSTRUCTION_MANUELLE")
    assert event == HUMAN


def test_human_override_port_can_be_satisfied_by_a_pure_function() -> None:
    port: HumanOverridePort = _build_from_manual_review
    assert port(COMMAND) == HUMAN


def _build_from_manual_review(command: OverrideCommand) -> HumanOverride:
    return build_human_override(command, "INSTRUCTION_MANUELLE")


def test_effective_decision_returns_initial_without_overrides() -> None:
    assert effective_decision("ACCORD", ()) == "ACCORD"


def test_effective_decision_ignores_non_override_events() -> None:
    emitted = DecisionEmitted(RUN_ID, "ACCORD", "Motif", 0.8, NOW)
    failure = ToolFailure(RUN_ID, "outil", "erreur", NOW)
    assert effective_decision("ACCORD", (emitted, failure)) == "ACCORD"


def test_effective_decision_applies_overrides_in_trace_order() -> None:
    assert effective_decision("ACCORD", (GUARDRAIL, HUMAN)) == "REFUS"


def test_effective_decision_rejects_an_inconsistent_chain() -> None:
    with pytest.raises(ValueError, match="chain is inconsistent"):
        effective_decision("ACCORD", (HUMAN, GUARDRAIL))


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda: replace(COMMAND, operator_id=""), "operator_id"),
        (lambda: replace(COMMAND, operator_id=" \t"), "operator_id"),
        (lambda: replace(COMMAND, reason=""), "reason"),
        (lambda: replace(COMMAND, reason="\n"), "reason"),
    ],
)
def test_override_command_rejects_blank_fields(
    factory: Callable[[], OverrideCommand], field_name: str
) -> None:
    with pytest.raises(ValueError, match=field_name):
        factory()


@pytest.mark.parametrize(
    "occurred_at",
    [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=1)))],
)
def test_override_command_requires_utc(occurred_at: datetime) -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        replace(COMMAND, occurred_at=occurred_at)


def test_override_command_is_immutable() -> None:
    field_name = "reason"
    with pytest.raises(FrozenInstanceError):
        setattr(COMMAND, field_name, "changed")
