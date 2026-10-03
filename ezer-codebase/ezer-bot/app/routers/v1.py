from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.agent_ui.agent_ui import UiActionEvent, UiInstanceRef
from app.auth import require_api_key
from app.mailbox.mailbox import MailboxUnavailableError
from app.middleware import parse_json_body
from app.routers.dto import AssistantRequestDto

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


def _invalid() -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST)


async def _payload(request: Request) -> AssistantRequestDto:
    raw = await request.body()
    if not raw:
        raise _invalid()
    try:
        body: Any = parse_json_body(raw)
        return AssistantRequestDto.model_validate(body)
    except (ValueError, ValidationError, RecursionError) as error:
        raise _invalid() from error


@router.post("/assistant", status_code=status.HTTP_201_CREATED)
async def assistant_turn(request: Request) -> JSONResponse:
    # Authentication (router dependency) runs before the body is validated.
    payload = await _payload(request)
    service = request.app.state.assistant_service
    if not service.configured:
        raise HTTPException(status_code=422)
    ui_action = None
    if payload.ui_action is not None:
        event = payload.ui_action
        ui_action = UiActionEvent(
            event_id=event.event_id,
            message_id=event.message_id,
            instance_id=event.instance_id,
            component_id=event.component_id,
            component_version=event.component_version or "1.0",
            action_id=event.action_id,
            idempotency_key=event.idempotency_key,
            values=event.values,
            result=event.result,
        )
    ui_instances = [
        UiInstanceRef(
            instance_id=ref.instance_id,
            component_id=ref.component_id,
            component_version=ref.component_version or "1.0",
        )
        for ref in payload.ui_instances or []
    ]
    try:
        answer = await service.instance().ask(
            payload.account_id,
            [{"role": turn.role, "content": turn.content} for turn in payload.messages],
            payload.approved_deletions or [],
            ui_action,
            ui_instances,
        )
    except MailboxUnavailableError as error:
        raise HTTPException(status_code=422) from error
    return JSONResponse(answer.to_dict(), status_code=status.HTTP_201_CREATED)
