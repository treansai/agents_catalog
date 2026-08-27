from collections.abc import Callable
from functools import partial
from time import perf_counter_ns
from typing import cast

import httpx

from domain.llm import LlmPort, LlmRequest, LlmResponse, parse_recommendation

SCALEWAY_BASE_URL = "https://api.scaleway.ai/v1"
type ClockNs = Callable[[], int]


def build_scaleway_llm(
    client: httpx.Client,
    api_key: str,
    base_url: str = SCALEWAY_BASE_URL,
    clock_ns: ClockNs = perf_counter_ns,
) -> LlmPort:
    _validate_connection(api_key, base_url)
    return partial(call_scaleway, client, api_key, base_url=base_url, clock_ns=clock_ns)


def call_scaleway(
    client: httpx.Client,
    api_key: str,
    request: LlmRequest,
    base_url: str = SCALEWAY_BASE_URL,
    clock_ns: ClockNs = perf_counter_ns,
) -> LlmResponse:
    response, latency_ms = _timed_post(client, api_key, request, base_url, clock_ns)
    response.raise_for_status()
    return _response(response, latency_ms)


def _timed_post(
    client: httpx.Client,
    api_key: str,
    request: LlmRequest,
    base_url: str,
    clock_ns: ClockNs,
) -> tuple[httpx.Response, int]:
    started_at, response = clock_ns(), _post(client, api_key, request, base_url)
    return response, _milliseconds(started_at, clock_ns())


def _post(
    client: httpx.Client, api_key: str, request: LlmRequest, base_url: str
) -> httpx.Response:
    endpoint, headers = _endpoint(base_url), _headers(api_key)
    return client.post(endpoint, headers=headers, json=_body(request))


def _validate_connection(api_key: str, base_url: str) -> None:
    if not api_key.strip():
        raise ValueError("api_key must not be blank")
    if not base_url.strip():
        raise ValueError("base_url must not be blank")


def _endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def _body(request: LlmRequest) -> dict[str, object]:
    return {
        "model": request.model_id,
        "messages": [{"role": "user", "content": request.prompt_rendered}],
        "temperature": request.temperature,
        "top_p": request.top_p,
        "max_tokens": request.max_tokens,
        "response_format": _response_format(),
    }


def _response_format() -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "credit_recommendation",
            "strict": True,
            "schema": _recommendation_schema(),
        },
    }


def _recommendation_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": _schema_properties(),
        "required": ["decision", "justification", "confidence"],
        "additionalProperties": False,
    }


def _schema_properties() -> dict[str, object]:
    return {
        "decision": {"type": "string", "enum": _decisions()},
        "justification": {"type": "string", "minLength": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    }


def _decisions() -> list[str]:
    return ["ACCORD", "REFUS", "INSTRUCTION_MANUELLE"]


def _milliseconds(started_at: int, ended_at: int) -> int:
    return (ended_at - started_at) // 1_000_000


def _response(response: httpx.Response, latency_ms: int) -> LlmResponse:
    payload = _json_object(cast(object, response.json()), "response")
    recommendation = parse_recommendation(_content(payload))
    tokens_in, tokens_out = _usage(payload)
    return LlmResponse(response.text, recommendation, tokens_in, tokens_out, latency_ms)


def _content(payload: dict[str, object]) -> str:
    choices = _json_list(payload.get("choices"), "choices")
    if not choices:
        raise ValueError("choices must not be empty")
    choice = _json_object(choices[0], "choice")
    message = _json_object(choice.get("message"), "message")
    return _string(message.get("content"), "content")


def _usage(payload: dict[str, object]) -> tuple[int, int]:
    usage = _json_object(payload.get("usage"), "usage")
    tokens_in = _integer(usage.get("prompt_tokens"), "prompt_tokens")
    tokens_out = _integer(usage.get("completion_tokens"), "completion_tokens")
    return tokens_in, tokens_out


def _json_object(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return cast(dict[str, object], value)


def _json_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a JSON array")
    return cast(list[object], value)


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value
