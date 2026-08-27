"""Concurrent, testable OAuth token providers with fixed authority endpoints."""

from __future__ import annotations

import asyncio
import inspect
import math
import random
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from ezer.config import (
    ClientCredentialsAuth,
    MsalDeviceCodeAuth,
    RefreshTokenAuth,
    StaticTokenAuth,
)
from ezer.connectors.base import (
    AuthenticationError,
    ConnectorConfigurationError,
    Provider,
    TransientProviderError,
    safe_error_code,
)

GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105
MICROSOFT_LOGIN_HOST = "login.microsoftonline.com"
MICROSOFT_GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"
AUTHORITY = "https://login.microsoftonline.com/consumers"
MICROSOFT_CONSUMERS_TOKEN_ENDPOINT = f"{AUTHORITY}/oauth2/v2.0/token"

# The device-code page differs by authority: Entra ID directs operators to /devicelogin, while the
# /consumers authority used for personal mailboxes returns /link. Entering a code on the wrong page
# is rejected, so the URI Microsoft returns is used verbatim after matching this allow-list.
MICROSOFT_DEVICE_LOGIN_URIS = frozenset(
    {
        "https://microsoft.com/devicelogin",
        "https://www.microsoft.com/devicelogin",
        "https://microsoft.com/link",
        "https://www.microsoft.com/link",
    }
)

