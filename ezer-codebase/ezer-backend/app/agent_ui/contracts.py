from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

from app.agent_ui.errors import agent_ui_error
from app.agent_ui.schema import JsonSchema, assert_schema, json_size

AGENT_UI_PROTOCOL_VERSION = "1.0"
MAX_INLINE_BYTES = 32 * 1024
MAX_INPUT_BYTES = 8 * 1024
MAX_ACTION_VALUES_BYTES = 8 * 1024

ID_PATTERN = "^[A-Za-z0-9._:-]{1,128}$"
ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class _Missing:
    """Valeur absente (`undefined` en JavaScript), distincte d'un `null` explicite."""

    _instance: _Missing | None = None

    def __new__(cls) -> _Missing:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING: Final = _Missing()


@dataclass
class AgentUiDataSource:
    mode: str
    value: Any = None
    resolver_id: str | None = None
    input: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        if self.mode == "inline":
            return {"mode": "inline", "value": self.value}
        return {"mode": "resolver", "resolverId": self.resolver_id, "input": self.input}


@dataclass
class AgentUiRenderSpec:
    instanceId: str
    componentId: str
    componentVersion: str
    props: dict[str, Any]
    fallbackText: str
    data: AgentUiDataSource | None = None

    def to_json(self) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "instanceId": self.instanceId,
            "componentId": self.componentId,
            "componentVersion": self.componentVersion,
            "props": self.props,
        }
        if self.data is not None:
            spec["data"] = self.data.to_json()
        spec["fallbackText"] = self.fallbackText
        return spec


@dataclass
class DataResolverContext:
    user_id: str
    workspace_id: str
    permissions: list[str]
    trace_id: str


@dataclass
class AgentUiActionEvent:
    kind: str
    eventId: str
    messageId: str
    instanceId: str
    componentId: str
    componentVersion: str
    actionId: str
    values: dict[str, Any]
    idempotencyKey: str
    createdAt: str


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def as_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise agent_ui_error("invalid_payload", 400, f"invalid {field_name}")
    return value


def parse_data_source(value: Any) -> AgentUiDataSource | None:
    if value is MISSING:
        return None
    if not _is_record(value) or not isinstance(value.get("mode"), str):
        raise agent_ui_error("invalid_payload", 400, "invalid data source")
    if value["mode"] == "inline":
        if "value" not in value:
            # `JSON.stringify(undefined)` n'est pas une chaîne : le service répond par une erreur interne.
            raise TypeError("inline data source without a value")
        if json_size(value["value"]) > MAX_INLINE_BYTES:
            raise agent_ui_error("payload_too_large", 413, "inline data too large")
        return AgentUiDataSource(mode="inline", value=value["value"])
    if value["mode"] == "resolver":
        resolver_id = value.get("resolverId")
        if not isinstance(resolver_id, str) or not ID.fullmatch(resolver_id):
            raise agent_ui_error("unknown_resolver", 400, "invalid resolverId")
        if not _is_record(value.get("input")):
            raise agent_ui_error("invalid_payload", 400, "invalid resolver input")
        if json_size(value["input"]) > MAX_INPUT_BYTES:
            raise agent_ui_error("payload_too_large", 413, "resolver input too large")
        cleaned = dict(value["input"])
        for key in ("userId", "workspaceId", "permissions", "account_id"):
            cleaned.pop(key, None)
        return AgentUiDataSource(mode="resolver", resolver_id=resolver_id, input=cleaned)
    raise agent_ui_error("invalid_payload", 400, "invalid data mode")


def parse_render_spec(value: Any) -> AgentUiRenderSpec:
    if not _is_record(value):
        raise agent_ui_error("invalid_payload", 400, "invalid ui spec")
    fallback_text = value.get("fallbackText")
    if not isinstance(fallback_text, str) or len(fallback_text) < 1 or len(fallback_text) > 2_000:
        raise agent_ui_error("invalid_payload", 400, "fallbackText is required")
    props = value["props"] if _is_record(value.get("props")) else {}
    if json_size(props) > MAX_INPUT_BYTES:
        raise agent_ui_error("payload_too_large", 413, "props too large")
    return AgentUiRenderSpec(
        instanceId=as_id(value.get("instanceId"), "instanceId"),
        componentId=as_id(value.get("componentId"), "componentId"),
        componentVersion=as_id(value.get("componentVersion"), "componentVersion"),
        props=props,
        data=parse_data_source(value.get("data", MISSING)),
        fallbackText=fallback_text,
    )


def parse_action_event(value: Any) -> AgentUiActionEvent:
    from app.services.timeutil import now_iso

    if not _is_record(value) or value.get("kind") != "ui.action":
        raise agent_ui_error("invalid_payload", 400, "invalid action event")
    values = value["values"] if _is_record(value.get("values")) else {}
    if json_size(values) > MAX_ACTION_VALUES_BYTES:
        raise agent_ui_error("payload_too_large", 413, "action values too large")
    created_at = value["createdAt"] if isinstance(value.get("createdAt"), str) else now_iso()
    return AgentUiActionEvent(
        kind="ui.action",
        eventId=as_id(value.get("eventId"), "eventId"),
        messageId=as_id(value.get("messageId"), "messageId"),
        instanceId=as_id(value.get("instanceId"), "instanceId"),
        componentId=as_id(value.get("componentId"), "componentId"),
        componentVersion=as_id(value.get("componentVersion"), "componentVersion"),
        actionId=as_id(value.get("actionId"), "actionId"),
        values=values,
        idempotencyKey=as_id(value.get("idempotencyKey"), "idempotencyKey"),
        createdAt=created_at,
    )


MAIL_ROW_SCHEMA: JsonSchema = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "sender", "subject", "snippet", "receivedAt", "unread"],
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 512},
        "sender": {"type": "string", "maxLength": 320},
        "senderAddress": {"type": "string", "maxLength": 320},
        "subject": {"type": "string", "maxLength": 400},
        "snippet": {"type": "string", "maxLength": 600},
        "receivedAt": {"type": "string", "maxLength": 64},
        "unread": {"type": "boolean"},
        "hasAttachments": {"type": "boolean"},
        "tag": {"type": "string", "maxLength": 32},
    },
}

MAIL_PAGE_SCHEMA: JsonSchema = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "total", "limit", "offset"],
    "properties": {
        "items": {"type": "array", "maxItems": 25, "items": MAIL_ROW_SCHEMA},
        "total": {"type": "integer", "minimum": 0, "maximum": 1_000_000},
        "limit": {"type": "integer", "minimum": 1, "maximum": 25},
        "offset": {"type": "integer", "minimum": 0, "maximum": 1_000_000},
    },
}


def assert_output(value: Any, schema: JsonSchema | None) -> Any:
    if schema is None:
        return value
    assert_schema(value, schema)
    return value
