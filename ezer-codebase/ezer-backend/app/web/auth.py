from __future__ import annotations

import hashlib
import hmac

from starlette.requests import Request

from app.errors import HttpError


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def api_key_matches(supplied: str | None, expected: str) -> bool:
    """Comparaison en temps constant : on compare des condensats de longueur fixe."""
    if supplied is None:
        return False
    return hmac.compare_digest(_digest(supplied), _digest(expected))


async def require_api_key(request: Request) -> None:
    services = request.app.state.services
    if not api_key_matches(request.headers.get("x-api-key"), services.settings.api_key):
        raise HttpError(401)
