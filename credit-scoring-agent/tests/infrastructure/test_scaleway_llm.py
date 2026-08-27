import json
from functools import partial
from pathlib import Path
from typing import cast

import httpx

from domain.llm import LlmRequest, LlmResponse, Recommendation
from infrastructure.llm.scaleway import SCALEWAY_BASE_URL, build_scaleway_llm

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures/llm/scaleway_refus.json"
FIXTURE_BODY = FIXTURE_PATH.read_text(encoding="utf-8")
REQUEST = LlmRequest("Analyse le dossier 147 rendu.", "model-v1", 0.1, 0.9, 256)


def _fixture_response(
    captured: list[httpx.Request], request: httpx.Request
) -> httpx.Response:
    captured.append(request)
    headers = {"Content-Type": "application/json"}
    return httpx.Response(200, content=FIXTURE_BODY.encode(), headers=headers)


def _invoke() -> tuple[LlmResponse, httpx.Request]:
    captured: list[httpx.Request] = []
    clock = iter((1_000_000_000, 1_012_000_000))
    transport = httpx.MockTransport(partial(_fixture_response, captured))
    with httpx.Client(transport=transport) as client:
        port = build_scaleway_llm(client, "fixture-api-key", clock_ns=clock.__next__)
        response = port(REQUEST)
    return response, captured[0]


def _request_json(request: httpx.Request) -> dict[str, object]:
    return cast(dict[str, object], json.loads(request.content))


def test_scaleway_maps_static_fixture_and_measures_latency() -> None:
    response, _ = _invoke()
    expected = Recommendation(
        "REFUS",
        "Le taux d'endettement est de 29.00 %, le reste à vivre de "
        "2407.61 EUR et 0 incident est enregistré.",
        0.87,
    )
    assert response == LlmResponse(FIXTURE_BODY, expected, 184, 47, 12)


def test_scaleway_posts_authenticated_structured_request() -> None:
    _, request = _invoke()
    payload = _request_json(request)
    assert str(request.url) == f"{SCALEWAY_BASE_URL}/chat/completions"
    assert request.headers["Authorization"] == "Bearer fixture-api-key"
    assert payload["messages"] == [{"role": "user", "content": REQUEST.prompt_rendered}]
    assert payload["model"] == REQUEST.model_id
    assert cast(dict[str, object], payload["response_format"])["type"] == "json_schema"


def test_scaleway_factory_rejects_blank_connection_values() -> None:
    transport = httpx.MockTransport(partial(_fixture_response, []))
    with httpx.Client(transport=transport) as client:
        _assert_blank_values_rejected(client)


def _assert_blank_values_rejected(client: httpx.Client) -> None:
    for api_key, base_url in ((" ", SCALEWAY_BASE_URL), ("key", " ")):
        try:
            build_scaleway_llm(client, api_key, base_url)
        except ValueError:
            continue
        raise AssertionError("blank connection value was accepted")
