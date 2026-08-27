"""Read-only Gmail connector with race-safe bootstrap and history checkpoints."""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Literal, TypeGuard, cast
from urllib.parse import quote

import httpx

from ezer.config import GmailAccount
from ezer.connectors.auth import RefreshTokenSink, TokenProvider, create_token_provider
from ezer.connectors.base import (
    ConnectorConfigurationError,
    EmailConnector,
    InvalidCursorError,
    ProviderResponseError,
    ResourceNotFoundError,
    decode_cursor,
    encode_cursor,
    html_to_safe_text,
    sanitize_text,
    validate_fetch_limit,
    validate_max_body_chars,
)
from ezer.connectors.http import (
    DEFAULT_RETRY_POLICY,
    AuthorizedHttpClient,
    RetryPolicy,
)
from ezer.domain import AttachmentInfo, EmailEnvelope, FetchBatch, FetchDeadLetter

_GMAIL_API_ROOT = "https://gmail.googleapis.com/gmail/v1"
_MAX_PENDING_IDS = 20_000
_PERMANENT_MESSAGE_RESPONSE_CODES = frozenset(
    {
        "invalid_json",
        "invalid_json_shape",
        "response_too_large",
    }
)
_PERMANENT_MESSAGE_PARSE_CODES = frozenset(
    {
        "invalid_mime_payload",
        "missing_message_id",
        "missing_mime_payload",
    }
)


