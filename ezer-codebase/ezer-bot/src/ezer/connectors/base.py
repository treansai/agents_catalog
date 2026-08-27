"""Provider-neutral connector contracts, cursors, and safe error types."""

from __future__ import annotations

import base64
import binascii
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any, Literal

from ezer.domain import FetchBatch

type Provider = Literal["gmail", "outlook"]

_SAFE_CODE = re.compile(r"[^A-Za-z0-9_.-]+")
_CURSOR_PREFIX = "ezer1."
_MAX_CURSOR_BYTES = 512 * 1024
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INLINE_SPACE = re.compile(r"[ \t\f\v]+")
_AROUND_NEWLINE = re.compile(r" *\n *")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_IGNORED_TAGS = frozenset({"head", "noscript", "script", "style", "svg", "template"})


class _SafeHTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fragments: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in _IGNORED_TAGS:
            self._ignored_depth += 1
        elif self._ignored_depth == 0 and normalized in _BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if self._ignored_depth == 0 and tag.casefold() in _BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in _IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
        elif self._ignored_depth == 0 and normalized in _BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self.fragments.append(data)


def safe_error_code(value: object, *, default: str = "unknown") -> str:
    """Return a bounded machine-readable code without provider-supplied prose."""

    if not isinstance(value, str) or not value:
        return default
    cleaned = _SAFE_CODE.sub("_", value)[:96]
    return cleaned or default


def sanitize_text(value: str, *, max_chars: int = 500_000) -> str:
    """Remove unsafe controls and normalize whitespace without following links."""

    bounded = value[: max(max_chars, 0) * 4]
    cleaned = _CONTROL_CHARACTERS.sub("", bounded).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _INLINE_SPACE.sub(" ", cleaned)
    cleaned = _AROUND_NEWLINE.sub("\n", cleaned)
    cleaned = _MANY_NEWLINES.sub("\n\n", cleaned).strip()
    return cleaned[:max_chars]


def html_to_safe_text(value: str, *, max_chars: int = 500_000) -> str:
    """Extract visible HTML text; scripts, styles, SVG, and URLs are never evaluated."""

    parser = _SafeHTMLTextExtractor()
    try:
        parser.feed(value[: max_chars * 4])
        parser.close()
    except UnicodeError, ValueError:
        return ""
    return sanitize_text("".join(parser.fragments), max_chars=max_chars)


class ConnectorError(Exception):
    """Base class whose string representation is deliberately secret-free."""

    def __init__(
        self,
        *,
        provider: Provider,
        account_id: str,
        operation: str,
        code: str,
    ) -> None:
        self.provider = provider
        self.account_id = account_id
        self.operation = safe_error_code(operation, default="request")
        self.code = safe_error_code(code)
        super().__init__(
            f"{self.provider} connector failed for account "
            f"{self.account_id!r} during {self.operation} (code={self.code})"
        )


class ConnectorConfigurationError(ConnectorError):
    """The connector was configured with an unsupported or unsafe value."""


class AuthenticationError(ConnectorError):
    """Credentials are missing, expired, revoked, or rejected."""


class AuthorizationError(ConnectorError):
    """The identity lacks a required provider permission."""


class InvalidCursorError(ConnectorError):
    """A cursor was malformed or belongs to another account/provider."""


class CursorExpiredError(ConnectorError):
    """A provider can no longer continue from the supplied checkpoint."""


class ResourceNotFoundError(ConnectorError):
    """A message disappeared between listing and retrieval."""


