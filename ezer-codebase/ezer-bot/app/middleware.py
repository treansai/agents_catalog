"""Pure-ASGI request plumbing, applied before routing and authentication.

* ``X-Request-ID`` (echoed when well formed, generated otherwise), ``Cache-Control: no-store``
  and ``X-Content-Type-Options: nosniff`` on every response, errors included;
* bounded request bodies (64 KiB, with or without ``Content-Length``) on POST/PUT/PATCH;
* ``Content-Type: application/json`` and well-formed JSON enforced on non-empty write bodies;
* any unhandled exception becomes the neutral 500 envelope.
"""

import json
import re
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import error_response, log_failure

MAX_REQUEST_BODY_BYTES = 64 * 1024
VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH"})
DIGITS = re.compile(r"[0-9]+")

SECURITY_HEADERS = (
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
)


def _reject_constant(name: str) -> Any:
    raise ValueError(name)


def parse_json_body(raw: bytes) -> Any:
    """``JSON.parse`` semantics: NaN/Infinity are rejected."""
    return json.loads(raw.decode("utf-8", errors="replace"), parse_constant=_reject_constant)


class RequestMetadataMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = None
        content_lengths: list[bytes] = []
        content_type = ""
        for name, value in scope["headers"]:
            if name == b"x-request-id" and supplied is None:
                supplied = value.decode("latin-1")
            elif name == b"content-length":
                content_lengths.append(value)
            elif name == b"content-type" and not content_type:
                content_type = value.decode("latin-1")
        request_id = (
            supplied if supplied is not None and VALID_REQUEST_ID.fullmatch(supplied) else str(uuid.uuid4())
        )
        scope.setdefault("state", {})["request_id"] = request_id

        started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                managed = {b"x-request-id", b"cache-control", b"x-content-type-options"}
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in managed]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                headers.extend(SECURITY_HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        async def respond(status: int) -> None:
            response = error_response(status, request_id)
            await response(scope, receive, send_with_headers)

        try:
            if scope["method"] in WRITE_METHODS:
                downstream_receive = await self._bounded_body(
                    scope, receive, respond, content_lengths, content_type
                )
                if downstream_receive is None:
                    return
            else:
                downstream_receive = receive
            await self.app(scope, downstream_receive, send_with_headers)
        except Exception as error:
            log_failure(request_id, scope["method"], scope["path"], error)
            if started:
                raise
            await respond(500)

    async def _bounded_body(
        self,
        scope: Scope,
        receive: Receive,
        respond: Any,
        content_lengths: list[bytes],
        content_type: str,
    ) -> Receive | None:
        if content_lengths:
            declared = content_lengths[0]
            if len(content_lengths) > 1 or not DIGITS.fullmatch(declared.decode("latin-1")):
                await respond(400)
                return None
            if int(declared) > MAX_REQUEST_BODY_BYTES:
                await respond(413)
                return None

        chunks: list[bytes] = []
        received = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            body = message.get("body", b"")
            received += len(body)
            if received > MAX_REQUEST_BODY_BYTES:
                await respond(413)
                return None
            chunks.append(body)
            if not message.get("more_body", False):
                break
        raw = b"".join(chunks)

        if raw:
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                await respond(415)
                return None
            try:
                parsed = parse_json_body(raw)
                if not isinstance(parsed, dict | list):
                    raise ValueError("not an object")
            except (ValueError, RecursionError):
                await respond(400)
                return None

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()

        return replay
