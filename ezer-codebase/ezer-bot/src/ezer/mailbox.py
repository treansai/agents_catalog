"""Read and trash mailbox messages through the Ezer backend.

The agents never hold a Microsoft credential and never talk to Graph. Authentication and mailbox
access live in ezer-backend; this client is the only door the assistant has to a mailbox.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Protocol
from urllib.parse import urljoin

import httpx

_MESSAGE_ID: Final = re.compile(r"^[A-Za-z0-9_\-=+/]{1,512}$")
_ACCOUNT_ID: Final = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_MAX_TOP: Final = 25
_MAX_QUERY_CHARS: Final = 200
_TIMEOUT: Final = httpx.Timeout(30.0, connect=10.0)


class MailboxUnavailableError(RuntimeError):
    """A safe, content-free failure raised when the backend cannot serve a mailbox request."""

    def __init__(self, operation: str, code: str) -> None:
        self.operation = operation
        self.code = code
        super().__init__(f"{operation} failed: {code}")


@dataclass(frozen=True, slots=True)
class MessageHeader:
    message_id: str
    subject: str
    sender_name: str
    sender_address: str
    received_at: str
    is_read: bool
    has_attachments: bool
    snippet: str


@dataclass(frozen=True, slots=True)
class MessageBody:
    header: MessageHeader
    body_text: str


@dataclass(frozen=True, slots=True)
class SenderTally:
    sender_address: str
    sender_name: str
    total: int
    unread: int


@dataclass(frozen=True, slots=True)
class TriagedAnalysis:
    """Résultat du pipeline d'analyse : le triage déjà calculé, réutilisé par l'assistant."""

    summary: str
    category: str
    priority: str
    needs_human_review: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class MailboxStats:
    mailbox: str
    total_messages: int
    unread_messages: int
    folders: tuple[tuple[str, int, int], ...]


class MailboxClient(Protocol):
    """Injectable surface so the assistant can be tested without a backend."""

    async def list_recent(self, account_id: str, top: int) -> list[MessageHeader]: ...

    async def list_messages(
        self,
        account_id: str,
        *,
        top: int,
        unread_only: bool = False,
        from_address: str | None = None,
        since: str | None = None,
        until: str | None = None,
        order: str = "desc",
    ) -> list[MessageHeader]: ...

    async def senders(self, account_id: str, sample: int) -> list[SenderTally]: ...

    async def analyses(
        self,
        account_id: str,
        *,
        limit: int,
        category: str | None = None,
        priority: str | None = None,
        needs_human_review: bool | None = None,
    ) -> tuple[list[TriagedAnalysis], int]: ...

    async def search(self, account_id: str, query: str, top: int) -> list[MessageHeader]: ...

    async def get_message(self, account_id: str, message_id: str) -> MessageBody: ...

    async def stats(self, account_id: str) -> MailboxStats: ...

    async def move_to_trash(self, account_id: str, message_id: str) -> None: ...


def _text(payload: dict[str, Any], field: str, maximum: int) -> str:
    value = payload.get(field)
    return value[:maximum] if isinstance(value, str) else ""


def _header_from(payload: object) -> MessageHeader | None:
    if not isinstance(payload, dict):
        return None
    message_id = payload.get("message_id")
    if not isinstance(message_id, str) or _MESSAGE_ID.fullmatch(message_id) is None:
        return None
    return MessageHeader(
        message_id=message_id,
        subject=_text(payload, "subject", 400),
        sender_name=_text(payload, "sender_name", 320),
        sender_address=_text(payload, "sender_address", 320),
        received_at=_text(payload, "received_at", 64),
        is_read=payload.get("is_read") is True,
        has_attachments=payload.get("has_attachments") is True,
        snippet=_text(payload, "snippet", 600),
    )


class HttpMailboxClient:
    """`MailboxClient` backed by the Ezer backend's account endpoints."""

    def __init__(
        self, base_url: str, api_key: str, client: httpx.AsyncClient | None = None
    ) -> None:
        normalized = base_url.strip()
        if not normalized:
            raise ValueError("backend base URL must not be empty")
        if not normalized.endswith("/"):
            normalized = f"{normalized}/"
        parsed = httpx.URL(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("backend base URL must be an absolute http(s) URL")
        if not api_key.strip():
            raise ValueError("backend API key must not be empty")
        self._base_url = normalized
        self._api_key = api_key
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None

    async def list_recent(self, account_id: str, top: int) -> list[MessageHeader]:
        return await self.list_messages(account_id, top=top)

    async def list_messages(
        self,
        account_id: str,
        *,
        top: int,
        unread_only: bool = False,
        from_address: str | None = None,
        since: str | None = None,
        until: str | None = None,
        order: str = "desc",
    ) -> list[MessageHeader]:
        params: dict[str, Any] = {
            "top": self._top(top),
            "order": "asc" if order == "asc" else "desc",
        }
        if unread_only:
            params["unread_only"] = "true"
        if from_address:
            params["from_address"] = from_address.strip()[:320]
        if since:
            params["since"] = since.strip()[:64]
        if until:
            params["until"] = until.strip()[:64]
        payload = await self._request(
            "GET",
            f"v1/accounts/{self._account(account_id)}/messages",
            operation="list_messages",
            params=params,
        )
        return self._headers_from(payload)

    async def senders(self, account_id: str, sample: int) -> list[SenderTally]:
        payload = await self._request(
            "GET",
            f"v1/accounts/{self._account(account_id)}/senders",
            operation="senders",
            params={"sample": self._top(sample)},
        )
        entries = payload.get("senders")
        if not isinstance(entries, list) or len(entries) > _MAX_TOP:
            raise MailboxUnavailableError("senders", "invalid_backend_payload")
        tallies: list[SenderTally] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            total = entry.get("total")
            unread = entry.get("unread")
            tallies.append(
                SenderTally(
                    sender_address=_text(entry, "sender_address", 320),
                    sender_name=_text(entry, "sender_name", 320),
                    total=total if isinstance(total, int) else 0,
                    unread=unread if isinstance(unread, int) else 0,
                )
            )
        return tallies

    async def analyses(
        self,
        account_id: str,
        *,
        limit: int,
        category: str | None = None,
        priority: str | None = None,
        needs_human_review: bool | None = None,
    ) -> tuple[list[TriagedAnalysis], int]:
        params: dict[str, Any] = {
            "account_id": self._account(account_id),
            "limit": max(1, min(limit, 100)),
            "offset": 0,
        }
        if category:
            params["category"] = category
        if priority:
            params["priority"] = priority
        if needs_human_review is not None:
            params["needs_human_review"] = "true" if needs_human_review else "false"
        payload = await self._request("GET", "v1/analyses", operation="analyses", params=params)
        items = payload.get("items")
        if not isinstance(items, list) or len(items) > 100:
            raise MailboxUnavailableError("analyses", "invalid_backend_payload")
        total = payload.get("total")
        analyses = [
            TriagedAnalysis(
                summary=_text(item, "summary", 2_000),
                category=_text(item, "category", 64),
                priority=_text(item, "priority", 32),
                needs_human_review=item.get("needs_human_review") is True,
                created_at=_text(item, "created_at", 64),
            )
            for item in items
            if isinstance(item, dict)
        ]
        return analyses, total if isinstance(total, int) else len(analyses)

    async def search(self, account_id: str, query: str, top: int) -> list[MessageHeader]:
        trimmed = query.strip()[:_MAX_QUERY_CHARS]
        if not trimmed:
            return []
        payload = await self._request(
            "GET",
            f"v1/accounts/{self._account(account_id)}/messages",
            operation="search",
            params={"top": self._top(top), "query": trimmed},
        )
        return self._headers_from(payload)

    async def get_message(self, account_id: str, message_id: str) -> MessageBody:
        payload = await self._request(
            "GET",
            f"v1/accounts/{self._account(account_id)}/message",
            operation="get_message",
            params={"message_id": self._message(message_id)},
        )
        header = _header_from(payload)
        if header is None:
            raise MailboxUnavailableError("get_message", "invalid_backend_payload")
        return MessageBody(header=header, body_text=_text(payload, "body_text", 20_000))

    async def stats(self, account_id: str) -> MailboxStats:
        payload = await self._request(
            "GET",
            f"v1/accounts/{self._account(account_id)}/mailbox-stats",
            operation="stats",
        )
        folders_payload = payload.get("folders")
        folders: list[tuple[str, int, int]] = []
        if isinstance(folders_payload, list):
            for folder in folders_payload[:20]:
                if not isinstance(folder, dict):
                    continue
                name = folder.get("name")
                total = folder.get("total")
                unread = folder.get("unread")
                if isinstance(name, str) and isinstance(total, int) and isinstance(unread, int):
                    folders.append((name[:120], total, unread))
        total_messages = payload.get("total_messages")
        unread_messages = payload.get("unread_messages")
        return MailboxStats(
            mailbox=_text(payload, "mailbox", 320),
            total_messages=total_messages if isinstance(total_messages, int) else 0,
            unread_messages=unread_messages if isinstance(unread_messages, int) else 0,
            folders=tuple(folders),
        )

    async def move_to_trash(self, account_id: str, message_id: str) -> None:
        await self._request(
            "POST",
            f"v1/accounts/{self._account(account_id)}/message/trash",
            operation="move_to_trash",
            json={"message_id": self._message(message_id)},
        )

    @staticmethod
    def _account(account_id: str) -> str:
        if _ACCOUNT_ID.fullmatch(account_id) is None:
            raise MailboxUnavailableError("request", "invalid_account_id")
        return account_id

    @staticmethod
    def _message(message_id: str) -> str:
        if _MESSAGE_ID.fullmatch(message_id) is None:
            raise MailboxUnavailableError("request", "invalid_message_id")
        return message_id

    @staticmethod
    def _top(top: int) -> int:
        return max(1, min(int(top), _MAX_TOP))

    @staticmethod
    def _headers_from(payload: dict[str, Any]) -> list[MessageHeader]:
        messages = payload.get("messages")
        if not isinstance(messages, list) or len(messages) > _MAX_TOP:
            raise MailboxUnavailableError("list", "invalid_backend_payload")
        return [header for header in map(_header_from, messages) if header is not None]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._client
        if client is None:
            if self._owned_client is None:
                self._owned_client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
            client = self._owned_client
        try:
            response = await client.request(
                method,
                urljoin(self._base_url, path),
                params=params,
                json=json,
                headers={"Accept": "application/json", "X-API-Key": self._api_key},
            )
        except httpx.HTTPError:
            raise MailboxUnavailableError(operation, "backend_unreachable") from None

        if response.status_code == 401:
            raise MailboxUnavailableError(operation, "backend_unauthorized")
        if response.status_code == 404:
            raise MailboxUnavailableError(operation, "account_not_found")
        if response.status_code == 422:
            # Le backend refuse : boîte déconnectée, ou consentement d'écriture absent.
            raise MailboxUnavailableError(operation, "mailbox_not_available")
        if response.status_code >= 400:
            raise MailboxUnavailableError(operation, "backend_request_failed")

        try:
            payload = response.json()
        except ValueError:
            raise MailboxUnavailableError(operation, "invalid_backend_payload") from None
        if not isinstance(payload, dict):
            raise MailboxUnavailableError(operation, "invalid_backend_payload")
        return payload


__all__ = [
    "HttpMailboxClient",
    "MailboxClient",
    "MailboxStats",
    "MailboxUnavailableError",
    "MessageBody",
    "MessageHeader",
    "SenderTally",
    "TriagedAnalysis",
]
