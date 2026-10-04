from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import Response

from app.web.responses import json_response

router = APIRouter(prefix="/health")


@router.get("/live")
async def live() -> Response:
    return json_response({"status": "ok"})


@router.get("/ready")
async def ready(request: Request) -> Response:
    if not await request.app.state.services.persistence.health():
        return json_response({"status": "unavailable"}, 503)
    return json_response({"status": "ready"})
