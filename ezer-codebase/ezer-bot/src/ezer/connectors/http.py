"""Authenticated HTTP transport with bounded provider-aware retries."""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from ezer.connectors.auth import TokenProvider, decoded_response
from ezer.connectors.base import (
    AuthenticationError,
    AuthorizationError,
    Provider,
    ProviderResponseError,
    RateLimitError,
    ResourceNotFoundError,
    TransientProviderError,
    safe_error_code,
)

type QueryValue = str | int | float | bool | Sequence[str]
Sleeper = Callable[[float], Awaitable[None]]

_GMAIL_RETRY_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
_RETRIABLE_STATUS = frozenset({429, 500, 502, 503, 504})
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= 10
        ):
            raise ValueError("max_attempts must be between 1 and 10")
        if (
            not math.isfinite(self.base_delay_seconds)
            or not math.isfinite(self.max_delay_seconds)
            or self.base_delay_seconds <= 0
            or self.max_delay_seconds <= 0
        ):
            raise ValueError("retry delays must be positive")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")


DEFAULT_RETRY_POLICY = RetryPolicy()
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class AuthorizedHttpClient:
    """Small JSON-only transport that never includes response prose in errors."""

    def __init__(
        self,
        *,
        provider: Provider,
        account_id: str,
        client: httpx.AsyncClient,
        token_provider: TokenProvider,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        sleep: Sleeper = asyncio.sleep,
        random_value: Callable[[], float] = random.random,
        wall_clock: Callable[[], float] = time.time,
        timeout: httpx.Timeout | float = _DEFAULT_TIMEOUT,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not 1 <= max_response_bytes <= DEFAULT_MAX_RESPONSE_BYTES
        ):
            raise ValueError(
                f"max_response_bytes must be between 1 and {DEFAULT_MAX_RESPONSE_BYTES}"
            )
        self.provider = provider
        self.account_id = account_id
        self._client = client
        self._token_provider = token_provider
        self._retry = retry_policy
        self._sleep = sleep
        self._random = random_value
        self._wall_clock = wall_clock
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes

    async def get_json(
        self,
        url: str,
        *,
        operation: str,
        params: Mapping[str, QueryValue] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        authentication_retried = False
        last_status: int | None = None
        last_code = "request_failed"
        last_retry_after: float | None = None

        for attempt in range(self._retry.max_attempts):
            bearer = await self._token_provider.get_token()
            request_headers = {"Accept": "application/json", **(headers or {})}
            request_headers["Authorization"] = f"Bearer {bearer.value}"
            try:
                response = await self._bounded_get(
                    url=url,
                    operation=operation,
                    params=params,
                    headers=request_headers,
                )
            except httpx.TransportError as exc:
                if attempt + 1 >= self._retry.max_attempts:
                    raise TransientProviderError(
                        provider=self.provider,
                        account_id=self.account_id,
                        operation=operation,
                        code="transport_error",
                    ) from exc
                await self._sleep(self._backoff(attempt))
                continue

            status = response.status_code
            if 200 <= status < 300:
                return self._json_object(response, operation=operation)

            code, gmail_reason = self._provider_error(response)
            retry_after = self._retry_after(response)
            last_status = status
            last_code = code
            last_retry_after = retry_after

            if status == 401:
                if not authentication_retried and attempt + 1 < self._retry.max_attempts:
                    authentication_retried = True
                    await self._token_provider.invalidate(bearer.value)
                    continue
                raise AuthenticationError(
                    provider=self.provider,
                    account_id=self.account_id,
                    operation=operation,
                    code=code or "unauthorized",
                )
            if status == 403 and not (
                self.provider == "gmail" and gmail_reason in _GMAIL_RETRY_REASONS
            ):
                raise AuthorizationError(
                    provider=self.provider,
                    account_id=self.account_id,
                    operation=operation,
                    code=code or "forbidden",
                )
            if status == 404:
                raise ResourceNotFoundError(
                    provider=self.provider,
                    account_id=self.account_id,
                    operation=operation,
                    code=code or "not_found",
                )

            retriable = status in _RETRIABLE_STATUS or (
                status == 403 and self.provider == "gmail" and gmail_reason in _GMAIL_RETRY_REASONS
            )
            if retriable and attempt + 1 < self._retry.max_attempts:
                delay = retry_after if retry_after is not None else self._backoff(attempt)
                await self._sleep(min(delay, self._retry.max_delay_seconds))
                continue
            if retriable:
                error_type = RateLimitError if status in {403, 429} else TransientProviderError
                raise error_type(
                    provider=self.provider,
                    account_id=self.account_id,
                    operation=operation,
                    code=code,
                    status_code=status,
                    retry_after=retry_after,
                )
            raise ProviderResponseError(
                provider=self.provider,
                account_id=self.account_id,
                operation=operation,
                code=code,
                status_code=status,
                retry_after=retry_after,
            )

        raise TransientProviderError(
            provider=self.provider,
            account_id=self.account_id,
            operation=operation,
            code=last_code,
            status_code=last_status,
            retry_after=last_retry_after,
        )

    async def _bounded_get(
        self,
        *,
        url: str,
        operation: str,
        params: Mapping[str, QueryValue] | None,
        headers: Mapping[str, str],
    ) -> httpx.Response:
        async with self._client.stream(
            "GET",
            url,
            params=params,
            headers=headers,
            follow_redirects=False,
            timeout=self._timeout,
        ) as response:
            declared_length = response.headers.get("Content-Length")
            if declared_length is not None:
                try:
                    if int(declared_length) > self._max_response_bytes:
                        raise self._response_too_large(operation, response.status_code)
                except ValueError:
                    pass

            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > self._max_response_bytes:
                    raise self._response_too_large(operation, response.status_code)
                content.extend(chunk)
            return decoded_response(response, bytes(content))

    def _response_too_large(
        self,
        operation: str,
        status_code: int,
    ) -> ProviderResponseError:
        return ProviderResponseError(
            provider=self.provider,
            account_id=self.account_id,
            operation=operation,
            code="response_too_large",
            status_code=status_code,
        )

    def _json_object(self, response: httpx.Response, *, operation: str) -> Mapping[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderResponseError(
                provider=self.provider,
                account_id=self.account_id,
                operation=operation,
                code="invalid_json",
                status_code=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderResponseError(
                provider=self.provider,
                account_id=self.account_id,
                operation=operation,
                code="invalid_json_shape",
                status_code=response.status_code,
            )
        return payload

    def _backoff(self, attempt: int) -> float:
        exponential = min(
            self._retry.max_delay_seconds,
            self._retry.base_delay_seconds * (2**attempt),
        )
        jitter = self._random() * min(self._retry.base_delay_seconds, exponential)
        return float(min(self._retry.max_delay_seconds, exponential + jitter))

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

    @staticmethod
    def _provider_error(response: httpx.Response) -> tuple[str, str | None]:
        try:
            payload = response.json()
        except ValueError:
            return f"http_{response.status_code}", None
        if not isinstance(payload, dict):
            return f"http_{response.status_code}", None
        error = payload.get("error")
        if not isinstance(error, dict):
            return f"http_{response.status_code}", None

        code = safe_error_code(error.get("code"), default=f"http_{response.status_code}")
        reasons = error.get("errors")
        if isinstance(reasons, list):
            for item in reasons:
                if isinstance(item, dict) and isinstance(item.get("reason"), str):
                    reason = item["reason"]
                    return safe_error_code(reason), reason
        return code, None


def validate_fixed_https_url(
    url: str,
    *,
    expected_host: str,
    path_prefix: str,
) -> str:
    """Reject cursor URLs that could turn an API continuation into SSRF."""

    if not isinstance(url, str) or not url or "\\" in url or len(url) > 32_768:
        raise ValueError("invalid continuation URL")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.hostname.casefold() != expected_host.casefold()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or not parsed.path.startswith(path_prefix)
        or parsed.fragment
    ):
        raise ValueError("invalid continuation URL")
    return url


__all__ = [
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_RETRY_POLICY",
    "AuthorizedHttpClient",
    "QueryValue",
    "RetryPolicy",
    "validate_fixed_https_url",
]
