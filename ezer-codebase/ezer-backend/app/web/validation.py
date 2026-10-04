"""Validation stricte des entrées HTTP, équivalente à l'ancien `ValidationPipe` :
champs inconnus refusés, aucune coercition implicite, une seule réponse (400) pour toute anomalie.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from starlette.requests import Request

from app.agent_ui.schema import is_safe_integer
from app.errors import HttpError
from app.web.envelope import BODY_KEY

ACCOUNT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
ANALYSIS_ID = re.compile(r"[a-f0-9]{64}")
MESSAGE_ID = re.compile(r"[A-Za-z0-9_\-=+/]{1,512}")
SENDER_ADDRESS = re.compile(r"[^\s@'\"]{1,64}@[^\s@'\"]{1,255}")
# Date seule ou instant ISO 8601 complet.
ISO_INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:\.[0-9]{1,3})?Z?)?")
TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
DIGITS = re.compile(r"[0-9]+")

QueryValue = str | list[str]


def invalid() -> HttpError:
    return HttpError(400)


def query_params(request: Request) -> dict[str, QueryValue]:
    """Paramètres de requête ; une clé répétée devient une liste (comportement du parseur Express)."""
    result: dict[str, QueryValue] = {}
    for key, value in request.query_params.multi_items():
        existing = result.get(key)
        if existing is None:
            result[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            result[key] = [existing, value]
    return result


def reject_unknown(values: dict[str, Any], allowed: Iterable[str]) -> None:
    allowed_set = set(allowed)
    if any(key not in allowed_set for key in values):
        raise invalid()


def query_integer(
    query: dict[str, QueryValue], key: str, minimum: int, maximum: int, default: int | None = None
) -> int | None:
    if key not in query:
        return default
    raw = query[key]
    if not isinstance(raw, str) or not DIGITS.fullmatch(raw):
        raise invalid()
    parsed = int(raw)
    if parsed < minimum or parsed > maximum:
        raise invalid()
    return parsed


def query_boolean(query: dict[str, QueryValue], key: str) -> bool | None:
    if key not in query:
        return None
    raw = query[key]
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise invalid()


def query_string(
    query: dict[str, QueryValue],
    key: str,
    *,
    pattern: re.Pattern[str] | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    required: bool = False,
) -> str | None:
    if key not in query:
        if required:
            raise invalid()
        return None
    raw = query[key]
    if not isinstance(raw, str):
        raise invalid()
    if min_length is not None and len(raw) < min_length:
        raise invalid()
    if max_length is not None and len(raw) > max_length:
        raise invalid()
    if pattern is not None and not pattern.fullmatch(raw):
        raise invalid()
    return raw


def query_choice(query: dict[str, QueryValue], key: str, choices: Iterable[str]) -> str | None:
    if key not in query:
        return None
    raw = query[key]
    if not isinstance(raw, str) or raw not in set(choices):
        raise invalid()
    return raw


def path_param(value: str, pattern: re.Pattern[str]) -> str:
    if not pattern.fullmatch(value):
        raise invalid()
    return value


def json_body(request: Request) -> dict[str, Any]:
    """Corps JSON déjà décodé par le middleware ; un corps absent vaut `{}`, une liste est refusée."""
    body = request.scope.get(BODY_KEY)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise invalid()
    return body


# --- Validateurs de champs de corps (JSON déjà typé) -------------------------------------------------


def is_string(value: Any, *, pattern: re.Pattern[str] | None = None, max_length: int | None = None) -> bool:
    if not isinstance(value, str):
        return False
    if max_length is not None and len(value) > max_length:
        return False
    return pattern is None or pattern.fullmatch(value) is not None


def require_string(
    body: dict[str, Any],
    key: str,
    *,
    pattern: re.Pattern[str] | None = None,
    max_length: int | None = None,
) -> str:
    if key not in body or not is_string(body[key], pattern=pattern, max_length=max_length):
        raise invalid()
    return body[key]  # type: ignore[no-any-return]


def optional_string(
    body: dict[str, Any],
    key: str,
    *,
    pattern: re.Pattern[str] | None = None,
    max_length: int | None = None,
) -> str | None:
    """Champ facultatif : absent ou `null` valent « non fourni » (`@IsOptional`)."""
    if body.get(key) is None:
        return None
    return require_string(body, key, pattern=pattern, max_length=max_length)


def require_object(body: dict[str, Any], key: str) -> dict[str, Any]:
    value = body.get(key)
    if not isinstance(value, dict):
        raise invalid()
    return value


def optional_object(body: dict[str, Any], key: str) -> dict[str, Any] | None:
    if body.get(key) is None:
        return None
    return require_object(body, key)


def optional_integer(body: dict[str, Any], key: str, minimum: int, maximum: int) -> int | None:
    value = body.get(key)
    if value is None:
        return None
    if not is_safe_integer(value) or value < minimum or value > maximum:
        raise invalid()
    return int(value)
