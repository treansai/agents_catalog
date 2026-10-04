"""FastAPI application factory.

``uvicorn app.main:app`` builds the application lazily from the environment, so importing this
module (for example from tests) never requires EZER_API_KEY.
"""

from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.assistant.chat_model import ChatModelFactory, create_anthropic_chat_model
from app.assistant.service import AssistantService
from app.config import Settings
from app.errors import error_response, log_failure
from app.middleware import MAX_REQUEST_BODY_BYTES, RequestMetadataMiddleware
from app.routers import health, v1

__all__ = ["MAX_REQUEST_BODY_BYTES", "create_app"]  # plus the lazily built ``app``


def create_app(
    settings: Settings | None = None,
    *,
    model_factory: ChatModelFactory = create_anthropic_chat_model,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the application. ``model_factory`` and ``transport`` are injection points for tests."""
    resolved = settings if settings is not None else Settings()
    application = FastAPI(
        title="Ezer bot",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    application.state.settings = resolved
    application.state.assistant_service = AssistantService(
        resolved, model_factory=model_factory, transport=transport
    )

    def request_id_of(request: Request) -> str | None:
        return request.scope.get("state", {}).get("request_id")

    @application.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = 404 if exc.status_code == 405 else exc.status_code
        if status >= 500:
            log_failure(request_id_of(request) or "unavailable", request.method, request.url.path, exc)
        return error_response(status, request_id_of(request))

    @application.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(400, request_id_of(request))

    application.include_router(health.router)
    application.include_router(v1.router)
    application.add_middleware(RequestMetadataMiddleware)
    return application


def __getattr__(name: str) -> Any:
    if name == "app":
        instance = create_app()
        globals()["app"] = instance
        return instance
    raise AttributeError(name)
