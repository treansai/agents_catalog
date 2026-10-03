"""End-to-end flow through HTTP with a scripted model and a mocked backend."""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

from tests.fakes import ScriptedModel, final, tool_call

API_KEY = "test-api-key"
BODY = {"account_id": "outlook-perso", "messages": [{"role": "user", "content": "Mes messages ?"}]}


def backend(request: httpx.Request) -> httpx.Response:
    assert request.headers["x-api-key"] == "backend-key"
    if request.url.path == "/v1/accounts/outlook-perso/messages":
        return httpx.Response(
            200,
            json={
                "messages": [
                    {
                        "message_id": "AAMk1",
                        "subject": "Bonjour",
                        "sender_name": "Ami",
                        "sender_address": "ami@example.com",
                        "received_at": "2026-08-27T08:15:00Z",
                        "is_read": False,
                        "has_attachments": False,
                        "snippet": "Salut",
                    }
                ]
            },
        )
    if request.url.path == "/v1/accounts/missing/messages":
        return httpx.Response(404, json={})
    return httpx.Response(500)


def make_client(script: list[Any], transport: httpx.AsyncBaseTransport | None = None) -> TestClient:
    orchestrator = ScriptedModel(script)

    def factory(config: Any, *, tools: bool) -> ScriptedModel:
        return orchestrator if tools else ScriptedModel([final("Synthèse.")])

    settings = Settings(
        _env_file=None,
        api_key=API_KEY,
        anthropic_api_key="anthropic-secret",
        backend_url="http://backend:8080",
        backend_api_key="backend-key",
    )
    return TestClient(
        create_app(settings, model_factory=factory, transport=transport or httpx.MockTransport(backend))
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with make_client(
        [tool_call("list_recent_messages", {"top": 3}), final("Un message de Ami.")]
    ) as test_client:
        yield test_client


def test_answers_a_turn_with_the_documented_response_shape(client: TestClient) -> None:
    response = client.post("/v1/assistant", json=BODY, headers={"X-API-Key": API_KEY})

    assert response.status_code == 201
    assert response.headers["x-content-type-options"] == "nosniff"
    payload = response.json()
    assert set(payload) == {
        "reply",
        "pending_deletions",
        "deleted",
        "views",
        "ui_messages",
        "tools_used",
        "model_id",
        "prompt_version",
    }
    assert payload["reply"] == "Un message de Ami."
    assert payload["tools_used"] == ["list_recent_messages"]
    assert payload["views"][0]["kind"] == "messages"
    assert payload["views"][0]["items"][0]["message_id"] == "AAMk1"
    assert payload["model_id"] == "claude-sonnet-5"


def test_maps_an_unreachable_mailbox_to_a_neutral_422() -> None:
    script = [tool_call("list_recent_messages", {"top": 3}), final("Indisponible.")]
    with make_client(script) as client:
        body = {**BODY, "account_id": "missing"}
        response = client.post("/v1/assistant", json=body, headers={"X-API-Key": API_KEY})
        # A failing tool is reported to the model, not to the caller.
        assert response.status_code == 201
        assert response.json()["reply"] == "Indisponible."


def test_unexpected_failures_become_a_neutral_500() -> None:
    class Exploding:
        async def invoke(self, messages: list[Any]) -> Any:
            raise RuntimeError("secret internal detail")

    settings = Settings(
        _env_file=None,
        api_key=API_KEY,
        anthropic_api_key="anthropic-secret",
        backend_url="http://backend:8080",
        backend_api_key="backend-key",
    )
    app = create_app(settings, model_factory=lambda config, *, tools: Exploding())
    with TestClient(app) as client:
        response = client.post("/v1/assistant", json=BODY, headers={"X-API-Key": API_KEY})
    assert response.status_code == 500
    body = response.json()
    assert body["message"] == "internal server error"
    assert "secret" not in json.dumps(body)
    assert body["request_id"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
