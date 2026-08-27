"""Read-only Microsoft Graph connector using per-folder delta checkpoints."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeGuard, cast
from urllib.parse import quote, urlsplit

import httpx

from ezer.config import (
    ClientCredentialsAuth,
    MsalDeviceCodeAuth,
    OutlookAccount,
    RefreshTokenAuth,
    is_personal_microsoft_mailbox,
)
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
    validate_fixed_https_url,
)
from ezer.domain import AttachmentInfo, EmailEnvelope, FetchBatch, FetchDeadLetter

_GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_GRAPH_HOST = "graph.microsoft.com"
_GRAPH_PATH_PREFIX = "/v1.0/"
# Graph addresses a keyed entity as ``mailFolders('inbox')`` where this connector builds
# ``mailFolders/inbox``. Names are plain camelCase; anything else fails closed.
_GRAPH_KEY_SEGMENT = re.compile(r"^([A-Za-z][A-Za-z0-9]*)\('(.*)'\)$")
_GENERIC_MICROSOFT_AUTHORITIES = frozenset({"common", "consumers", "organizations"})
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
        "invalid_message_payload",
        "missing_message_id",
    }
)
_DELTA_RESET_CODES = frozenset(
    {
        "resyncchangesapplydifferences",
        "resyncchangesuploaddifferences",
        "resyncrequired",
        "syncstatenotfound",
    }
)
_MESSAGE_SELECT = ",".join(
    (
        "id",
        "conversationId",
        "subject",
        "from",
        "toRecipients",
        "ccRecipients",
        "receivedDateTime",
        "body",
        "bodyPreview",
        "importance",
        "isRead",
        "webLink",
        "categories",
        "hasAttachments",
    )
)
_ATTACHMENT_EXPAND = "attachments($select=name,contentType,size,isInline)"


@dataclass(frozen=True, slots=True)
class _OutlookCursorState:
    url: str
    pending_ids: tuple[str, ...] = ()

    def to_mapping(self) -> Mapping[str, Any]:
        return {"pending_ids": list(self.pending_ids), "url": self.url}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> _OutlookCursorState:
        url = value.get("url")
        pending_raw = value.get("pending_ids", [])
        if not isinstance(url, str):
            raise ValueError
        validate_fixed_https_url(
            url,
            expected_host=_GRAPH_HOST,
            path_prefix=_GRAPH_PATH_PREFIX,
        )
        if not isinstance(pending_raw, list) or len(pending_raw) > _MAX_PENDING_IDS:
            raise ValueError
        if not all(_valid_message_id(item) for item in pending_raw):
            raise ValueError
        return cls(url=url, pending_ids=tuple(pending_raw))


class OutlookConnector(EmailConnector):
    """Fetch an Inbox delta without mutating mail or downloading attachment data."""

    def __init__(
        self,
        account: OutlookAccount,
        client: httpx.AsyncClient | None = None,
        *,
        token_provider: TokenProvider | None = None,
        refresh_token_sink: RefreshTokenSink | None = None,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        max_concurrency: int = 4,
        max_body_chars: int = 500_000,
        msal_cache_dir: Path = Path("./var/msal-cache"),
        msal_cache_encryption_key: bytes | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        validate_outlook_account_configuration(account)
        if not 1 <= max_concurrency <= 16:
            raise ValueError("max_concurrency must be between 1 and 16")
        self._account = account
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._max_concurrency = max_concurrency
        self._max_body_chars = validate_max_body_chars(
            max_body_chars,
            provider="outlook",
            account_id=account.id,
        )
        auth = token_provider or create_token_provider(
            provider="outlook",
            account_id=account.id,
            config=account.auth,
            client=self._client,
            mailbox=account.mailbox,
            msal_cache_path=msal_cache_dir / f"{account.id}.bin",
            msal_cache_encryption_key=msal_cache_encryption_key,
            refresh_token_sink=refresh_token_sink,
            sleep=sleep,
        )
        self._http = AuthorizedHttpClient(
            provider="outlook",
            account_id=account.id,
            client=self._client,
            token_provider=auth,
            retry_policy=retry_policy,
            sleep=sleep,
        )
        folder = quote(account.folder, safe="")
        if isinstance(account.auth, MsalDeviceCodeAuth):
            self._messages_root = f"{_GRAPH_ROOT}/me/messages"
            self._delta_url = f"{_GRAPH_ROOT}/me/mailFolders/{folder}/messages/delta"
        else:
            mailbox = quote(account.mailbox, safe="")
            self._messages_root = f"{_GRAPH_ROOT}/users/{mailbox}/messages"
            self._delta_url = f"{_GRAPH_ROOT}/users/{mailbox}/mailFolders/{folder}/messages/delta"
        self._delta_path = _canonical_graph_path(urlsplit(self._delta_url).path)

    @property
    def account_id(self) -> str:
        return self._account.id

    @property
    def provider(self) -> Literal["outlook"]:
        return "outlook"

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        page_size = validate_fetch_limit(
            limit,
            provider="outlook",
            account_id=self.account_id,
        )
        if cursor is None:
            return await self._fetch_delta_page(
                url=self._delta_url,
                page_size=page_size,
                initial=True,
            )
        raw_state = decode_cursor(
            cursor,
            account_id=self.account_id,
            provider="outlook",
        )
        try:
            state = _OutlookCursorState.from_mapping(raw_state)
            self._validate_delta_url(state.url)
        except ValueError as exc:
            raise InvalidCursorError(
                provider="outlook",
                account_id=self.account_id,
                operation="decode_cursor",
                code="invalid_outlook_cursor",
            ) from exc
        if state.pending_ids:
            selected = state.pending_ids[:page_size]
            remaining = state.pending_ids[page_size:]
            next_state = _OutlookCursorState(url=state.url, pending_ids=remaining)
            messages, dead_letters = await self._fetch_messages(list(selected))
            return FetchBatch(
                messages=messages,
                dead_letters=dead_letters,
                next_cursor=self._encode_state(next_state),
                cursor_reset=False,
            )
        return await self._fetch_delta_page(
            url=state.url,
            page_size=page_size,
            initial=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _fetch_delta_page(
        self,
        *,
        url: str,
        page_size: int,
        initial: bool,
    ) -> FetchBatch:
        try:
            payload = await self._http.get_json(
                url,
                operation="outlook_messages_delta",
                params={"$select": "id"} if initial else None,
                headers={"Prefer": (f'IdType="ImmutableId", odata.maxpagesize={page_size}')},
            )
        except ResourceNotFoundError as exc:
            if exc.code.casefold() in _DELTA_RESET_CODES:
                return FetchBatch(messages=[], next_cursor=None, cursor_reset=True)
            raise
        except ProviderResponseError as exc:
            if exc.status_code == 410 or exc.code.casefold() in _DELTA_RESET_CODES:
                return FetchBatch(messages=[], next_cursor=None, cursor_reset=True)
            raise

        try:
            changed_ids = _message_ids_from_delta(payload.get("value"))
            continuation = _continuation_url(payload)
            validate_fixed_https_url(
                continuation,
                expected_host=_GRAPH_HOST,
                path_prefix=_GRAPH_PATH_PREFIX,
            )
            self._validate_delta_url(continuation)
        except ValueError as exc:
            raise self._invalid_response("outlook_messages_delta", "invalid_delta_payload") from exc

        selected = changed_ids[:page_size]
        remaining = tuple(changed_ids[page_size:])
        next_state = _OutlookCursorState(url=continuation, pending_ids=remaining)
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
                        f"{self._messages_root}/{quote(message_id, safe='')}",
                        operation="outlook_message_get",
                        params={
                            "$expand": _ATTACHMENT_EXPAND,
                            "$select": _MESSAGE_SELECT,
                        },
                        headers={
                            "Prefer": ('IdType="ImmutableId", outlook.body-content-type="text"')
                        },
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
            provider="outlook",
            provider_message_id=message_id,
            code=code,
        )

    def _parse_message(self, payload: Mapping[str, Any]) -> EmailEnvelope:
        message_id = payload.get("id")
        if not _valid_message_id(message_id):
            raise self._invalid_response("outlook_message_get", "missing_message_id")
        try:
            sender_name, sender_address = _email_address(payload.get("from"))
            body = _body_text(
                payload.get("body"),
                max_chars=self._max_body_chars,
            )
            received_at = _received_at(payload.get("receivedDateTime"))
            attachments = _attachments(payload.get("attachments"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise self._invalid_response("outlook_message_get", "invalid_message_payload") from exc

        thread_id = payload.get("conversationId")
        if not isinstance(thread_id, str) or not thread_id:
            thread_id = None
        else:
            thread_id = thread_id[:1024]
        importance_raw = payload.get("importance")
        importance: Literal["low", "normal", "high"] = (
            cast(Literal["low", "normal", "high"], importance_raw)
            if isinstance(importance_raw, str) and importance_raw in ("low", "normal", "high")
            else "normal"
        )
        is_read = payload.get("isRead")
        if not isinstance(is_read, bool):
            is_read = None
        assert isinstance(message_id, str)
        return EmailEnvelope(
            account_id=self.account_id,
            provider="outlook",
            provider_message_id=message_id,
            thread_id=thread_id,
            subject=_optional_text(payload.get("subject"), max_chars=998),
            sender_name=sender_name,
            sender_address=sender_address,
            recipients=_recipient_addresses(payload.get("toRecipients")),
            cc=_recipient_addresses(payload.get("ccRecipients")),
            received_at=received_at,
            body_text=body,
            snippet=_optional_text(payload.get("bodyPreview"), max_chars=2_000),
            attachments=attachments,
            labels=_bounded_strings(payload.get("categories"), count=200, length=255),
            importance=importance,
            is_read=is_read,
            web_url=_safe_web_url(payload.get("webLink")),
        )

    def _encode_state(self, state: _OutlookCursorState) -> str:
        return encode_cursor(
            account_id=self.account_id,
            provider="outlook",
            state=state.to_mapping(),
        )

    def _validate_delta_url(self, url: str) -> None:
        """Bind provider continuations to this connector's mailbox and folder."""

        validate_fixed_https_url(
            url,
            expected_host=_GRAPH_HOST,
            path_prefix=_GRAPH_PATH_PREFIX,
        )
        if _canonical_graph_path(urlsplit(url).path) != self._delta_path:
            raise ValueError("continuation URL does not match the configured mailbox folder")

    def _invalid_response(self, operation: str, code: str) -> ProviderResponseError:
        return ProviderResponseError(
            provider="outlook",
            account_id=self.account_id,
            operation=operation,
            code=code,
        )


