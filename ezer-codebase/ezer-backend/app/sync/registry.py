"""Choix du connecteur d'un compte : Microsoft Graph si la boîte est connectée, sinon le catalogue."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from pydantic import ValidationError

from app.config import Settings
from app.demo.data import DEMO_MESSAGES
from app.domain.models import PROVIDERS, Account, MailMessage
from app.mail.graph_connector import GraphMailConnector
from app.mail.outlook_auth import OutlookAuth
from app.mail.token_store import TokenStore
from app.services.http_fetch import HttpFetch
from app.services.timeutil import parse_instant, to_iso
from app.sync.catalog_connector import CatalogConnector
from app.sync.connector import MailConnector

_ACCOUNT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_ALLOWED_FIELDS = frozenset(
    {
        "account_id",
        "provider",
        "provider_message_id",
        "thread_id",
        "subject",
        "sender_name",
        "sender_address",
        "received_at",
        "body_text",
        "snippet",
    }
)


def _optional_string(value: dict[str, Any], field: str, maximum: int, fallback: str = "") -> str:
    candidate = value.get(field, fallback) if field in value else fallback
    if not isinstance(candidate, str) or len(candidate) > maximum:
        raise ValueError(f"message field {field} is invalid")
    return candidate


def parse_message(value: Any, index: int) -> MailMessage:
    if not isinstance(value, dict):
        raise ValueError(f"source message {index} must be an object")
    if any(key not in _ALLOWED_FIELDS for key in value):
        raise ValueError(f"source message {index} has unknown fields")
    account_id = value.get("account_id")
    if not isinstance(account_id, str) or not _ACCOUNT_ID.fullmatch(account_id):
        raise ValueError(f"source message {index} has an invalid account_id")
    provider = value.get("provider")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError(f"source message {index} has an invalid provider")
    message_id = value.get("provider_message_id")
    if not isinstance(message_id, str) or len(message_id) < 1 or len(message_id) > 1024:
        raise ValueError(f"source message {index} has an invalid provider_message_id")
    received = value.get("received_at")
    if not isinstance(received, str) or parse_instant(received) is None:
        raise ValueError(f"source message {index} has an invalid received_at")
    thread = value.get("thread_id")
    if thread is not None and (not isinstance(thread, str) or len(thread) > 1024):
        raise ValueError(f"source message {index} has an invalid thread_id")
    try:
        return MailMessage(
            account_id=account_id,
            provider=provider,  # type: ignore[arg-type]
            provider_message_id=message_id,
            thread_id=thread,
            subject=_optional_string(value, "subject", 998),
            sender_name=_optional_string(value, "sender_name", 320),
            sender_address=_optional_string(value, "sender_address", 320),
            received_at=to_iso(received),
            body_text=_optional_string(value, "body_text", 500_000),
            snippet=_optional_string(value, "snippet", 2_000),
        )
    except ValidationError as error:  # pragma: no cover - garde-fou
        raise ValueError(f"source message {index} is invalid") from error


def _reject_constant(name: str) -> Any:
    raise ValueError(f"invalid JSON constant {name}")


class ConnectorRegistry:
    def __init__(
        self, settings: Settings, tokens: TokenStore, outlook_auth: OutlookAuth, http_fetch: HttpFetch
    ) -> None:
        self._settings = settings
        self._tokens = tokens
        self._auth = outlook_auth
        self._fetch = http_fetch

    async def connector_for(self, account: Account) -> MailConnector:
        """Un compte Outlook réellement connecté est lu depuis Microsoft Graph.

        Les autres comptes conservent le connecteur catalogue, qui alimente la démonstration et les tests.
        """
        if account.provider == "outlook" and account.mailbox is not None:
            stored = await self._tokens.get(account.id)
            if stored is not None:
                return GraphMailConnector(account, lambda: self._auth.access_token_for(account), self._fetch)
        return CatalogConnector(account, self._load_catalog)

    async def _load_catalog(self) -> list[MailMessage]:
        settings = self._settings
        if settings.mode == "demo":
            return [message.model_copy(deep=True) for message in DEMO_MESSAGES]
        if settings.source_file is None:
            return []
        raw = await asyncio.to_thread(settings.source_file.read_text, "utf-8")
        try:
            parsed = json.loads(raw, parse_constant=_reject_constant)
        except ValueError as error:
            raise ValueError("EZER_SOURCE_FILE is not valid JSON") from error
        values = parsed.get("messages") if isinstance(parsed, dict) else parsed
        if not isinstance(values, list) or len(values) > 10_000:
            raise ValueError("EZER_SOURCE_FILE must contain at most 10000 messages")
        return [parse_message(value, index) for index, value in enumerate(values)]
