from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import cast

import httpx
from fastapi import FastAPI

from ezer.api import create_app
from ezer.bootstrap import Runtime, RuntimeRole
from ezer.config import Settings
from ezer.domain import SyncReport
from ezer.observability import Metrics


class _Persistence:
    async def health(self) -> bool:
        return True

    async def get_analysis(self, analysis_id: str) -> None:
        del analysis_id
        return None


class _Checkpointer:
    async def aget_tuple(self, config: object) -> None:
        del config
        return None


@dataclass
class _Runtime:
    settings: Settings
    persistence: _Persistence = field(default_factory=_Persistence)
    checkpointer: _Checkpointer = field(default_factory=_Checkpointer)
    metrics: Metrics = field(default_factory=Metrics)
    calls: list[tuple[list[str] | None, int | None]] = field(default_factory=list)

    async def sync(
        self,
        *,
        account_ids: list[str] | None,
        limit: int | None,
    ) -> list[SyncReport]:
        self.calls.append((account_ids, limit))
        return [SyncReport(account_id="mail", provider="gmail", fetched=1)]


def _application(runtime: _Runtime) -> FastAPI:
    @asynccontextmanager
    async def runtime_factory(
        role: RuntimeRole,
        *,
        settings: Settings | None = None,
    ) -> AsyncGenerator[Runtime]:
        del settings
        assert role == "api"
        yield cast(Runtime, runtime)

    return create_app(runtime_factory=runtime_factory)


async def test_api_key_protects_business_routes_and_sync_is_bounded() -> None:
    runtime = _Runtime(Settings(_env_file=None, api_key="api-key"))
    application = _application(runtime)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            live = await client.get("/health/live")
            assert live.status_code == 200
            assert live.headers["x-request-id"]
            assert (await client.get("/health/ready")).status_code == 200

            assert (await client.get("/metrics")).status_code == 401
            response = await client.post(
                "/v1/sync",
                headers={"X-API-Key": "api-key"},
                json={"account_ids": ["mail"], "limit": 3},
            )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["reports"][0]["fetched"] == 1
    assert runtime.calls == [(["mail"], 3)]


async def test_oversized_request_is_rejected_before_authentication() -> None:
    runtime = _Runtime(Settings(_env_file=None, api_key="api-key"))
    application = _application(runtime)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/sync", content=b"x" * (64 * 1024 + 1))

    assert response.status_code == 413
    assert response.json() == {"detail": "request body too large"}
    assert runtime.calls == []


async def test_chunked_request_is_bounded_without_content_length() -> None:
    async def oversized_chunks() -> AsyncIterator[bytes]:
        yield b'{"padding":"' + b"x" * (32 * 1024)
        yield b"x" * (32 * 1024) + b'"}'

    runtime = _Runtime(Settings(_env_file=None, api_key="api-key"))
    application = _application(runtime)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/sync", content=oversized_chunks())

    assert response.status_code == 413
    assert response.json() == {"detail": "request body too large"}
    assert runtime.calls == []


async def test_unhandled_exception_response_does_not_expose_exception_text() -> None:
    class FailingPersistence(_Persistence):
        async def get_analysis(self, analysis_id: str) -> None:
            del analysis_id
            raise RuntimeError("sensitive database detail")

    runtime = _Runtime(
        Settings(_env_file=None, api_key="api-key"),
        persistence=FailingPersistence(),
    )
    application = _application(runtime)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/v1/analyses/{'a' * 64}",
                headers={"X-API-Key": "api-key"},
            )

    assert response.status_code == 500
    assert response.json()["detail"] == "internal server error"
    assert "sensitive database detail" not in response.text