class ProviderResponseError(ConnectorError):
    """A provider returned a non-success response or malformed payload."""

    def __init__(
        self,
        *,
        provider: Provider,
        account_id: str,
        operation: str,
        code: str,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(
            provider=provider,
            account_id=account_id,
            operation=operation,
            code=code,
        )


class RateLimitError(ProviderResponseError):
    """The provider kept throttling after the bounded retry budget."""


class TransientProviderError(ProviderResponseError):
    """A transient network or provider failure exhausted its retries."""


class EmailConnector(ABC):
    """Read-only asynchronous mailbox connector."""

    @property
    @abstractmethod
    def account_id(self) -> str:
        """Stable local account identifier."""

    @property
    @abstractmethod
    def provider(self) -> Provider:
        """Provider discriminator."""

    @abstractmethod
    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        """Fetch one page and return its opaque continuation/checkpoint."""

    async def aclose(self) -> None:
        """Release connector-owned resources, if any."""

        return None

    async def __aenter__(self) -> EmailConnector:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.aclose()


MailConnector = EmailConnector


def encode_cursor(*, account_id: str, provider: Provider, state: Mapping[str, Any]) -> str:
    """Encode connector-owned state without exposing provider tokens directly."""

    envelope = {
        "account_id": account_id,
        "provider": provider,
        "state": dict(state),
        "version": 1,
    }
    raw = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > _MAX_CURSOR_BYTES:
        raise ConnectorConfigurationError(
            provider=provider,
            account_id=account_id,
            operation="encode_cursor",
            code="cursor_too_large",
        )
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"{_CURSOR_PREFIX}{encoded}"


def decode_cursor(
    cursor: str,
    *,
    account_id: str,
    provider: Provider,
) -> Mapping[str, Any]:
    """Decode and bind an opaque cursor to its intended account and provider."""

    if (
        not isinstance(cursor, str)
        or not cursor.startswith(_CURSOR_PREFIX)
        or len(cursor) > _MAX_CURSOR_BYTES * 2
    ):
        raise InvalidCursorError(
            provider=provider,
            account_id=account_id,
            operation="decode_cursor",
            code="invalid_cursor",
        )
    encoded = cursor.removeprefix(_CURSOR_PREFIX)
    padding = "=" * (-len(encoded) % 4)
    try:
        raw = base64.b64decode(f"{encoded}{padding}", altchars=b"-_", validate=True)
        if len(raw) > _MAX_CURSOR_BYTES:
            raise ValueError
        payload = json.loads(raw)
    except (ValueError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise InvalidCursorError(
            provider=provider,
            account_id=account_id,
            operation="decode_cursor",
            code="invalid_cursor",
        ) from exc

    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("account_id") != account_id
        or payload.get("provider") != provider
        or not isinstance(payload.get("state"), dict)
    ):
        raise InvalidCursorError(
            provider=provider,
            account_id=account_id,
            operation="decode_cursor",
            code="cursor_scope_mismatch",
        )
    state = payload["state"]
    assert isinstance(state, dict)
    return state


def validate_fetch_limit(limit: int, *, provider: Provider, account_id: str) -> int:
    """Reject booleans and unsafe page sizes at the connector boundary."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ConnectorConfigurationError(
            provider=provider,
            account_id=account_id,
            operation="fetch",
            code="invalid_limit",
        )
    return limit


def validate_max_body_chars(
    max_body_chars: int,
    *,
    provider: Provider,
    account_id: str,
) -> int:
    """Bound normalized message bodies before they enter the application graph."""

    if (
        isinstance(max_body_chars, bool)
        or not isinstance(max_body_chars, int)
        or not 1 <= max_body_chars <= 500_000
    ):
        raise ConnectorConfigurationError(
            provider=provider,
            account_id=account_id,
            operation="connector_config",
            code="invalid_max_body_chars",
        )
    return max_body_chars


__all__ = [
    "AuthenticationError",
    "AuthorizationError",
    "ConnectorConfigurationError",
    "ConnectorError",
    "CursorExpiredError",
    "EmailConnector",
    "InvalidCursorError",
    "MailConnector",
    "Provider",
    "ProviderResponseError",
    "RateLimitError",
    "ResourceNotFoundError",
    "TransientProviderError",
    "decode_cursor",
    "encode_cursor",
    "html_to_safe_text",
    "safe_error_code",
    "sanitize_text",
    "validate_fetch_limit",
    "validate_max_body_chars",
]
