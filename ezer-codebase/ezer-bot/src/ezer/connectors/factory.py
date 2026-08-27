"""Typed connector construction from validated account configuration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import overload

import httpx

from ezer.config import EmailAccount, GmailAccount, OutlookAccount
from ezer.connectors.auth import RefreshTokenSink, TokenProvider
from ezer.connectors.base import EmailConnector
from ezer.connectors.gmail import GmailConnector
from ezer.connectors.http import DEFAULT_RETRY_POLICY, RetryPolicy
from ezer.connectors.outlook import OutlookConnector


@overload
def create_connector(
    account: GmailAccount,
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
) -> GmailConnector: ...


@overload
def create_connector(
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
) -> OutlookConnector: ...


def create_connector(
    account: EmailAccount,
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
) -> EmailConnector:
    """Create a provider connector; an injected client remains caller-owned."""

    if isinstance(account, GmailAccount):
        return GmailConnector(
            account,
            client,
            token_provider=token_provider,
            refresh_token_sink=refresh_token_sink,
            retry_policy=retry_policy,
            max_concurrency=max_concurrency,
            max_body_chars=max_body_chars,
            sleep=sleep,
        )
    if isinstance(account, OutlookAccount):
        return OutlookConnector(
            account,
            client,
            token_provider=token_provider,
            refresh_token_sink=refresh_token_sink,
            retry_policy=retry_policy,
            max_concurrency=max_concurrency,
            max_body_chars=max_body_chars,
            msal_cache_dir=msal_cache_dir,
            msal_cache_encryption_key=msal_cache_encryption_key,
            sleep=sleep,
        )
    raise TypeError("unsupported email account configuration")


build_connector = create_connector


def create_connectors(
    accounts: Sequence[EmailAccount],
    client: httpx.AsyncClient | None = None,
    *,
    max_body_chars: int,
    max_concurrency: int,
    msal_cache_dir: Path = Path("./var/msal-cache"),
    msal_cache_encryption_key: bytes | None = None,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[EmailConnector]:
    """Build one connector per account with application-wide safety bounds."""

    account_ids = [account.id for account in accounts]
    if len(account_ids) != len(set(account_ids)):
        raise ValueError("email account IDs must be unique")
    return [
        create_connector(
            account,
            client,
            retry_policy=retry_policy,
            max_concurrency=max_concurrency,
            max_body_chars=max_body_chars,
            msal_cache_dir=msal_cache_dir,
            msal_cache_encryption_key=msal_cache_encryption_key,
            sleep=sleep,
        )
        for account in accounts
    ]


__all__ = ["build_connector", "create_connector", "create_connectors"]
