from __future__ import annotations

import asyncio
import gzip
from collections.abc import AsyncIterator
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from ezer.config import ClientCredentialsAuth, RefreshTokenAuth
from ezer.connectors.auth import (
    AUTHORITY,
    GOOGLE_TOKEN_ENDPOINT,
    MICROSOFT_CONSUMERS_TOKEN_ENDPOINT,
    MICROSOFT_GRAPH_DEFAULT_SCOPE,
    BearerToken,
    ClientCredentialsTokenProvider,
    RefreshTokenProvider,
    microsoft_token_endpoint,
)
from ezer.connectors.base import AuthenticationError, ProviderResponseError, RateLimitError
from ezer.connectors.http import AuthorizedHttpClient, RetryPolicy


class RotatingTokenProvider:
    def __init__(self) -> None:
        self.generation = 0
        self.invalidated: list[str | None] = []

    async def get_token(self) -> BearerToken:
        return BearerToken(f"bearer-{self.generation}")

    async def invalidate(self, token: str | None = None) -> None:
        self.invalidated.append(token)
        self.generation += 1


class GzipStream(httpx.AsyncByteStream):
    def __init__(self, payload: bytes) -> None:
        self._compressed = gzip.compress(payload)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._compressed


class ChunkedStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'{"value":"'
        yield b"x" * 64
        yield b'"}'


async def fail_sleep(delay: float) -> None:
    raise AssertionError(f"unexpected sleep: {delay}")


async def test_gzipped_provider_page_is_decoded_exactly_once() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
            stream=GzipStream(b'{"value":[{"id":"message-one"}]}'),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = AuthorizedHttpClient(
            provider="outlook",
            account_id="work",
            client=client,
            token_provider=RotatingTokenProvider(),
            sleep=fail_sleep,
        )
        result = await transport.get_json(
            "https://graph.microsoft.com/v1.0/users/user/messages",
            operation="messages",
        )

    assert result == {"value": [{"id": "message-one"}]}


