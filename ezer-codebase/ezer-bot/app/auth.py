"""API key authentication (constant-time byte comparison)."""

import hmac

from fastapi import HTTPException, Request


async def require_api_key(request: Request) -> None:
    supplied = request.headers.get("x-api-key")
    expected: str = request.app.state.settings.api_key
    if supplied is None or not hmac.compare_digest(
        supplied.encode("utf-8", errors="surrogatepass"),
        expected.encode("utf-8", errors="surrogatepass"),
    ):
        raise HTTPException(status_code=401)
