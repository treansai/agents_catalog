"""Mailbox access. The bot never talks to Microsoft: everything goes through ezer-backend."""

import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

MESSAGE_ID = re.compile(r"[A-Za-z0-9_\-=+/]{1,512}")
ACCOUNT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
MAX_TOP = 25
MAX_QUERY_CHARS = 200
TIMEOUT_SECONDS = 30.0


class MailboxUnavailableError(Exception):
    def __init__(self, operation: str, code: str) -> None:
        super().__init__(f"{operation} failed: {code}")
        self.operation = operation
        self.code = code


@dataclass
class MessageHeader:
    message_id: str
    subject: str
    sender_name: str
    sender_address: str
    received_at: str
    is_read: bool
    has_attachments: bool
    snippet: str


@dataclass
class MessageBody:
    header: MessageHeader
    body_text: str


@dataclass
class SenderTally:
    sender_address: str
    sender_name: str
    total: int
    unread: int


@dataclass
class TriagedAnalysis:
    summary: str
    category: str
    priority: str
    needs_human_review: bool
    created_at: str


@dataclass
class MailboxStats:
    mailbox: str
    total_messages: int
    unread_messages: int
    folders: list[tuple[str, int, int]]


class MailboxClient(Protocol):
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
        order: str | None = None,
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


