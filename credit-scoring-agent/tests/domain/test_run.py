from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from domain.events import DecisionEmitted, HumanOverride, ToolFailure
from domain.run import LlmCallTrace, RunTrace
from domain.tools import ToolCallTrace

RUN_ID = UUID("20000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 8, 28, 10, tzinfo=UTC)
TOOL_CALL = ToolCallTrace("fetch_dossier", {"dossier_id": "147"}, {"age": 54}, 2, None)
RATIO_CALL = ToolCallTrace("compute_debt_ratio", {}, {"ratio": "29.00"}, 1, None)
LIST_CALL = ToolCallTrace("check_internal_list", {}, {"is_listed": False}, 1, None)
LLM_CALL = LlmCallTrace("prompt", '{"response":"raw"}', 120, 32, 18)
EMITTED = DecisionEmitted(RUN_ID, "REFUS", "Taux 29.00 %.", 0.87, NOW)
TRACE = RunTrace(
    RUN_ID,
    "a" * 64,
    "147",
    NOW,
    NOW + timedelta(seconds=1),
    (TOOL_CALL, RATIO_CALL, LIST_CALL),
    (LLM_CALL,),
    "REFUS",
    "Taux 29.00 %.",
    0.87,
    (EMITTED,),
)
HUMAN = HumanOverride(RUN_ID, "analyst-7", "REFUS", "ACCORD", "Revue", TRACE.ended_at)


def test_run_trace_is_immutable_and_preserves_evidence() -> None:
    assert TRACE.tool_calls == (TOOL_CALL, RATIO_CALL, LIST_CALL)
    assert TRACE.llm_calls == (LLM_CALL,)
    field_name = "decision"
    with pytest.raises(FrozenInstanceError):
        setattr(TRACE, field_name, "ACCORD")


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: replace(LLM_CALL, prompt_rendered=" "), "prompt_rendered"),
        (lambda: replace(LLM_CALL, raw_response=""), "raw_response"),
        (lambda: replace(LLM_CALL, tokens_in=-1), "metrics"),
        (lambda: replace(LLM_CALL, tokens_out=-1), "metrics"),
        (lambda: replace(LLM_CALL, latency_ms=-1), "metrics"),
    ],
)
def test_llm_call_trace_rejects_invalid_values(
    factory: Callable[[], LlmCallTrace], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: replace(TRACE, started_at=NOW.replace(tzinfo=None)), "started_at"),
        (lambda: replace(TRACE, ended_at=NOW.replace(tzinfo=None)), "ended_at"),
        (lambda: replace(TRACE, ended_at=NOW - timedelta(seconds=1)), "precede"),
        (lambda: replace(TRACE, tool_calls=()), "tool contract"),
        (lambda: replace(TRACE, llm_calls=()), "LLM call"),
        (lambda: replace(TRACE, events=()), "exactly one"),
        (lambda: replace(TRACE, events=(EMITTED, EMITTED)), "exactly one"),
        (lambda: replace(TRACE, events=(_foreign_event(),)), "belong"),
        (lambda: replace(TRACE, decision="ACCORD"), "match"),
        (lambda: replace(TRACE, justification="Autre valeur 1.00"), "match"),
        (lambda: replace(TRACE, confidence=0.2), "match"),
        (
            lambda: replace(TRACE, events=(EMITTED, replace(HUMAN, occurred_at=NOW))),
            "after the run",
        ),
    ],
)
def test_run_trace_rejects_inconsistent_graphs(
    factory: Callable[[], RunTrace], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def _foreign_event() -> DecisionEmitted:
    other_run = UUID("20000000-0000-0000-0000-000000000002")
    return replace(EMITTED, run_id=other_run)


def test_run_trace_accepts_a_tool_failure_before_the_decision() -> None:
    failure = ToolFailure(RUN_ID, "check_internal_list", "timeout", NOW)
    trace = replace(TRACE, events=(failure, EMITTED))
    assert trace.events == (failure, EMITTED)


def test_run_trace_accepts_a_human_override_after_the_run() -> None:
    trace = replace(TRACE, events=(EMITTED, HUMAN))
    assert trace.events[-1] == HUMAN
