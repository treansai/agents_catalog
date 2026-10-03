"""Câblage des services (l'équivalent du module d'injection de l'ancien service)."""

from __future__ import annotations

from dataclasses import dataclass

from app.agent_ui.actions import ActionRegistry
from app.agent_ui.cache import IdempotencyStore, ResolverCache
from app.agent_ui.confirmation import ConfirmationStore
from app.agent_ui.resolvers import DataResolverRegistry
from app.analysis.analyzer import MessageAnalyzer
from app.analysis.jev import JevClient
from app.config import Settings
from app.mail.graph_mail import GraphMail
from app.mail.outlook_auth import OutlookAuth, Sleeper, real_sleep
from app.mail.token_store import TokenStore
from app.persistence.json_store import JsonPersistence
from app.services.http_fetch import HttpFetch, HttpxFetch
from app.sync.registry import ConnectorRegistry
from app.sync.service import SyncService


@dataclass
class Services:
    settings: Settings
    http_fetch: HttpFetch
    owns_http_fetch: bool
    persistence: JsonPersistence
    tokens: TokenStore
    outlook_auth: OutlookAuth
    graph_mail: GraphMail
    connectors: ConnectorRegistry
    analyzer: MessageAnalyzer
    sync: SyncService
    resolver_cache: ResolverCache
    idempotency: IdempotencyStore
    confirmations: ConfirmationStore
    resolvers: DataResolverRegistry
    actions: ActionRegistry
    # Accorde la permission `mail.write` sans consentement Outlook (tests uniquement).
    grant_write_permission: bool = False


def build_services(
    settings: Settings,
    *,
    http_fetch: HttpFetch | None = None,
    sleeper: Sleeper = real_sleep,
    jev_client: JevClient | None = None,
    grant_write_permission: bool = False,
) -> Services:
    owns_fetch = http_fetch is None
    fetch: HttpFetch = http_fetch if http_fetch is not None else HttpxFetch()
    persistence = JsonPersistence(settings)
    tokens = TokenStore(settings)
    auth = OutlookAuth(settings, tokens, fetch, sleeper)
    graph = GraphMail(auth, tokens, fetch)
    connectors = ConnectorRegistry(settings, tokens, auth, fetch)
    analyzer = MessageAnalyzer(jev_client, settings.typesafe_api_key)
    sync = SyncService(settings, persistence, connectors, analyzer)
    cache = ResolverCache()
    idempotency = IdempotencyStore()
    confirmations = ConfirmationStore()
    resolvers = DataResolverRegistry(persistence, graph, settings, cache, fetch)
    actions = ActionRegistry(graph, persistence, confirmations, idempotency)
    return Services(
        settings=settings,
        http_fetch=fetch,
        owns_http_fetch=owns_fetch,
        persistence=persistence,
        tokens=tokens,
        outlook_auth=auth,
        graph_mail=graph,
        connectors=connectors,
        analyzer=analyzer,
        sync=sync,
        resolver_cache=cache,
        idempotency=idempotency,
        confirmations=confirmations,
        resolvers=resolvers,
        actions=actions,
        grant_write_permission=grant_write_permission,
    )
