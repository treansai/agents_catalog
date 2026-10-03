"""API key authentication (constant-time comparison of keyed HMAC-SHA256 digests)."""

import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request

_MAC_KEY = secrets.token_bytes(32)


def _digest(value: str) -> bytes:
    return hmac.digest(_MAC_KEY, value.encode("utf-8", errors="surrogatepass"), hashlib.sha256)


async def require_api_key(request: Request) -> None:
    supplied = request.headers.get("x-api-key")
    expected: str = request.app.state.settings.api_key
    if supplied is None or not hmac.compare_digest(_digest(supplied), _digest(expected)):
        raise HTTPException(status_code=401)
