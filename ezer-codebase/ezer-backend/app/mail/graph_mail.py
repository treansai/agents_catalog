"""Opérations Microsoft Graph exposées aux agents.

Chaque appel repart du jeton délégué du compte : aucun agent ne détient de credential, et la seule
écriture possible est le déplacement d'un message vers la corbeille.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from app.domain.models import Account
from app.mail.errors import MailConnectionError
from app.mail.outlook_auth import OutlookAuth, grants_write_access
from app.mail.token_store import TokenStore
from app.services.http_fetch import HttpFetch, build_url
from app.services.timeutil import parse_instant, to_iso

GRAPH_ORIGIN = "https://graph.microsoft.com"
REQUEST_TIMEOUT_S = 20.0
MAX_RESPONSE_CHARS = 4 * 1024 * 1024
MAX_PAGE_SIZE = 25
MAX_SUBJECT_CHARS = 400
MAX_ADDRESS_CHARS = 320
MAX_SNIPPET_CHARS = 600
MAX_BODY_CHARS = 20_000
MAX_SEARCH_CHARS = 200

# Identifiants Graph : base64url étendu. Contrôlés avant toute concaténation d'URL.
MESSAGE_ID = re.compile(r"[A-Za-z0-9_\-=+/]{1,512}")
ADDRESS = re.compile(r"[^\s@'\"]{1,64}@[^\s@'\"]{1,255}")

LIST_FIELDS = "id,conversationId,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments"

MessageHeader = dict[str, Any]
MessageBody = dict[str, Any]
SenderTally = dict[str, Any]
MailboxStats = dict[str, Any]


def _bounded_string(value: Any, maximum: int) -> str:
    return value[:maximum] if isinstance(value, str) else ""


def _count_of(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    if value != value or value in (float("inf"), float("-inf")) or value < 0:
        return 0
    return int(value)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"invalid JSON constant {name}")


class GraphMail:
    def __init__(self, auth: OutlookAuth, tokens: TokenStore, http_fetch: HttpFetch) -> None:
        self._auth = auth
        self._tokens = tokens
        self._fetch = http_fetch

    async def list_messages(
        self,
        account: Account,
        *,
        top: int,
        unread_only: bool | None = None,
        from_address: str | None = None,
        since: str | None = None,
        until: str | None = None,
        order: str | None = None,
    ) -> list[MessageHeader]:
        """Liste filtrée et triée par Microsoft Graph.

        Le filtrage et le tri se font à la source plutôt qu'après coup : un agent qui demande « les
        non-lus de la semaine » ne doit pas rapatrier la boîte entière pour la trier lui-même.
        """
        params: list[tuple[str, str]] = [
            ("$select", LIST_FIELDS),
            ("$orderby", f"receivedDateTime {'asc' if order == 'asc' else 'desc'}"),
            ("$top", str(min(max(top, 1), MAX_PAGE_SIZE))),
        ]
        filters: list[str] = []
        if unread_only is True:
            filters.append("isRead eq false")
        if from_address is not None:
            address = from_address.strip().lower()
            if not ADDRESS.fullmatch(address):
                raise MailConnectionError("invalid_sender_address")
            filters.append(f"from/emailAddress/address eq '{address}'")
        start = self._instant(since)
        if start is not None:
            filters.append(f"receivedDateTime ge {start}")
        end = self._instant(until)
        if end is not None:
            filters.append(f"receivedDateTime le {end}")
        if filters:
            params.append(("$filter", " and ".join(filters)))
        return await self._read_headers(account, build_url(GRAPH_ORIGIN, "/v1.0/me/mailFolders/inbox/messages", params))

    async def tallies_by_sender(self, account: Account, sample_size: int) -> list[SenderTally]:
        """Agrège un échantillon récent par expéditeur : le tri « qui m'écrit le plus »."""
        headers = await self.list_messages(account, top=min(max(sample_size, 1), MAX_PAGE_SIZE))
        tallies: dict[str, SenderTally] = {}
        for header in headers:
            key = header["sender_address"].lower() or "(inconnu)"
            existing = tallies.get(key)
            if existing is None:
                tallies[key] = {
                    "sender_address": key,
                    "sender_name": header["sender_name"],
                    "total": 1,
                    "unread": 0 if header["is_read"] else 1,
                }
                continue
            existing["total"] += 1
            if not header["is_read"]:
                existing["unread"] += 1
        return sorted(tallies.values(), key=lambda tally: tally["total"], reverse=True)

    @staticmethod
    def _instant(value: str | None) -> str | None:
        """Graph n'accepte qu'un instant ISO 8601 ; une date seule est ramenée à minuit UTC."""
        if value is None or value.strip() == "":
            return None
        if parse_instant(value) is None:
            raise MailConnectionError("invalid_date")
        return to_iso(value)

    async def search(self, account: Account, query: str, top: int) -> list[MessageHeader]:
        trimmed = query.strip()[:MAX_SEARCH_CHARS]
        if trimmed == "":
            return []
        params = [
            ("$select", LIST_FIELDS),
            ("$top", str(min(max(top, 1), MAX_PAGE_SIZE))),
            # $search impose son propre classement par pertinence : $orderby serait rejeté.
            ("$search", f'"{trimmed.replace(chr(34), " ")}"'),
        ]
        return await self._read_headers(account, build_url(GRAPH_ORIGIN, "/v1.0/me/messages", params))

    async def get_message(self, account: Account, message_id: str) -> MessageBody:
        url = build_url(
            GRAPH_ORIGIN,
            f"/v1.0/me/messages/{self._safe_id(message_id)}",
            [("$select", f"{LIST_FIELDS},body")],
        )
        payload = await self._request(account, url, "GET")
        header = self._to_header(payload)
        if header is None:
            raise MailConnectionError("graph_response_invalid")
        body = payload.get("body")
        content = body.get("content") if isinstance(body, dict) else None
        return {**header, "body_text": _bounded_string(content, MAX_BODY_CHARS)}

    async def move_to_deleted_items(self, account: Account, message_id: str) -> str:
        """Déplace un message vers la corbeille.

        Graph conserve l'élément dans « Éléments supprimés » : l'opération reste réversible par
        l'utilisateur, et aucune suppression définitive n'est exposée.
        """
        stored = await self._tokens.get(account.id)
        if not grants_write_access(stored.get("scopes") if stored else None):
            raise MailConnectionError("write_consent_required")
        url = build_url(GRAPH_ORIGIN, f"/v1.0/me/messages/{self._safe_id(message_id)}/move")
        payload = await self._request(account, url, "POST", {"destinationId": "deleteditems"})
        moved = payload.get("id")
        return moved if isinstance(moved, str) else message_id

    async def stats(self, account: Account) -> MailboxStats:
        url = build_url(
            GRAPH_ORIGIN,
            "/v1.0/me/mailFolders",
            [("$select", "displayName,totalItemCount,unreadItemCount"), ("$top", "20")],
        )
        payload = await self._request(account, url, "GET")
        values = payload.get("value")
        folders = [
            {
                "name": _bounded_string(folder.get("displayName"), 120),
                "total": _count_of(folder.get("totalItemCount")),
                "unread": _count_of(folder.get("unreadItemCount")),
            }
            for folder in (values if isinstance(values, list) else [])
            if isinstance(folder, dict)
        ]
        return {
            "mailbox": account.mailbox or account.id,
            "total_messages": sum(folder["total"] for folder in folders),
            "unread_messages": sum(folder["unread"] for folder in folders),
            "folders": folders,
        }

    @staticmethod
    def _safe_id(message_id: str) -> str:
        if not MESSAGE_ID.fullmatch(message_id):
            raise MailConnectionError("invalid_message_id")
        return quote(message_id, safe="")

    async def _read_headers(self, account: Account, url: str) -> list[MessageHeader]:
        payload = await self._request(account, url, "GET")
        values = payload.get("value")
        headers = (self._to_header(value) for value in (values if isinstance(values, list) else []))
        return [header for header in headers if header is not None]

    @staticmethod
    def _to_header(value: Any) -> MessageHeader | None:
        if not isinstance(value, dict):
            return None
        identifier = value.get("id")
        received = value.get("receivedDateTime")
        if not isinstance(identifier, str) or len(identifier) == 0 or len(identifier) > 512:
            return None
        if not isinstance(received, str) or parse_instant(received) is None:
            return None
        from_field = value.get("from")
        sender = from_field.get("emailAddress") if isinstance(from_field, dict) else None
        if not isinstance(sender, dict):
            sender = {}
        return {
            "message_id": identifier,
            "subject": _bounded_string(value.get("subject"), MAX_SUBJECT_CHARS),
            "sender_name": _bounded_string(sender.get("name"), MAX_ADDRESS_CHARS),
            "sender_address": _bounded_string(sender.get("address"), MAX_ADDRESS_CHARS),
            "received_at": to_iso(received),
            "is_read": value.get("isRead") is True,
            "has_attachments": value.get("hasAttachments") is True,
            "snippet": _bounded_string(value.get("bodyPreview"), MAX_SNIPPET_CHARS),
        }

    async def _request(self, account: Account, url: str, method: str, body: Any = None) -> dict[str, Any]:
        token = await self._auth.access_token_for(account)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Prefer": 'outlook.body-content-type="text"',
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = await self._fetch(
                url,
                method=method,
                headers=headers,
                body=json.dumps(body, separators=(",", ":")) if body is not None else None,
                timeout=REQUEST_TIMEOUT_S,
            )
        except Exception as error:
            raise MailConnectionError("graph_unreachable") from error
        status = response.status_code
        if status == 401:
            raise MailConnectionError("reauthentication_required")
        if status == 403:
            raise MailConnectionError("write_consent_required")
        if status == 404:
            raise MailConnectionError("message_not_found")
        if status == 429:
            raise MailConnectionError("graph_rate_limited")
        if not response.is_success:
            raise MailConnectionError("graph_request_failed")

        raw = response.text
        if len(raw) > MAX_RESPONSE_CHARS:
            raise MailConnectionError("graph_response_too_large")
        if raw.strip() == "":
            return {}
        try:
            payload = json.loads(raw, parse_constant=_reject_constant)
        except (ValueError, RecursionError) as error:
            raise MailConnectionError("graph_response_invalid") from error
        if not isinstance(payload, dict):
            raise MailConnectionError("graph_response_invalid")
        return payload
