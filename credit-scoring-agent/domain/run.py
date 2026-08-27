"""Immutable execution trace contract for an observable agent run."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from domain._validation import require_finite_range, require_non_blank
from domain.decisions import Decision
from domain.events import DecisionEmitted, HumanOverride, TraceEvent
from domain.overrides import effective_decision
from domain.tools import TOOL_NAMES, ToolCallTrace


@dataclass(frozen=True, slots=True)
class LlmCallTrace:
    prompt_rendered: str
    raw_response: str
    tokens_in: int
    tokens_out: int
    latency_ms: int

    def __post_init__(self) -> None:
        _validate_llm_call(self)


@dataclass(frozen=True, slots=True)
class RunTrace:
    run_id: UUID
    config_hash: str
    dossier_id: str
    started_at: datetime
    ended_at: datetime
    tool_calls: tuple[ToolCallTrace, ...]
    llm_calls: tuple[LlmCallTrace, ...]
    decision: Decision
    justification: str
    confidence: float
    events: tuple[TraceEvent, ...]

    def __post_init__(self) -> None:
        _validate_run(self)


type PersistRunPort = Callable[[RunTrace], None]


def _validate_llm_call(call: LlmCallTrace) -> None:
    require_non_blank(call.prompt_rendered, "prompt_rendered")
    require_non_blank(call.raw_response, "raw_response")
    if min(call.tokens_in, call.tokens_out, call.latency_ms) < 0:
        raise ValueError("LLM metrics must not be negative")


def _validate_run(trace: RunTrace) -> None:
    require_non_blank(trace.config_hash, "config_hash")
    require_non_blank(trace.dossier_id, "dossier_id")
    require_non_blank(trace.justification, "justification")
    require_finite_range(trace.confidence, 0.0, 1.0, "confidence")
    _validate_timing(trace)
    _validate_calls(trace)
    _validate_events(trace)


def _validate_timing(trace: RunTrace) -> None:
    if trace.started_at.utcoffset() != timedelta(0):
        raise ValueError("started_at must be timezone-aware UTC")
    if trace.ended_at.utcoffset() != timedelta(0):
        raise ValueError("ended_at must be timezone-aware UTC")
    if trace.ended_at < trace.started_at:
        raise ValueError("ended_at must not precede started_at")


def _validate_calls(trace: RunTrace) -> None:
    names = tuple(call.name for call in trace.tool_calls)
    if names != TOOL_NAMES:
        raise ValueError("tool calls must match the versioned tool contract")
    if not trace.llm_calls:
        raise ValueError("a decided run must contain an LLM call")


def _validate_events(trace: RunTrace) -> None:
    if any(event.run_id != trace.run_id for event in trace.events):
        raise ValueError("every event must belong to the run")
    _validate_emitted(trace, _emitted_event(trace))
    _validate_human_timing(trace)
    effective_decision(trace.decision, trace.events)


def _emitted_event(trace: RunTrace) -> DecisionEmitted:
    emitted = tuple(
        event for event in trace.events if isinstance(event, DecisionEmitted)
    )
    if len(emitted) != 1:
        raise ValueError("a run must contain exactly one DECISION_EMITTED event")
    return emitted[0]


def _validate_emitted(trace: RunTrace, emitted: DecisionEmitted) -> None:
    expected = trace.decision, trace.justification, trace.confidence
    actual = emitted.decision, emitted.justification, emitted.confidence
    if actual != expected:
        raise ValueError("DECISION_EMITTED must match the run outcome")


def _validate_human_timing(trace: RunTrace) -> None:
    invalid = any(
        isinstance(event, HumanOverride) and event.occurred_at < trace.ended_at
        for event in trace.events
    )
    if invalid:
        raise ValueError("HUMAN_OVERRIDE must occur after the run ended")
