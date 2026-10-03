"""Middleware ASGI : identifiant de requête, en-têtes de sécurité, borne de 64 Kio, corps JSON.

Il s'exécute avant le routage et l'authentification, comme l'ancien middleware Express : une requête
d'écriture trop grosse est refusée (413) avant toute validation.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.web.envelope import BODY_KEY, REQUEST_ID_KEY, envelope

MAX_REQUEST_BODY_BYTES = 64 * 1024
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH"})
VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")

logger = logging.getLogger("ezer.http")


def _reject_constant(name: str) -> Any:
    raise ValueError(f"invalid JSON constant {name}")


class _BodyError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _body_error(status: int) -> _BodyError:
    message = {413: "request too large", 415: "unsupported media type"}.get(status, "invalid request")
    return _BodyError(status, message)


class EzerMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        supplied = headers.getlist("x-request-id")
        request_id = (
            supplied[0] if len(supplied) == 1 and VALID_REQUEST_ID.fullmatch(supplied[0]) else str(uuid.uuid4())
        )
        scope[REQUEST_ID_KEY] = request_id
        started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
                response_headers["Cache-Control"] = "no-store"
                response_headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        async def reply(status: int, message: str | None = None) -> None:
            body = json.dumps(envelope(status, request_id, message), ensure_ascii=False, separators=(",", ":"))
            data = body.encode("utf-8")
            await send_with_headers(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(data)).encode()),
                    ],
                }
            )
            await send_with_headers({"type": "http.response.body", "body": data})

        try:
            downstream_receive = receive
            if scope["method"] in WRITE_METHODS:
                try:
                    raw = await self._read_bounded_body(headers, receive)
                    scope[BODY_KEY] = self._decode_json(headers, raw)
                except _BodyError as error:
                    await reply(error.status, error.message)
                    return

                consumed = False

                async def replay() -> Message:
                    nonlocal consumed
                    if consumed:
                        return await receive()
                    consumed = True
                    return {"type": "http.request", "body": raw, "more_body": False}

                downstream_receive = replay
            await self.app(scope, downstream_receive, send_with_headers)
        except Exception as error:
            logger.error(
                json.dumps(
                    {
                        "event": "http_request_failed",
                        "request_id": request_id,
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "error_type": type(error).__name__,
                    }
                )
            )
            if not started:
                await reply(500)

    @staticmethod
    async def _read_bounded_body(headers: Headers, receive: Receive) -> bytes:
        """Borne le corps d'après Content-Length puis d'après les octets réellement reçus."""
        declared = headers.getlist("content-length")
        if declared:
            value = declared[0]
            if len(declared) > 1 or not re.fullmatch(r"[0-9]+", value):
                raise _body_error(400)
            if int(value) > MAX_REQUEST_BODY_BYTES:
                raise _body_error(413)
        chunks: list[bytes] = []
        received = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise _body_error(400)
            chunk = message.get("body", b"")
            received += len(chunk)
            if received > MAX_REQUEST_BODY_BYTES:
                raise _body_error(413)
            chunks.append(chunk)
            if not message.get("more_body", False):
                return b"".join(chunks)

    @staticmethod
    def _decode_json(headers: Headers, raw: bytes) -> Any:
        """Renvoie l'objet JSON (dict ou liste), ou None quand le corps est vide."""
        if len(raw) == 0:
            return None
        media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise _body_error(415)
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"), parse_constant=_reject_constant)
        except (ValueError, RecursionError) as error:
            raise _body_error(400) from error
        if not isinstance(parsed, dict | list):
            raise _body_error(400)
        return parsed
