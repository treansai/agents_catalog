"""API key authentication (constant-time comparison of SHA-256 digests)."""

import hashlib
import hmac

from fastapi import HTTPException, Request


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).digest()


async def require_api_key(request: Request) -> None:
    supplied = request.headers.get("x-api-key")
    expected: str = request.app.state.settings.api_key
    if supplied is None or not hmac.compare_digest(_digest(supplied), _digest(expected)):
        raise HTTPException(status_code=401)
