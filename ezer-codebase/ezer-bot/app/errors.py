"""Neutral error envelope: no internal detail ever reaches the caller."""

import json
import logging
from typing import Any

from fastapi.responses import JSONResponse

logger = logging.getLogger("ezer.bot")

UNAVAILABLE_REQUEST_ID = "unavailable"


def neutral_message(status: int) -> str:
    if status in (400, 422):
        return "invalid request"
    if status in (401, 403):
        return "unauthorized"
    if status == 404:
        return "resource not found"
    if status == 413:
        return "request too large"
    if status == 415:
        return "unsupported media type"
    if status == 503:
        return "service unavailable"
    if status >= 500:
        return "internal server error"
    return "request failed"


def envelope(status: int, request_id: str | None, message: str | None = None) -> dict[str, Any]:
    text = message if message is not None else neutral_message(status)
    return {
        "statusCode": status,
        "message": text,
        "detail": text,
        "request_id": request_id or UNAVAILABLE_REQUEST_ID,
    }


def error_response(status: int, request_id: str | None, message: str | None = None) -> JSONResponse:
    return JSONResponse(envelope(status, request_id, message), status_code=status)


def log_failure(request_id: str, method: str, path: str, error: BaseException | None) -> None:
    logger.error(
        json.dumps(
            {
                "event": "http_request_failed",
                "request_id": request_id,
                "method": method,
                "path": path,
                "error_type": type(error).__name__ if error is not None else "UnknownError",
            }
        )
    )
