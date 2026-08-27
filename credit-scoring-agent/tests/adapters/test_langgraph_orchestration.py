from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import cast
from uuid import UUID

import httpx
import pytest

from adapters.run_agent import (
    GRAPH_NODE_NAMES,
    AgentGraph,
    AgentGraphInput,
    ConfigLookup,
    RunPorts,
    RunRequest,
    build_agent_graph,
    invoke_agent_graph,
)
from domain.configuration import AgentConfig, config_hash
from domain.dataset import generate_dossiers
from domain.dossier import Dossier
from domain.events import DecisionEmitted, GuardrailOverride, ToolFailure
from domain.run import RunTrace
from domain.tools import DossierLookup, InternalListLookup
from infrastructure.llm.scaleway import build_scaleway_llm

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures/llm/scaleway_refus.json"
FIXTURE_BODY = FIXTURE_PATH.read_text(encoding="utf-8")
NOW = datetime(2026, 8, 28, 10, tzinfo=UTC)
RUN_ID = UUID("40000000-0000-0000-0000-000000000001")
DOSSIER = generate_dossiers()[146]
CONFIG = AgentConfig(
    "Analyse ce dossier synthétique.",
    "mistral-nemo-instruct-2407",
    0.0,
    1.0,
    512,
    "1.0.0",
    ("revenu_mensuel", "charges_mensuelles", "nb_incidents_passes"),
)
CONFIG_HASH = config_hash(CONFIG)
REQUEST = RunRequest(CONFIG_HASH, DOSSIER.dossier_id)
EXPECTED_NODE_NAMES = (
    "load_config",
    "fetch_dossier",
    "compute_debt_ratio",
    "check_internal_list",
    "render_prompt",
    "call_llm",
    "build_trace",
    "persist_trace",
)
EXPECTED_EDGES = frozenset(
    zip(
        ("__start__", *EXPECTED_NODE_NAMES),
        (*EXPECTED_NODE_NAMES, "__end__"),
        strict=True,
    )
)
type Invocation = tuple[RunTrace, list[RunTrace], list[httpx.Request]]


def _fixture_response(
    captured: list[httpx.Request], request: httpx.Request
) -> httpx.Response:
    captured.append(request)
    headers = {"Content-Type": "application/json"}
    return httpx.Response(200, content=FIXTURE_BODY.encode(), headers=headers)


def _client(captured: list[httpx.Request]) -> httpx.Client:
    transport = httpx.MockTransport(partial(_fixture_response, captured))
    return httpx.Client(transport=transport)


def _llm_clock() -> Callable[[], int]:
    return iter((10_000_000, 22_000_000)).__next__


def _tool_clock() -> Callable[[], int]:
    ticks = (0, 1_000_000, 2_000_000, 4_000_000, 5_000_000, 8_000_000)
    return iter(ticks).__next__


def _utc_clock() -> Callable[[], datetime]:
    return iter((NOW, NOW + timedelta(seconds=1))).__next__


def _new_uuid() -> UUID:
    return RUN_ID


def _config_lookup(value: str) -> AgentConfig | None:
    return CONFIG if value == CONFIG_HASH else None


def _missing_config(value: str) -> AgentConfig | None:
    del value
    return None


def _dossier_lookup(value: str) -> Dossier | None:
    return DOSSIER if value == DOSSIER.dossier_id else None


def _internal_list(value: str) -> str | None:
    del value
    return None


def _failing_internal_list(value: str) -> str | None:
    raise TimeoutError(f"internal list unavailable for {value}")


def _ports(
    client: httpx.Client,
    config_lookup: ConfigLookup,
    dossier_lookup: DossierLookup,
    list_lookup: InternalListLookup,
    persisted: list[RunTrace],
) -> RunPorts:
    llm = build_scaleway_llm(client, "fixture-key", clock_ns=_llm_clock())
    factory = partial(RunPorts, config_lookup, dossier_lookup, list_lookup, llm)
    return factory(persisted.append, _utc_clock(), _tool_clock(), _new_uuid)


def _invoke(list_lookup: InternalListLookup = _internal_list) -> Invocation:
    persisted: list[RunTrace] = []
    requests: list[httpx.Request] = []
    with _client(requests) as client:
        ports = _ports(client, _config_lookup, _dossier_lookup, list_lookup, persisted)
        trace = invoke_agent_graph(build_agent_graph(), REQUEST, ports)
    return trace, persisted, requests


def _stream(graph: AgentGraph, ports: RunPorts) -> tuple[dict[str, object], ...]:
    updates = tuple(
        graph.stream(AgentGraphInput(REQUEST), context=ports, stream_mode="updates")
    )
    return cast(tuple[dict[str, object], ...], updates)


def test_graph_has_explicit_sequential_topology_and_names() -> None:
    drawable = build_agent_graph().get_graph()
    nodes = tuple(name for name in drawable.nodes if not name.startswith("__"))
    edges = {(edge.source, edge.target) for edge in drawable.edges}
    assert GRAPH_NODE_NAMES == EXPECTED_NODE_NAMES
    assert nodes == EXPECTED_NODE_NAMES
    assert edges == EXPECTED_EDGES


def test_graph_streams_one_update_per_node_in_contract_order() -> None:
    persisted: list[RunTrace] = []
    requests: list[httpx.Request] = []
    with _client(requests) as client:
        ports = _ports(
            client, _config_lookup, _dossier_lookup, _internal_list, persisted
        )
        updates = _stream(build_agent_graph(), ports)
    assert tuple(next(iter(update)) for update in updates) == GRAPH_NODE_NAMES
    assert len(persisted) == 1


def test_public_invocation_uses_recorded_llm_fixture_and_persists_once() -> None:
    trace, persisted, requests = _invoke()
    assert trace.decision == "REFUS"
    assert trace.llm_calls[0].raw_response == FIXTURE_BODY
    assert len(requests) == 1
    assert persisted == [trace]


def _capturing_dossier(captured: list[str], dossier_id: str) -> Dossier | None:
    captured.append(dossier_id)
    return _dossier_lookup(dossier_id)


def test_missing_config_stops_before_tools_llm_and_persistence() -> None:
    dossier_calls: list[str] = []
    requests: list[httpx.Request] = []
    persisted: list[RunTrace] = []
    with _client(requests) as client:
        lookup = partial(_capturing_dossier, dossier_calls)
        ports = _ports(client, _missing_config, lookup, _internal_list, persisted)
        with pytest.raises(LookupError, match="not registered"):
            invoke_agent_graph(build_agent_graph(), REQUEST, ports)
    assert not dossier_calls and not requests and not persisted


def test_tool_failure_is_an_event_without_hidden_guardrail_override() -> None:
    trace, persisted, _ = _invoke(_failing_internal_list)
    failures = tuple(event for event in trace.events if isinstance(event, ToolFailure))
    assert failures[0].tool_name == "check_internal_list"
    assert isinstance(trace.events[-1], DecisionEmitted)
    assert not any(isinstance(event, GuardrailOverride) for event in trace.events)
    assert persisted == [trace]
