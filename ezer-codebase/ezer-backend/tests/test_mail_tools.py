"""Accès aux messages pour les agents : lecture filtrée, recherche, corbeille, jetons."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from tests.conftest import API_KEY, json_response

MAILBOX = "marctelly@hotmail.fr"
MESSAGE_ID = "AAMkAGI1"
AUTH = {"X-API-Key": API_KEY}

MESSAGE = {
    "id": MESSAGE_ID,
    "conversationId": "AAQkAGI1",
    "subject": "Offre exceptionnelle, agissez vite",
    "from": {"emailAddress": {"name": "Promo", "address": "promo@example.com"}},
    "receivedDateTime": "2026-08-27T08:15:00Z",
    "bodyPreview": "Cliquez ici",
    "isRead": False,
    "hasAttachments": False,
    "body": {"contentType": "text", "content": "Corps du message."},
}


@dataclass
class GraphCall:
    url: str
    method: str
    body: str


@dataclass
class GraphDouble:
    token_response: Callable[[], httpx.Response] | None = None
    calls: list[GraphCall] = field(default_factory=list)

    async def __call__(self, url, *, method="GET", headers=None, body=None, timeout=15.0) -> httpx.Response:
        self.calls.append(GraphCall(url, method, body or ""))
        if url.endswith("/token"):
            if self.token_response is not None:
                return self.token_response()
            return json_response({"access_token": "graph-access-token", "expires_in": 3600})
        if "/messages/" in url and url.endswith("/move"):
            return json_response({"id": "moved-id"})
        if "/mailFolders/inbox/messages" in url:
            return json_response({"value": [MESSAGE]})
        if "/mailFolders" in url:
            return json_response(
                {"value": [{"displayName": "Boîte de réception", "totalItemCount": 42, "unreadItemCount": 7}]}
            )
        if "/me/messages" in url:
            return json_response({"value": [MESSAGE]} if "%24search" in url else MESSAGE)
        return json_response({"error": "unexpected"}, 404)


def write_tokens(work_dir: Path, scopes: str = "offline_access Mail.ReadWrite User.Read") -> None:
    token_file = work_dir / "tokens.json"
    token_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tokens": [
                    {
                        "account_id": "outlook-perso",
                        "mailbox": MAILBOX,
                        "refresh_token": "refresh-token",
                        "connected_at": "2026-08-27T16:19:18.536Z",
                        "scopes": scopes,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    token_file.chmod(0o600)


def environment(work_dir: Path) -> dict[str, str | None]:
    return {
        "EZER_MODE": "configured",
        "EZER_API_KEY": API_KEY,
        "EZER_DATA_FILE": str(work_dir / "ezer.json"),
        "EZER_TOKEN_FILE": str(work_dir / "tokens.json"),
        "EZER_OUTLOOK_CLIENT_ID": "9c82235d-0527-40f5-bdc2-bfe493fcbc3c",
        "EZER_ACCOUNTS_JSON": json.dumps([{"id": "outlook-perso", "provider": "outlook", "mailbox": MAILBOX}]),
    }


def test_lists_the_most_recent_messages_first(start_app, work_dir):
    write_tokens(work_dir)
    graph = GraphDouble()
    client = start_app(environment(work_dir), http_fetch=graph)

    response = client.get("/v1/accounts/outlook-perso/messages?top=5", headers=AUTH)

    assert response.status_code == 200
    first = response.json()["messages"][0]
    assert first["message_id"] == MESSAGE_ID
    assert first["sender_address"] == "promo@example.com"
    assert first["is_read"] is False
    assert any("%24orderby=receivedDateTime+desc" in call.url for call in graph.calls)


def test_searches_reads_a_message_and_returns_the_mailbox_state(start_app, work_dir):
    write_tokens(work_dir)
    graph = GraphDouble()
    client = start_app(environment(work_dir), http_fetch=graph)

    found = client.get("/v1/accounts/outlook-perso/messages?query=promo&top=5", headers=AUTH)
    assert found.status_code == 200
    assert len(found.json()["messages"]) == 1
    assert any("%24search" in call.url for call in graph.calls)

    message = client.get(f"/v1/accounts/outlook-perso/message?message_id={MESSAGE_ID}", headers=AUTH)
    assert message.status_code == 200
    assert message.json()["body_text"] == "Corps du message."

    stats = client.get("/v1/accounts/outlook-perso/mailbox-stats", headers=AUTH)
    assert stats.status_code == 200
    assert stats.json()["total_messages"] == 42
    assert stats.json()["unread_messages"] == 7
    assert stats.json()["mailbox"] == MAILBOX
    assert stats.json()["folders"] == [{"name": "Boîte de réception", "total": 42, "unread": 7}]


def test_moves_a_message_to_the_trash_never_a_permanent_deletion(start_app, work_dir):
    write_tokens(work_dir)
    graph = GraphDouble()
    client = start_app(environment(work_dir), http_fetch=graph)

    response = client.post(
        "/v1/accounts/outlook-perso/message/trash",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"message_id": MESSAGE_ID},
    )

    assert response.status_code == 200
    assert response.json() == {"message_id": MESSAGE_ID, "moved_to": "deleteditems"}
    move = next(call for call in graph.calls if call.url.endswith("/move"))
    assert "deleteditems" in move.body
    assert move.method == "POST"
    assert not any("permanentDelete" in call.url for call in graph.calls)


def test_trash_requires_the_write_consent(start_app, work_dir):
    write_tokens(work_dir, scopes="offline_access Mail.Read User.Read")
    client = start_app(environment(work_dir), http_fetch=GraphDouble())

    accounts = client.get("/v1/accounts", headers=AUTH).json()["accounts"]
    assert accounts[0]["write_enabled"] is False

    response = client.post("/v1/accounts/outlook-perso/message/trash", headers=AUTH, json={"message_id": MESSAGE_ID})
    assert response.status_code == 422


def test_filters_and_sorts_at_the_source_rather_than_afterwards(start_app, work_dir):
    write_tokens(work_dir)
    graph = GraphDouble()
    client = start_app(environment(work_dir), http_fetch=graph)

    response = client.get(
        "/v1/accounts/outlook-perso/messages?top=5&unread_only=true"
        "&from_address=promo@example.com&since=2026-08-01&order=asc",
        headers=AUTH,
    )
    assert response.status_code == 200

    call = next(entry for entry in graph.calls if "mailFolders/inbox/messages" in entry.url)
    parameters = {key: values[0] for key, values in parse_qs(urlsplit(call.url).query).items()}
    assert parameters["$orderby"] == "receivedDateTime asc"
    assert "isRead eq false" in parameters["$filter"]
    assert "from/emailAddress/address eq 'promo@example.com'" in parameters["$filter"]
    assert "receivedDateTime ge 2026-08-01T00:00:00.000Z" in parameters["$filter"]


def test_tallies_recent_messages_by_sender(start_app, work_dir):
    write_tokens(work_dir)
    client = start_app(environment(work_dir), http_fetch=GraphDouble())

    response = client.get("/v1/accounts/outlook-perso/senders?sample=10", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["senders"] == [
        {"sender_address": "promo@example.com", "sender_name": "Promo", "total": 1, "unread": 1}
    ]


def test_refuses_a_sender_or_date_that_are_not_simple_values(start_app, work_dir):
    write_tokens(work_dir)
    client = start_app(environment(work_dir), http_fetch=GraphDouble())

    # Une apostrophe fermerait le littéral du $filter OData : la valeur est rejetée avant l'appel.
    assert (
        client.get("/v1/accounts/outlook-perso/messages?from_address=a'%20or%201%20eq%201", headers=AUTH).status_code
        == 400
    )
    assert client.get("/v1/accounts/outlook-perso/messages?since=hier", headers=AUTH).status_code == 400
    # Un instant bien formé mais impossible est refusé par la couche Graph (422).
    assert client.get("/v1/accounts/outlook-perso/messages?since=2026-13-45", headers=AUTH).status_code == 422


def test_renews_on_the_granted_scopes_not_the_ones_the_code_asks_for(start_app, work_dir):
    write_tokens(work_dir)
    graph = GraphDouble()
    client = start_app(environment(work_dir), http_fetch=graph)

    assert client.get("/v1/accounts/outlook-perso/messages?top=3", headers=AUTH).status_code == 200

    refresh = next(call for call in graph.calls if call.url.endswith("/token"))
    assert "Mail.ReadWrite" in refresh.body
    assert "grant_type=refresh_token" in refresh.body


def test_normalizes_the_renewal_scopes_returned_by_microsoft(start_app, work_dir):
    # Ce que Microsoft renvoie réellement : pas d'`offline_access`, et `profile` seule, que l'endpoint
    # refuse sans `openid`. Le renouvellement ne doit pas rejouer cette chaîne telle quelle.
    write_tokens(work_dir, scopes="Mail.ReadWrite User.Read Mail.Read profile")
    graph = GraphDouble()
    client = start_app(environment(work_dir), http_fetch=graph)

    assert client.get("/v1/accounts/outlook-perso/messages?top=3", headers=AUTH).status_code == 200

    refresh = next(call for call in graph.calls if call.url.endswith("/token"))
    scope = parse_qs(refresh.body)["scope"][0]
    assert set(scope.split(" ")) >= {"offline_access", "Mail.ReadWrite", "User.Read"}
    assert "profile" not in scope


def test_keeps_the_connection_when_a_renewal_fails_without_revocation(start_app, work_dir):
    # Portée refusée : incident de configuration, pas une révocation du consentement.
    write_tokens(work_dir)
    client = start_app(
        environment(work_dir),
        http_fetch=GraphDouble(token_response=lambda: json_response({"error": "invalid_scope"}, 400)),
    )

    assert client.get("/v1/accounts/outlook-perso/messages?top=3", headers=AUTH).status_code == 422

    accounts = client.get("/v1/accounts", headers=AUTH).json()["accounts"]
    assert accounts[0]["status"] == "connected"


def test_forgets_the_token_only_on_an_explicit_revocation_by_microsoft(start_app, work_dir):
    write_tokens(work_dir)
    client = start_app(
        environment(work_dir),
        http_fetch=GraphDouble(token_response=lambda: json_response({"error": "invalid_grant"}, 400)),
    )

    assert client.get("/v1/accounts/outlook-perso/messages?top=3", headers=AUTH).status_code == 422

    accounts = client.get("/v1/accounts", headers=AUTH).json()["accounts"]
    assert accounts[0]["status"] == "disconnected"


def test_requires_the_api_key_a_known_account_and_a_valid_identifier(start_app, work_dir):
    write_tokens(work_dir)
    client = start_app(environment(work_dir), http_fetch=GraphDouble())

    assert client.get("/v1/accounts/outlook-perso/messages").status_code == 401
    assert client.get("/v1/accounts/inconnu/messages", headers=AUTH).status_code == 404
    assert client.get("/v1/accounts/outlook-perso/message?message_id=../../secret", headers=AUTH).status_code == 400
    assert client.get("/v1/accounts/outlook-perso/message", headers=AUTH).status_code == 400
    assert client.get("/v1/accounts/outlook-perso/messages?top=26", headers=AUTH).status_code == 400
    assert client.get("/v1/accounts/outlook-perso/messages?query=", headers=AUTH).status_code == 400
    assert (
        client.post(
            "/v1/accounts/outlook-perso/message/trash", headers=AUTH, json={"message_id": MESSAGE_ID, "x": 1}
        ).status_code
        == 400
    )


def test_a_rotated_refresh_token_replaces_only_that_account(start_app, work_dir):
    write_tokens(work_dir)
    graph = GraphDouble(
        token_response=lambda: json_response(
            {"access_token": "graph-access-token", "refresh_token": "rotated", "expires_in": 3600}
        )
    )
    client = start_app(environment(work_dir), http_fetch=graph)

    assert client.get("/v1/accounts/outlook-perso/messages", headers=AUTH).status_code == 200

    stored = json.loads((work_dir / "tokens.json").read_text(encoding="utf-8"))
    assert stored["tokens"][0]["refresh_token"] == "rotated"
    assert stored["tokens"][0]["scopes"] == "offline_access Mail.ReadWrite User.Read"
    assert (work_dir / "tokens.json").stat().st_mode & 0o777 == 0o600
