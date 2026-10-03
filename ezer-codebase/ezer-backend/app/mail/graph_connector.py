"""Lecture seule d'une boîte Outlook via Microsoft Graph, page par page."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.domain.models import Account, FetchBatch, MailMessage
from app.mail.errors import MailConnectionError
from app.services.http_fetch import HttpFetch, build_url
from app.services.timeutil import parse_instant, to_iso

GRAPH_HOST = "graph.microsoft.com"
GRAPH_ORIGIN = f"https://{GRAPH_HOST}"
MESSAGES_PATH = "/v1.0/me/mailFolders/inbox/messages"
SELECT_FIELDS = "id,conversationId,subject,from,receivedDateTime,bodyPreview,body"
REQUEST_TIMEOUT_S = 20.0
MAX_RESPONSE_CHARS = 8 * 1024 * 1024
MAX_PAGE_SIZE = 50
MAX_SUBJECT_CHARS = 998
MAX_ADDRESS_CHARS = 320
MAX_SNIPPET_CHARS = 2_000
MAX_BODY_CHARS = 500_000

AccessTokenProvider = Callable[[], Awaitable[str]]


def _bounded_string(value: Any, maximum: int) -> str:
    return value[:maximum] if isinstance(value, str) else ""


def _reject_constant(name: str) -> Any:
    raise ValueError(f"invalid JSON constant {name}")


def trusted_graph_url(value: str) -> str | None:
    """Un `@odata.nextLink` est une valeur opaque, mais elle est suivie comme une URL.

    Elle est donc acceptée uniquement si elle pointe encore vers Microsoft Graph. L'analyse utilise
    le même parseur que le client HTTP, pour qu'aucune divergence ne détourne la destination.
    """
    if any(char in value for char in ("\\", "\r", "\n", "\t")) or value != value.strip():
        return None
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError):
        return None
    if url.scheme != "https" or url.host != GRAPH_HOST or url.port not in (None, 443):
        return None
    if url.userinfo or not url.path.startswith("/v1.0/"):
        return None
    return str(url)


class GraphMailConnector:
    def __init__(self, account: Account, access_token: AccessTokenProvider, http_fetch: HttpFetch) -> None:
        self.account_id = account.id
        self.provider = account.provider
        self._access_token = access_token
        self._fetch = http_fetch

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        page_size = min(max(limit, 1), MAX_PAGE_SIZE)
        cursor_reset = False
        if cursor is None:
            url = self._first_page_url(page_size)
        else:
            trusted = trusted_graph_url(cursor)
            if trusted is None:
                cursor_reset = True
                url = self._first_page_url(page_size)
            else:
                url = trusted

        payload = await self._request(url)
        values = payload.get("value")
        if not isinstance(values, list):
            raise MailConnectionError("graph_response_invalid")
        parsed = (self._to_mail_message(value) for value in values)
        messages = [message for message in parsed if message is not None]

        next_link = payload.get("@odata.nextLink")
        next_cursor = trusted_graph_url(next_link) if isinstance(next_link, str) else None
        # Sans page suivante, la page courante est rejouée au prochain tour : Graph ne renvoie alors
        # que les messages arrivés depuis, l'identité de message évitant les doublons.
        if next_cursor is None:
            next_cursor = None if cursor_reset else cursor
        return FetchBatch(messages=messages, next_cursor=next_cursor, cursor_reset=cursor_reset)

    @staticmethod
    def _first_page_url(page_size: int) -> str:
        return build_url(
            GRAPH_ORIGIN,
            MESSAGES_PATH,
            [
                ("$select", SELECT_FIELDS),
                # Ordre croissant : le curseur progresse dans le sens d'arrivée des messages.
                ("$orderby", "receivedDateTime asc"),
                ("$top", str(page_size)),
            ],
        )

    async def _request(self, url: str) -> dict[str, Any]:
        token = await self._access_token()
        try:
            response = await self._fetch(
                url,
                method="GET",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    # Graph renvoie alors un corps texte : aucun HTML n'a besoin d'être nettoyé ici.
                    "Prefer": 'outlook.body-content-type="text"',
                },
                timeout=REQUEST_TIMEOUT_S,
            )
        except Exception as error:
            raise MailConnectionError("graph_unreachable") from error
        if response.status_code in (401, 403):
            raise MailConnectionError("reauthentication_required")
        if response.status_code == 429:
            raise MailConnectionError("graph_rate_limited")
        if not response.is_success:
            raise MailConnectionError("graph_request_failed")

        raw = response.text
        if len(raw) > MAX_RESPONSE_CHARS:
            raise MailConnectionError("graph_response_too_large")
        try:
            payload = json.loads(raw, parse_constant=_reject_constant)
        except (ValueError, RecursionError) as error:
            raise MailConnectionError("graph_response_invalid") from error
        if not isinstance(payload, dict):
            raise MailConnectionError("graph_response_invalid")
        return payload

    def _to_mail_message(self, value: Any) -> MailMessage | None:
        if not isinstance(value, dict):
            return None
        identifier = value.get("id")
        if not isinstance(identifier, str) or len(identifier) == 0 or len(identifier) > 1_024:
            return None
        received = value.get("receivedDateTime")
        if not isinstance(received, str) or parse_instant(received) is None:
            return None
        from_field = value.get("from")
        sender = from_field.get("emailAddress") if isinstance(from_field, dict) else None
        if not isinstance(sender, dict):
            sender = {}
        body = value.get("body")
        if not isinstance(body, dict):
            body = {}
        conversation = value.get("conversationId")
        return MailMessage(
            account_id=self.account_id,
            provider=self.provider,
            provider_message_id=identifier,
            thread_id=conversation if isinstance(conversation, str) and len(conversation) <= 1_024 else None,
            subject=_bounded_string(value.get("subject"), MAX_SUBJECT_CHARS),
            sender_name=_bounded_string(sender.get("name"), MAX_ADDRESS_CHARS),
            sender_address=_bounded_string(sender.get("address"), MAX_ADDRESS_CHARS),
            received_at=to_iso(received),
            body_text=_bounded_string(body.get("content"), MAX_BODY_CHARS),
            snippet=_bounded_string(value.get("bodyPreview"), MAX_SNIPPET_CHARS),
        )
