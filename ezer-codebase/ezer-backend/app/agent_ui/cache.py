from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from app.agent_ui.errors import ConfirmationRequiredError

TTL_MS = 15 * 60_000
MAX_ENTRIES = 512
IDEMPOTENCY_TTL_MS = 5 * 60_000

T = TypeVar("T")


def _now_ms() -> float:
    return time.time() * 1000


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float
    workspace_id: str


class ResolverCache:
    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}

    def get(self, key: str, workspace_id: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.workspace_id != workspace_id or entry.expires_at < _now_ms():
            del self._entries[key]
            return None
        return entry.value

    def set(self, key: str, workspace_id: str, value: Any, ttl_ms: float = TTL_MS) -> None:
        if len(self._entries) >= MAX_ENTRIES:
            del self._entries[next(iter(self._entries))]
        self._entries[key] = _CacheEntry(value=value, workspace_id=workspace_id, expires_at=_now_ms() + ttl_ms)


class IdempotencyStore:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[Any, float]] = {}

    async def remember(self, key: str, produce: Callable[[], Awaitable[T]]) -> T:
        existing = self._entries.get(key)
        if existing is not None and existing[1] >= _now_ms():
            if isinstance(existing[0], BaseException):
                raise existing[0]
            return existing[0]
        try:
            result = await produce()
        except ConfirmationRequiredError as error:
            self._entries[key] = (error, _now_ms() + IDEMPOTENCY_TTL_MS)
            raise
        self._entries[key] = (result, _now_ms() + IDEMPOTENCY_TTL_MS)
        self._purge()
        return result

    def _purge(self) -> None:
        if len(self._entries) < 1024:
            return
        now = _now_ms()
        for key in [key for key, (_, expires) in self._entries.items() if expires < now]:
            del self._entries[key]
