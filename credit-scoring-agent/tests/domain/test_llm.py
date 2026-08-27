from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from domain.decisions import Decision
from domain.llm import LlmRequest, LlmResponse, Recommendation, parse_recommendation

REQUEST = LlmRequest("Dossier rendu", "model-v1", 0.1, 0.9, 256)
RECOMMENDATION = Recommendation("REFUS", "Ratio 48.75 %, reste 1230.00 EUR.", 0.87)
RESPONSE = LlmResponse('{"choices":[]}', RECOMMENDATION, 184, 47, 12)
CONTENT = (
    '{"decision":"REFUS","justification":"Ratio 48.75 %, reste 1230.00 EUR.",'
    '"confidence":0.87}'
)


def test_llm_dtos_are_immutable() -> None:
    for value, field_name in (
        (REQUEST, "model_id"),
        (RECOMMENDATION, "decision"),
        (RESPONSE, "latency_ms"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field_name, "changed")


def test_parse_recommendation_returns_typed_decision() -> None:
    assert parse_recommendation(CONTENT) == RECOMMENDATION


@pytest.mark.parametrize("raw_content", ["not JSON", "[]", "null"])
def test_parse_recommendation_rejects_invalid_json_contract(raw_content: str) -> None:
    with pytest.raises(ValueError, match="recommendation"):
        parse_recommendation(raw_content)


@pytest.mark.parametrize(
    "raw_content",
    [
        '{"decision":"REFUS","justification":"motif"}',
        '{"decision":"REFUS","justification":"motif","confidence":1,"extra":0}',
    ],
)
def test_parse_recommendation_requires_exact_fields(raw_content: str) -> None:
    with pytest.raises(ValueError, match="fields"):
        parse_recommendation(raw_content)


@pytest.mark.parametrize(
    "raw_content",
    [
        '{"decision":1,"justification":"motif","confidence":0.5}',
        '{"decision":"REFUS","justification":1,"confidence":0.5}',
    ],
)
def test_parse_recommendation_requires_string_fields(raw_content: str) -> None:
    with pytest.raises(ValueError, match="must be a string"):
        parse_recommendation(raw_content)


@pytest.mark.parametrize("confidence", ["true", '"high"'])
def test_parse_recommendation_requires_numeric_confidence(confidence: str) -> None:
    prefix = '{"decision":"REFUS","justification":"motif","confidence":'
    raw_content = prefix + confidence + "}"
    with pytest.raises(ValueError, match="confidence must be a number"):
        parse_recommendation(raw_content)


def test_recommendation_rejects_unknown_decision() -> None:
    unknown = cast(Decision, "UNKNOWN")
    with pytest.raises(ValueError, match="unknown decision"):
        replace(RECOMMENDATION, decision=unknown)


@pytest.mark.parametrize("justification", ["", " "])
def test_recommendation_rejects_blank_justification(justification: str) -> None:
    with pytest.raises(ValueError, match="justification"):
        replace(RECOMMENDATION, justification=justification)


@pytest.mark.parametrize("confidence", [float("nan"), -0.01, 1.01])
def test_recommendation_rejects_invalid_confidence(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        replace(RECOMMENDATION, confidence=confidence)


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda: replace(REQUEST, prompt_rendered=" "), "prompt_rendered"),
        (lambda: replace(REQUEST, model_id=" "), "model_id"),
    ],
)
def test_request_rejects_blank_text(
    factory: Callable[[], LlmRequest], field_name: str
) -> None:
    with pytest.raises(ValueError, match=field_name):
        factory()


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda: replace(REQUEST, temperature=-0.1), "temperature"),
        (lambda: replace(REQUEST, temperature=2.1), "temperature"),
        (lambda: replace(REQUEST, top_p=-0.1), "top_p"),
        (lambda: replace(REQUEST, top_p=1.1), "top_p"),
    ],
)
def test_request_rejects_invalid_sampling(
    factory: Callable[[], LlmRequest], field_name: str
) -> None:
    with pytest.raises(ValueError, match=field_name):
        factory()


def test_request_rejects_non_positive_max_tokens() -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        replace(REQUEST, max_tokens=0)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: replace(RESPONSE, raw_response=" "),
        lambda: replace(RESPONSE, tokens_in=-1),
        lambda: replace(RESPONSE, tokens_out=-1),
        lambda: replace(RESPONSE, latency_ms=-1),
    ],
)
def test_response_rejects_invalid_trace_fields(
    factory: Callable[[], LlmResponse],
) -> None:
    with pytest.raises(ValueError):
        factory()
