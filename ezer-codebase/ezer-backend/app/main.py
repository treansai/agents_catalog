"""Point d'entrée ASGI : `uvicorn app.main:app`."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.analysis.jev import JevClient
from app.config import Settings
from app.errors import HttpError
from app.mail.outlook_auth import Sleeper, real_sleep
from app.routers import agent_ui, health, v1
from app.services.container import Services, build_services
from app.services.http_fetch import HttpFetch, HttpxFetch
from app.web.envelope import REQUEST_ID_KEY, envelope
from app.web.middleware import MAX_REQUEST_BODY_BYTES, EzerMiddleware
from app.web.responses import json_response

__all__ = ["MAX_REQUEST_BODY_BYTES", "app", "create_app", "run"]


def _configure_logging() -> None:
    logger = logging.getLogger("ezer")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def create_app(
    settings: Settings | None = None,
    *,
    http_fetch: HttpFetch | None = None,
    sleeper: Sleeper = real_sleep,
    jev_client: JevClient | None = None,
    grant_write_permission: bool = False,
) -> FastAPI:
    """Construit l'application. Les paramètres nommés servent aux tests (doubles réseau, horloge)."""
    _configure_logging()
    resolved = settings if settings is not None else Settings()  # type: ignore[call-arg]
    services: Services = build_services(
        resolved,
        http_fetch=http_fetch,
        sleeper=sleeper,
        jev_client=jev_client,
        grant_write_permission=grant_write_permission,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await services.persistence.initialize()
        try:
            yield
        finally:
            await services.outlook_auth.aclose()
            if services.owns_http_fetch and isinstance(services.http_fetch, HttpxFetch):
                await services.http_fetch.aclose()

    application = FastAPI(
        title="Ezer backend",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        lifespan=lifespan,
    )
    application.state.services = services

    @application.exception_handler(HttpError)
    async def handle_http_error(request: Request, error: HttpError) -> Response:
        return _neutral(request, error.status)

    @application.exception_handler(StarletteHTTPException)
    async def handle_routing_error(request: Request, error: StarletteHTTPException) -> Response:
        # Un chemin ou une méthode inconnus donnent le même refus neutre.
        return _neutral(request, 404 if error.status_code == 405 else error.status_code)

    application.include_router(health.router)
    application.include_router(v1.router)
    application.include_router(agent_ui.router)
    application.add_middleware(EzerMiddleware)
    return application


def _neutral(request: Request, status: int) -> Response:
    return json_response(envelope(status, request.scope.get(REQUEST_ID_KEY)), status)


app = create_app()


def run() -> None:  # pragma: no cover - lancement local
    import uvicorn

    settings = app.state.services.settings
    uvicorn.run(app, host=settings.host, port=settings.port)
