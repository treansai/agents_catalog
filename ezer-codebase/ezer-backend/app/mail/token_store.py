"""Magasin des refresh tokens Microsoft : fichier dédié, atomique, 0600."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from app.config import Settings
from app.persistence.atomic import dump_json, write_atomically
from app.services.timeutil import parse_instant

_ACCOUNT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
MAX_TOKEN_FILE_BYTES = 1024 * 1024

# Jetons délégués d'un compte (account_id, mailbox, refresh_token, connected_at, scopes?).
StoredToken = dict[str, Any]


def _reject_constant(name: str) -> Any:
    raise ValueError(f"invalid JSON constant {name}")


def parse_token_file(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise ValueError("the Ezer token file is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("the Ezer token file is invalid")
    if value.get("schema_version") != 1 or not isinstance(value.get("tokens"), list):
        raise ValueError("the Ezer token file has an unsupported schema")
    tokens: list[StoredToken] = []
    seen: set[str] = set()
    for entry in value["tokens"]:
        if not isinstance(entry, dict):
            raise ValueError("the Ezer token file has an invalid entry")
        account_id = entry.get("account_id")
        mailbox = entry.get("mailbox")
        refresh = entry.get("refresh_token")
        connected = entry.get("connected_at")
        scopes = entry.get("scopes")
        if (
            not isinstance(account_id, str)
            or not _ACCOUNT_ID.fullmatch(account_id)
            or not isinstance(mailbox, str)
            or len(mailbox) > 320
            or not isinstance(refresh, str)
            or len(refresh) == 0
            or len(refresh) > 32_768
            or not isinstance(connected, str)
            or parse_instant(connected) is None
            or ("scopes" in entry and (not isinstance(scopes, str) or len(scopes) > 2_048))
            or account_id in seen
        ):
            raise ValueError("the Ezer token file has an invalid entry")
        seen.add(account_id)
        token: StoredToken = {
            "account_id": account_id,
            "mailbox": mailbox,
            "refresh_token": refresh,
            "connected_at": connected,
        }
        if isinstance(scopes, str):
            token["scopes"] = scopes
        tokens.append(token)
    return {"schema_version": 1, "tokens": tokens}


class TokenStore:
    """Conserve les refresh tokens dans un fichier distinct du fichier de données du tableau de bord."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def get(self, account_id: str) -> StoredToken | None:
        async with self._lock:
            file = await self._load()
            for entry in file["tokens"]:
                if entry["account_id"] == account_id:
                    return dict(entry)
            return None

    async def save(self, token: StoredToken) -> None:
        async with self._lock:
            file = await self._load()
            tokens = [entry for entry in file["tokens"] if entry["account_id"] != token["account_id"]]
            tokens.append(dict(token))
            await self._write({"schema_version": 1, "tokens": tokens})

    async def rotate(self, account_id: str, refresh_token: str) -> None:
        """Remplace le seul refresh token d'un compte lors d'une rotation, sans toucher aux autres."""
        async with self._lock:
            file = await self._load()
            if not any(entry["account_id"] == account_id for entry in file["tokens"]):
                return
            tokens = [
                {**entry, "refresh_token": refresh_token} if entry["account_id"] == account_id else entry
                for entry in file["tokens"]
            ]
            await self._write({"schema_version": 1, "tokens": tokens})

    async def remove(self, account_id: str) -> bool:
        async with self._lock:
            file = await self._load()
            tokens = [entry for entry in file["tokens"] if entry["account_id"] != account_id]
            if len(tokens) == len(file["tokens"]):
                return False
            await self._write({"schema_version": 1, "tokens": tokens})
            return True

    async def _load(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache
        try:
            raw = await asyncio.to_thread(self._settings.token_file.read_text, "utf-8")
            if len(raw) > MAX_TOKEN_FILE_BYTES:
                raise ValueError("the Ezer token file is too large")
            self._cache = parse_token_file(raw)
        except FileNotFoundError:
            self._cache = {"schema_version": 1, "tokens": []}
        return self._cache

    async def _write(self, file: dict[str, Any]) -> None:
        target = Path(self._settings.token_file)
        await asyncio.to_thread(self._write_sync, target, file)
        self._cache = file

    @staticmethod
    def _write_sync(target: Path, file: dict[str, Any]) -> None:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_atomically(target, dump_json(file), sync_directory=False)
