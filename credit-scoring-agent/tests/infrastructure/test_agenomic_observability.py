import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import cast
from uuid import UUID

import httpx
import pytest

from adapters.run_agent import GRAPH_NODE_NAMES, ConfigLookup, RunPorts, RunRequest
from domain.configuration import AgentConfig, config_hash
from domain.dataset import generate_dossiers
from domain.dossier import Dossier
from domain.run import RunTrace
from infrastructure.llm.scaleway import build_scaleway_llm
from infrastructure.observability.agenomic import (
    DEFAULT_AGENT_ID,
    AgenomicSettings,
    AgenomicTelemetry,
    build_agenomic_telemetry,
    close_agenomic_telemetry,
    execute_agenomic_run,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
LLM_BODY = (FIXTURES / "llm/scaleway_refus.json").read_text(encoding="utf-8")
AGENOMIC_BODY = (FIXTURES / "agenomic/upload_traces_success.json").read_text()
AGENOMIC_URL = "https://api.agenomic.io"
AGENOMIC_API_KEY = "fixture-write-api-key"
NOW = datetime(2026, 8, 28, 10, tzinfo=UTC)
RUN_ID = UUID("50000000-0000-0000-0000-000000000147")
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
type ObservedRun = tuple[RunTrace, list[RunTrace], list[httpx.Request]]
type JsonObject = dict[str, object]


def _llm_response(request: httpx.Request) -> httpx.Response:
    headers = {"Content-Type": "application/json"}
    return httpx.Response(200, content=LLM_BODY.encode(), headers=headers)


def _agenomic_response(
    captured: list[httpx.Request], request: httpx.Request
) -> httpx.Response:
    captured.append(request)
    headers = {"Content-Type": "application/json"}
    return httpx.Response(202, content=AGENOMIC_BODY.encode(), headers=headers)


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


def _ports(
    client: httpx.Client,
    persisted: list[RunTrace],
    config_lookup: ConfigLookup,
) -> RunPorts:
    llm = build_scaleway_llm(client, "fixture-key", clock_ns=_llm_clock())
    factory = partial(RunPorts, config_lookup, _dossier_lookup, _internal_list, llm)
    return factory(persisted.append, _utc_clock(), _tool_clock(), _new_uuid)


def _telemetry(captured: list[httpx.Request]) -> AgenomicTelemetry:
    handler = partial(_agenomic_response, captured)
    transport = httpx.MockTransport(handler)
    settings = AgenomicSettings(AGENOMIC_URL, AGENOMIC_API_KEY)
    return build_agenomic_telemetry(settings, transport)


def _invoke(
    telemetry: AgenomicTelemetry,
    persisted: list[RunTrace],
    config_lookup: ConfigLookup,
) -> RunTrace:
    with httpx.Client(transport=httpx.MockTransport(_llm_response)) as client:
        ports = _ports(client, persisted, config_lookup)
        return execute_agenomic_run(REQUEST, ports, telemetry)


def _observed_run() -> ObservedRun:
    persisted: list[RunTrace] = []
    captured: list[httpx.Request] = []
    telemetry = _telemetry(captured)
    try:
        trace = _invoke(telemetry, persisted, _config_lookup)
    finally:
        close_agenomic_telemetry(telemetry)
    return trace, persisted, captured


def _captured_failure() -> tuple[list[RunTrace], list[httpx.Request]]:
    persisted: list[RunTrace] = []
    captured: list[httpx.Request] = []
    telemetry = _telemetry(captured)
    try:
        with pytest.raises(LookupError, match="not registered"):
            _invoke(telemetry, persisted, _missing_config)
    finally:
        close_agenomic_telemetry(telemetry)
    return persisted, captured


def _as_object(value: object) -> JsonObject:
    assert isinstance(value, dict)
    return cast(JsonObject, value)


def _envelope(requests: list[httpx.Request]) -> JsonObject:
    assert len(requests) == 1
    body = _as_object(json.loads(requests[0].content))
    traces = body["traces"]
    assert isinstance(traces, list) and len(traces) == 1
    return _as_object(traces[0])


def _assert_http_request(request: httpx.Request) -> None:
    assert request.method == "POST"
    assert str(request.url) == f"{AGENOMIC_URL}/v1/traces"
    assert request.headers["Authorization"] == f"Bearer {AGENOMIC_API_KEY}"
    assert request.headers["Idempotency-Key"]
    assert AGENOMIC_API_KEY.encode() not in request.content


def _assert_identity(envelope: JsonObject, trace: RunTrace) -> None:
    assert envelope["agent_id"] == DEFAULT_AGENT_ID
    assert envelope["release"] == CONFIG_HASH
    metadata = _as_object(envelope["metadata"])
    assert metadata["run_id"] == str(trace.run_id)
    assert metadata["config_hash"] == CONFIG_HASH
    assert metadata["dossier_id"] == DOSSIER.dossier_id


def _assert_input_output(envelope: JsonObject, trace: RunTrace) -> None:
    input_payload = _as_object(_as_object(envelope["input"])["payload_inline"])
    assert input_payload == {"args": [CONFIG_HASH, DOSSIER.dossier_id], "kwargs": {}}
    final_output = _as_object(envelope["final_output"])
    output_payload = _as_object(final_output["payload_inline"])
    assert output_payload["run_id"] == str(trace.run_id)
    assert output_payload["decision"] == trace.decision


def _assert_node_calls(envelope: JsonObject) -> None:
    calls = envelope["tool_calls"]
    assert isinstance(calls, list)
    objects = tuple(_as_object(call) for call in calls)
    assert tuple(call["tool"] for call in objects) == GRAPH_NODE_NAMES
    assert all(call["status"] == "success" for call in objects)
    assert all(call.get("input_hash") and call.get("output_hash") for call in objects)


def test_agenomic_settings_are_validated_without_exposing_token() -> None:
    settings = AgenomicSettings(AGENOMIC_URL, AGENOMIC_API_KEY)
    assert AGENOMIC_API_KEY not in repr(settings)
    with pytest.raises(ValueError, match="api_key"):
        AgenomicSettings(AGENOMIC_URL, " ")


def test_successful_run_exports_http_envelope_and_all_langgraph_nodes() -> None:
    trace, persisted, requests = _observed_run()
    envelope = _envelope(requests)
    assert persisted == [trace]
    _assert_http_request(requests[0])
    _assert_identity(envelope, trace)
    _assert_input_output(envelope, trace)
    _assert_node_calls(envelope)


def test_graph_failure_is_rethrown_and_exported_without_persistence() -> None:
    persisted, requests = _captured_failure()
    envelope = _envelope(requests)
    calls = envelope["tool_calls"]
    assert not persisted
    assert isinstance(calls, list) and len(calls) == 1
    assert _as_object(calls[0])["tool"] == "load_config"
    assert _as_object(calls[0])["status"] == "error"
    assert str(envelope["error"]).startswith("LookupError:")
