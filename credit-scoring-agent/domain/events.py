from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from domain._validation import require_finite_range, require_non_blank, require_utc
from domain.decisions import Decision


@dataclass(frozen=True, slots=True)
class DecisionEmitted:
    run_id: UUID
    decision: Decision
    justification: str
    confidence: float
    occurred_at: datetime
    event_type: Literal["DECISION_EMITTED"] = field(
        default="DECISION_EMITTED", init=False
    )

    def __post_init__(self) -> None:
        require_non_blank(self.justification, "justification")
        require_finite_range(self.confidence, 0.0, 1.0, "confidence")
        require_utc(self.occurred_at)


@dataclass(frozen=True, slots=True)
class ToolFailure:
    run_id: UUID
    tool_name: str
    error: str
    occurred_at: datetime
    event_type: Literal["TOOL_FAILURE"] = field(default="TOOL_FAILURE", init=False)

    def __post_init__(self) -> None:
        require_non_blank(self.tool_name, "tool_name")
        require_non_blank(self.error, "error")
        require_utc(self.occurred_at)


@dataclass(frozen=True, slots=True)
class GuardrailOverride:
    run_id: UUID
    guardrail_id: str
    original_decision: Decision
    corrected_decision: Decision
    reason: str
    occurred_at: datetime
    event_type: Literal["GUARDRAIL_OVERRIDE"] = field(
        default="GUARDRAIL_OVERRIDE", init=False
    )

    def __post_init__(self) -> None:
        require_non_blank(self.guardrail_id, "guardrail_id")
        require_non_blank(self.reason, "reason")
        require_utc(self.occurred_at)


@dataclass(frozen=True, slots=True)
class HumanOverride:
    run_id: UUID
    operator_id: str
    original_decision: Decision
    corrected_decision: Decision
    reason: str
    occurred_at: datetime
    event_type: Literal["HUMAN_OVERRIDE"] = field(default="HUMAN_OVERRIDE", init=False)

    def __post_init__(self) -> None:
        require_non_blank(self.operator_id, "operator_id")
        require_non_blank(self.reason, "reason")
        require_utc(self.occurred_at)


type TraceEvent = DecisionEmitted | ToolFailure | GuardrailOverride | HumanOverride
