from __future__ import annotations

from typing import Protocol

from app.domain.models import FetchBatch, Provider


class MailConnector(Protocol):
    account_id: str
    provider: Provider

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch: ...
