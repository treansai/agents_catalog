import json
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import cast
from uuid import UUID

import httpx
import pytest
from agenomic import __version__ as agenomic_version

from domain.configuration import AgentConfig, config_hash
from domain.events import DecisionEmitted
from domain.run import LlmCallTrace, RunTrace
from domain.tools import TOOL_NAMES, ToolCallTrace
from infrastructure.observability.agenomic import DEFAULT_AGENT_ID
from infrastructure.observability.agenomic_enrollment import (
    AgenomicEnrollmentSettings,
    enroll_agenomic_run,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/agenomic/enroll_success.json"
SUCCESS_BODY = FIXTURE.read_text(encoding="utf-8")
API_URL = "https://api.agenomic.io"
TOKEN = "fixture-enrollment-token"
NOW = datetime(2026, 8, 28, 10, tzinfo=UTC)
RUN_ID = UUID("70000000-0000-0000-0000-000000000147")
CONFIG = AgentConfig(
    "Analyse les valeurs numériques.",
    "mistral-nemo-instruct-2407",
    0.37,
    0.83,
    512,
    "1.0.0",
    ("revenu_mensuel", "charges_mensuelles"),
)
TOOLS = tuple(ToolCallTrace(name, {}, {}, 1, None) for name in TOOL_NAMES)
LLM_CALLS = (LlmCallTrace("prompt", '{"decision":"REFUS"}', 1, 1, 1),)
EVENT = DecisionEmitted(RUN_ID, "REFUS", "Ratio 48.75 %.", 0.87, NOW)
TRACE = RunTrace(
    RUN_ID,
    config_hash(CONFIG),
    "147",
    NOW - timedelta(seconds=1),
    NOW,
    TOOLS,
    LLM_CALLS,
    "REFUS",
    EVENT.justification,
    EVENT.confidence,
    (EVENT,),
)
SETTINGS = AgenomicEnrollmentSettings(API_URL, TOKEN)
type JsonObject = dict[str, object]


def _success_response(
    captured: list[httpx.Request], request: httpx.Request
) -> httpx.Response:
    captured.append(request)
    headers = {"Content-Type": "application/json"}
    return httpx.Response(200, content=SUCCESS_BODY.encode(), headers=headers)


def _enroll(captured: list[httpx.Request], api_url: str = API_URL) -> object:
    transport = httpx.MockTransport(partial(_success_response, captured))
    settings = AgenomicEnrollmentSettings(api_url, TOKEN)
    with httpx.Client(transport=transport) as client:
        return enroll_agenomic_run(client, settings, CONFIG, TRACE)


def _as_object(value: object) -> JsonObject:
    assert isinstance(value, dict)
    return cast(JsonObject, value)


def _as_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _payload(request: httpx.Request) -> JsonObject:
    return _as_object(json.loads(request.content))


def _assert_authentication(request: httpx.Request) -> None:
    assert request.method == "POST"
    assert str(request.url) == f"{API_URL}/v1/onboarding/enroll"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["x-agenomic-enrollment-token"] == TOKEN
    assert "Authorization" not in request.headers
    assert TOKEN.encode() not in request.content


def _assert_identity(payload: JsonObject) -> None:
    assert _as_object(payload["sdk"]) == {
        "name": "agenomic-python",
        "version": agenomic_version,
    }
    assert payload["framework"] == "langgraph"
    assert _as_object(payload["agent"]) == {
        "name": "credit-scoring-agent",
        "external_id": DEFAULT_AGENT_ID,
    }


def _assert_model(payload: JsonObject) -> None:
    model = _as_object(payload["model_config"])
    assert model["provider"] == "scaleway"
    assert model["model"] == CONFIG.model_id
    assert model["temperature"] == CONFIG.temperature
    assert model["top_p"] == CONFIG.top_p
    assert "seed" not in model


def _assert_tools_and_permissions(payload: JsonObject) -> None:
    tools = tuple(_as_object(item) for item in _as_list(payload["tools"]))
    permissions = tuple(_as_object(item) for item in _as_list(payload["permissions"]))
    assert tuple(item["name"] for item in tools) == TOOL_NAMES
    assert all(item["sensitive"] is True for item in tools)
    assert tuple(item["action"] for item in permissions) == TOOL_NAMES
    assert all(item["requires_approval"] is False for item in permissions)


def _assert_trace(payload: JsonObject) -> None:
    trace = _as_object(payload["trace"])
    events = tuple(_as_object(item) for item in _as_list(trace["events"]))
    tools = tuple(_as_object(item["payload"])["tool"] for item in events)
    assert trace["external_id"] == str(RUN_ID)
    assert all(item["type"] == "tool_call" for item in events)
    assert tools == TOOL_NAMES


def test_enroll_posts_real_credit_agent_contract_and_returns_milestones() -> None:
    captured: list[httpx.Request] = []
    milestones = _enroll(captured)
    payload = _payload(captured[0])
    assert milestones == _as_object(json.loads(SUCCESS_BODY))["milestones"]
    _assert_authentication(captured[0])
    _assert_identity(payload)
    _assert_model(payload)
    _assert_tools_and_permissions(payload)
    _assert_trace(payload)


def test_enroll_normalizes_trailing_api_slash() -> None:
    captured: list[httpx.Request] = []
    _enroll(captured, f"{API_URL}/")
    assert str(captured[0].url) == f"{API_URL}/v1/onboarding/enroll"


def test_enrollment_settings_validate_without_exposing_token() -> None:
    assert TOKEN not in repr(SETTINGS)
    with pytest.raises(ValueError, match="enrollment_token"):
        AgenomicEnrollmentSettings(API_URL, " ")


def _status_response(status_code: int, request: httpx.Request) -> httpx.Response:
    return httpx.Response(status_code, json={"error": "rejected"}, request=request)


@pytest.mark.parametrize("status_code", [401, 422, 500])
def test_enroll_propagates_http_status_without_leaking_token(
    status_code: int,
) -> None:
    transport = httpx.MockTransport(partial(_status_response, status_code))
    with (
        httpx.Client(transport=transport) as client,
        pytest.raises(httpx.HTTPStatusError) as captured,
    ):
        enroll_agenomic_run(client, SETTINGS, CONFIG, TRACE)
    assert TOKEN not in str(captured.value)


def _connection_failure(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("offline", request=request)


def test_enroll_propagates_network_failure() -> None:
    transport = httpx.MockTransport(_connection_failure)
    with (
        httpx.Client(transport=transport) as client,
        pytest.raises(httpx.ConnectError, match="offline"),
    ):
        enroll_agenomic_run(client, SETTINGS, CONFIG, TRACE)


def _json_response(payload: object, request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=payload, request=request)


@pytest.mark.parametrize(
    "payload",
    [[], {}, {"milestones": "invalid"}, {"milestones": {}}, {"milestones": [1]}],
)
def test_enroll_rejects_invalid_milestones(payload: object) -> None:
    transport = httpx.MockTransport(partial(_json_response, payload))
    with (
        httpx.Client(transport=transport) as client,
        pytest.raises(ValueError, match=r"milestones|JSON object"),
    ):
        enroll_agenomic_run(client, SETTINGS, CONFIG, TRACE)