async def test_gzipped_token_response_is_decoded_exactly_once() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
            stream=GzipStream(
                b'{"access_token":"access-one","expires_in":3600,"token_type":"Bearer"}'
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ClientCredentialsTokenProvider(
            account_id="work",
            config=ClientCredentialsAuth(
                type="client_credentials",
                tenant_id="tenant-id",
                client_id="client-id",
                client_secret=SecretStr("client-secret"),
            ),
            client=client,
            sleep=fail_sleep,
        )
        token = await provider.get_token()

    assert token.value == "access-one"


async def test_refresh_token_cache_is_shared_by_concurrent_callers() -> None:
    request_count = 0
    forms: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        forms.append(parse_qs(request.content.decode("ascii")))
        return httpx.Response(
            200,
            json={
                "access_token": "access-one",
                "expires_in": 3600,
                "refresh_token": "refresh-new",
                "token_type": "Bearer",
            },
        )

    rotated: list[str] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RefreshTokenProvider(
            provider="gmail",
            account_id="personal",
            config=RefreshTokenAuth(
                type="refresh_token",
                client_id="client-id",
                client_secret=SecretStr("client-secret"),
                refresh_token=SecretStr("refresh-old"),
            ),
            client=client,
            refresh_token_sink=rotated.append,
            clock=lambda: 100.0,
            sleep=fail_sleep,
        )

        results = await asyncio.gather(*(provider.get_token() for _ in range(25)))

    assert request_count == 1
    assert {result.value for result in results} == {"access-one"}
    assert "access-one" not in repr(results[0])
    assert forms == [
        {
            "client_id": ["client-id"],
            "client_secret": ["client-secret"],
            "grant_type": ["refresh_token"],
            "refresh_token": ["refresh-old"],
        }
    ]
    assert rotated == ["refresh-new"]


async def test_refresh_and_client_credentials_use_fixed_authority_endpoints() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"access_token": "issued", "expires_in": 3600, "token_type": "Bearer"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        refresh = RefreshTokenProvider(
            provider="gmail",
            account_id="personal",
            config=RefreshTokenAuth(
                type="refresh_token",
                client_id="gmail-client",
                client_secret=SecretStr("gmail-secret"),
                refresh_token=SecretStr("gmail-refresh"),
            ),
            client=client,
            sleep=fail_sleep,
        )
        application = ClientCredentialsTokenProvider(
            account_id="work",
            config=ClientCredentialsAuth(
                type="client_credentials",
                tenant_id="tenant-id",
                client_id="graph-client",
                client_secret=SecretStr("graph-secret"),
            ),
            client=client,
            sleep=fail_sleep,
        )
        await refresh.get_token()
        await application.get_token()

    assert str(requests[0].url) == GOOGLE_TOKEN_ENDPOINT
    assert str(requests[1].url) == ("https://login.microsoftonline.com/tenant-id/oauth2/v2.0/token")
    application_form = parse_qs(requests[1].content.decode("ascii"))
    assert application_form["grant_type"] == ["client_credentials"]
    assert application_form["scope"] == [MICROSOFT_GRAPH_DEFAULT_SCOPE]


def test_consumer_authority_is_fixed_and_builds_the_delegated_token_endpoint() -> None:
    assert AUTHORITY == "https://login.microsoftonline.com/consumers"
    assert MICROSOFT_CONSUMERS_TOKEN_ENDPOINT == f"{AUTHORITY}/oauth2/v2.0/token"
    assert microsoft_token_endpoint("consumers") == MICROSOFT_CONSUMERS_TOKEN_ENDPOINT


async def test_outlook_delegated_refresh_uses_consumers_authority() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"access_token": "issued", "expires_in": 3600, "token_type": "Bearer"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RefreshTokenProvider(
            provider="outlook",
            account_id="personal",
            config=RefreshTokenAuth(
                type="refresh_token",
                tenant_id="consumers",
                client_id="client-id",
                client_secret=SecretStr("client-secret"),
                refresh_token=SecretStr("refresh-token"),
            ),
            client=client,
            sleep=fail_sleep,
        )
        await provider.get_token()

    assert str(requests[0].url) == MICROSOFT_CONSUMERS_TOKEN_ENDPOINT
    assert parse_qs(requests[0].content.decode("ascii"))["grant_type"] == ["refresh_token"]


async def test_oauth_token_response_rejects_oversized_declared_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"Content-Length": str(2 * 1024 * 1024)},
            content=b"{}",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RefreshTokenProvider(
            provider="gmail",
            account_id="personal",
            config=RefreshTokenAuth(
                type="refresh_token",
                client_id="client-id",
                client_secret=SecretStr("client-secret"),
                refresh_token=SecretStr("refresh-old"),
            ),
            client=client,
            sleep=fail_sleep,
        )
        with pytest.raises(AuthenticationError) as caught:
            await provider.get_token()

    assert caught.value.code == "token_response_too_large_http_200"


