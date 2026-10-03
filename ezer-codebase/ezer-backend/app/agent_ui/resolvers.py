"""Résolveurs de données : la seule source des données affichées par les composants."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.agent_ui import places
from app.agent_ui.cache import ResolverCache
from app.agent_ui.contracts import MAIL_PAGE_SCHEMA, DataResolverContext
from app.agent_ui.errors import AgentUiError, agent_ui_error
from app.agent_ui.schema import JsonSchema, SchemaValidationError, assert_schema, is_number
from app.agent_ui.telemetry import emit_agent_ui_event
from app.config import Settings
from app.domain.models import Account
from app.mail.errors import MailConnectionError
from app.mail.graph_mail import GraphMail, MessageHeader
from app.persistence.json_store import JsonPersistence
from app.services.http_fetch import HttpFetch
from app.services.timeutil import now_iso

RESOLVER_TIMEOUT_S = 12.0
MAX_PAGE = 25


@dataclass
class DataResolverDefinition:
    id: str
    input_schema: JsonSchema
    output_schema: JsonSchema
    required_permissions: list[str]
    cache_ttl_ms: float
    execute: Callable[[dict[str, Any], DataResolverContext], Awaitable[Any]]


PAGE_INPUT: JsonSchema = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {"type": "string", "maxLength": 200},
        "limit": {"type": "integer", "minimum": 1, "maximum": 25},
        "offset": {"type": "integer", "minimum": 0, "maximum": 1_000_000},
        "unread_only": {"type": "boolean"},
    },
}

PLACE_SCHEMA: JsonSchema = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "lon", "lat"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "lon": {"type": "number", "minimum": -180, "maximum": 180},
        "lat": {"type": "number", "minimum": -90, "maximum": 90},
    },
}

ROUTE_SCHEMA: JsonSchema = {
    "type": "object",
    "additionalProperties": False,
    "required": ["origin", "destination", "mode", "distanceKm", "durationMin", "coordinates", "estimated"],
    "properties": {
        "origin": PLACE_SCHEMA,
        "destination": PLACE_SCHEMA,
        "mode": {"type": "string", "enum": ["driving", "walking", "cycling"]},
        "distanceKm": {"type": "number", "minimum": 0, "maximum": 40_000},
        "durationMin": {"type": "integer", "minimum": 0, "maximum": 100_000},
        "estimated": {"type": "boolean"},
        "coordinates": {
            "type": "array",
            "minItems": 2,
            "maxItems": 500,
            "items": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": {"type": "number", "minimum": -180, "maximum": 180},
            },
        },
    },
}

# Lieux de repli, utilisés sans clé de géocodage. Le jeu est volontairement court : il sert la
# démonstration, pas la production.
KNOWN_PLACES: list[tuple[re.Pattern[str], places.Place]] = [
    (
        re.compile(r"le rival", re.I),
        {"name": "Le Rival, 1 Rue Rambuteau, 75004 Paris", "lon": 2.3532, "lat": 48.8606},
    ),
    (
        re.compile(r"ch[aâ]telet|h[oô]tel de ville", re.I),
        {"name": "Châtelet, Paris", "lon": 2.3473, "lat": 48.8582},
    ),
    (re.compile(r"gare de lyon", re.I), {"name": "Gare de Lyon, Paris", "lon": 2.3736, "lat": 48.8443}),
    (re.compile(r"gare du nord", re.I), {"name": "Gare du Nord, Paris", "lon": 2.3554, "lat": 48.8809}),
    (
        re.compile(r"montmartre|sacr[ée].?c[oœ]ur", re.I),
        {"name": "Montmartre, Paris", "lon": 2.3431, "lat": 48.8867},
    ),
    (re.compile(r"la d[ée]fense", re.I), {"name": "La Défense, Puteaux", "lon": 2.238, "lat": 48.8919}),
    (re.compile(r"paris", re.I), {"name": "Paris, France", "lon": 2.3522, "lat": 48.8566}),
]

_INVOICE = re.compile(r"facture|invoice|reçu|paiement|billing")


def known_place(query: str) -> places.Place | None:
    for pattern, place in KNOWN_PLACES:
        if pattern.search(query):
            return place
    return None


def _route_to_output(computed: places.Route) -> dict[str, Any]:
    return {
        "origin": dict(computed.origin),
        "destination": dict(computed.destination),
        "mode": computed.mode,
        "distanceKm": computed.distance_km,
        "durationMin": computed.duration_min,
        "coordinates": computed.coordinates,
    }


def _page_bounds(data: dict[str, Any]) -> tuple[int, int]:
    limit = data["limit"] if is_number(data.get("limit")) else 20
    offset = data["offset"] if is_number(data.get("offset")) else 0
    return min(max(limit, 1), MAX_PAGE), min(max(offset, 0), 1_000_000)


def _analysis_to_row(analysis: dict[str, Any]) -> dict[str, Any]:
    parts = analysis["message_ref"].split(":")
    key_points = analysis["key_points"]
    return {
        "id": parts[2] if len(parts) > 2 else analysis["analysis_id"],
        "sender": analysis["summary"][:80],
        "senderAddress": "",
        "subject": analysis["summary"][:400],
        "snippet": key_points[0] if key_points else analysis["summary"][:200],
        "receivedAt": analysis["created_at"],
        "unread": analysis["needs_human_review"],
        "hasAttachments": False,
        "tag": analysis["category"],
    }


def _header_to_row(header: MessageHeader, tag: str | None = None) -> dict[str, Any]:
    row = {
        "id": header["message_id"],
        "sender": header["sender_name"] or header["sender_address"],
        "senderAddress": header["sender_address"],
        "snippet": header["snippet"],
        "subject": header["subject"],
        "receivedAt": header["received_at"],
        "unread": header["is_read"] is False,
        "hasAttachments": header["has_attachments"],
    }
    if tag is not None:
        row["tag"] = tag
    return row


def _looks_like_invoice(analysis: dict[str, Any]) -> bool:
    if analysis["category"] == "receipt":
        return True
    haystack = f"{analysis['summary']} {' '.join(analysis['key_points'])}".lower()
    return _INVOICE.search(haystack) is not None


def _haystack(analysis: dict[str, Any]) -> str:
    return f"{analysis['summary']} {' '.join(analysis['key_points'])}".lower()


class DataResolverRegistry:
    def __init__(
        self,
        persistence: JsonPersistence,
        graph: GraphMail,
        settings: Settings,
        cache: ResolverCache,
        http_fetch: HttpFetch,
    ) -> None:
        self._persistence = persistence
        self._graph = graph
        self._settings = settings
        self._cache = cache
        self._fetch = http_fetch
        self._in_flight: dict[str, asyncio.Future[Any]] = {}
        definitions = [
            self._messages_search(),
            self._invoices_search(),
            self._messages_get(),
            self._mailbox_stats(),
            self._metrics_receipts(),
            self._senders_tally(),
            self._analyses_triage(),
            self._places_route(),
        ]
        self._resolvers = {definition.id: definition for definition in definitions}

    def get(self, resolver_id: str) -> DataResolverDefinition | None:
        return self._resolvers.get(resolver_id)

    async def execute(
        self, resolver_id: str, data: dict[str, Any], context: DataResolverContext, component_id: str
    ) -> Any:
        resolver = self._resolvers.get(resolver_id)
        if resolver is None:
            raise agent_ui_error("unknown_resolver", 400)
        if not all(permission in context.permissions for permission in resolver.required_permissions):
            emit_agent_ui_event(
                event="data_resolver_failed",
                traceId=context.trace_id,
                resolverId=resolver_id,
                componentId=component_id,
                status="denied",
                code="permission_denied",
            )
            raise agent_ui_error("permission_denied", 403)
        try:
            assert_schema(data, resolver.input_schema)
        except SchemaValidationError as error:
            raise agent_ui_error("invalid_payload", 400) from error
        cache_key = (
            f"{context.workspace_id}:{resolver_id}:{json.dumps(data, ensure_ascii=False, separators=(',', ':'))}"
        )
        cached = self._cache.get(cache_key, context.workspace_id)
        if cached is not None:
            return cached

        started = asyncio.get_running_loop().time()
        emit_agent_ui_event(
            event="data_resolver_started",
            traceId=context.trace_id,
            resolverId=resolver_id,
            componentId=component_id,
            instanceId=context.trace_id,
            status="ok",
        )

        pending = self._in_flight.get(cache_key)
        if pending is not None:
            return await pending

        run = asyncio.ensure_future(self._with_timeout(lambda: resolver.execute(data, context)))
        self._in_flight[cache_key] = run
        try:
            output = await run
            assert_schema(output, resolver.output_schema)
            self._cache.set(cache_key, context.workspace_id, output, resolver.cache_ttl_ms)
            emit_agent_ui_event(
                event="data_resolver_succeeded",
                traceId=context.trace_id,
                resolverId=resolver_id,
                componentId=component_id,
                durationMs=round((asyncio.get_running_loop().time() - started) * 1000),
                status="ok",
            )
            return output
        except AgentUiError:
            raise
        except SchemaValidationError as error:
            raise agent_ui_error("resolver_failed", 500) from error
        except Exception as error:
            emit_agent_ui_event(
                event="data_resolver_failed",
                traceId=context.trace_id,
                resolverId=resolver_id,
                componentId=component_id,
                durationMs=round((asyncio.get_running_loop().time() - started) * 1000),
                status="error",
                code=type(error).__name__,
            )
            if isinstance(error, MailConnectionError):
                raise agent_ui_error("resolver_failed", 422) from error
            raise
        finally:
            self._in_flight.pop(cache_key, None)

    @staticmethod
    async def _with_timeout(operation: Callable[[], Awaitable[Any]]) -> Any:
        timeout = asyncio.timeout(RESOLVER_TIMEOUT_S)
        try:
            async with timeout:
                return await operation()
        except TimeoutError:
            if timeout.expired():
                raise agent_ui_error("timeout", 504) from None
            raise

    async def _account(self, workspace_id: str) -> Account:
        for raw in await self._persistence.list_accounts():
            if raw["id"] == workspace_id:
                return Account.model_validate(raw)
        raise agent_ui_error("workspace_mismatch", 403)

    async def _scoped_analyses(self, workspace_id: str) -> list[dict[str, Any]]:
        page = await self._persistence.list_analyses(100, 0, {"account_id": workspace_id})
        return page["items"]

    def _may_fall_back(self, error: Exception) -> bool:
        """En démonstration, toute panne amont retombe sur les analyses locales ; sinon, seule une
        boîte non connectée le fait."""
        return isinstance(error, MailConnectionError) or self._settings.mode == "demo"

    async def _page_from_analyses(
        self, workspace_id: str, limit: int, offset: int, predicate: Callable[[dict[str, Any]], bool]
    ) -> dict[str, Any]:
        matching = [a for a in await self._scoped_analyses(workspace_id) if predicate(a)]
        return {
            "items": [_analysis_to_row(a) for a in matching[offset : offset + limit]],
            "total": len(matching),
            "limit": limit,
            "offset": offset,
        }

    def _places_route(self) -> DataResolverDefinition:
        async def execute(data: dict[str, Any], _context: DataResolverContext) -> dict[str, Any]:
            mode = places.travel_mode(data.get("mode"))
            destination_query = str(data["to"])[:200]
            origin_input = data.get("from")
            origin_query = origin_input[:200] if isinstance(origin_input, str) else self._settings.default_origin
            providers = places.MapProviders(
                maptiler_key=self._settings.maptiler_key,
                routing_url=self._settings.routing_url,
                nominatim_url=self._settings.nominatim_url,
                contact=self._settings.map_contact,
            )
            origin, destination = await asyncio.gather(
                self._locate(origin_query, providers), self._locate(destination_query, providers)
            )
            return await self._trace(origin, destination, mode, providers)

        return DataResolverDefinition(
            id="places.route",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["to"],
                "properties": {
                    "to": {"type": "string", "minLength": 1, "maxLength": 200},
                    "from": {"type": "string", "minLength": 1, "maxLength": 200},
                    "mode": {"type": "string", "enum": ["driving", "walking", "cycling"]},
                },
            },
            output_schema=ROUTE_SCHEMA,
            required_permissions=["mail.read"],
            cache_ttl_ms=60_000,
            execute=execute,
        )

    async def _locate(self, query: str, providers: places.MapProviders) -> places.Place:
        known = known_place(query)
        try:
            found = await places.geocode(self._fetch, query, providers)
            if found is not None:
                return found
        except Exception:
            # Fournisseur injoignable : on retombe sur les lieux connus plutôt que d'échouer sèchement.
            pass
        if known is None:
            raise agent_ui_error("resolver_failed", 422, "place_not_found")
        return known

    async def _trace(
        self,
        origin: places.Place,
        destination: places.Place,
        mode: places.TravelMode,
        providers: places.MapProviders,
    ) -> dict[str, Any]:
        try:
            computed = await places.route(self._fetch, origin, destination, mode, providers)
            if computed is not None:
                return {**_route_to_output(computed), "estimated": False}
        except Exception:
            # Service de routage indisponible : l'estimation vaut mieux qu'une carte vide.
            pass
        return {**_route_to_output(places.straight_line(origin, destination, mode)), "estimated": True}

    def _messages_search(self) -> DataResolverDefinition:
        async def execute(data: dict[str, Any], context: DataResolverContext) -> dict[str, Any]:
            limit, offset = _page_bounds(data)
            query = data["query"] if isinstance(data.get("query"), str) else None
            account = await self._account(context.workspace_id)
            try:
                top = min(limit + offset, MAX_PAGE)
                if query is None:
                    headers = await self._graph.list_messages(
                        account, top=top, unread_only=data.get("unread_only") is True
                    )
                else:
                    headers = await self._graph.search(account, query, top)
                return {
                    "items": [_header_to_row(h) for h in headers[offset : offset + limit]],
                    "total": len(headers),
                    "limit": limit,
                    "offset": offset,
                }
            except Exception as error:
                if not self._may_fall_back(error):
                    raise
                return await self._page_from_analyses(
                    context.workspace_id,
                    limit,
                    offset,
                    lambda analysis: query is None or query.lower() in _haystack(analysis),
                )

        return DataResolverDefinition("messages.search", PAGE_INPUT, MAIL_PAGE_SCHEMA, ["mail.read"], 15_000, execute)

    def _invoices_search(self) -> DataResolverDefinition:
        async def execute(data: dict[str, Any], context: DataResolverContext) -> dict[str, Any]:
            limit, offset = _page_bounds(data)
            query = data["query"] if isinstance(data.get("query"), str) else "facture"
            account = await self._account(context.workspace_id)
            try:
                headers = await self._graph.search(account, query, min(limit + offset, MAX_PAGE))
                return {
                    "items": [_header_to_row(h, "finances") for h in headers[offset : offset + limit]],
                    "total": len(headers),
                    "limit": limit,
                    "offset": offset,
                }
            except Exception as error:
                if not self._may_fall_back(error):
                    raise
                return await self._page_from_analyses(
                    context.workspace_id,
                    limit,
                    offset,
                    _looks_like_invoice,
                )

        return DataResolverDefinition("invoices.search", PAGE_INPUT, MAIL_PAGE_SCHEMA, ["mail.read"], 15_000, execute)

    def _messages_get(self) -> DataResolverDefinition:
        async def execute(data: dict[str, Any], context: DataResolverContext) -> dict[str, Any]:
            message_id = str(data["message_id"])
            account = await self._account(context.workspace_id)
            try:
                message = await self._graph.get_message(account, message_id)
                return {
                    "id": message["message_id"],
                    "subject": message["subject"],
                    "sender": message["sender_name"] or message["sender_address"],
                    "senderAddress": message["sender_address"],
                    "receivedAt": message["received_at"],
                    "body": message["body_text"],
                }
            except Exception as error:
                if not self._may_fall_back(error):
                    raise
                analyses = await self._scoped_analyses(context.workspace_id)
                match = next(
                    (
                        a
                        for a in analyses
                        if [*a["message_ref"].split(":"), "", "", ""][2] == message_id or a["analysis_id"] == message_id
                    ),
                    None,
                )
                if match is None:
                    raise agent_ui_error("resolver_failed", 404) from error
                return {
                    "id": message_id,
                    "subject": match["summary"][:400],
                    "sender": "expéditeur",
                    "senderAddress": "",
                    "receivedAt": match["created_at"],
                    "body": "\n".join([match["summary"], *match["key_points"]]),
                }

        return DataResolverDefinition(
            "messages.get",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["message_id"],
                "properties": {"message_id": {"type": "string", "minLength": 1, "maxLength": 512}},
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "subject", "sender", "receivedAt", "body"],
                "properties": {
                    "id": {"type": "string", "maxLength": 512},
                    "subject": {"type": "string", "maxLength": 400},
                    "sender": {"type": "string", "maxLength": 320},
                    "senderAddress": {"type": "string", "maxLength": 320},
                    "receivedAt": {"type": "string", "maxLength": 64},
                    "body": {"type": "string", "maxLength": 20_000},
                },
            },
            ["mail.read"],
            30_000,
            execute,
        )

    def _mailbox_stats(self) -> DataResolverDefinition:
        async def execute(_data: dict[str, Any], context: DataResolverContext) -> dict[str, Any]:
            account = await self._account(context.workspace_id)
            try:
                stats = await self._graph.stats(account)
                return {
                    "value": str(stats["unread_messages"]),
                    "label": "non lus",
                    "hint": f"{stats['total_messages']} messages",
                    "title": stats["mailbox"],
                    "body": f"{stats['unread_messages']} non lus sur {stats['total_messages']} messages.",
                    "when": now_iso()[:10],
                }
            except Exception as error:
                if not self._may_fall_back(error):
                    raise
                analyses = await self._scoped_analyses(context.workspace_id)
                return {
                    "value": str(len(analyses)),
                    "label": "analyses",
                    "hint": "volume dans ce workspace",
                    "title": account.mailbox or account.id,
                    "body": f"{len(analyses)} analyses disponibles dans cette boîte.",
                    "when": now_iso()[:10],
                }

        return DataResolverDefinition(
            "mailbox.stats",
            {"type": "object", "additionalProperties": False, "properties": {}},
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["value", "label"],
                "properties": {
                    "value": {"type": "string", "maxLength": 32},
                    "label": {"type": "string", "maxLength": 80},
                    "hint": {"type": "string", "maxLength": 160},
                    "series": {"type": "array", "maxItems": 31, "items": {"type": "number"}},
                    "title": {"type": "string", "maxLength": 80},
                    "body": {"type": "string", "maxLength": 600},
                    "when": {"type": "string", "maxLength": 40},
                },
            },
            ["mail.read"],
            20_000,
            execute,
        )

    def _metrics_receipts(self) -> DataResolverDefinition:
        async def execute(_data: dict[str, Any], context: DataResolverContext) -> dict[str, Any]:
            month = now_iso()[:7]
            analyses = [
                a
                for a in await self._scoped_analyses(context.workspace_id)
                if _looks_like_invoice(a) and a["created_at"].startswith(month)
            ]
            return {
                "value": str(len(analyses)),
                "label": "reçus ce mois",
                "hint": "volume de factures et reçus détectés, pas un montant extrait",
                "series": [1 for _ in analyses],
            }

        return DataResolverDefinition(
            "metrics.receipts",
            {"type": "object", "additionalProperties": False, "properties": {}},
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["value", "label"],
                "properties": {
                    "value": {"type": "string", "maxLength": 32},
                    "label": {"type": "string", "maxLength": 80},
                    "hint": {"type": "string", "maxLength": 160},
                    "series": {"type": "array", "maxItems": 31, "items": {"type": "number"}},
                },
            },
            ["mail.read"],
            20_000,
            execute,
        )

    def _senders_tally(self) -> DataResolverDefinition:
        async def execute(data: dict[str, Any], context: DataResolverContext) -> dict[str, Any]:
            sample = data["sample"] if is_number(data.get("sample")) else 25
            account = await self._account(context.workspace_id)
            try:
                senders = await self._graph.tallies_by_sender(account, sample)
                return {
                    "items": [
                        {
                            "sender": s["sender_name"] or s["sender_address"],
                            "senderAddress": s["sender_address"],
                            "total": s["total"],
                            "unread": s["unread"],
                        }
                        for s in senders
                    ]
                }
            except Exception as error:
                if not self._may_fall_back(error):
                    raise
                return {"items": []}

        return DataResolverDefinition(
            "senders.tally",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"sample": {"type": "integer", "minimum": 1, "maximum": 25}},
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["items"],
                "properties": {
                    "items": {
                        "type": "array",
                        "maxItems": 25,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["sender", "senderAddress", "total", "unread"],
                            "properties": {
                                "sender": {"type": "string", "maxLength": 320},
                                "senderAddress": {"type": "string", "maxLength": 320},
                                "total": {"type": "integer", "minimum": 0},
                                "unread": {"type": "integer", "minimum": 0},
                            },
                        },
                    }
                },
            },
            ["mail.read"],
            20_000,
            execute,
        )

    def _analyses_triage(self) -> DataResolverDefinition:
        async def execute(data: dict[str, Any], context: DataResolverContext) -> dict[str, Any]:
            limit, offset = _page_bounds(data)
            return await self._page_from_analyses(context.workspace_id, limit, offset, lambda _a: True)

        return DataResolverDefinition("analyses.triage", PAGE_INPUT, MAIL_PAGE_SCHEMA, ["mail.read"], 15_000, execute)
