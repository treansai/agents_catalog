"""Application runtime assembly and deterministic resource lifecycle."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import httpx
from langgraph.checkpoint.base import BaseCheckpointSaver

from ezer.agents import AgentRuntime, create_agent_runtime
from ezer.checkpoints import open_checkpointer
from ezer.config import EmailAccount, Settings
from ezer.connectors.factory import create_connector
from ezer.domain import FetchBatch, SyncReport
from ezer.graph import EmailAnalysisGraph, build_email_analysis_graph
from ezer.observability import Metrics, configure_logging
from ezer.persistence import Persistence, persistence_from_settings
from ezer.service import EmailConnector, EmailSyncService, sync_accounts

RuntimeRole = Literal["api", "worker", "sync"]
PersistenceFactory = Callable[[Settings], Persistence]
AgentFactory = Callable[[Settings], AgentRuntime]
MetricsFactory = Callable[[], Metrics]


class ConnectorFactory(Protocol):
    def __call__(
        self,
        account: EmailAccount,
        client: httpx.AsyncClient,
        *,
        max_body_chars: int,
        max_concurrency: int,
        msal_cache_dir: Path,
        msal_cache_encryption_key: bytes | None,
    ) -> EmailConnector: ...


class UnknownAccountError(ValueError):
    """Raised when a caller selects an account that is not configured."""


class SyncLimitError(ValueError):
    """Raised when a per-run page limit exceeds the configured safety bound."""


@dataclass(frozen=True, slots=True)
class _LimitedConnector:
    """Apply a request-local page limit without mutating global settings."""

    connector: EmailConnector
    limit: int

    @property
    def account_id(self) -> str:
        return self.connector.account_id

    @property
    def provider(self) -> Literal["gmail", "outlook"]:
        return self.connector.provider

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        return await self.connector.fetch(cursor, min(limit, self.limit))


@dataclass(slots=True)
class Runtime:
    """Initialized dependencies shared for the lifetime of one process role."""

    role: RuntimeRole
    settings: Settings
    accounts: tuple[EmailAccount, ...]
    connectors: dict[str, EmailConnector]
    http_client: httpx.AsyncClient
    persistence: Persistence
    checkpointer: BaseCheckpointSaver[Any]
    graph: EmailAnalysisGraph
    agents: AgentRuntime
    metrics: Metrics
    service: EmailSyncService
    # Assistant multi-agents, construit à la première demande de l'API.
    assistant: object | None = None

    def select_accounts(self, account_ids: Sequence[str] | None = None) -> tuple[EmailAccount, ...]:
        """Resolve a stable, de-duplicated subset without revealing unknown IDs."""

        if account_ids is None:
            return self.accounts
        selected_ids = tuple(dict.fromkeys(account_ids))
        if not selected_ids:
            raise UnknownAccountError("at least one account ID is required")
        by_id = {account.id: account for account in self.accounts}
        if any(account_id not in by_id for account_id in selected_ids):
            raise UnknownAccountError("one or more account IDs are not configured")
        return tuple(by_id[account_id] for account_id in selected_ids)

    async def sync(
        self,
        *,
        account_ids: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[SyncReport]:
        """Synchronize one page for selected accounts within the configured limit."""

        effective_limit = self.settings.sync_page_size if limit is None else limit
        if not 1 <= effective_limit <= self.settings.sync_page_size:
            raise SyncLimitError(f"limit must be between 1 and {self.settings.sync_page_size}")
        accounts = self.select_accounts(account_ids)
        connectors: list[EmailConnector] = []
        for account in accounts:
            connector = self.connectors[account.id]
            if effective_limit != self.settings.sync_page_size:
                connector = _LimitedConnector(connector=connector, limit=effective_limit)
            connectors.append(connector)
        return await sync_accounts(self.service, accounts, connectors)


async def _close_connector(connector: EmailConnector) -> None:
    close = getattr(connector, "aclose", None)
    if close is None:
        return
    result = close()
    if isinstance(result, Awaitable):
        await result


@asynccontextmanager
async def bootstrap(
    role: RuntimeRole,
    *,
    settings: Settings | None = None,
    connector_factory: ConnectorFactory | None = None,
    persistence_factory: PersistenceFactory | None = None,
    agent_factory: AgentFactory | None = None,
    metrics_factory: MetricsFactory = Metrics,
) -> AsyncGenerator[Runtime]:
    """Validate configuration, open resources, and yield an application runtime.

    Resources are registered as they are created, so partial startup failures still close
    connectors, the checkpointer, persistence, and the shared HTTP client in a safe order.
    """

    resolved_settings = settings or Settings()
    resolved_settings.validate_for_role(role)
    accounts = tuple(resolved_settings.load_accounts())
    configure_logging(resolved_settings.log_level)

    make_connector = connector_factory or cast(ConnectorFactory, create_connector)
    make_persistence = persistence_factory or persistence_from_settings
    make_agents = agent_factory or create_agent_runtime

    limits = httpx.Limits(
        max_connections=max(20, resolved_settings.provider_concurrency * len(accounts) * 2),
        max_keepalive_connections=max(10, resolved_settings.provider_concurrency * len(accounts)),
        keepalive_expiry=30.0,
    )
    timeout = httpx.Timeout(30.0, connect=10.0, pool=10.0)

    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=False)
        )
        persistence = make_persistence(resolved_settings)
        stack.push_async_callback(persistence.close)
        await persistence.open()

        checkpointer = await stack.enter_async_context(open_checkpointer(resolved_settings))
        agents = make_agents(resolved_settings)
        graph = build_email_analysis_graph(
            checkpointer=checkpointer,
            max_attempts=resolved_settings.llm_max_attempts,
        )
        metrics = metrics_factory()
        service = EmailSyncService(
            settings=resolved_settings,
            persistence=persistence,
            graph=graph,
            agents=agents,
            metrics=metrics,
        )

        connectors: dict[str, EmailConnector] = {}
        msal_cache_encryption_key = (
            resolved_settings.msal_cache_aes_key.get_secret_value().encode("utf-8")
            if resolved_settings.msal_cache_aes_key is not None
            else None
        )
        for account in accounts:
            connector = make_connector(
                account,
                client,
                max_body_chars=resolved_settings.max_body_chars,
                max_concurrency=resolved_settings.provider_concurrency,
                msal_cache_dir=resolved_settings.msal_cache_dir,
                msal_cache_encryption_key=msal_cache_encryption_key,
            )
            if account.id in connectors:
                raise ValueError("email account IDs must be unique")
            connectors[account.id] = connector
            stack.push_async_callback(_close_connector, connector)

        yield Runtime(
            role=role,
            settings=resolved_settings,
            accounts=accounts,
            connectors=connectors,
            http_client=client,
            persistence=persistence,
            checkpointer=checkpointer,
            graph=graph,
            agents=agents,
            metrics=metrics,
            service=service,
        )


__all__ = [
    "Runtime",
    "RuntimeRole",
    "SyncLimitError",
    "UnknownAccountError",
    "bootstrap",
]
