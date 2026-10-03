from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

TTL_MS = 10 * 60_000


@dataclass
class ConfirmationRecord:
    id: str
    token: str
    workspace_id: str
    action_id: str
    target_id: str
    target_label: str
    impact: str
    reversible: bool
    expires_at: float


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _now_ms() -> float:
    return time.time() * 1000


class ConfirmationStore:
    def __init__(self) -> None:
        self._records: dict[str, ConfirmationRecord] = {}

    def issue(
        self,
        *,
        workspace_id: str,
        action_id: str,
        target_id: str,
        target_label: str,
        impact: str,
        reversible: bool,
    ) -> ConfirmationRecord:
        self._gc()
        record = ConfirmationRecord(
            id=f"cnf_{secrets.token_hex(12)}",
            token=secrets.token_urlsafe(24),
            workspace_id=workspace_id,
            action_id=action_id,
            target_id=target_id,
            target_label=target_label,
            impact=impact,
            reversible=reversible,
            expires_at=_now_ms() + TTL_MS,
        )
        self._records[record.id] = record
        return record

    def consume(self, record_id: str, token: str, workspace_id: str, action_id: str) -> ConfirmationRecord | None:
        self._gc()
        record = self._records.get(record_id)
        if record is None:
            return None
        if record.workspace_id != workspace_id or record.action_id != action_id:
            return None
        if record.expires_at < _now_ms():
            del self._records[record_id]
            return None
        if not hmac.compare_digest(_digest(record.token), _digest(token)):
            return None
        del self._records[record_id]
        return record

    def peek(self, record_id: str, workspace_id: str) -> ConfirmationRecord | None:
        record = self._records.get(record_id)
        if record is None or record.workspace_id != workspace_id:
            return None
        return record

    def _gc(self) -> None:
        now = _now_ms()
        for key in [key for key, record in self._records.items() if record.expires_at < now]:
            del self._records[key]
