"""Contrat HTTP de base : santé, authentification, validation, synchronisation, limites de corps."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.web.middleware import MAX_REQUEST_BODY_BYTES
from tests.conftest import API_KEY


def demo_environment(work_dir: Path) -> dict[str, str | None]:
    return {
        "EZER_MODE": "demo",
        "EZER_API_KEY": API_KEY,
        "EZER_DATA_FILE": str(work_dir / "data" / "ezer.json"),
        "EZER_DEMO_RESET_ON_START": "true",
    }


def test_exposes_unauthenticated_liveness_and_readiness_checks(start_app, work_dir):
    client = start_app(demo_environment(work_dir))

    live = client.get("/health/live")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}

    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert ready.headers["cache-control"] == "no-store"
    assert ready.headers["x-content-type-options"] == "nosniff"
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,128}", ready.headers["x-request-id"])


def test_protects_every_v1_route_with_a_neutral_api_key_failure(start_app, work_dir):
    client = start_app(demo_environment(work_dir))

    response = client.get("/v1/accounts")
    assert response.status_code == 401
    assert response.json()["message"] == "unauthorized"
    assert "stack" not in response.json()
    assert response.headers["cache-control"] == "no-store"

    wrong = client.get("/v1/accounts", headers={"X-API-Key": "wrong-key"})
    assert wrong.status_code == 401
    assert set(wrong.json()) == {"statusCode", "message", "detail", "request_id"}


def test_returns_only_public_account_fields(start_app, work_dir, auth):
    client = start_app(demo_environment(work_dir))

    response = client.get("/v1/accounts", headers={**auth, "X-Request-ID": "frontend.request-42"})

    assert response.status_code == 200
    assert response.json() == {
        "accounts": [
            {
                "id": "gmail-primary",
                "provider": "gmail",
                "mailbox": None,
                "status": "disconnected",
                "connected_at": None,
                "write_enabled": False,
            },
            {
                "id": "outlook-ops",
                "provider": "outlook",
                "mailbox": None,
                "status": "disconnected",
                "connected_at": None,
                "write_enabled": False,
            },
        ]
    }
    assert response.headers["x-request-id"] == "frontend.request-42"


def test_paginates_filters_and_returns_analysis_details(start_app, work_dir, auth):
    client = start_app(demo_environment(work_dir))

    page = client.get("/v1/analyses?limit=2&offset=0", headers=auth)
    assert page.status_code == 200
    assert page.json()["limit"] == 2
    assert page.json()["offset"] == 0
    assert page.json()["total"] == 4
    assert len(page.json()["items"]) == 2

    filtered = client.get("/v1/analyses?category=security&needs_human_review=true", headers=auth)
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["category"] == "security"

    analysis_id = page.json()["items"][0]["analysis_id"]
    detail = client.get(f"/v1/analyses/{analysis_id}", headers=auth)
    assert detail.status_code == 200
    assert detail.json()["analysis_id"] == analysis_id
    assert detail.json()["safety"] is not None
    assert detail.json()["triage"] is not None

    assert client.get("/v1/analyses/" + "0" * 64, headers=auth).status_code == 404
    assert client.get("/v1/analyses/not-a-hash", headers=auth).status_code == 400


def test_rejects_unknown_fields_and_malformed_filters(start_app, work_dir, auth):
    client = start_app(demo_environment(work_dir))

    unknown_query = client.get("/v1/analyses?unexpected=true", headers=auth)
    assert unknown_query.status_code == 400
    assert unknown_query.json()["message"] == "invalid request"

    assert client.get("/v1/analyses?needs_human_review=1", headers=auth).status_code == 400
    assert client.get("/v1/analyses?limit=0", headers=auth).status_code == 400
    assert client.get("/v1/analyses?limit=101", headers=auth).status_code == 400
    assert client.get("/v1/analyses?limit=1&limit=2", headers=auth).status_code == 400
    assert client.get("/v1/analyses?category=nope", headers=auth).status_code == 400

    assert client.post("/v1/sync", headers=auth, json={"account_ids": [], "extra": True}).status_code == 400
    assert client.post("/v1/sync", headers=auth, json={"account_ids": []}).status_code == 400
    assert client.post("/v1/sync", headers=auth, json={"limit": 501}).status_code == 400
    assert client.post("/v1/sync", headers=auth, json=[1]).status_code == 400
    assert client.post("/v1/sync", headers=auth, json={"account_ids": ["a", "a"]}).status_code == 400


def test_synchronizes_seeded_connectors_end_to_end_and_advances_cursors(start_app, work_dir, auth):
    client = start_app(demo_environment(work_dir))
    data_file = work_dir / "data" / "ezer.json"

    first = client.post("/v1/sync", headers=auth, json={})
    assert first.status_code == 200
    reports = first.json()["reports"]
    assert len(reports) == 2
    assert sum(report["processed"] for report in reports) == 3
    assert all(report["cursor_advanced"] for report in reports)

    page = client.get("/v1/analyses?limit=100", headers=auth)
    assert page.json()["total"] == 7

    second = client.post("/v1/sync", headers=auth, json={})
    assert second.status_code == 200
    assert all(report["fetched"] == 0 for report in second.json()["reports"])

    persisted = json.loads(data_file.read_text(encoding="utf-8"))
    assert len(persisted["analyses"]) == 7
    assert [name for name in data_file.parent.iterdir() if name.name.endswith(".tmp")] == []
    assert (data_file.stat().st_mode & 0o777) == 0o600


def test_sync_without_body_uses_defaults(start_app, work_dir, auth):
    client = start_app(demo_environment(work_dir))
    response = client.post("/v1/sync", headers=auth)
    assert response.status_code == 200
    assert len(response.json()["reports"]) == 2


def test_returns_422_for_an_unknown_synchronization_account(start_app, work_dir, auth):
    client = start_app(demo_environment(work_dir))

    response = client.post("/v1/sync", headers=auth, json={"account_ids": ["unknown-account"], "limit": 10})
    assert response.status_code == 422
    assert response.json()["message"] == "invalid request"


def test_rejects_json_bodies_larger_than_64_kib_before_controller_validation(start_app, work_dir, auth):
    client = start_app(demo_environment(work_dir))

    response = client.post(
        "/v1/sync",
        headers={**auth, "Content-Type": "application/json"},
        content=json.dumps({"padding": "x" * MAX_REQUEST_BODY_BYTES}),
    )
    assert response.status_code == 413
    assert response.json()["message"] == "request too large"
    assert "x-request-id" in response.headers

    # Même sans clé d'API : la borne s'applique avant l'authentification.
    unauthenticated = client.post(
        "/v1/sync",
        headers={"Content-Type": "application/json"},
        content=json.dumps({"padding": "x" * MAX_REQUEST_BODY_BYTES}),
    )
    assert unauthenticated.status_code == 413


def test_enforces_the_body_limit_on_chunked_requests_and_every_media_type(start_app, work_dir, auth):
    client = start_app(demo_environment(work_dir))

    text = client.post(
        "/v1/sync",
        headers={**auth, "Content-Type": "text/plain"},
        content="x" * (MAX_REQUEST_BODY_BYTES + 1),
    )
    assert text.status_code == 413

    body = json.dumps({"padding": "x" * MAX_REQUEST_BODY_BYTES})
    midpoint = len(body) // 2

    def chunks():
        yield body[:midpoint].encode()
        yield body[midpoint:].encode()

    chunked = client.post(
        "/v1/sync",
        headers={**auth, "Content-Type": "application/json", "Transfer-Encoding": "chunked"},
        content=chunks(),
    )
    assert chunked.status_code == 413
    assert chunked.json()["message"] == "request too large"


def test_accepts_json_only_on_write_routes(start_app, work_dir, auth):
    client = start_app(demo_environment(work_dir))

    response = client.post("/v1/sync", headers={**auth, "Content-Type": "text/plain"}, content="{}")
    assert response.status_code == 415
    assert response.json()["message"] == "unsupported media type"
    assert response.headers["cache-control"] == "no-store"

    invalid = client.post("/v1/sync", headers={**auth, "Content-Type": "application/json"}, content="{nope")
    assert invalid.status_code == 400
    assert invalid.json()["message"] == "invalid request"

    scalar = client.post("/v1/sync", headers={**auth, "Content-Type": "application/json"}, content="12")
    assert scalar.status_code == 400

    constant = client.post("/v1/sync", headers={**auth, "Content-Type": "application/json"}, content="NaN")
    assert constant.status_code == 400


def test_replaces_invalid_incoming_request_ids(start_app, work_dir):
    client = start_app(demo_environment(work_dir))

    response = client.get("/health/live", headers={"X-Request-ID": "invalid request id with spaces"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] != "invalid request id with spaces"
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,128}", response.headers["x-request-id"])


def test_unknown_routes_and_methods_use_the_neutral_envelope(start_app, work_dir, auth):
    client = start_app(demo_environment(work_dir))

    missing = client.get("/v1/nothing", headers=auth)
    assert missing.status_code == 404
    assert missing.json()["message"] == "resource not found"
    assert missing.json()["request_id"] == missing.headers["x-request-id"]

    wrong_method = client.delete("/v1/analyses", headers=auth)
    assert wrong_method.status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_readiness_reports_unavailable_when_the_data_file_disappears(start_app, work_dir):
    client = start_app(demo_environment(work_dir))
    (work_dir / "data" / "ezer.json").unlink()

    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_internal_failures_never_leak_details(start_app, work_dir, auth, monkeypatch):
    client = start_app(demo_environment(work_dir))

    async def boom(*_args, **_kwargs):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(client.app.state.services.persistence, "list_accounts", boom)
    response = client.get("/v1/accounts", headers=auth)
    assert response.status_code == 500
    assert response.json()["message"] == "internal server error"
    assert "secret" not in response.text
    assert response.headers["cache-control"] == "no-store"
