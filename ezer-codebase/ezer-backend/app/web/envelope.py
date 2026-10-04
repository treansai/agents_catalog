"""Enveloppe d'erreur neutre : jamais de message interne, de trace ni de contenu d'exception."""

from __future__ import annotations

from typing import Any

REQUEST_ID_KEY = "ezer.request_id"
BODY_KEY = "ezer.body"


def neutral_message(status: int) -> str:
    if status in (400, 422):
        return "invalid request"
    if status in (401, 403):
        return "unauthorized"
    if status == 404:
        return "resource not found"
    if status == 413:
        return "request too large"
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
        "request_id": request_id or "unavailable",
    }