def as_record(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def as_integer(value: Any) -> int | None:
    """Mirror of ``typeof value === "number" && Number.isInteger(value)``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def text(payload: dict[str, Any], field: str, maximum: int) -> str:
    value = payload.get(field)
    return value[:maximum] if isinstance(value, str) else ""


def header_from(payload: Any) -> MessageHeader | None:
    record = as_record(payload)
    if record is None:
        return None
    message_id = record.get("message_id")
    if not isinstance(message_id, str) or MESSAGE_ID.fullmatch(message_id) is None:
        return None
    return MessageHeader(
        message_id=message_id,
        subject=text(record, "subject", 400),
        sender_name=text(record, "sender_name", 320),
        sender_address=text(record, "sender_address", 320),
        received_at=text(record, "received_at", 64),
        is_read=record.get("is_read") is True,
        has_attachments=record.get("has_attachments") is True,
        snippet=text(record, "snippet", 600),
    )


def normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip()
    if normalized == "":
        raise ValueError("backend base URL must not be empty")
    with_slash = normalized if normalized.endswith("/") else f"{normalized}/"
    try:
        parsed = urlsplit(with_slash)
        hostname = parsed.hostname
    except ValueError as error:
        raise ValueError("backend base URL must be an absolute http(s) URL") from error
    if parsed.scheme not in ("http", "https") or not hostname:
        raise ValueError("backend base URL must be an absolute http(s) URL")
    return with_slash


class HttpMailboxClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = normalize_base_url(base_url)
        if api_key.strip() == "":
            raise ValueError("backend API key must not be empty")
        self._api_key = api_key
        self._transport = transport

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
        order: str | None = None,
    ) -> list[MessageHeader]:
        params: dict[str, str] = {
            "top": str(self._top(top)),
            "order": "asc" if order == "asc" else "desc",
        }
        if unread_only is True:
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
            params={"sample": str(self._top(sample))},
        )
        entries = payload.get("senders")
        if not isinstance(entries, list) or len(entries) > MAX_TOP:
            raise MailboxUnavailableError("senders", "invalid_backend_payload")
        tallies: list[SenderTally] = []
        for entry in entries:
            record = as_record(entry)
            if record is None:
                continue
            tallies.append(
                SenderTally(
                    sender_address=text(record, "sender_address", 320),
                    sender_name=text(record, "sender_name", 320),
                    total=as_integer(record.get("total")) or 0,
                    unread=as_integer(record.get("unread")) or 0,
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
        params: dict[str, str] = {
            "account_id": self._account(account_id),
            "limit": str(max(1, min(limit, 100))),
            "offset": "0",
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
        analyses: list[TriagedAnalysis] = []
        for item in items:
            record = as_record(item)
            if record is None:
                continue
            analyses.append(
                TriagedAnalysis(
                    summary=text(record, "summary", 2_000),
                    category=text(record, "category", 64),
                    priority=text(record, "priority", 32),
                    needs_human_review=record.get("needs_human_review") is True,
                    created_at=text(record, "created_at", 64),
                )
            )
        total = as_integer(payload.get("total"))
        return analyses, total if total is not None else len(analyses)

    async def search(self, account_id: str, query: str, top: int) -> list[MessageHeader]:
        trimmed = query.strip()[:MAX_QUERY_CHARS]
        if trimmed == "":
            return []
        payload = await self._request(
            "GET",
            f"v1/accounts/{self._account(account_id)}/messages",
            operation="search",
            params={"top": str(self._top(top)), "query": trimmed},
        )
        return self._headers_from(payload)

    async def get_message(self, account_id: str, message_id: str) -> MessageBody:
        payload = await self._request(
            "GET",
            f"v1/accounts/{self._account(account_id)}/message",
            operation="get_message",
            params={"message_id": self._message(message_id)},
        )
        header = header_from(payload)
        if header is None:
            raise MailboxUnavailableError("get_message", "invalid_backend_payload")
        return MessageBody(header=header, body_text=text(payload, "body_text", 20_000))

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
                record = as_record(folder)
                if record is None:
                    continue
                name = record.get("name")
                total = record.get("total")
                unread = record.get("unread")
                if isinstance(name, str) and is_number(total) and is_number(unread):
                    folders.append((name[:120], total, unread))
        total_messages = as_integer(payload.get("total_messages"))
        unread_messages = as_integer(payload.get("unread_messages"))
        return MailboxStats(
            mailbox=text(payload, "mailbox", 320),
            total_messages=total_messages if total_messages is not None else 0,
            unread_messages=unread_messages if unread_messages is not None else 0,
            folders=folders,
        )

    async def move_to_trash(self, account_id: str, message_id: str) -> None:
        await self._request(
            "POST",
            f"v1/accounts/{self._account(account_id)}/message/trash",
            operation="move_to_trash",
            json_body={"message_id": self._message(message_id)},
        )

    @staticmethod
    def _account(account_id: str) -> str:
        if ACCOUNT_ID.fullmatch(account_id) is None:
            raise MailboxUnavailableError("request", "invalid_account_id")
        return account_id

    @staticmethod
    def _message(message_id: str) -> str:
        if MESSAGE_ID.fullmatch(message_id) is None:
            raise MailboxUnavailableError("request", "invalid_message_id")
        return message_id

    @staticmethod
    def _top(top: int) -> int:
        return max(1, min(int(top), MAX_TOP))

    @staticmethod
    def _headers_from(payload: dict[str, Any]) -> list[MessageHeader]:
        messages = payload.get("messages")
        if not isinstance(messages, list) or len(messages) > MAX_TOP:
            raise MailboxUnavailableError("list", "invalid_backend_payload")
        headers: list[MessageHeader] = []
        for entry in messages:
            header = header_from(entry)
            if header is not None:
                headers.append(header)
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json", "X-API-Key": self._api_key}
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                response = await client.request(
                    method,
                    self._base_url + path,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
        except Exception as error:
            raise MailboxUnavailableError(operation, "backend_unreachable") from error
        if response.status_code == 401:
            raise MailboxUnavailableError(operation, "backend_unauthorized")
        if response.status_code == 404:
            raise MailboxUnavailableError(operation, "account_not_found")
        if response.status_code == 422:
            raise MailboxUnavailableError(operation, "mailbox_not_available")
        if response.status_code >= 400:
            raise MailboxUnavailableError(operation, "backend_request_failed")
        try:
            payload = response.json()
        except ValueError as error:
            raise MailboxUnavailableError(operation, "invalid_backend_payload") from error
        record = as_record(payload)
        if record is None:
            raise MailboxUnavailableError(operation, "invalid_backend_payload")
        return record
