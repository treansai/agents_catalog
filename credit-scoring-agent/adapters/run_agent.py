"""LangGraph orchestration of one observable credit recommendation run."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from itertools import pairwise
from typing import TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from adapters.prompting import render_prompt
from adapters.tools import (
    check_internal_list,
    compute_debt_ratio,
    debt_ratio_to_payload,
    dossier_to_payload,
    fetch_dossier,
    list_status_to_payload,
)
from domain._validation import require_non_blank
from domain.configuration import AgentConfig, config_hash
from domain.decisions import Decision
from domain.dossier import Dossier
from domain.events import DecisionEmitted, ToolFailure, TraceEvent
from domain.llm import LlmPort, LlmRequest, LlmResponse
from domain.run import LlmCallTrace, PersistRunPort, RunTrace
from domain.tools import (
    TOOLS_VERSION,
    DossierLookup,
    InternalListLookup,
    JsonObject,
    ToolCallTrace,
)

type ClockNs = Callable[[], int]
type UtcClock = Callable[[], datetime]
type UuidFactory = Callable[[], UUID]
type ConfigLookup = Callable[[str], AgentConfig | None]
type Spec[Result] = tuple[
    str, JsonObject, Callable[[], Result], Callable[[Result], JsonObject]
]
type Captured[Result] = tuple[Result | None, ToolCallTrace]
type RunTraceArgs = tuple[
    UUID,
    str,
    str,
    datetime,
    datetime,
    tuple[ToolCallTrace, ...],
    tuple[LlmCallTrace, ...],
    Decision,
    str,
    float,
    tuple[TraceEvent, ...],
]


class GraphUpdate(TypedDict, total=False):
    config: AgentConfig
    run_id: UUID
    started_at: datetime
    dossier: Dossier
    tool_calls: tuple[ToolCallTrace, ...]
    prompt: str
    response: LlmResponse
    ended_at: datetime
    trace: RunTrace


@dataclass(frozen=True, slots=True)
class RunRequest:
    config_hash: str
    dossier_id: str

    def __post_init__(self) -> None:
        require_non_blank(self.config_hash, "config_hash")
        require_non_blank(self.dossier_id, "dossier_id")


@dataclass(frozen=True, slots=True)
class RunPorts:
    config_lookup: ConfigLookup
    dossier_lookup: DossierLookup
    internal_list_lookup: InternalListLookup
    llm: LlmPort
    persist_run: PersistRunPort
    utc_now: UtcClock
    clock_ns: ClockNs
    new_uuid: UuidFactory


@dataclass(frozen=True, slots=True)
class RunEvidence:
    dossier: Dossier
    tool_calls: tuple[ToolCallTrace, ...]


@dataclass(frozen=True, slots=True)
class RunBuild:
    request: RunRequest
    run_id: UUID
    started_at: datetime
    ended_at: datetime
    evidence: RunEvidence
    prompt: str


@dataclass(frozen=True, slots=True)
class AgentGraphInput:
    request: RunRequest


@dataclass(frozen=True, slots=True)
class AgentGraphOutput:
    trace: RunTrace


@dataclass(frozen=True, slots=True)
class AgentGraphState:
    request: RunRequest
    config: AgentConfig | None = None
    run_id: UUID | None = None
    started_at: datetime | None = None
    dossier: Dossier | None = None
    tool_calls: tuple[ToolCallTrace, ...] = ()
    prompt: str | None = None
    response: LlmResponse | None = None
    ended_at: datetime | None = None
    trace: RunTrace | None = None


type AgentGraph = CompiledStateGraph[
    AgentGraphState, RunPorts, AgentGraphInput, AgentGraphOutput
]
type AgentGraphBuilder = StateGraph[
    AgentGraphState, RunPorts, AgentGraphInput, AgentGraphOutput
]

GRAPH_NODE_NAMES = (
    "load_config",
    "fetch_dossier",
    "compute_debt_ratio",
    "check_internal_list",
    "render_prompt",
    "call_llm",
    "build_trace",
    "persist_trace",
)


def execute_run(request: RunRequest, ports: RunPorts) -> RunTrace:
    return invoke_agent_graph(build_agent_graph(), request, ports)


def invoke_agent_graph(
    graph: AgentGraph, request: RunRequest, ports: RunPorts
) -> RunTrace:
    result = graph.invoke(AgentGraphInput(request), context=ports)
    return _graph_trace(result)


def build_agent_graph() -> AgentGraph:
    builder = _graph_builder()
    _add_graph_nodes(builder)
    _add_graph_edges(builder)
    return builder.compile(name="credit-scoring-agent")


def _graph_builder() -> AgentGraphBuilder:
    return StateGraph(
        AgentGraphState,
        context_schema=RunPorts,
        input_schema=AgentGraphInput,
        output_schema=AgentGraphOutput,
    )


def _add_graph_nodes(builder: AgentGraphBuilder) -> None:
    builder.add_node("load_config", _load_config_node)
    builder.add_node("fetch_dossier", _fetch_dossier_node)
    builder.add_node("compute_debt_ratio", _compute_debt_ratio_node)
    builder.add_node("check_internal_list", _check_internal_list_node)
    builder.add_node("render_prompt", _render_prompt_node)
    builder.add_node("call_llm", _call_llm_node)
    builder.add_node("build_trace", _build_trace_node)
    builder.add_node("persist_trace", _persist_trace_node)


def _add_graph_edges(builder: AgentGraphBuilder) -> None:
    builder.add_edge(START, GRAPH_NODE_NAMES[0])
    for source, target in pairwise(GRAPH_NODE_NAMES):
        builder.add_edge(source, target)
    builder.add_edge(GRAPH_NODE_NAMES[-1], END)


def _load_config_node(
    state: AgentGraphState, runtime: Runtime[RunPorts]
) -> GraphUpdate:
    ports = runtime.context
    config = _registered_config(state.request.config_hash, ports.config_lookup)
    return {"config": config, "run_id": ports.new_uuid(), "started_at": ports.utc_now()}


def _fetch_dossier_node(
    state: AgentGraphState, runtime: Runtime[RunPorts]
) -> GraphUpdate:
    dossier, call = _fetch_call(state.request.dossier_id, runtime.context)
    if dossier is None:
        raise LookupError(call.error)
    return {"dossier": dossier, "tool_calls": _append_call(state, call)}


def _compute_debt_ratio_node(
    state: AgentGraphState, runtime: Runtime[RunPorts]
) -> GraphUpdate:
    dossier = _required(state.dossier, "dossier")
    _ratio, call = _ratio_call(dossier, runtime.context.clock_ns)
    return {"tool_calls": _append_call(state, call)}


def _check_internal_list_node(
    state: AgentGraphState, runtime: Runtime[RunPorts]
) -> GraphUpdate:
    dossier = _required(state.dossier, "dossier")
    _status, call = _list_call(dossier, runtime.context)
    return {"tool_calls": _append_call(state, call)}


def _render_prompt_node(
    state: AgentGraphState, runtime: Runtime[RunPorts]
) -> GraphUpdate:
    del runtime
    config = _required(state.config, "config")
    dossier = _required(state.dossier, "dossier")
    return {"prompt": render_prompt(config, dossier, state.tool_calls)}


def _call_llm_node(state: AgentGraphState, runtime: Runtime[RunPorts]) -> GraphUpdate:
    config = _required(state.config, "config")
    prompt = _required(state.prompt, "prompt")
    return {"response": runtime.context.llm(_llm_request(config, prompt))}


def _build_trace_node(
    state: AgentGraphState, runtime: Runtime[RunPorts]
) -> GraphUpdate:
    ended_at = runtime.context.utc_now()
    build = _graph_run_build(state, ended_at)
    response = _required(state.response, "response")
    return {"ended_at": ended_at, "trace": _run_trace(build, response)}


def _persist_trace_node(
    state: AgentGraphState, runtime: Runtime[RunPorts]
) -> GraphUpdate:
    runtime.context.persist_run(_required(state.trace, "trace"))
    return {}


def _graph_run_build(state: AgentGraphState, ended_at: datetime) -> RunBuild:
    run_id = _required(state.run_id, "run_id")
    started_at = _required(state.started_at, "started_at")
    evidence = RunEvidence(_required(state.dossier, "dossier"), state.tool_calls)
    prompt = _required(state.prompt, "prompt")
    return RunBuild(state.request, run_id, started_at, ended_at, evidence, prompt)


def _append_call(
    state: AgentGraphState, call: ToolCallTrace
) -> tuple[ToolCallTrace, ...]:
    return (*state.tool_calls, call)


def _required[Value](value: Value | None, field_name: str) -> Value:
    if value is None:
        raise RuntimeError(f"LangGraph state is missing {field_name}")
    return value


def _graph_trace(result: object) -> RunTrace:
    if not isinstance(result, Mapping):
        raise RuntimeError("LangGraph returned an invalid output")
    trace = result.get("trace")
    if not isinstance(trace, RunTrace):
        raise RuntimeError("LangGraph output is missing trace")
    return trace


def _registered_config(config_hash_value: str, lookup: ConfigLookup) -> AgentConfig:
    config = lookup(config_hash_value)
    if config is None:
        raise LookupError(f"agent config not registered: {config_hash_value}")
    if config_hash(config) != config_hash_value:
        raise ValueError("registered config does not match config_hash")
    if config.tools_version != TOOLS_VERSION:
        raise ValueError(f"unsupported tools_version: {config.tools_version}")
    return config


def _fetch_call(
    dossier_id: str, ports: RunPorts
) -> tuple[Dossier | None, ToolCallTrace]:
    operation = partial(fetch_dossier, dossier_id, ports.dossier_lookup)
    spec = "fetch_dossier", {"dossier_id": dossier_id}, operation, dossier_to_payload
    return _capture(spec, ports.clock_ns)


def _ratio_call(dossier: Dossier, clock: ClockNs) -> tuple[object, ToolCallTrace]:
    operation = partial(compute_debt_ratio, dossier)
    spec = (
        "compute_debt_ratio",
        dossier_to_payload(dossier),
        operation,
        debt_ratio_to_payload,
    )
    return _capture(spec, clock)


def _list_call(dossier: Dossier, ports: RunPorts) -> tuple[object, ToolCallTrace]:
    operation = partial(check_internal_list, dossier, ports.internal_list_lookup)
    spec = (
        "check_internal_list",
        dossier_to_payload(dossier),
        operation,
        list_status_to_payload,
    )
    return _capture(spec, ports.clock_ns)


def _capture[Result](spec: Spec[Result], clock: ClockNs) -> Captured[Result]:
    name, input_payload, operation, _serialize = spec
    started_at, (result, error) = clock(), _invoke(operation)
    ended_at = clock()
    if error is not None:
        return None, _failed_call(name, input_payload, error, started_at, ended_at)
    return _captured_success(spec, result, started_at, ended_at)


def _captured_success[Result](
    spec: Spec[Result], result: Result | None, start: int, end: int
) -> Captured[Result]:
    name, input_payload, _operation, serialize = spec
    if result is None:
        raise ValueError(f"{name} returned no result")
    call = _successful_call(name, input_payload, serialize(result), start, end)
    return result, call


def _invoke[Result](
    operation: Callable[[], Result],
) -> tuple[Result | None, Exception | None]:
    try:
        return operation(), None
    except Exception as error:
        return None, error


def _successful_call(
    name: str, input_payload: JsonObject, output: JsonObject, start: int, end: int
) -> ToolCallTrace:
    return ToolCallTrace(name, input_payload, output, _milliseconds(start, end), None)


def _failed_call(
    name: str, input_payload: JsonObject, error: Exception, start: int, end: int
) -> ToolCallTrace:
    message = str(error).strip() or type(error).__name__
    return ToolCallTrace(name, input_payload, None, _milliseconds(start, end), message)


def _milliseconds(started_at: int, ended_at: int) -> int:
    return max(0, (ended_at - started_at) // 1_000_000)


def _llm_request(config: AgentConfig, prompt: str) -> LlmRequest:
    return LlmRequest(
        prompt, config.model_id, config.temperature, config.top_p, config.max_tokens
    )


def _run_trace(build: RunBuild, response: LlmResponse) -> RunTrace:
    return RunTrace(*_run_trace_args(build, response))


def _run_trace_args(build: RunBuild, response: LlmResponse) -> RunTraceArgs:
    request, evidence = build.request, build.evidence
    events = _events(build.run_id, evidence.tool_calls, response, build.ended_at)
    identity = build.run_id, request.config_hash, request.dossier_id
    timing = build.started_at, build.ended_at
    calls = evidence.tool_calls, (_llm_call(build.prompt, response),)
    return (*identity, *timing, *calls, *_outcome(response), events)


def _outcome(response: LlmResponse) -> tuple[Decision, str, float]:
    recommendation = response.recommendation
    return (
        recommendation.decision,
        recommendation.justification,
        recommendation.confidence,
    )


def _llm_call(prompt: str, response: LlmResponse) -> LlmCallTrace:
    return LlmCallTrace(
        prompt,
        response.raw_response,
        response.tokens_in,
        response.tokens_out,
        response.latency_ms,
    )


def _events(
    run_id: UUID,
    calls: tuple[ToolCallTrace, ...],
    response: LlmResponse,
    occurred_at: datetime,
) -> tuple[TraceEvent, ...]:
    return (
        *_failures(run_id, calls, occurred_at),
        _decision_event(run_id, response, occurred_at),
    )


def _failures(
    run_id: UUID, calls: tuple[ToolCallTrace, ...], occurred_at: datetime
) -> tuple[ToolFailure, ...]:
    return tuple(
        _tool_failure(run_id, call, occurred_at) for call in calls if call.error
    )


def _decision_event(
    run_id: UUID, response: LlmResponse, occurred_at: datetime
) -> DecisionEmitted:
    decision, justification, confidence = _outcome(response)
    return DecisionEmitted(run_id, decision, justification, confidence, occurred_at)


def _tool_failure(
    run_id: UUID, call: ToolCallTrace, occurred_at: datetime
) -> ToolFailure:
    if call.error is None:
        raise ValueError("tool failure requires an error")
    return ToolFailure(run_id, call.name, call.error, occurred_at)
