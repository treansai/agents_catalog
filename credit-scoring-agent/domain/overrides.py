"""Pure human-override commands and effective-decision calculation."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from domain._validation import require_non_blank, require_utc
from domain.decisions import Decision
from domain.events import GuardrailOverride, HumanOverride, TraceEvent


@dataclass(frozen=True, slots=True)
class OverrideCommand:
    run_id: UUID
    operator_id: str
    corrected_decision: Decision
    reason: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        _validate_command(self)


type HumanOverridePort = Callable[[OverrideCommand], HumanOverride]
type HumanOverrideArgs = tuple[UUID, str, Decision, Decision, str, datetime]


def _validate_command(command: OverrideCommand) -> None:
    require_non_blank(command.operator_id, "operator_id")
    require_non_blank(command.reason, "reason")
    require_utc(command.occurred_at)


def _human_override_args(
    command: OverrideCommand, original_decision: Decision
) -> HumanOverrideArgs:
    identity = command.run_id, command.operator_id, original_decision
    correction = command.corrected_decision, command.reason, command.occurred_at
    return (*identity, *correction)


def build_human_override(
    command: OverrideCommand, original_decision: Decision
) -> HumanOverride:
    return HumanOverride(*_human_override_args(command, original_decision))


def effective_decision(initial: Decision, events: Iterable[TraceEvent]) -> Decision:
    decision = initial
    for event in events:
        decision = _apply_event(decision, event)
    return decision


def _apply_event(decision: Decision, event: TraceEvent) -> Decision:
    if not isinstance(event, GuardrailOverride | HumanOverride):
        return decision
    if event.original_decision != decision:
        raise ValueError("override decision chain is inconsistent")
    return event.corrected_decision