def validate_outlook_account_configuration(account: OutlookAccount) -> None:
    """Reject OAuth flows that Microsoft cannot use for the configured mailbox."""

    if isinstance(account.auth, ClientCredentialsAuth):
        if is_personal_microsoft_mailbox(account.mailbox):
            raise ConnectorConfigurationError(
                provider="outlook",
                account_id=account.id,
                operation="oauth_config",
                code="personal_microsoft_account_requires_refresh_token",
            )
        if account.auth.tenant_id.casefold() in _GENERIC_MICROSOFT_AUTHORITIES:
            raise ConnectorConfigurationError(
                provider="outlook",
                account_id=account.id,
                operation="oauth_config",
                code="client_credentials_requires_tenant_specific_authority",
            )
    elif (
        isinstance(account.auth, RefreshTokenAuth)
        and is_personal_microsoft_mailbox(account.mailbox)
        and (account.auth.tenant_id is None or account.auth.tenant_id.casefold() != "consumers")
    ):
        raise ConnectorConfigurationError(
            provider="outlook",
            account_id=account.id,
            operation="oauth_config",
            code="personal_microsoft_account_requires_consumers_authority",
        )


def _valid_message_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and 1 <= len(value) <= 1024


def _is_permanent_message_response_error(error: ProviderResponseError) -> bool:
    status = error.status_code
    return (
        error.code in _PERMANENT_MESSAGE_RESPONSE_CODES
        and status is not None
        and 200 <= status < 300
    )


