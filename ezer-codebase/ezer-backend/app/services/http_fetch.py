"""Appels HTTP sortants (Microsoft, Graph, géocodage) derrière une interface remplaçable en test."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import httpx

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class HttpFetch(Protocol):
    """Équivalent de `fetch` : renvoie la réponse HTTP, ou lève en cas d'échec réseau."""

    async def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: str | None = None,
        timeout: float = 15.0,
    ) -> httpx.Response: ...


class HttpxFetch:
    """Implémentation par défaut : un `httpx.AsyncClient` partagé, sans suivi de redirection."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: str | None = None,
        timeout: float = 15.0,
    ) -> httpx.Response:
        if self._client is None:
            self._client = httpx.AsyncClient(follow_redirects=False, trust_env=True)
        response = await self._client.request(
            method,
            url,
            headers=dict(headers or {}),
            content=body.encode("utf-8") if body is not None else None,
            timeout=timeout,
        )
        # Équivalent de `redirect: "error"` : une redirection n'est jamais suivie.
        if response.status_code in REDIRECT_STATUSES:
            raise httpx.TooManyRedirects("redirect refused", request=response.request)
        return response

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def form_encode(value: str) -> str:
    """Sérialisation `application/x-www-form-urlencoded` de `URLSearchParams`."""
    encoded: list[str] = []
    for byte in value.encode("utf-8"):
        char = chr(byte)
        if byte < 128 and (char.isalnum() or char in "*-._"):
            encoded.append(char)
        elif char == " ":
            encoded.append("+")
        else:
            encoded.append(f"%{byte:02X}")
    return "".join(encoded)


def build_url(origin: str, path: str, params: list[tuple[str, str]] | None = None) -> str:
    query = "&".join(f"{form_encode(key)}={form_encode(val)}" for key, val in (params or []))
    return f"{origin}{path}" + (f"?{query}" if query else "")
