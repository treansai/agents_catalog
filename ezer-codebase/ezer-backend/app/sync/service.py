from __future__ import annotations

import asyncio
from typing import Any

from app.analysis.analyzer import MessageAnalyzer
from app.config import Settings
from app.domain.models import Account, SyncReport
from app.errors import HttpError
from app.persistence.json_store import JsonPersistence
from app.sync.registry import ConnectorRegistry


class SyncService:
    def __init__(
        self,
        settings: Settings,
        persistence: JsonPersistence,
        connectors: ConnectorRegistry,
        analyzer: MessageAnalyzer,
    ) -> None:
        self._settings = settings
        self._persistence = persistence
        self._connectors = connectors
        self._analyzer = analyzer

    async def sync(self, account_ids: list[str] | None = None, requested_limit: int | None = None) -> list[dict]:
        limit = self._settings.sync_default_limit if requested_limit is None else requested_limit
        if limit < 1 or limit > self._settings.sync_max_limit:
            raise HttpError(422)
        accounts = await self._select_accounts(account_ids)
        reports = await asyncio.gather(*(self._sync_account(account, limit) for account in accounts))
        return [report.model_dump(mode="json") for report in reports]

    async def _select_accounts(self, account_ids: list[str] | None) -> list[Account]:
        accounts = [Account.model_validate(raw) for raw in await self._persistence.list_accounts()]
        if account_ids is None:
            return accounts
        by_id = {account.id: account for account in accounts}
        selected: list[Account] = []
        for account_id in account_ids:
            account = by_id.get(account_id)
            if account is None:
                raise HttpError(422)
            selected.append(account)
        return selected

    async def _sync_account(self, account: Account, limit: int) -> SyncReport:
        connector = await self._connectors.connector_for(account)
        cursor = await self._persistence.get_cursor(account.id)
        batch = await connector.fetch(cursor, limit)
        analyses: list[dict[str, Any]] = []
        failed = 0
        for message in batch.messages:
            if message.account_id != account.id or message.provider != account.provider:
                failed += 1
                continue
            try:
                analyses.append((await self._analyzer.analyse(message)).model_dump(mode="json"))
            except Exception:
                failed += 1
        saved = await self._persistence.save_analyses(analyses)
        cursor_advanced = (
            await self._persistence.set_cursor(account.id, cursor, batch.next_cursor) if failed == 0 else False
        )
        return SyncReport(
            account_id=account.id,
            provider=account.provider,
            fetched=len(batch.messages),
            processed=len(saved["inserted"]),
            skipped=saved["skipped"],
            failed=failed,
            dead_lettered=0,
            cursor_advanced=cursor_advanced,
            cursor_reset=batch.cursor_reset,
            analyses=saved["inserted"],
        )
