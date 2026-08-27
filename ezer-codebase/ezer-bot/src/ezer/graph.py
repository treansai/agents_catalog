"""LangGraph workflow for safe, checkpointable multi-agent email analysis."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal, NotRequired, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Checkpointer, RetryPolicy
from typing_extensions import TypedDict

from ezer.agents import AgentRuntime
from ezer.domain import (
    ActionItem,
    EmailAnalysis,
    EmailCategory,
    EmailEnvelope,
    Priority,
    SafetyAssessment,
    SummaryResult,
    TaskExtractionResult,
    TriageResult,
)
from ezer.security import deterministic_injection_indicators

GRAPH_VERSION: Final = "email-analysis-graph-2026-08-27.2"

SECURITY_NODE: Final = "security"
RESTRICTED_SUMMARY_NODE: Final[Literal["restricted_summary"]] = "restricted_summary"
TRIAGE_NODE: Final[Literal["triage"]] = "triage"
SUMMARY_NODE: Final = "summary"
TASK_NODE: Final = "tasks"
AGGREGATE_NODE: Final = "aggregate"


class EmailGraphInput(TypedDict):
    """Checkpoint-safe input; deliberately excludes the email body and headers."""

    message_ref: str
    content_hash: str
    analysis_id: NotRequired[str]


class EmailGraphState(EmailGraphInput, total=False):
    """Internal checkpoint state containing only references and structured results."""

    safety: SafetyAssessment
    triage: TriageResult
    summary: SummaryResult
    tasks: TaskExtractionResult
    analysis: EmailAnalysis


class EmailGraphOutput(TypedDict):
    analysis: EmailAnalysis


@dataclass(frozen=True, slots=True)
class EmailGraphContext:
    """Non-checkpointed dependencies and sensitive email data for one graph run."""

    envelope: EmailEnvelope
    agents: AgentRuntime
    pipeline_version: str
    output_language: str = "fr"
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.pipeline_version.strip():
            raise ValueError("pipeline_version must not be empty")
        if not self.output_language.strip():
            raise ValueError("output_language must not be empty")
        normalized = self.analyzed_at
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=UTC)
        else:
            normalized = normalized.astimezone(UTC)
        object.__setattr__(self, "analyzed_at", normalized)


type EmailAnalysisGraph = CompiledStateGraph[
    EmailGraphState,
    EmailGraphContext,
    EmailGraphInput,
    EmailGraphOutput,
]


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def make_email_graph_input(
    envelope: EmailEnvelope,
    *,
    analysis_id: str | None = None,
) -> EmailGraphInput:
    """Create the complete checkpoint input without exposing email content."""

    graph_input: EmailGraphInput = {
        "message_ref": envelope.message_ref(),
        "content_hash": envelope.content_hash(),
    }
    if analysis_id is not None:
        if not _is_sha256(analysis_id):
            raise ValueError("analysis_id must be a lowercase SHA-256 hex digest")
        graph_input["analysis_id"] = analysis_id
    return graph_input


def _validated_context(
    state: EmailGraphState,
    runtime: Runtime[EmailGraphContext],
) -> EmailGraphContext:
    context = runtime.context
    if state["message_ref"] != context.envelope.message_ref():
        raise ValueError("graph input message_ref does not match runtime envelope")
    if state["content_hash"] != context.envelope.content_hash():
        raise ValueError("graph input content_hash does not match runtime envelope")
    return context


async def _security_node(
    state: EmailGraphState,
    runtime: Runtime[EmailGraphContext],
) -> dict[str, object]:
    context = _validated_context(state, runtime)
    safety = await context.agents.assess_safety(context.envelope)
    deterministic = deterministic_injection_indicators(context.envelope)
    if deterministic:
        indicators = list(dict.fromkeys([*safety.indicators, *deterministic]))[:12]
        safety = safety.model_copy(
            update={
                "risk_level": "high",
                "prompt_injection_detected": True,
                "indicators": indicators,
                "confidence": max(safety.confidence, 0.95),
            }
        )
    return {"safety": safety}


def _route_after_security(
    state: EmailGraphState,
) -> Literal["restricted_summary", "triage"]:
    safety = state.get("safety")
    if safety is None:
        raise ValueError("security node did not produce a safety assessment")
    if safety.risk_level == "high":
        return RESTRICTED_SUMMARY_NODE
    return TRIAGE_NODE


async def _restricted_summary_node(
    state: EmailGraphState,
    runtime: Runtime[EmailGraphContext],
) -> dict[str, object]:
    context = _validated_context(state, runtime)
    summary = await context.agents.summarize(
        context.envelope,
        output_language=context.output_language,
        restricted=True,
    )
    return {"summary": summary.model_copy(update={"restricted": True})}


async def _triage_node(
    state: EmailGraphState,
    runtime: Runtime[EmailGraphContext],
) -> dict[str, object]:
    context = _validated_context(state, runtime)
    triage = await context.agents.triage(context.envelope)
    return {"triage": triage}


async def _summary_node(
    state: EmailGraphState,
    runtime: Runtime[EmailGraphContext],
) -> dict[str, object]:
    context = _validated_context(state, runtime)
    summary = await context.agents.summarize(
        context.envelope,
        output_language=context.output_language,
        restricted=False,
    )
    return {"summary": summary.model_copy(update={"restricted": False})}


async def _task_node(
    state: EmailGraphState,
    runtime: Runtime[EmailGraphContext],
) -> dict[str, object]:
    context = _validated_context(state, runtime)
    tasks = await context.agents.extract_tasks(context.envelope)
    return {"tasks": tasks}


def _require_state_value(state: EmailGraphState, key: str, expected: type[object]) -> object:
    value = state.get(key)
    if not isinstance(value, expected):
        raise ValueError(f"graph state is missing valid {key}")
    return value


def _analysis_id(state: EmailGraphState, context: EmailGraphContext) -> str:
    supplied_analysis_id = state.get("analysis_id")
    if supplied_analysis_id is not None:
        if not _is_sha256(supplied_analysis_id):
            raise ValueError("graph input analysis_id is not a lowercase SHA-256 hex digest")
        return supplied_analysis_id

    payload = "\x1f".join(
        (
            state["message_ref"],
            state["content_hash"],
            context.pipeline_version,
            context.agents.model_id,
            context.agents.prompt_version,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _aggregate_node(
    state: EmailGraphState,
    runtime: Runtime[EmailGraphContext],
) -> dict[str, object]:
    context = _validated_context(state, runtime)
    safety = cast(
        SafetyAssessment,
        _require_state_value(state, "safety", SafetyAssessment),
    )
    summary = cast(
        SummaryResult,
        _require_state_value(state, "summary", SummaryResult),
    )

    if safety.risk_level == "high":
        category: EmailCategory = "security"
        priority: Priority = "critical"
        needs_human_review = True
        triage: TriageResult | None = None
        action_items: list[ActionItem] = []
    else:
        triage = cast(
            TriageResult,
            _require_state_value(state, "triage", TriageResult),
        )
        tasks = cast(
            TaskExtractionResult,
            _require_state_value(state, "tasks", TaskExtractionResult),
        )
        category = triage.category
        priority = triage.priority
        needs_human_review = triage.needs_human_review
        action_items = tasks.action_items

    analysis = EmailAnalysis(
        analysis_id=_analysis_id(state, context),
        message_ref=state["message_ref"],
        content_hash=state["content_hash"],
        pipeline_version=context.pipeline_version,
        model_id=context.agents.model_id,
        prompt_version=context.agents.prompt_version,
        created_at=context.analyzed_at,
        category=category,
        priority=priority,
        needs_human_review=needs_human_review,
        summary=summary.summary,
        key_points=summary.key_points,
        action_items=action_items,
        safety=safety,
        triage=triage,
        detected_language=summary.detected_language,
    )
    return {"analysis": analysis}


def build_email_analysis_graph(
    *,
    checkpointer: Checkpointer = None,
    max_attempts: int = 3,
) -> EmailAnalysisGraph:
    """Build the security-first multi-agent graph.

    Normal summaries and task extraction fan out in the same LangGraph superstep. The aggregate
    node waits for both and constructs the final domain object without another model call.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    retry_policy = RetryPolicy(max_attempts=max_attempts)

    builder = StateGraph(
        EmailGraphState,
        context_schema=EmailGraphContext,
        input_schema=EmailGraphInput,
        output_schema=EmailGraphOutput,
    )
    builder.add_node(SECURITY_NODE, _security_node, retry_policy=retry_policy)
    builder.add_node(
        RESTRICTED_SUMMARY_NODE,
        _restricted_summary_node,
        retry_policy=retry_policy,
    )
    builder.add_node(TRIAGE_NODE, _triage_node, retry_policy=retry_policy)
    builder.add_node(SUMMARY_NODE, _summary_node, retry_policy=retry_policy)
    builder.add_node(TASK_NODE, _task_node, retry_policy=retry_policy)
    builder.add_node(AGGREGATE_NODE, _aggregate_node)

    builder.add_edge(START, SECURITY_NODE)
    builder.add_conditional_edges(
        SECURITY_NODE,
        _route_after_security,
        {
            RESTRICTED_SUMMARY_NODE: RESTRICTED_SUMMARY_NODE,
            TRIAGE_NODE: TRIAGE_NODE,
        },
    )
    builder.add_edge(RESTRICTED_SUMMARY_NODE, AGGREGATE_NODE)
    builder.add_edge(TRIAGE_NODE, SUMMARY_NODE)
    builder.add_edge(TRIAGE_NODE, TASK_NODE)
    builder.add_edge([SUMMARY_NODE, TASK_NODE], AGGREGATE_NODE)
    builder.add_edge(AGGREGATE_NODE, END)

    return builder.compile(checkpointer=checkpointer, name="ezer-email-analysis")


async def analyze_email(
    graph: EmailAnalysisGraph,
    envelope: EmailEnvelope,
    agents: AgentRuntime,
    *,
    pipeline_version: str,
    analysis_id: str | None = None,
    output_language: str = "fr",
    analyzed_at: datetime | None = None,
    config: RunnableConfig | None = None,
) -> EmailAnalysis:
    """Run one analysis while keeping the sensitive envelope out of checkpoint state."""

    context = EmailGraphContext(
        envelope=envelope,
        agents=agents,
        pipeline_version=pipeline_version,
        output_language=output_language,
        analyzed_at=analyzed_at or datetime.now(UTC),
    )
    output = await graph.ainvoke(
        make_email_graph_input(envelope, analysis_id=analysis_id),
        config,
        context=context,
    )
    analysis = output.get("analysis")
    if isinstance(analysis, EmailAnalysis):
        return analysis
    return EmailAnalysis.model_validate(analysis)
