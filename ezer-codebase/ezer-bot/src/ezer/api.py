"""FastAPI application exposing health, metrics, sync, and analysis reads."""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated, Protocol, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request, Response, status
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import RequestResponseEndpoint
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ezer import __version__
from ezer.agent_ui import HttpAgentUiClient, UiActionEvent, UiInstanceRef
from ezer.assistant import AssistantAnswer, AssistantTurn, MailboxAssistant
from ezer.bootstrap import (
    Runtime,
    RuntimeRole,
    SyncLimitError,
    UnknownAccountError,
    bootstrap,
)
from ezer.config import Settings
from ezer.domain import EmailAnalysis, SyncReport
from ezer.mailbox import HttpMailboxClient, MailboxUnavailableError
from ezer.observability import Timer, log_event, request_context

logger = logging.getLogger(__name__)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_ACCOUNT_ID = r"^[A-Za-z0-9._-]+$"
_ANALYSIS_ID = r"^[a-f0-9]{64}$"
_MAX_REQUEST_BODY_BYTES = 64 * 1024

AccountId = Annotated[str, Field(min_length=1, max_length=128, pattern=_ACCOUNT_ID)]


class RuntimeContextFactory(Protocol):
    def __call__(
        self,
        role: RuntimeRole,
        *,
        settings: Settings | None = None,
    ) -> AbstractAsyncContextManager[Runtime]: ...


class SyncRequest(BaseModel):
    """A bounded, read-only mailbox synchronization request."""

    model_config = ConfigDict(extra="forbid")

    account_ids: list[AccountId] | None = Field(default=None, min_length=1, max_length=100)
    limit: int | None = Field(default=None, ge=1, le=500)


class SyncResponse(BaseModel):
    reports: list[SyncReport]


class AssistantRequest(BaseModel):
    """One assistant turn. The caller owns the history; the API stores none of it."""

    model_config = ConfigDict(extra="forbid")

    account_id: AccountId
    messages: list[AssistantTurn] = Field(min_length=1, max_length=40)
    # Identifiants que l'opérateur vient de confirmer dans l'interface, pour ce tour uniquement.
    approved_deletions: list[str] = Field(default_factory=list, max_length=20)
    # Interaction déclarative issue d'un composant : entrée utilisateur non fiable, revalidée.
    ui_action: UiActionEvent | None = None
    # Instances encore affichées, pour qu'un patch puisse viser un composant du tour précédent.
    ui_instances: list[UiInstanceRef] = Field(default_factory=list, max_length=24)


class HealthResponse(BaseModel):
    status: str


