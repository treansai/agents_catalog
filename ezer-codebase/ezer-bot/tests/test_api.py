import re
from collections.abc import Iterator

import pytest
from app.config import Settings
from app.main import MAX_REQUEST_BODY_BYTES, create_app
from fastapi.testclient import TestClient

API_KEY = "test-api-key"
VALID_BODY = {"account_id": "outlook-perso", "messages": [{"role": "user", "content": "Bonjour"}]}


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(_env_file=None, api_key=API_KEY)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_exposes_unauthenticated_liveness_and_readiness_checks(client: TestClient) -> None:
    live = client.get("/health/live")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}

    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert ready.headers["cache-control"] == "no-store"
    assert ready.headers["x-content-type-options"] == "nosniff"
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,128}", ready.headers["x-request-id"])


def test_echoes_a_well_formed_request_id_and_replaces_a_malformed_one(client: TestClient) -> None:
    kept = client.get("/health/live", headers={"X-Request-ID": "trace-123"})
    assert kept.headers["x-request-id"] == "trace-123"
    replaced = client.get("/health/live", headers={"X-Request-ID": "bad id with spaces"})
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,128}", replaced.headers["x-request-id"])
    assert replaced.headers["x-request-id"] != "bad id with spaces"


def test_protects_the_assistant_route_with_the_api_key(client: TestClient) -> None:
    response = client.post("/v1/assistant", json=VALID_BODY)
    assert response.status_code == 401
    body = response.json()
    assert body["statusCode"] == 401
    assert body["message"] == "unauthorized"
    assert body["detail"] == "unauthorized"
    assert body["request_id"] == response.headers["x-request-id"]

    wrong = client.post("/v1/assistant", json=VALID_BODY, headers={"X-API-Key": "nope"})
    assert wrong.status_code == 401


def test_rejects_an_unconfigured_assistant_instead_of_calling_a_model(client: TestClient) -> None:
    response = client.post("/v1/assistant", json=VALID_BODY, headers={"X-API-Key": API_KEY})
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid request"
    assert response.headers["cache-control"] == "no-store"


def test_rejects_an_oversized_request_before_authentication(client: TestClient) -> None:
    response = client.post("/v1/assistant", content="x" * (MAX_REQUEST_BODY_BYTES + 1))
    assert response.status_code == 413
    assert response.json()["message"] == "request too large"


def test_bounds_a_chunked_request_without_content_length(client: TestClient) -> None:
    padding = f'"padding":"{"x" * MAX_REQUEST_BODY_BYTES}"'
    payload = ("{" + padding + "}").encode()
    half = len(payload) // 2

    def chunks() -> Iterator[bytes]:
        yield payload[:half]
        yield payload[half:]

    response = client.post(
        "/v1/assistant",
        content=chunks(),
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
    )
    assert response.status_code == 413


def test_rejects_non_json_and_malformed_bodies(client: TestClient) -> None:
    headers = {"X-API-Key": API_KEY}
    text = client.post("/v1/assistant", content="hello", headers={**headers, "Content-Type": "text/plain"})
    assert text.status_code == 415
    assert text.json()["message"] == "unsupported media type"
    broken = client.post(
        "/v1/assistant", content="{not json", headers={**headers, "Content-Type": "application/json"}
    )
    assert broken.status_code == 400
    scalar = client.post(
        "/v1/assistant", content="null", headers={**headers, "Content-Type": "application/json"}
    )
    assert scalar.status_code == 400


def test_validates_the_payload_after_authentication(client: TestClient) -> None:
    headers = {"X-API-Key": API_KEY}
    assert client.post("/v1/assistant", headers=headers).status_code == 400
    assert client.post("/v1/assistant", json={}, headers=headers).status_code == 400
    assert client.post("/v1/assistant", json={**VALID_BODY, "extra": 1}, headers=headers).status_code == 400
    bad_account = {**VALID_BODY, "account_id": "../etc"}
    assert client.post("/v1/assistant", json=bad_account, headers=headers).status_code == 400
    bad_role = {**VALID_BODY, "messages": [{"role": "system", "content": "x"}]}
    assert client.post("/v1/assistant", json=bad_role, headers=headers).status_code == 400
    assert client.post("/v1/assistant", json=[], headers=headers).status_code == 400
    assert client.post("/v1/assistant", json=[], headers={}).status_code == 401


def test_unknown_routes_and_methods_use_the_neutral_envelope(client: TestClient) -> None:
    missing = client.get("/nope")
    assert missing.status_code == 404
    assert missing.json()["message"] == "resource not found"
    wrong_method = client.get("/v1/assistant", headers={"X-API-Key": API_KEY})
    assert wrong_method.status_code == 404
