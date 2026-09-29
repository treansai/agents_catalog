"""One-shot Agenomic Cloud enrollment for an audited real agent run."""

from dataclasses import dataclass, field
from typing import cast

import httpx
from agenomic import __version__ as agenomic_version

from domain.configuration import AgentConfig
from domain.run import RunTrace
from domain.tools import TOOL_NAMES
from infrastructure.observability.agenomic import DEFAULT_AGENT_ID

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type Milestones = list[str]

AGENT_NAME = "credit-scoring-agent"
FRAMEWORK = "langgraph"
MODEL_PROVIDER = "scaleway"
SDK_NAME = "agenomic-python"


@dataclass(frozen=True, slots=True)
class AgenomicEnrollmentSettings:
    api_url: str
    enrollment_token: str = field(repr=False)
    agent_id: str = DEFAULT_AGENT_ID

    def __post_init__(self) -> None:
        _require_non_blank(self.api_url, "api_url")
        _require_non_blank(self.enrollment_token, "enrollment_token")
        _require_non_blank(self.agent_id, "agent_id")


def enroll_agenomic_run(
    client: httpx.Client,
    settings: AgenomicEnrollmentSettings,
    config: AgentConfig,
    trace: RunTrace,
) -> Milestones:
    response = _post_enrollment(client, settings, config, trace)
    response.raise_for_status()
    return _milestones(response)


def enrollment_payload(
    settings: AgenomicEnrollmentSettings, config: AgentConfig, trace: RunTrace
) -> JsonObject:
    return _identity_payload(settings.agent_id) | _execution_payload(config, trace)


def _identity_payload(agent_id: str) -> JsonObject:
    return {
        "sdk": _sdk_payload(),
        "framework": FRAMEWORK,
        "agent": _agent_payload(agent_id),
    }


def _execution_payload(config: AgentConfig, trace: RunTrace) -> JsonObject:
    return {
        "model_config": _model_payload(config),
        "tools": _tools_payload(),
        "permissions": _permissions_payload(),
        "trace": _trace_payload(trace),
    }


def _post_enrollment(
    client: httpx.Client,
    settings: AgenomicEnrollmentSettings,
    config: AgentConfig,
    trace: RunTrace,
) -> httpx.Response:
    url = _endpoint(settings.api_url)
    headers = _headers(settings.enrollment_token)
    payload = enrollment_payload(settings, config, trace)
    return client.post(url, headers=headers, json=payload)


def _endpoint(api_url: str) -> str:
    return f"{api_url.rstrip('/')}/v1/onboarding/enroll"


def _headers(token: str) -> dict[str, str]:
    return {"x-agenomic-enrollment-token": token}


def _sdk_payload() -> JsonObject:
    return {"name": SDK_NAME, "version": agenomic_version}


def _agent_payload(agent_id: str) -> JsonObject:
    return {"name": AGENT_NAME, "external_id": agent_id}


def _model_payload(config: AgentConfig) -> JsonObject:
    return {
        "provider": MODEL_PROVIDER,
        "model": config.model_id,
        "temperature": config.temperature,
        "top_p": config.top_p,
    }


def _tools_payload() -> list[JsonValue]:
    return [{"name": name, "sensitive": True} for name in TOOL_NAMES]


def _permissions_payload() -> list[JsonValue]:
    return [{"action": name, "requires_approval": False} for name in TOOL_NAMES]


def _trace_payload(trace: RunTrace) -> JsonObject:
    return {
        "external_id": str(trace.run_id),
        "events": [_tool_event(call.name) for call in trace.tool_calls],
    }


def _tool_event(tool_name: str) -> JsonObject:
    return {"type": "tool_call", "payload": {"tool": tool_name}}


def _milestones(response: httpx.Response) -> Milestones:
    payload = _response_object(cast(object, response.json()))
    milestones = payload.get("milestones")
    if not isinstance(milestones, list):
        raise ValueError("Agenomic enrollment milestones must be a string list")
    if not all(isinstance(item, str) for item in milestones):
        raise ValueError("Agenomic enrollment milestones must be a string list")
    return cast(list[str], milestones)


def _response_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Agenomic enrollment response must be a JSON object")
    return cast(dict[str, object], value)


def _require_non_blank(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