_TENANT_ID = re.compile(r"^[A-Za-z0-9._-]+$")
# Describe the compressed bytes on the wire, not the decoded body a streamed read produces.
_TRANSFER_HEADERS = ("content-encoding", "content-length", "transfer-encoding")
_TOKEN_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_MAX_TOKEN_RESPONSE_BYTES = 1024 * 1024
_MAX_TOKEN_RETRY_DELAY_SECONDS = 30.0

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]
RandomValue = Callable[[], float]
RefreshTokenSink = Callable[[str], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class BearerToken:
    """A bearer token whose secret is excluded from repr and equality output."""

    value: str = field(repr=False)
    expires_at: float | None = None

    def is_valid(self, now: float, *, skew_seconds: float = 60.0) -> bool:
        return self.expires_at is None or now + skew_seconds < self.expires_at


class TokenProvider(Protocol):
    async def get_token(self) -> BearerToken:
        """Return a valid token, refreshing it if necessary."""

    async def invalidate(self, token: str | None = None) -> None:
        """Invalidate a cached token after an authentication rejection."""


class StaticTokenProvider:
    """Static access-token provider intended for development and smoke tests."""

    def __init__(self, config: StaticTokenAuth) -> None:
        self._token = BearerToken(config.access_token.get_secret_value())

    async def get_token(self) -> BearerToken:
        return self._token

    async def invalidate(self, token: str | None = None) -> None:
        del token


class _CachedOAuthProvider:
    """Serialize refreshes while allowing lock-free reads of valid tokens."""

    def __init__(
        self,
        *,
        provider: Provider,
        account_id: str,
        client: httpx.AsyncClient,
        token_endpoint: str,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
        random_value: RandomValue = random.random,
        wall_clock: Clock = time.time,
    ) -> None:
        self._provider = provider
        self._account_id = account_id
        self._client = client
        self._token_endpoint = token_endpoint
        self._clock = clock
        self._sleep = sleep
        self._random = random_value
        self._wall_clock = wall_clock
        self._lock = asyncio.Lock()
        self._cached: BearerToken | None = None

    async def get_token(self) -> BearerToken:
        cached = self._cached
        now = self._clock()
        if cached is not None and cached.is_valid(now):
            return cached
        async with self._lock:
            cached = self._cached
            now = self._clock()
            if cached is not None and cached.is_valid(now):
                return cached
            refreshed = await self._request_token_with_retry()
            self._cached = refreshed
            return refreshed

    async def invalidate(self, token: str | None = None) -> None:
        async with self._lock:
            if token is None or (self._cached is not None and self._cached.value == token):
                self._cached = None

    async def _request_token_with_retry(self) -> BearerToken:
        for attempt in range(3):
            try:
                response = await self._bounded_token_request()
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise TransientProviderError(
                        provider=self._provider,
                        account_id=self._account_id,
                        operation="oauth_token",
                        code="token_transport_error",
                    ) from exc
                await self._sleep(self._backoff(attempt))
                continue

            if response.status_code in {429, 500, 502, 503, 504}:
                retry_after = self._retry_after(response)
                if attempt == 2:
                    raise TransientProviderError(
                        provider=self._provider,
                        account_id=self._account_id,
                        operation="oauth_token",
                        code=f"token_http_{response.status_code}",
                        status_code=response.status_code,
                        retry_after=retry_after,
                    )
                await self._sleep(
                    min(
                        retry_after if retry_after is not None else self._backoff(attempt),
                        _MAX_TOKEN_RETRY_DELAY_SECONDS,
                    )
                )
                continue
            if response.status_code < 200 or response.status_code >= 300:
                raise AuthenticationError(
                    provider=self._provider,
                    account_id=self._account_id,
                    operation="oauth_token",
                    code=self._oauth_error_code(response),
                )
            return await self._parse_token(response)
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int) -> float:
        exponential = min(_MAX_TOKEN_RETRY_DELAY_SECONDS, float(2**attempt))
        random_value = self._random()
        jitter = random_value if math.isfinite(random_value) else 0.0
        return min(_MAX_TOKEN_RETRY_DELAY_SECONDS, exponential + min(max(jitter, 0.0), 1.0))

    def _retry_after(self, response: httpx.Response) -> float | None:
        milliseconds = response.headers.get("x-ms-retry-after-ms")
        if milliseconds is not None:
            try:
                delay = float(milliseconds) / 1000.0
                if math.isfinite(delay):
                    return min(max(delay, 0.0), 3600.0)
            except ValueError:
                pass
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            delay = float(value)
            if not math.isfinite(delay):
                return None
            return min(max(delay, 0.0), 3600.0)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
            except TypeError, ValueError, OverflowError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            delay = parsed.timestamp() - self._wall_clock()
            return min(max(delay, 0.0), 3600.0)

    async def _bounded_token_request(self) -> httpx.Response:
        async with self._client.stream(
            "POST",
            self._token_endpoint,
            data=self._form_data(),
            headers={"Accept": "application/json"},
            follow_redirects=False,
            timeout=_TOKEN_TIMEOUT,
        ) as response:
            declared_length = response.headers.get("Content-Length")
            if declared_length is not None:
                try:
                    if int(declared_length) > _MAX_TOKEN_RESPONSE_BYTES:
                        raise self._token_response_too_large(response.status_code)
                except ValueError:
                    pass

            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > _MAX_TOKEN_RESPONSE_BYTES:
                    raise self._token_response_too_large(response.status_code)
                content.extend(chunk)
            return decoded_response(response, bytes(content))

    def _token_response_too_large(self, status_code: int) -> AuthenticationError:
        return AuthenticationError(
            provider=self._provider,
            account_id=self._account_id,
            operation="oauth_token",
            code=f"token_response_too_large_http_{status_code}",
        )

    @staticmethod
    def _oauth_error_code(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"token_http_{response.status_code}"
        if isinstance(payload, dict):
            base = safe_error_code(payload.get("error"), default="token_rejected")
            error_codes = payload.get("error_codes")
            if isinstance(error_codes, list):
                for error_code in error_codes:
                    if (
                        isinstance(error_code, int)
                        and not isinstance(error_code, bool)
                        and 0 <= error_code <= 999_999_999
                    ):
                        return safe_error_code(f"{base}_aadsts{error_code}")
            return base
        return "token_rejected"

    async def _parse_token(self, response: httpx.Response) -> BearerToken:
        try:
            payload = response.json()
        except ValueError as exc:
            raise AuthenticationError(
                provider=self._provider,
                account_id=self._account_id,
                operation="oauth_token",
                code="invalid_token_response",
            ) from exc
        if not isinstance(payload, dict):
            raise AuthenticationError(
                provider=self._provider,
                account_id=self._account_id,
                operation="oauth_token",
                code="invalid_token_response",
            )
        access_token = payload.get("access_token")
        token_type = payload.get("token_type", "Bearer")
        if (
            not isinstance(access_token, str)
            or not access_token
            or not isinstance(token_type, str)
            or token_type.casefold() != "bearer"
        ):
            raise AuthenticationError(
                provider=self._provider,
                account_id=self._account_id,
                operation="oauth_token",
                code="invalid_token_response",
            )
        expires_in_raw = payload.get("expires_in", 3600)
        try:
            expires_in = float(expires_in_raw)
        except TypeError, ValueError:
            expires_in = 3600.0
        expires_in = min(max(expires_in, 0.0), 86_400.0)
        await self._accept_rotated_refresh_token(payload)
        return BearerToken(access_token, self._clock() + expires_in)

    async def _accept_rotated_refresh_token(self, payload: Mapping[str, Any]) -> None:
        del payload

    def _form_data(self) -> Mapping[str, str]:
        raise NotImplementedError


class RefreshTokenProvider(_CachedOAuthProvider):
    """Refresh delegated Google or Microsoft OAuth credentials."""

    def __init__(
        self,
        *,
        provider: Provider,
        account_id: str,
        config: RefreshTokenAuth,
        client: httpx.AsyncClient,
        refresh_token_sink: RefreshTokenSink | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
        random_value: RandomValue = random.random,
        wall_clock: Clock = time.time,
    ) -> None:
        if provider == "gmail":
            if config.tenant_id is not None:
                raise ConnectorConfigurationError(
                    provider=provider,
                    account_id=account_id,
                    operation="oauth_config",
                    code="unexpected_tenant",
                )
            endpoint = GOOGLE_TOKEN_ENDPOINT
        else:
            if config.tenant_id is None or not _TENANT_ID.fullmatch(config.tenant_id):
                raise ConnectorConfigurationError(
                    provider=provider,
                    account_id=account_id,
                    operation="oauth_config",
                    code="invalid_tenant",
                )
            endpoint = microsoft_token_endpoint(config.tenant_id)
        super().__init__(
            provider=provider,
            account_id=account_id,
            client=client,
            token_endpoint=endpoint,
            clock=clock,
            sleep=sleep,
            random_value=random_value,
            wall_clock=wall_clock,
        )
        self._client_id = config.client_id
        self._client_secret = config.client_secret.get_secret_value()
        self._refresh_token = config.refresh_token.get_secret_value()
        self._refresh_token_sink = refresh_token_sink

    def _form_data(self) -> Mapping[str, str]:
        return {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }

    async def _accept_rotated_refresh_token(self, payload: Mapping[str, Any]) -> None:
        rotated = payload.get("refresh_token")
        if not isinstance(rotated, str) or not rotated or rotated == self._refresh_token:
            return
        if self._refresh_token_sink is not None:
            result = self._refresh_token_sink(rotated)
            if inspect.isawaitable(result):
                await result
        self._refresh_token = rotated


class ClientCredentialsTokenProvider(_CachedOAuthProvider):
    """Acquire app-only Microsoft Graph tokens using client credentials."""

    def __init__(
        self,
        *,
        account_id: str,
        config: ClientCredentialsAuth,
        client: httpx.AsyncClient,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
        random_value: RandomValue = random.random,
        wall_clock: Clock = time.time,
    ) -> None:
        if not _TENANT_ID.fullmatch(config.tenant_id):
            raise ConnectorConfigurationError(
                provider="outlook",
                account_id=account_id,
                operation="oauth_config",
                code="invalid_tenant",
            )
        super().__init__(
            provider="outlook",
            account_id=account_id,
            client=client,
            token_endpoint=microsoft_token_endpoint(config.tenant_id),
            clock=clock,
            sleep=sleep,
            random_value=random_value,
            wall_clock=wall_clock,
        )
        self._client_id = config.client_id
        self._client_secret = config.client_secret.get_secret_value()

    def _form_data(self) -> Mapping[str, str]:
        return {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "client_credentials",
            "scope": MICROSOFT_GRAPH_DEFAULT_SCOPE,
        }


def decoded_response(response: httpx.Response, content: bytes) -> httpx.Response:
    """Rebuild a streamed response around the body ``aiter_bytes`` already decoded.

    Streaming yields decompressed bytes, so carrying the original transfer headers over
    would make httpx decode the plaintext a second time and raise ``DecodingError``.
    """

    headers = httpx.Headers(response.headers)
    for name in _TRANSFER_HEADERS:
        if name in headers:
            del headers[name]
    return httpx.Response(
        response.status_code,
        headers=headers,
        content=content,
        request=response.request,
    )


def normalize_microsoft_device_login_uri(value: object) -> str | None:
    """Return a trusted Microsoft device-login URI, or ``None`` for anything unrecognized."""

    if not isinstance(value, str):
        return None
    candidate = value.strip().rstrip("/")
    return candidate if candidate in MICROSOFT_DEVICE_LOGIN_URIS else None


def microsoft_token_endpoint(tenant_id: str) -> str:
    if not _TENANT_ID.fullmatch(tenant_id):
        raise ValueError("invalid Microsoft tenant ID")
    if tenant_id.casefold() == "consumers":
        return MICROSOFT_CONSUMERS_TOKEN_ENDPOINT
    return f"https://{MICROSOFT_LOGIN_HOST}/{tenant_id}/oauth2/v2.0/token"


def create_token_provider(
    *,
    provider: Provider,
    account_id: str,
    config: StaticTokenAuth | RefreshTokenAuth | ClientCredentialsAuth | MsalDeviceCodeAuth,
    client: httpx.AsyncClient,
    mailbox: str | None = None,
    msal_cache_path: Path | None = None,
    msal_cache_encryption_key: bytes | None = None,
    refresh_token_sink: RefreshTokenSink | None = None,
    clock: Clock = time.monotonic,
    sleep: Sleeper = asyncio.sleep,
    random_value: RandomValue = random.random,
    wall_clock: Clock = time.time,
) -> TokenProvider:
    if isinstance(config, StaticTokenAuth):
        return StaticTokenProvider(config)
    if isinstance(config, MsalDeviceCodeAuth):
        if provider != "outlook" or mailbox is None or msal_cache_path is None:
            raise ConnectorConfigurationError(
                provider=provider,
                account_id=account_id,
                operation="oauth_config",
                code="invalid_device_code_configuration",
            )
        # Imported lazily to keep the core OAuth provider independent from MSAL's
        # synchronous implementation and to avoid a module import cycle.
        from ezer.connectors.msal_auth import MsalDeviceCodeTokenProvider

        return MsalDeviceCodeTokenProvider(
            account_id=account_id,
            mailbox=mailbox,
            client_id=config.client_id,
            cache_path=msal_cache_path,
            cache_encryption_key=msal_cache_encryption_key,
        )
    if isinstance(config, RefreshTokenAuth):
        return RefreshTokenProvider(
            provider=provider,
            account_id=account_id,
            config=config,
            client=client,
            refresh_token_sink=refresh_token_sink,
            clock=clock,
            sleep=sleep,
            random_value=random_value,
            wall_clock=wall_clock,
        )
    if provider != "outlook":
        raise ConnectorConfigurationError(
            provider=provider,
            account_id=account_id,
            operation="oauth_config",
            code="unsupported_client_credentials",
        )
    return ClientCredentialsTokenProvider(
        account_id=account_id,
        config=config,
        client=client,
        clock=clock,
        sleep=sleep,
        random_value=random_value,
        wall_clock=wall_clock,
    )


__all__ = [
    "AUTHORITY",
    "GOOGLE_TOKEN_ENDPOINT",
    "MICROSOFT_CONSUMERS_TOKEN_ENDPOINT",
    "MICROSOFT_DEVICE_LOGIN_URIS",
    "MICROSOFT_GRAPH_DEFAULT_SCOPE",
    "BearerToken",
    "ClientCredentialsTokenProvider",
    "RefreshTokenProvider",
    "StaticTokenProvider",
    "TokenProvider",
    "create_token_provider",
    "decoded_response",
    "microsoft_token_endpoint",
    "normalize_microsoft_device_login_uri",
]
