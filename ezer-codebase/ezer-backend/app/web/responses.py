from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse

JSON_CONTENT_TYPE = "application/json; charset=utf-8"


def json_response(content: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status, headers={"content-type": JSON_CONTENT_TYPE})
