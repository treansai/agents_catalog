import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from domain._validation import require_finite_range, require_non_blank
from domain.decisions import Decision, parse_decision


@dataclass(frozen=True, slots=True)
class LlmRequest:
    prompt_rendered: str
    model_id: str
    temperature: float
    top_p: float
    max_tokens: int

    def __post_init__(self) -> None:
        _validate_request(self)


@dataclass(frozen=True, slots=True)
class Recommendation:
    decision: Decision
    justification: str
    confidence: float

    def __post_init__(self) -> None:
        _validate_recommendation(self)


@dataclass(frozen=True, slots=True)
class LlmResponse:
    raw_response: str
    recommendation: Recommendation
    tokens_in: int
    tokens_out: int
    latency_ms: int

    def __post_init__(self) -> None:
        _validate_response(self)


type LlmPort = Callable[[LlmRequest], LlmResponse]


def _validate_request(request: LlmRequest) -> None:
    require_non_blank(request.prompt_rendered, "prompt_rendered")
    require_non_blank(request.model_id, "model_id")
    require_finite_range(request.temperature, 0.0, 2.0, "temperature")
    require_finite_range(request.top_p, 0.0, 1.0, "top_p")
    if request.max_tokens <= 0:
        raise ValueError("max_tokens must be positive")


def _validate_recommendation(recommendation: Recommendation) -> None:
    parse_decision(recommendation.decision)
    require_non_blank(recommendation.justification, "justification")
    require_finite_range(recommendation.confidence, 0.0, 1.0, "confidence")


def _validate_response(response: LlmResponse) -> None:
    require_non_blank(response.raw_response, "raw_response")
    if response.tokens_in < 0 or response.tokens_out < 0:
        raise ValueError("token counts must not be negative")
    if response.latency_ms < 0:
        raise ValueError("latency_ms must not be negative")


def parse_recommendation(raw_content: str) -> Recommendation:
    try:
        decoded = cast(object, json.loads(raw_content))
    except json.JSONDecodeError as error:
        raise ValueError("recommendation must be valid JSON") from error
    payload = _json_object(decoded)
    return _recommendation_from_payload(payload)


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("recommendation must be a JSON object")
    return cast(dict[str, object], value)


def _recommendation_from_payload(payload: dict[str, object]) -> Recommendation:
    _require_exact_fields(payload)
    decision = parse_decision(_string_field(payload, "decision"))
    justification = _string_field(payload, "justification")
    return Recommendation(decision, justification, _confidence_field(payload))


def _require_exact_fields(payload: dict[str, object]) -> None:
    expected = {"decision", "justification", "confidence"}
    if set(payload) != expected:
        raise ValueError("recommendation fields do not match the contract")


def _string_field(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _confidence_field(payload: dict[str, object]) -> float:
    value = payload.get("confidence")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("confidence must be a number")
    return float(value)
