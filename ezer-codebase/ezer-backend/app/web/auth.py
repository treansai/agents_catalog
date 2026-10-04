from __future__ import annotations

import hmac

from starlette.requests import Request

from app.errors import HttpError


def api_key_matches(supplied: str | None, expected: str) -> bool:
    """Comparaison en temps constant des octets (seule la longueur peut fuiter)."""
    if supplied is None:
        return False
    return hmac.compare_digest(
        supplied.encode("utf-8", errors="surrogatepass"),
        expected.encode("utf-8", errors="surrogatepass"),
    )


async def require_api_key(request: Request) -> None:
    services = request.app.state.services
    if not api_key_matches(request.headers.get("x-api-key"), services.settings.api_key):
        raise HttpError(401)