@dataclass(frozen=True, slots=True)
class _GmailCursorState:
    phase: Literal["bootstrap", "history"]
    checkpoint: str
    page_token: str | None = None
    pending_ids: tuple[str, ...] = ()
    resume_page_token: str | None = None
    terminal_checkpoint: str | None = None

    def to_mapping(self) -> Mapping[str, Any]:
        return {
            "checkpoint": self.checkpoint,
            "page_token": self.page_token,
            "pending_ids": list(self.pending_ids),
            "phase": self.phase,
            "resume_page_token": self.resume_page_token,
            "terminal_checkpoint": self.terminal_checkpoint,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> _GmailCursorState:
        phase = value.get("phase")
        checkpoint = value.get("checkpoint")
        page_token = value.get("page_token")
        pending_raw = value.get("pending_ids", [])
        resume_page_token = value.get("resume_page_token")
        terminal_checkpoint = value.get("terminal_checkpoint")
        if not isinstance(phase, str) or phase not in ("bootstrap", "history"):
            raise ValueError
        if not _valid_history_id(checkpoint):
            raise ValueError
        if not _valid_optional_token(page_token):
            raise ValueError
        if not _valid_optional_token(resume_page_token):
            raise ValueError
        if terminal_checkpoint is not None and not _valid_history_id(terminal_checkpoint):
            raise ValueError
        if not isinstance(pending_raw, list) or len(pending_raw) > _MAX_PENDING_IDS:
            raise ValueError
        if not all(_valid_message_id(item) for item in pending_raw):
            raise ValueError
        pending = tuple(pending_raw)
        if phase == "bootstrap" and (pending or resume_page_token or terminal_checkpoint):
            raise ValueError
        if pending:
            if page_token is not None:
                raise ValueError
            if (resume_page_token is None) == (terminal_checkpoint is None):
                raise ValueError
        elif resume_page_token is not None or terminal_checkpoint is not None:
            raise ValueError
        return cls(
            phase=cast(Literal["bootstrap", "history"], phase),
            checkpoint=checkpoint,
            page_token=page_token,
            pending_ids=pending,
            resume_page_token=resume_page_token,
            terminal_checkpoint=terminal_checkpoint,
        )


class GmailConnector(EmailConnector):
    """Fetch Inbox messages without mutating state or loading attachments."""

    def __init__(
        self,
        account: GmailAccount,
        client: httpx.AsyncClient | None = None,
        *,
        token_provider: TokenProvider | None = None,
        refresh_token_sink: RefreshTokenSink | None = None,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        max_concurrency: int = 4,
        max_body_chars: int = 500_000,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not 1 <= max_concurrency <= 16:
            raise ValueError("max_concurrency must be between 1 and 16")
        if account.query.strip().casefold() != "in:inbox":
            raise ConnectorConfigurationError(
                provider="gmail",
                account_id=account.id,
                operation="connector_config",
                code="unsupported_incremental_query",
            )
        self._account = account
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._max_concurrency = max_concurrency
        self._max_body_chars = validate_max_body_chars(
            max_body_chars,
            provider="gmail",
            account_id=account.id,
        )
        auth = token_provider or create_token_provider(
            provider="gmail",
            account_id=account.id,
            config=account.auth,
            client=self._client,
            refresh_token_sink=refresh_token_sink,
            sleep=sleep,
        )
        self._http = AuthorizedHttpClient(
            provider="gmail",
            account_id=account.id,
            client=self._client,
            token_provider=auth,
            retry_policy=retry_policy,
            sleep=sleep,
        )
        mailbox = quote(account.mailbox, safe="")
        self._user_root = f"{_GMAIL_API_ROOT}/users/{mailbox}"

    @property
    def account_id(self) -> str:
        return self._account.id

    @property
    def provider(self) -> Literal["gmail"]:
        return "gmail"

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        page_size = validate_fetch_limit(limit, provider="gmail", account_id=self.account_id)
        if cursor is None:
            profile = await self._http.get_json(
                f"{self._user_root}/profile",
                operation="gmail_profile",
                params={"fields": "historyId"},
            )
            checkpoint = profile.get("historyId")
            if not _valid_history_id(checkpoint):
                raise self._invalid_response("gmail_profile", "missing_history_id")
            state = _GmailCursorState(phase="bootstrap", checkpoint=checkpoint)
        else:
            raw_state = decode_cursor(
                cursor,
                account_id=self.account_id,
                provider="gmail",
            )
            try:
                state = _GmailCursorState.from_mapping(raw_state)
            except ValueError as exc:
                raise InvalidCursorError(
                    provider="gmail",
                    account_id=self.account_id,
                    operation="decode_cursor",
                    code="invalid_gmail_cursor",
                ) from exc

        if state.phase == "bootstrap":
            return await self._fetch_bootstrap(state, page_size)
        return await self._fetch_history(state, page_size)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _fetch_bootstrap(self, state: _GmailCursorState, limit: int) -> FetchBatch:
        params: dict[str, Any] = {
            "fields": "messages(id),nextPageToken",
            "maxResults": limit,
        }
        if self._account.query:
            params["q"] = self._account.query
        if state.page_token is not None:
            params["pageToken"] = state.page_token
        payload = await self._http.get_json(
            f"{self._user_root}/messages",
            operation="gmail_messages_list",
            params=params,
        )
        try:
            message_ids = _message_ids_from_list(payload.get("messages"))
            next_page = _optional_string(payload.get("nextPageToken"), max_length=8192)
        except ValueError as exc:
            raise self._invalid_response("gmail_messages_list", "invalid_message_list") from exc
        if next_page is not None:
            next_state = _GmailCursorState(
                phase="bootstrap",
                checkpoint=state.checkpoint,
                page_token=next_page,
            )
        else:
            # The profile checkpoint was captured before enumeration. Any concurrent
            # changes are therefore replayed by history.list after bootstrap completes.
            next_state = _GmailCursorState(
                phase="history",
                checkpoint=state.checkpoint,
            )
        messages, dead_letters = await self._fetch_messages(message_ids)
        return FetchBatch(
            messages=messages,
            dead_letters=dead_letters,
            next_cursor=self._encode_state(next_state),
            cursor_reset=False,
        )

    async def _fetch_history(self, state: _GmailCursorState, limit: int) -> FetchBatch:
        if state.pending_ids:
            selected = list(state.pending_ids[:limit])
            remaining = state.pending_ids[limit:]
            if remaining:
                next_state = _GmailCursorState(
                    phase="history",
                    checkpoint=state.checkpoint,
                    pending_ids=remaining,
                    resume_page_token=state.resume_page_token,
                    terminal_checkpoint=state.terminal_checkpoint,
                )
            elif state.resume_page_token is not None:
                next_state = _GmailCursorState(
                    phase="history",
                    checkpoint=state.checkpoint,
                    page_token=state.resume_page_token,
                )
            elif state.terminal_checkpoint is not None:
                next_state = _GmailCursorState(
                    phase="history",
                    checkpoint=state.terminal_checkpoint,
                )
            else:  # guarded by cursor validation
                raise AssertionError("invalid pending Gmail cursor")
            messages, dead_letters = await self._fetch_messages(list(selected))
            return FetchBatch(
                messages=messages,
                dead_letters=dead_letters,
                next_cursor=self._encode_state(next_state),
                cursor_reset=False,
            )

        params: dict[str, Any] = {
            "fields": (
                "history(messagesAdded(message(id)),messagesDeleted(message(id)),"
                "labelsAdded(message(id)),labelsRemoved(message(id))),"
                "nextPageToken,historyId"
            ),
            "historyTypes": [
                "messageAdded",
                "messageDeleted",
                "labelAdded",
                "labelRemoved",
            ],
            "labelId": "INBOX",
            "maxResults": limit,
            "startHistoryId": state.checkpoint,
        }
        if state.page_token is not None:
            params["pageToken"] = state.page_token
        try:
            payload = await self._http.get_json(
                f"{self._user_root}/history",
                operation="gmail_history_list",
                params=params,
            )
        except ResourceNotFoundError:
            return FetchBatch(messages=[], next_cursor=None, cursor_reset=True)

        terminal = payload.get("historyId")
        if not _valid_history_id(terminal):
            raise self._invalid_response("gmail_history_list", "missing_history_id")
        try:
            changed_ids = _changed_message_ids(payload.get("history"))
            next_page = _optional_string(payload.get("nextPageToken"), max_length=8192)
        except ValueError as exc:
            raise self._invalid_response("gmail_history_list", "invalid_history_payload") from exc
        selected = changed_ids[:limit]
        remaining = tuple(changed_ids[limit:])
        if remaining:
            next_state = _GmailCursorState(
                phase="history",
                checkpoint=state.checkpoint,
                pending_ids=remaining,
                resume_page_token=next_page,
                terminal_checkpoint=None if next_page is not None else terminal,
            )
        elif next_page is not None:
            next_state = _GmailCursorState(
                phase="history",
                checkpoint=state.checkpoint,
                page_token=next_page,
            )
        else:
            next_state = _GmailCursorState(phase="history", checkpoint=terminal)
        messages, dead_letters = await self._fetch_messages(selected)
        return FetchBatch(
            messages=messages,
            dead_letters=dead_letters,
            next_cursor=self._encode_state(next_state),
            cursor_reset=False,
        )

    async def _fetch_messages(
        self,
        message_ids: list[str],
    ) -> tuple[list[EmailEnvelope], list[FetchDeadLetter]]:
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def fetch_one(
            message_id: str,
        ) -> EmailEnvelope | FetchDeadLetter | None:
            async with semaphore:
                try:
                    payload = await self._http.get_json(
                        f"{self._user_root}/messages/{quote(message_id, safe='')}",
                        operation="gmail_message_get",
                        params={"format": "full"},
                    )
                except ResourceNotFoundError:
                    return None
                except ProviderResponseError as exc:
                    if _is_permanent_message_response_error(exc):
                        return self._dead_letter(message_id, exc.code)
                    raise
                try:
                    return self._parse_message(payload)
                except ProviderResponseError as exc:
                    if exc.code in _PERMANENT_MESSAGE_PARSE_CODES:
                        return self._dead_letter(message_id, exc.code)
                    raise
                except ValueError:
                    return self._dead_letter(message_id, "invalid_message_model")

        results = await asyncio.gather(*(fetch_one(message_id) for message_id in message_ids))
        messages = [result for result in results if isinstance(result, EmailEnvelope)]
        dead_letters = [result for result in results if isinstance(result, FetchDeadLetter)]
        return messages, dead_letters

    def _dead_letter(self, message_id: str, code: str) -> FetchDeadLetter:
        return FetchDeadLetter(
            account_id=self.account_id,
            provider="gmail",
            provider_message_id=message_id,
            code=code,
        )

    def _parse_message(self, payload: Mapping[str, Any]) -> EmailEnvelope:
        message_id = payload.get("id")
        if not _valid_message_id(message_id):
            raise self._invalid_response("gmail_message_get", "missing_message_id")
        raw_part = payload.get("payload")
        if not isinstance(raw_part, dict):
            raise self._invalid_response("gmail_message_get", "missing_mime_payload")
        headers = _headers(raw_part.get("headers"))
        try:
            plain_parts, html_parts, attachments = _extract_mime(
                raw_part,
                max_body_chars=self._max_body_chars,
            )
        except (ValueError, LookupError, UnicodeError, binascii.Error) as exc:
            raise self._invalid_response("gmail_message_get", "invalid_mime_payload") from exc

        plain_body = sanitize_text(
            "\n\n".join(plain_parts),
            max_chars=self._max_body_chars,
        )
        if plain_body:
            body = plain_body
        else:
            body = html_to_safe_text(
                "\n\n".join(html_parts),
                max_chars=self._max_body_chars,
            )

        sender_name, sender_address = _first_address(headers.get("from", []))
        recipients = _addresses(headers.get("to", []))
        cc = _addresses(headers.get("cc", []))
        labels = _bounded_strings(payload.get("labelIds"), count=200, length=255)
        thread_id = payload.get("threadId")
        if not isinstance(thread_id, str) or not thread_id:
            thread_id = None
        else:
            thread_id = thread_id[:1024]
        return EmailEnvelope(
            account_id=self.account_id,
            provider="gmail",
            provider_message_id=message_id,
            thread_id=thread_id,
            subject=_decode_header_value(_first_header(headers, "subject"))[:998],
            sender_name=sender_name[:320],
            sender_address=sender_address[:320],
            recipients=recipients[:200],
            cc=cc[:200],
            received_at=_gmail_received_at(payload.get("internalDate"), headers),
            body_text=body,
            snippet=_optional_text(payload.get("snippet"), max_chars=2_000),
            attachments=attachments[:100],
            labels=labels,
            importance=_gmail_importance(headers),
            is_read="UNREAD" not in labels,
            web_url=None,
        )

    def _encode_state(self, state: _GmailCursorState) -> str:
        return encode_cursor(
            account_id=self.account_id,
            provider="gmail",
            state=state.to_mapping(),
        )

    def _invalid_response(self, operation: str, code: str) -> ProviderResponseError:
        return ProviderResponseError(
            provider="gmail",
            account_id=self.account_id,
            operation=operation,
            code=code,
        )


def _valid_history_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value.isdecimal() and 1 <= len(value) <= 64


def _is_permanent_message_response_error(error: ProviderResponseError) -> bool:
    status = error.status_code
    return (
        error.code in _PERMANENT_MESSAGE_RESPONSE_CODES
        and status is not None
        and 200 <= status < 300
    )


def _valid_message_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and 1 <= len(value) <= 1024


def _valid_optional_token(value: object) -> bool:
    return value is None or (isinstance(value, str) and 1 <= len(value) <= 8192)


def _optional_string(value: object, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= max_length:
        raise ValueError("invalid provider string")
    return value


def _message_ids_from_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("invalid Gmail message list")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or not _valid_message_id(item.get("id")):
            raise ValueError("invalid Gmail message reference")
        message_id = item["id"]
        assert isinstance(message_id, str)
        if message_id not in seen:
            seen.add(message_id)
            result.append(message_id)
        if len(result) > _MAX_PENDING_IDS:
            raise ValueError("too many Gmail message references")
    return result


def _changed_message_ids(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("invalid Gmail history")
    candidates: list[str] = []
    deleted: set[str] = set()
    for record in value:
        if not isinstance(record, dict):
            raise ValueError("invalid Gmail history record")
        for field in ("messagesAdded", "labelsAdded", "labelsRemoved"):
            entries = record.get(field, [])
            if not isinstance(entries, list):
                raise ValueError("invalid Gmail history entries")
            for entry in entries:
                message_id = _history_entry_id(entry)
                if message_id is not None:
                    candidates.append(message_id)
        entries = record.get("messagesDeleted", [])
        if not isinstance(entries, list):
            raise ValueError("invalid Gmail deleted entries")
        for entry in entries:
            message_id = _history_entry_id(entry)
            if message_id is not None:
                deleted.add(message_id)
    result: list[str] = []
    seen: set[str] = set()
    for message_id in candidates:
        if message_id not in deleted and message_id not in seen:
            seen.add(message_id)
            result.append(message_id)
        if len(result) > _MAX_PENDING_IDS:
            raise ValueError("too many Gmail history references")
    return result


def _history_entry_id(value: object) -> str | None:
    if not isinstance(value, dict):
        raise ValueError("invalid Gmail history entry")
    message = value.get("message")
    if not isinstance(message, dict):
        return None
    message_id = message.get("id")
    if not _valid_message_id(message_id):
        return None
    assert isinstance(message_id, str)
    return message_id


def _headers(value: object) -> dict[str, list[str]]:
    if not isinstance(value, list):
        return {}
    result: dict[str, list[str]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        header_value = item.get("value")
        if isinstance(name, str) and isinstance(header_value, str):
            result.setdefault(name.casefold(), []).append(header_value)
    return result


def _first_header(headers: Mapping[str, list[str]], name: str) -> str:
    values = headers.get(name.casefold(), [])
    return values[0] if values else ""


def _decode_header_value(value: str) -> str:
    if not value:
        return ""
    try:
        return sanitize_text(str(make_header(decode_header(value))), max_chars=4096)
    except LookupError, UnicodeError, ValueError:
        return sanitize_text(value, max_chars=4096)


def _addresses(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for _name, address in getaddresses(values):
        normalized = sanitize_text(address, max_chars=320)
        if normalized and normalized.casefold() not in seen:
            seen.add(normalized.casefold())
            result.append(normalized)
    return result


def _first_address(values: list[str]) -> tuple[str, str]:
    addresses = getaddresses(values)
    if not addresses:
        return "", ""
    name, address = addresses[0]
    return (
        _decode_header_value(name)[:320],
        sanitize_text(address, max_chars=320),
    )


def _extract_mime(
    root: Mapping[str, Any],
    *,
    max_body_chars: int,
) -> tuple[list[str], list[str], list[AttachmentInfo]]:
    plain: list[str] = []
    html: list[str] = []
    attachments: list[AttachmentInfo] = []

    part_count = 0
    remaining_plain_chars = max_body_chars
    remaining_html_chars = max_body_chars

    def visit(part: Mapping[str, Any], depth: int = 0) -> None:
        nonlocal part_count, remaining_html_chars, remaining_plain_chars
        part_count += 1
        if depth > 100 or part_count > 10_000:
            raise ValueError("MIME tree exceeds safe bounds")
        mime_type_raw = part.get("mimeType")
        mime_type = (
            mime_type_raw[:255]
            if isinstance(mime_type_raw, str) and mime_type_raw
            else "application/octet-stream"
        )
        filename_raw = part.get("filename", "")
        filename = _decode_header_value(filename_raw if isinstance(filename_raw, str) else "")[:512]
        part_headers = _headers(part.get("headers"))
        disposition = _first_header(part_headers, "content-disposition").casefold()
        content_id = _first_header(part_headers, "content-id")
        inline = disposition.startswith("inline") or bool(content_id)
        body = part.get("body")
        if not isinstance(body, dict):
            body = {}
        attachment_id = body.get("attachmentId")
        textual_body = mime_type.casefold() in {"text/plain", "text/html"}
        is_attachment = (
            bool(filename)
            or disposition.startswith("attachment")
            or (disposition.startswith("inline") and not textual_body)
            or (bool(content_id) and not textual_body)
        )
        if isinstance(attachment_id, str) and attachment_id:
            is_attachment = True
        if is_attachment and len(attachments) < 100:
            size = body.get("size")
            attachments.append(
                AttachmentInfo(
                    filename=filename,
                    content_type=mime_type or "application/octet-stream",
                    size=(
                        size
                        if isinstance(size, int) and not isinstance(size, bool) and size >= 0
                        else None
                    ),
                    inline=inline,
                )
            )
        if is_attachment:
            return

        data = body.get("data")
        normalized_mime_type = mime_type.casefold()
        if normalized_mime_type == "text/plain":
            remaining_chars = remaining_plain_chars
        elif normalized_mime_type == "text/html":
            remaining_chars = remaining_html_chars
        else:
            remaining_chars = 0
        if not is_attachment and remaining_chars > 0 and isinstance(data, str) and data:
            text = _decode_body_data(
                data,
                _first_header(part_headers, "content-type"),
                max_chars=remaining_chars,
            )
            if normalized_mime_type == "text/plain":
                remaining_plain_chars -= len(text)
                plain.append(text)
            else:
                remaining_html_chars -= len(text)
                html.append(text)

        children = part.get("parts", [])
        if children is None:
            return
        if not isinstance(children, list):
            raise ValueError("invalid MIME children")
        for child in children:
            if not isinstance(child, dict):
                raise ValueError("invalid MIME child")
            visit(child, depth + 1)

    visit(root)
    return plain, html, attachments


def _decode_body_data(value: str, content_type: str, *, max_chars: int) -> str:
    max_decoded_bytes = max_chars * 4
    max_encoded_chars = ((max_decoded_bytes + 2) // 3) * 4
    bounded = value[:max_encoded_chars]
    padding = "=" * (-len(bounded) % 4)
    decoded = base64.b64decode(f"{bounded}{padding}", altchars=b"-_", validate=True)
    charset = "utf-8"
    if content_type:
        message = Message()
        message["Content-Type"] = content_type
        charset = message.get_content_charset("utf-8") or "utf-8"
    try:
        return decoded.decode(charset, errors="replace")[:max_chars]
    except LookupError:
        return decoded.decode("utf-8", errors="replace")[:max_chars]


def _gmail_received_at(value: object, headers: Mapping[str, list[str]]) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromtimestamp(int(value) / 1000.0, tz=UTC)
        except ValueError, OverflowError, OSError:
            pass
    date_header = _first_header(headers, "date")
    if date_header:
        try:
            parsed = parsedate_to_datetime(date_header)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except TypeError, ValueError, OverflowError:
            pass
    return datetime.fromtimestamp(0, tz=UTC)


def _gmail_importance(headers: Mapping[str, list[str]]) -> Literal["low", "normal", "high"]:
    importance = _first_header(headers, "importance").casefold()
    priority = _first_header(headers, "x-priority").strip()
    if importance == "high" or priority.startswith(("1", "2")):
        return "high"
    if importance == "low" or priority.startswith(("4", "5")):
        return "low"
    return "normal"


def _bounded_strings(value: object, *, count: int, length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:count]:
        if isinstance(item, str) and item:
            sanitized = sanitize_text(item, max_chars=length)
            if sanitized:
                result.append(sanitized)
    return result


def _optional_text(value: object, *, max_chars: int) -> str:
    return sanitize_text(value, max_chars=max_chars) if isinstance(value, str) else ""


__all__ = ["GmailConnector"]
