"""Validation serveur des instructions d'interface produites par l'agent.

L'agent ne fabrique jamais une instance : il propose un `componentId`, une version, des props et une
source de données. Le serveur vérifie le tout contre le catalogue, puis frappe lui-même
l'`instanceId`. Aucun identifiant de session (userId, workspaceId, permissions) ne peut venir du
modèle : il est retiré des entrées avant validation.
"""

from __future__ import annotations

import secrets
from typing import Any

from app.agent_ui.catalog import ComponentCatalogDefinition, get_component_definition
from app.agent_ui.contracts import (
    ID,
    MAX_INPUT_BYTES,
    MISSING,
    AgentUiDataSource,
    AgentUiRenderSpec,
    DataResolverContext,
    parse_data_source,
)
from app.agent_ui.errors import agent_ui_error
from app.agent_ui.schema import JsonSchema, SchemaValidationError, assert_schema, json_size

AgentUiSession = DataResolverContext


def new_instance_id() -> str:
    return f"ui_{secrets.token_hex(12)}"


def require_component(component_id: str, component_version: Any, session: AgentUiSession) -> ComponentCatalogDefinition:
    """Le composant existe, la version correspond, et la session a les permissions requises."""
    component = get_component_definition(component_id)
    if component is None:
        raise agent_ui_error("unknown_component", 400, "unknown_component")
    granted = set(session.permissions)
    if not all(permission in granted for permission in component["requiredPermissions"]):
        raise agent_ui_error("permission_denied", 403, "permission_denied")
    if component_version is not MISSING and component_version != component["version"]:
        raise agent_ui_error("component_version_mismatch", 409, "component_version_mismatch")
    return component


def _assert_props(props: dict[str, Any], schema: JsonSchema) -> None:
    if json_size(props) > MAX_INPUT_BYTES:
        raise agent_ui_error("payload_too_large", 413, "props too large")
    try:
        assert_schema(props, schema)
    except SchemaValidationError as error:
        raise agent_ui_error("invalid_props", 400, str(error)) from error


def _assert_data_source(data: AgentUiDataSource | None, component: ComponentCatalogDefinition) -> None:
    if data is None or data.mode != "resolver":
        return
    if data.resolver_id not in component["allowedDataResolvers"]:
        raise agent_ui_error("unknown_resolver", 400, "unauthorized_resolver")


def build_render_spec(request: dict[str, Any], session: AgentUiSession) -> AgentUiRenderSpec:
    """Rend une instruction `ui.render` sûre à partir d'une proposition du modèle."""
    component = require_component(request["componentId"], request.get("componentVersion", MISSING), session)
    fallback_text = request["fallbackText"]
    if not isinstance(fallback_text, str) or len(fallback_text) < 1 or len(fallback_text) > 2_000:
        raise agent_ui_error("invalid_payload", 400, "fallbackText is required")
    raw_props = request.get("props")
    props = dict(raw_props) if raw_props is not None else {}
    _assert_props(props, component["propsSchema"])
    data = parse_data_source(request.get("data", MISSING))
    _assert_data_source(data, component)
    return AgentUiRenderSpec(
        instanceId=new_instance_id(),
        componentId=component["id"],
        componentVersion=component["version"],
        props=props,
        data=data,
        fallbackText=fallback_text,
    )


def build_patch(request: dict[str, Any], session: AgentUiSession) -> dict[str, Any]:
    """Un patch ne porte que des props : il est validé contre le schéma du composant privé de ses
    `required`, puisqu'il est partiel par nature.
    """
    component = require_component(request["componentId"], request.get("componentVersion", MISSING), session)
    instance_id = request["instanceId"]
    if not isinstance(instance_id, str) or not ID.fullmatch(instance_id):
        raise agent_ui_error("invalid_payload", 400, "invalid instanceId")
    patch = dict(request["props"])
    if not patch:
        raise agent_ui_error("invalid_payload", 400, "empty patch")
    partial: JsonSchema = {**component["propsSchema"], "required": []}
    _assert_props(patch, partial)
    return {"instanceId": instance_id, "patch": patch}
