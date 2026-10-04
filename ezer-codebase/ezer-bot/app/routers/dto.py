"""Request validation. Unknown fields and wrong types are rejected (HTTP 400)."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

ACCOUNT_ID = r"^[A-Za-z0-9._-]{1,128}$"
AGENT_UI_ID = r"^[A-Za-z0-9._:-]{1,128}$"
AGENT_UI_VERSION = r"^[A-Za-z0-9._:-]{1,32}$"
MESSAGE_ID = r"^[A-Za-z0-9_\-=+/]{1,512}$"

AccountId = Annotated[StrictStr, Field(pattern=ACCOUNT_ID)]
UiId = Annotated[StrictStr, Field(pattern=AGENT_UI_ID)]
UiVersion = Annotated[StrictStr, Field(pattern=AGENT_UI_VERSION)]
MessageId = Annotated[StrictStr, Field(pattern=MESSAGE_ID)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AssistantTurnDto(_Strict):
    role: Literal["user", "assistant"]
    content: Annotated[StrictStr, Field(min_length=1, max_length=8_000)]


class UiInstanceRefDto(_Strict):
    instance_id: UiId
    component_id: UiId
    component_version: UiVersion | None = "1.0"


class UiActionEventDto(_Strict):
    kind: Literal["ui.action"]
    event_id: UiId
    message_id: UiId
    instance_id: UiId
    component_id: UiId
    component_version: UiVersion | None = "1.0"
    action_id: UiId
    values: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: UiId
    result: dict[str, Any] | None = None


class AssistantRequestDto(_Strict):
    account_id: AccountId
    messages: Annotated[list[AssistantTurnDto], Field(min_length=1, max_length=40)]
    approved_deletions: Annotated[list[MessageId], Field(max_length=20)] | None = None
    ui_action: UiActionEventDto | None = None
    ui_instances: Annotated[list[UiInstanceRefDto], Field(max_length=24)] | None = None
