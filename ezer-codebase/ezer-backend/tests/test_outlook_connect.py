"""Connexion Outlook par device code : le code à saisir sort, jamais le device code ni les jetons."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl

import httpx

from tests.conftest import API_KEY, json_response

MAILBOX = "marctelly@outlook.com"
CLIENT_ID = "9c82235d-0527-40f5-bdc2-bfe493fcbc3c"


@dataclass
class FetchCall:
    url: str
    fields: dict[str, str]


@dataclass
class MicrosoftDouble:
    signed_in_mailbox: str = MAILBOX
    calls: list[FetchCall] = field(default_factory=list)
    token_calls: int = 0

    async def __call__(self, url, *, method="GET", headers=None, body=None, timeout=15.0) -> httpx.Response:
        self.calls.append(FetchCall(url, dict(parse_qsl(body or ""))))
        if url.endswith("/devicecode"):
            return json_response(
                {
                    "device_code": "device-code-secret",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://microsoft.com/link",
                    "expires_in": 900,
                    "interval": 5,
                }
            )
        if url.endswith("/token"):
            self.token_calls += 1
            if self.token_calls == 1:
                return json_response({"error": "authorization_pending"}, 400)
            return json_response(
                {"access_token": "graph-access-token", "refresh_token": "graph-refresh-token", "expires_in": 3600}
            )
        if url.startswith("https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"):
            return json_response(
                {
                    "value": [
                        {
                            "id": "AAMkAGI1",
                            "conversationId": "AAQkAGI1",
                            "subject": "Facture à régler avant vendredi",
                            "from": {"emailAddress": {"name": "Comptabilité", "address": "compta@example.com"}},
                            "receivedDateTime": "2026-08-27T08:15:00Z",
                            "bodyPreview": "Merci de régler la facture 2026-118.",
                            "body": {"contentType": "text", "content": "Merci de régler la facture 2026-118."},
                        }
                    ]
                }
            )
        if url.startswith("https://graph.microsoft.com/v1.0/me"):
            return json_response({"mail": self.signed_in_mailbox})
        return json_response({"error": "unexpected_endpoint"}, 404)


async def instant_sleep(_milliseconds: float) -> None:
    return None


def configured_environment(work_dir: Path, **overrides: str | None) -> dict[str, str | None]:
    environment: dict[str, str | None] = {
        "EZER_MODE": "configured",
        "EZER_API_KEY": API_KEY,
        "EZER_DATA_FILE": str(work_dir / "ezer.json"),
        "EZER_TOKEN_FILE": str(work_dir / "tokens.json"),
        "EZER_OUTLOOK_CLIENT_ID": CLIENT_ID,
        "EZER_ACCOUNTS_JSON": json.dumps([{"id": "outlook-perso", "provider": "outlook", "mailbox": MAILBOX}]),
    }
    environment.update(overrides)
    return environment


AUTH = {"X-API-Key": API_KEY}


def settled_connection(client) -> dict:
    for _ in range(100):
        response = client.get("/v1/accounts/outlook-perso/connection", headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        if body["status"] != "pending":
            return body
        time.sleep(0.01)
    raise AssertionError("the device-code flow never settled")


def test_exposes_the_code_to_enter_without_ever_revealing_the_device_code(start_app, work_dir):
    double = MicrosoftDouble()
    client = start_app(configured_environment(work_dir), http_fetch=double, sleeper=instant_sleep)

    started = client.post("/v1/accounts/outlook-perso/connection", headers=AUTH)

    assert started.status_code == 200
    body = started.json()
    assert body["account_id"] == "outlook-perso"
    assert body["provider"] == "outlook"
    assert body["mailbox"] == MAILBOX
    assert body["status"] == "pending"
    assert body["verification_uri"] == "https://microsoft.com/link"
    assert body["user_code"] == "ABCD-EFGH"
    assert "device-code-secret" not in json.dumps(body)
    assert "Mail.ReadWrite" in double.calls[0].fields["scope"]
    assert "offline_access" in double.calls[0].fields["scope"]


def test_connects_the_mailbox_keeps_the_refresh_token_out_of_the_data_file_and_protects_it(start_app, work_dir):
    client = start_app(configured_environment(work_dir), http_fetch=MicrosoftDouble(), sleeper=instant_sleep)

    assert client.post("/v1/accounts/outlook-perso/connection", headers=AUTH).status_code == 200

    settled = settled_connection(client)
    assert settled["status"] == "connected"
    assert settled["code"] is None
    assert settled["verification_uri"] is None
    assert settled["user_code"] is None

    accounts = client.get("/v1/accounts", headers=AUTH)
    assert accounts.status_code == 200
    [account] = accounts.json()["accounts"]
    assert account["id"] == "outlook-perso"
    assert account["mailbox"] == MAILBOX
    assert account["status"] == "connected"

    token_file = work_dir / "tokens.json"
    stored = json.loads(token_file.read_text(encoding="utf-8"))
    assert stored["tokens"][0]["account_id"] == "outlook-perso"
    assert stored["tokens"][0]["mailbox"] == MAILBOX
    assert stored["tokens"][0]["refresh_token"] == "graph-refresh-token"
    assert token_file.stat().st_mode & 0o777 == 0o600

    assert "graph-refresh-token" not in (work_dir / "ezer.json").read_text(encoding="utf-8")


def test_reads_the_connected_mailbox_through_microsoft_graph(start_app, work_dir):
    double = MicrosoftDouble()
    client = start_app(configured_environment(work_dir), http_fetch=double, sleeper=instant_sleep)

    client.post("/v1/accounts/outlook-perso/connection", headers=AUTH)
    settled_connection(client)

    synchronized = client.post(
        "/v1/sync",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"account_ids": ["outlook-perso"], "limit": 10},
    )

    assert synchronized.status_code == 200
    report = synchronized.json()["reports"][0]
    assert report["account_id"] == "outlook-perso"
    assert report["provider"] == "outlook"
    assert report["fetched"] == 1
    assert report["processed"] == 1
    assert report["failed"] == 0
    assert "outlook-perso" in report["analyses"][0]["message_ref"]

    graph_call = next(call for call in double.calls if "/mailFolders/inbox/messages" in call.url)
    assert "%24top=10" in graph_call.url


def test_refuses_a_mailbox_other_than_the_configured_one(start_app, work_dir):
    double = MicrosoftDouble(signed_in_mailbox="quelquun.dautre@outlook.com")
    client = start_app(configured_environment(work_dir), http_fetch=double, sleeper=instant_sleep)

    client.post("/v1/accounts/outlook-perso/connection", headers=AUTH)

    settled = settled_connection(client)
    assert settled["status"] == "failed"
    assert settled["code"] == "mailbox_mismatch"
    assert not (work_dir / "tokens.json").exists()


def test_reports_an_unconfigured_public_application_without_disclosing_configuration(start_app, work_dir):
    client = start_app(
        configured_environment(work_dir, EZER_OUTLOOK_CLIENT_ID=None),
        http_fetch=MicrosoftDouble(),
        sleeper=instant_sleep,
    )

    started = client.post("/v1/accounts/outlook-perso/connection", headers=AUTH)

    assert started.status_code == 200
    assert started.json()["status"] == "failed"
    assert started.json()["code"] == "client_not_configured"


def test_disconnects_the_account_and_forgets_its_refresh_token(start_app, work_dir):
    client = start_app(configured_environment(work_dir), http_fetch=MicrosoftDouble(), sleeper=instant_sleep)

    client.post("/v1/accounts/outlook-perso/connection", headers=AUTH)
    settled_connection(client)

    disconnected = client.delete("/v1/accounts/outlook-perso/connection", headers=AUTH)

    assert disconnected.status_code == 200
    assert disconnected.json()["status"] == "disconnected"
    assert json.loads((work_dir / "tokens.json").read_text(encoding="utf-8"))["tokens"] == []


def test_requires_the_api_key_and_ignores_an_unknown_account(start_app, work_dir):
    client = start_app(configured_environment(work_dir), http_fetch=MicrosoftDouble(), sleeper=instant_sleep)

    assert client.post("/v1/accounts/outlook-perso/connection").status_code == 401
    assert client.get("/v1/accounts/inconnu/connection", headers=AUTH).status_code == 404
    assert client.get("/v1/accounts/bad id/connection", headers=AUTH).status_code == 400


def test_a_second_connect_while_pending_reuses_the_running_flow(start_app, work_dir):
    class NeverAuthorized(MicrosoftDouble):
        async def __call__(self, url, **kwargs):
            if url.endswith("/token"):
                return json_response({"error": "authorization_pending"}, 400)
            return await super().__call__(url, **kwargs)

    double = NeverAuthorized()
    # Le dormeur ne rend jamais la main : le flux reste en attente, comme avant la saisie du code.
    import asyncio

    async def blocked_sleep(_ms: float) -> None:
        await asyncio.sleep(3600)

    client = start_app(configured_environment(work_dir), http_fetch=double, sleeper=blocked_sleep)
    first = client.post("/v1/accounts/outlook-perso/connection", headers=AUTH).json()
    second = client.post("/v1/accounts/outlook-perso/connection", headers=AUTH).json()

    assert first["status"] == second["status"] == "pending"
    assert first["user_code"] == second["user_code"]
    assert sum(call.url.endswith("/devicecode") for call in double.calls) == 1


def test_a_non_microsoft_verification_uri_is_rejected(start_app, work_dir):
    class Phishing(MicrosoftDouble):
        async def __call__(self, url, **kwargs):
            if url.endswith("/devicecode"):
                return json_response(
                    {
                        "device_code": "x",
                        "user_code": "ABCD-EFGH",
                        "verification_uri": "https://evil.example/link",
                        "expires_in": 900,
                    }
                )
            return await super().__call__(url, **kwargs)

    client = start_app(configured_environment(work_dir), http_fetch=Phishing(), sleeper=instant_sleep)

    started = client.post("/v1/accounts/outlook-perso/connection", headers=AUTH).json()
    assert started["status"] == "failed"
    assert started["code"] == "device_flow_failed"
    assert started["verification_uri"] is None
