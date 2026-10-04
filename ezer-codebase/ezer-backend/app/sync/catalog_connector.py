from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from app.domain.models import Account, FetchBatch, MailMessage

MessageLoader = Callable[[], Awaitable[list[MailMessage]]]
_DIGITS = re.compile(r"[0-9]+")


class CatalogConnector:
    """Connecteur « catalogue » : alimente la démonstration et le mode configuré sans boîte réelle."""

    def __init__(self, account: Account, load_messages: MessageLoader) -> None:
        self.account_id = account.id
        self.provider = account.provider
        self._load = load_messages

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        available = [
            message
            for message in await self._load()
            if message.account_id == self.account_id and message.provider == self.provider
        ]
        start = 0
        cursor_reset = False
        if cursor is not None:
            if _DIGITS.fullmatch(cursor) and int(cursor) <= len(available):
                start = int(cursor)
            else:
                cursor_reset = True
        end = min(start + limit, len(available))
        return FetchBatch(
            messages=[message.model_copy(deep=True) for message in available[start:end]],
            next_cursor=str(end),
            cursor_reset=cursor_reset,
        )
