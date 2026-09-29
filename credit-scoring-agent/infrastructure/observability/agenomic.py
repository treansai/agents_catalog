"""Agenomic Cloud observability boundary for LangGraph executions."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

import httpx
from agenomic.client.client import AgenomicClient
from agenomic.exporters.http import HttpExporter
from agenomic.integrations.langgraph import instrument_langgraph
from agenomic.trace import current_recorder, trace_agent_run

from adapters.run_agent import (
    AgentGraph,
    AgentNode,
    RunPorts,
    RunRequest,
    build_agent_graph,
    invoke_agent_graph,
)
from domain.run import RunTrace

DEFAULT_AGENT_ID = "agent://treansai/credit-scoring-agent"
type TracedInvoker = Callable[[str, str], RunTrace]
type RunDecorator = Callable[[TracedInvoker], TracedInvoker]


@dataclass(frozen=True, slots=True)
class AgenomicSettings:
    api_url: str
    api_key: str = field(repr=False)
    agent_id: str = DEFAULT_AGENT_ID

    def __post_init__(self) -> None:
        _require_non_blank(self.api_url, "api_url")
        _require_non_blank(self.api_key, "api_key")
        _require_non_blank(self.agent_id, "agent_id")


@dataclass(frozen=True, slots=True)
class AgenomicTelemetry:
    agent_id: str
    client: AgenomicClient
    exporter: HttpExporter


@dataclass(frozen=True, slots=True)
class _InstrumentableNodes:
    nodes: dict[str, AgentNode]


def build_agenomic_telemetry(
    settings: AgenomicSettings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AgenomicTelemetry:
    client = AgenomicClient(settings.api_url, settings.api_key, transport=transport)
    return AgenomicTelemetry(settings.agent_id, client, HttpExporter(client))


def build_instrumented_agent_graph() -> AgentGraph:
    graph = build_agent_graph(_instrument_node)
    return cast(AgentGraph, instrument_langgraph(graph))


def execute_agenomic_run(
    request: RunRequest, ports: RunPorts, telemetry: AgenomicTelemetry
) -> RunTrace:
    graph = build_instrumented_agent_graph()
    invoke = _traced_invoker(graph, ports, telemetry, request.config_hash)
    return invoke(request.config_hash, request.dossier_id)


def close_agenomic_telemetry(telemetry: AgenomicTelemetry) -> None:
    asyncio.run(_close_agenomic_telemetry(telemetry))


async def _close_agenomic_telemetry(telemetry: AgenomicTelemetry) -> None:
    await telemetry.exporter.aclose()
    await telemetry.client.aclose()


def _traced_invoker(
    graph: AgentGraph,
    ports: RunPorts,
    telemetry: AgenomicTelemetry,
    release: str,
) -> TracedInvoker:
    return _run_decorator(telemetry, release)(_graph_invoker(graph, ports))


def _run_decorator(telemetry: AgenomicTelemetry, release: str) -> RunDecorator:
    return trace_agent_run(
        telemetry.agent_id, release=release, exporter=telemetry.exporter
    )


def _graph_invoker(graph: AgentGraph, ports: RunPorts) -> TracedInvoker:
    def invoke(config_hash: str, dossier_id: str) -> RunTrace:
        request = RunRequest(config_hash, dossier_id)
        trace = invoke_agent_graph(graph, request, ports)
        _record_correlation(trace)
        return trace

    return invoke


def _record_correlation(trace: RunTrace) -> None:
    recorder = current_recorder()
    if recorder is None:
        return
    recorder.add_metadata("run_id", str(trace.run_id))
    recorder.add_metadata("config_hash", trace.config_hash)
    recorder.add_metadata("dossier_id", trace.dossier_id)


def _instrument_node(name: str, node: AgentNode) -> AgentNode:
    proxy = _InstrumentableNodes({name: node})
    instrument_langgraph(proxy)
    return proxy.nodes[name]


def _require_non_blank(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
