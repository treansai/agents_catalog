"""Persistance JSON : un fichier unique, mutations sérialisées et écrites atomiquement."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from pathlib import Path
from typing import Any

from app.config import Settings
from app.demo.data import create_demo_state
from app.domain.models import EMAIL_CATEGORIES, PRIORITIES, PROVIDERS, RISK_LEVELS
from app.persistence.atomic import dump_json, write_atomically
from app.services.timeutil import parse_instant

_ACCOUNT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_SHA256_HEX = re.compile(r"[a-f0-9]{64}")


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_probability(value: Any) -> bool:
    return _is_number(value) and 0 <= value <= 1


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def assert_analysis(value: Any, index: int) -> None:
    if not _is_record(value):
        raise ValueError(f"analysis {index} must be an object")
    required_strings = (
        "analysis_id",
        "message_ref",
        "content_hash",
        "pipeline_version",
        "model_id",
        "prompt_version",
        "created_at",
        "summary",
        "detected_language",
    )
    if any(not isinstance(value.get(field), str) for field in required_strings):
        raise ValueError(f"analysis {index} has an invalid string field")
    if not _SHA256_HEX.fullmatch(value["analysis_id"]):
        raise ValueError(f"analysis {index} has an invalid id")
    if not _SHA256_HEX.fullmatch(value["content_hash"]):
        raise ValueError(f"analysis {index} has an invalid content hash")
    if parse_instant(value["created_at"]) is None:
        raise ValueError(f"analysis {index} has an invalid date")
    if value.get("category") not in EMAIL_CATEGORIES:
        raise ValueError(f"analysis {index} has an invalid category")
    if value.get("priority") not in PRIORITIES:
        raise ValueError(f"analysis {index} has an invalid priority")
    if not isinstance(value.get("needs_human_review"), bool):
        raise ValueError(f"analysis {index} has an invalid review flag")
    if not _is_string_list(value.get("key_points")) or not isinstance(value.get("action_items"), list):
        raise ValueError(f"analysis {index} has invalid extracted data")
    for action in value["action_items"]:
        if (
            not _is_record(action)
            or not isinstance(action.get("description"), str)
            or (action.get("owner") is not None and not isinstance(action.get("owner"), str))
            or (action.get("due_date") is not None and not isinstance(action.get("due_date"), str))
            or not _is_probability(action.get("confidence"))
        ):
            raise ValueError(f"analysis {index} has an invalid action item")
    safety = value.get("safety")
    if (
        not _is_record(safety)
        or safety.get("risk_level") not in RISK_LEVELS
        or not isinstance(safety.get("prompt_injection_detected"), bool)
        or not _is_probability(safety.get("phishing_likelihood"))
        or not _is_string_list(safety.get("indicators"))
        or not isinstance(safety.get("rationale"), str)
        or not _is_probability(safety.get("confidence"))
    ):
        raise ValueError(f"analysis {index} has an invalid safety assessment")
    triage = value.get("triage")
    if (
        not _is_record(triage)
        or triage.get("category") not in EMAIL_CATEGORIES
        or triage.get("priority") not in PRIORITIES
        or not isinstance(triage.get("needs_human_review"), bool)
        or not _is_probability(triage.get("confidence"))
        or not isinstance(triage.get("rationale"), str)
    ):
        raise ValueError(f"analysis {index} has invalid triage data")


def parse_state(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise ValueError("the Ezer data file is not valid JSON") from error
    if not _is_record(value) or value.get("schema_version") != 1 or isinstance(value.get("schema_version"), bool):
        raise ValueError("the Ezer data file has an unsupported schema")
    if (
        not isinstance(value.get("accounts"), list)
        or not isinstance(value.get("analyses"), list)
        or not _is_record(value.get("cursors"))
    ):
        raise ValueError("the Ezer data file is incomplete")
    account_ids: set[str] = set()
    for index, account in enumerate(value["accounts"]):
        mailbox = account.get("mailbox") if _is_record(account) else None
        if (
            not _is_record(account)
            or not isinstance(account.get("id"), str)
            or not _ACCOUNT_ID.fullmatch(account["id"])
            or not isinstance(account.get("provider"), str)
            or account["provider"] not in PROVIDERS
            or ("mailbox" in account and (not isinstance(mailbox, str) or len(mailbox) > 320))
            or account["id"] in account_ids
        ):
            raise ValueError(f"the Ezer data file has an invalid account at index {index}")
        account_ids.add(account["id"])
    analysis_ids: set[str] = set()
    for index, analysis in enumerate(value["analyses"]):
        assert_analysis(analysis, index)
        if analysis["analysis_id"] in analysis_ids:
            raise ValueError(f"the Ezer data file has a duplicate analysis at index {index}")
        analysis_ids.add(analysis["analysis_id"])
    for account_id, cursor in value["cursors"].items():
        if not _ACCOUNT_ID.fullmatch(account_id) or (cursor is not None and not isinstance(cursor, str)):
            raise ValueError("the Ezer data file has an invalid cursor")
    return value


def _reject_constant(name: str) -> Any:
    raise ValueError(f"invalid JSON constant {name}")


def account_id_from_analysis(analysis: dict[str, Any]) -> str | None:
    parts = analysis["message_ref"].split(":", 3)[:3]
    return parts[1] if len(parts) > 1 else None


class JsonPersistence:
    """État persistant du tableau de bord (comptes, analyses, curseurs)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._state: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        settings = self._settings
        target = settings.data_file
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if settings.mode == "demo" and settings.demo_reset_on_start:
            initial = create_demo_state()
            self._write(initial)
            self._state = initial
            return
        try:
            self._state = parse_state(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if settings.mode == "demo":
                initial = create_demo_state()
            else:
                initial = {
                    "schema_version": 1,
                    "accounts": [account.to_json() for account in settings.accounts],
                    "analyses": [],
                    "cursors": {account.id: None for account in settings.accounts},
                }
            self._write(initial)
            self._state = initial

        current = self._require_state()
        configured = [account.to_json() for account in settings.accounts]
        if json.dumps(current["accounts"]) != json.dumps(configured):
            following = copy.deepcopy(current)
            following["accounts"] = copy.deepcopy(configured)
            for account in settings.accounts:
                following["cursors"].setdefault(account.id, None)
            self._write(following)
            self._state = following

    async def health(self) -> bool:
        async with self._lock:
            if self._state is None:
                return False
            return os.access(self._settings.data_file, os.R_OK | os.W_OK)

    async def list_accounts(self) -> list[dict[str, Any]]:
        async with self._lock:
            return copy.deepcopy(self._require_state()["accounts"])

    async def list_analyses(self, limit: int, offset: int, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        filters = filters or {}
        async with self._lock:
            state = self._require_state()
            configured_ids = {account["id"] for account in state["accounts"]}
            matching: list[dict[str, Any]] = []
            for analysis in state["analyses"]:
                account_id = account_id_from_analysis(analysis)
                if account_id is None or account_id not in configured_ids:
                    continue
                if filters.get("account_id") is not None and account_id != filters["account_id"]:
                    continue
                if filters.get("category") is not None and analysis["category"] != filters["category"]:
                    continue
                if filters.get("priority") is not None and analysis["priority"] != filters["priority"]:
                    continue
                review = filters.get("needs_human_review")
                if review is not None and analysis["needs_human_review"] != review:
                    continue
                matching.append(analysis)
            matching.sort(key=lambda item: (item["created_at"], item["analysis_id"]), reverse=True)
            return {"items": copy.deepcopy(matching[offset : offset + limit]), "total": len(matching)}

    async def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        async with self._lock:
            state = self._require_state()
            for analysis in state["analyses"]:
                if analysis["analysis_id"] != analysis_id:
                    continue
                account_id = account_id_from_analysis(analysis)
                if not any(account["id"] == account_id for account in state["accounts"]):
                    return None
                return copy.deepcopy(analysis)
            return None

    async def save_analyses(self, analyses: list[dict[str, Any]]) -> dict[str, Any]:
        async with self._lock:
            current = self._require_state()
            known = {analysis["analysis_id"] for analysis in current["analyses"]}
            inserted: list[dict[str, Any]] = []
            for analysis in analyses:
                if analysis["analysis_id"] in known:
                    continue
                known.add(analysis["analysis_id"])
                inserted.append(analysis)
            if not inserted:
                return {"inserted": [], "skipped": len(analyses)}
            following = copy.deepcopy(current)
            following["analyses"] = [*copy.deepcopy(inserted), *following["analyses"]]
            await asyncio.to_thread(self._write, following)
            self._state = following
            return {"inserted": copy.deepcopy(inserted), "skipped": len(analyses) - len(inserted)}

    async def get_cursor(self, account_id: str) -> str | None:
        async with self._lock:
            return self._require_state()["cursors"].get(account_id)

    async def set_cursor(self, account_id: str, expected: str | None, next_cursor: str | None) -> bool:
        async with self._lock:
            current = self._require_state()
            actual = current["cursors"].get(account_id)
            if actual != expected or actual == next_cursor:
                return False
            following = copy.deepcopy(current)
            following["cursors"][account_id] = next_cursor
            await asyncio.to_thread(self._write, following)
            self._state = following
            return True

    def _require_state(self) -> dict[str, Any]:
        if self._state is None:
            raise RuntimeError("persistence is not initialized")
        return self._state

    def _write(self, state: dict[str, Any]) -> None:
        write_atomically(Path(self._settings.data_file), dump_json(state), sync_directory=True)