class _RequestBodyLimitMiddleware:
    """Bound request bodies while they are streamed, before JSON parsing or authentication."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                declared = int(value)
            except ValueError:
                await self._reject(scope, receive, send, status.HTTP_400_BAD_REQUEST)
                return
            if declared < 0:
                await self._reject(scope, receive, send, status.HTTP_400_BAD_REQUEST)
                return
            if declared > self.max_bytes:
                await self._reject(scope, receive, send, status.HTTP_413_CONTENT_TOO_LARGE)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # FastAPI preserves HTTPException raised while it is reading the body;
                    # a custom exception would otherwise be normalized to a generic 400.
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="request body too large",
                    )
            return message

        await self.app(scope, limited_receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status_code: int) -> None:
        detail = "request body too large" if status_code == 413 else "invalid request body length"
        response = JSONResponse(status_code=status_code, content={"detail": detail})
        await response(scope, receive, send)


def _assistant_for(runtime: Runtime) -> MailboxAssistant | None:
    """Build the assistant lazily; an unconfigured backend simply disables the route."""

    existing = getattr(runtime, "assistant", None)
    if isinstance(existing, MailboxAssistant):
        return existing
    settings = runtime.settings
    if settings.backend_url is None or settings.backend_api_key is None:
        return None
    assistant = MailboxAssistant(
        settings,
        HttpMailboxClient(
            settings.backend_url,
            settings.backend_api_key.get_secret_value(),
            client=runtime.http_client,
        ),
        ui=HttpAgentUiClient(
            settings.backend_url,
            settings.backend_api_key.get_secret_value(),
            client=runtime.http_client,
        ),
    )
    runtime.assistant = assistant
    return assistant


def _get_runtime(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service unavailable",
        )
    return cast(Runtime, runtime)


def _new_request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id")
    if supplied is not None and _REQUEST_ID.fullmatch(supplied):
        return supplied
    return uuid.uuid4().hex


async def _require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    runtime = _get_runtime(request)
    configured = runtime.settings.api_key
    if configured is None or x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )
    supplied = x_api_key.encode("utf-8")
    expected = configured.get_secret_value().encode("utf-8")
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )


def create_app(
    settings: Settings | None = None,
    *,
    runtime_factory: RuntimeContextFactory | None = None,
) -> FastAPI:
    """Create an isolated ASGI application; no resource is opened at import time."""

    factory = runtime_factory or cast(RuntimeContextFactory, bootstrap)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        async with factory("api", settings=settings) as runtime:
            application.state.runtime = runtime
            try:
                yield
            finally:
                application.state.runtime = None

    application = FastAPI(
        title="Ezer",
        version=__version__,
        debug=False,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.add_middleware(
        _RequestBodyLimitMiddleware,
        max_bytes=_MAX_REQUEST_BODY_BYTES,
    )

    @application.middleware("http")
    async def request_metadata(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = _new_request_id(request)
        request.state.request_id = request_id
        timer = Timer()
        with request_context(request_id):
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            log_event(
                logger,
                "http_request_completed",
                request_id=request_id,
                status=str(response.status_code),
                duration_ms=timer.milliseconds,
            )
            return response

    @application.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
        with request_context(request_id):
            log_event(
                logger,
                "http_request_failed",
                request_id=request_id,
                status="500",
                error_type=type(exc).__name__,
            )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "internal server error", "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )

    @application.get("/health/live", response_model=HealthResponse)
    async def live() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get("/health/ready", response_model=HealthResponse)
    async def ready(request: Request) -> HealthResponse | JSONResponse:
        try:
            runtime = _get_runtime(request)
            healthy = await runtime.persistence.health()
            if healthy:
                await runtime.checkpointer.aget_tuple(
                    {
                        "configurable": {
                            "thread_id": "__ezer_readiness__",
                            "checkpoint_ns": "",
                        }
                    }
                )
        except Exception as exc:
            log_event(
                logger,
                "readiness_check_failed",
                status="unavailable",
                error_type=type(exc).__name__,
            )
            healthy = False
        if not healthy:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unavailable"},
            )
        return HealthResponse(status="ready")

    @application.get("/metrics", dependencies=[Depends(_require_api_key)])
    async def metrics(request: Request) -> Response:
        return Response(
            content=_get_runtime(request).metrics.render(),
            media_type=CONTENT_TYPE_LATEST,
        )

    @application.post(
        "/v1/sync",
        response_model=SyncResponse,
        dependencies=[Depends(_require_api_key)],
    )
    async def sync_mailboxes(
        request: Request,
        response: Response,
        payload: SyncRequest | None = None,
    ) -> SyncResponse:
        response.headers["Cache-Control"] = "no-store"
        runtime = _get_runtime(request)
        sync_request = payload or SyncRequest()
        try:
            reports = await runtime.sync(
                account_ids=sync_request.account_ids,
                limit=sync_request.limit,
            )
        except (UnknownAccountError, SyncLimitError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from None
        return SyncResponse(reports=reports)

    @application.post(
        "/v1/assistant",
        response_model=AssistantAnswer,
        dependencies=[Depends(_require_api_key)],
    )
    async def assistant_turn(
        request: Request,
        response: Response,
        payload: AssistantRequest,
    ) -> AssistantAnswer:
        """Run one turn of the multi-agent assistant over a backend-provided mailbox."""

        response.headers["Cache-Control"] = "no-store"
        runtime = _get_runtime(request)
        assistant = _assistant_for(runtime)
        if assistant is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="assistant backend is not configured",
            )
        try:
            return await assistant.ask(
                payload.account_id,
                payload.messages,
                payload.approved_deletions,
                ui_action=payload.ui_action,
                ui_instances=payload.ui_instances,
            )
        except MailboxUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=exc.code,
            ) from None

    @application.get(
        "/v1/analyses/{analysis_id}",
        response_model=EmailAnalysis,
        dependencies=[Depends(_require_api_key)],
    )
    async def get_analysis(
        request: Request,
        response: Response,
        analysis_id: Annotated[
            str,
            Path(min_length=64, max_length=64, pattern=_ANALYSIS_ID),
        ],
    ) -> EmailAnalysis:
        response.headers["Cache-Control"] = "no-store"
        analysis = await _get_runtime(request).persistence.get_analysis(analysis_id)
        if analysis is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="analysis not found",
            )
        return analysis

    return application


app = create_app()

__all__ = [
    "AssistantRequest",
    "HealthResponse",
    "SyncRequest",
    "SyncResponse",
    "app",
    "create_app",
]