async def test_oauth_error_preserves_safe_aadsts_diagnostic_code_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            400,
            json={
                "error": "invalid_request",
                "error_codes": [9002346],
                "error_description": "sensitive provider prose must not escape",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RefreshTokenProvider(
            provider="gmail",
            account_id="personal",
            config=RefreshTokenAuth(
                type="refresh_token",
                client_id="client-id",
                client_secret=SecretStr("client-secret"),
                refresh_token=SecretStr("refresh-old"),
            ),
            client=client,
            sleep=fail_sleep,
        )
        with pytest.raises(AuthenticationError) as caught:
            await provider.get_token()

    assert caught.value.code == "invalid_request_aadsts9002346"
    assert "sensitive provider prose" not in str(caught.value)


async def test_oauth_token_retry_after_is_honored_and_capped() -> None:
    calls = 0
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "120"},
                json={"error": "temporarily_unavailable"},
            )
        return httpx.Response(
            200,
            json={"access_token": "issued", "expires_in": 3600, "token_type": "Bearer"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RefreshTokenProvider(
            provider="gmail",
            account_id="personal",
            config=RefreshTokenAuth(
                type="refresh_token",
                client_id="client-id",
                client_secret=SecretStr("client-secret"),
                refresh_token=SecretStr("refresh-old"),
            ),
            client=client,
            sleep=record_sleep,
            random_value=lambda: 0.0,
        )
        token = await provider.get_token()

    assert token.value == "issued"
    assert calls == 2
    assert sleeps == [30.0]


async def test_401_invalidates_rejected_token_once_and_retries() -> None:
    provider = RotatingTokenProvider()
    authorization: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorization.append(request.headers["Authorization"])
        if len(authorization) == 1:
            return httpx.Response(
                401,
                json={"error": {"code": "InvalidAuthenticationToken"}},
            )
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = AuthorizedHttpClient(
            provider="outlook",
            account_id="work",
            client=client,
            token_provider=provider,
            retry_policy=RetryPolicy(max_attempts=2),
            sleep=fail_sleep,
        )
        result = await transport.get_json(
            "https://graph.microsoft.com/v1.0/users/user/messages",
            operation="messages",
        )

    assert result == {"ok": True}
    assert authorization == ["Bearer bearer-0", "Bearer bearer-1"]
    assert provider.invalidated == ["bearer-0"]


async def test_429_retry_after_sleep_is_capped_but_error_preserves_provider_delay() -> None:
    sleeps: list[float] = []
    calls = 0

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    def succeeds_after_throttle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "120"},
                json={"error": {"code": "TooManyRequests"}},
            )
        return httpx.Response(200, json={"ok": True})

    provider = RotatingTokenProvider()
    async with httpx.AsyncClient(transport=httpx.MockTransport(succeeds_after_throttle)) as client:
        transport = AuthorizedHttpClient(
            provider="outlook",
            account_id="work",
            client=client,
            token_provider=provider,
            retry_policy=RetryPolicy(max_attempts=2, max_delay_seconds=30),
            sleep=record_sleep,
        )
        assert await transport.get_json(
            "https://graph.microsoft.com/v1.0/users/user/messages",
            operation="messages",
        ) == {"ok": True}

    assert calls == 2
    assert sleeps == [30]

    def always_throttled(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            429,
            headers={"Retry-After": "120"},
            json={"error": {"code": "TooManyRequests"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(always_throttled)) as client:
        transport = AuthorizedHttpClient(
            provider="outlook",
            account_id="work",
            client=client,
            token_provider=provider,
            retry_policy=RetryPolicy(max_attempts=1),
            sleep=fail_sleep,
        )
        with pytest.raises(RateLimitError) as caught:
            await transport.get_json(
                "https://graph.microsoft.com/v1.0/users/user/messages",
                operation="messages",
            )
    assert caught.value.retry_after == 120


async def test_gmail_rate_limit_403_retries_without_real_sleep() -> None:
    sleeps: list[float] = []
    calls = 0

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        if calls == 1:
            return httpx.Response(
                403,
                json={
                    "error": {
                        "code": 403,
                        "errors": [{"reason": "userRateLimitExceeded"}],
                    }
                },
            )
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = AuthorizedHttpClient(
            provider="gmail",
            account_id="personal",
            client=client,
            token_provider=RotatingTokenProvider(),
            retry_policy=RetryPolicy(
                max_attempts=2,
                base_delay_seconds=1,
                max_delay_seconds=5,
            ),
            sleep=record_sleep,
            random_value=lambda: 0,
        )
        assert await transport.get_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            operation="profile",
        ) == {"ok": True}

    assert sleeps == [1]


async def test_streaming_response_limit_rejects_chunked_payload_safely() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, stream=ChunkedStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = AuthorizedHttpClient(
            provider="gmail",
            account_id="personal",
            client=client,
            token_provider=RotatingTokenProvider(),
            max_response_bytes=16,
            sleep=fail_sleep,
        )
        with pytest.raises(ProviderResponseError) as caught:
            await transport.get_json(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                operation="profile",
            )

    assert caught.value.code == "response_too_large"
    assert "x" * 16 not in str(caught.value)