def _message_ids_from_delta(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("invalid Graph delta entries")
    candidates: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid Graph delta entry")
        if "@removed" in item:
            continue
        message_id = item.get("id")
        if not _valid_message_id(message_id):
            raise ValueError("invalid Graph message reference")
        assert isinstance(message_id, str)
        candidates.append(message_id)
    result: list[str] = []
    seen: set[str] = set()
    for message_id in candidates:
        # Graph can emit the same entity more than once on a delta page and does not
        # guarantee ordering. If both an upsert and a tombstone exist, the bounded GET
        # below is the authority: it returns the current message or a harmless 404.
        if message_id not in seen:
            seen.add(message_id)
            result.append(message_id)
        if len(result) > _MAX_PENDING_IDS:
            raise ValueError("too many Graph message references")
    return result


def _canonical_graph_path(path: str) -> str:
    """Rewrite OData key segments so Graph continuations compare against a built URL.

    Only the ``name('key')`` form is rewritten. Every other byte, percent-encoding included,
    is left alone: decoding first would let an encoded separator such as
    ``mailFolders('inbox%2Fmessages%2Fdelta')`` collapse onto the configured path.
    """

    segments: list[str] = []
    for segment in path.split("/"):
        match = _GRAPH_KEY_SEGMENT.fullmatch(segment)
        if match is None:
            segments.append(segment)
            continue
        segments.append(match.group(1) + "/" + match.group(2).replace("''", "'"))
    return "/".join(segments)


def _continuation_url(payload: Mapping[str, Any]) -> str:
    next_link = payload.get("@odata.nextLink")
    delta_link = payload.get("@odata.deltaLink")
    if (next_link is None) == (delta_link is None):
        raise ValueError("Graph response must contain one continuation link")
    value = next_link if next_link is not None else delta_link
    if not isinstance(value, str):
        raise ValueError("invalid Graph continuation link")
    return value


def _email_address(value: object) -> tuple[str, str]:
    if not isinstance(value, dict):
        return "", ""
    email_address = value.get("emailAddress")
    if not isinstance(email_address, dict):
        return "", ""
    name = _optional_text(email_address.get("name"), max_chars=320)
    address = _optional_text(email_address.get("address"), max_chars=320)
    return name, address


def _recipient_addresses(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for recipient in value[:200]:
        _name, address = _email_address(recipient)
        folded = address.casefold()
        if address and folded not in seen:
            seen.add(folded)
            result.append(address)
    return result


def _body_text(value: object, *, max_chars: int) -> str:
    if not isinstance(value, dict):
        return ""
    content = value.get("content")
    if not isinstance(content, str):
        return ""
    content_type = value.get("contentType")
    if isinstance(content_type, str) and content_type.casefold() == "html":
        return html_to_safe_text(content, max_chars=max_chars)
    return sanitize_text(content, max_chars=max_chars)


def _received_at(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("missing receivedDateTime")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _attachments(value: object) -> list[AttachmentInfo]:
    if not isinstance(value, list):
        return []
    result: list[AttachmentInfo] = []
    for attachment in value[:100]:
        if not isinstance(attachment, dict):
            continue
        size = attachment.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            size = None
        inline = attachment.get("isInline")
        result.append(
            AttachmentInfo(
                filename=_optional_text(attachment.get("name"), max_chars=512),
                content_type=(
                    _optional_text(attachment.get("contentType"), max_chars=255)
                    or "application/octet-stream"
                ),
                size=size,
                inline=inline if isinstance(inline, bool) else False,
            )
        )
    return result


def _optional_text(value: object, *, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    return sanitize_text(value, max_chars=max_chars)


def _bounded_strings(value: object, *, count: int, length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        sanitize_text(item, max_chars=length)
        for item in value[:count]
        if isinstance(item, str) and item
    ]


def _safe_web_url(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value or len(value) > 4096:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return value


__all__ = ["OutlookConnector", "validate_outlook_account_configuration"]
