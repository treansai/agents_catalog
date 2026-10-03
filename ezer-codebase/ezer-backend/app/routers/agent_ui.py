from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends
from starlette.requests import Request
from starlette.responses import Response

from app.agent_ui.catalog import catalog_for_permissions, get_component_definition, search_catalog
from app.agent_ui.contracts import AGENT_UI_PROTOCOL_VERSION, parse_action_event
from app.agent_ui.errors import AgentUiError, ConfirmationRequiredError, agent_ui_error
from app.agent_ui.render import AgentUiSession, build_patch, build_render_spec
from app.domain.models import Account
from app.errors import HttpError
from app.services.container import Services
from app.web import validation as v
from app.web.auth import require_api_key
from app.web.envelope import REQUEST_ID_KEY
from app.web.responses import json_response

_PROTOCOL_VERSION = re.compile(r"1\.0")

router = APIRouter(prefix="/v1/agent-ui", dependencies=[Depends(require_api_key)])


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


async def _session(request: Request, workspace_id: str) -> AgentUiSession:
    services = _services(request)
    account = next((raw for raw in await services.persistence.list_accounts() if raw["id"] == workspace_id), None)
    if account is None:
        raise HttpError(403)
    permissions = ["mail.read"]
    if services.settings.mode == "demo" or services.grant_write_permission:
        permissions.append("mail.write")
    else:
        try:
            state = await services.outlook_auth.state(Account.model_validate(account))
            if state.write_enabled:
                permissions.append("mail.write")
        except Exception:
            # Lecture seule si l'état de connexion n'est pas disponible.
            pass
    return AgentUiSession(
        user_id=account.get("mailbox") or account["id"],
        workspace_id=account["id"],
        permissions=permissions,
        trace_id=request.scope.get(REQUEST_ID_KEY) or "unavailable",
    )


def _workspace_id(request: Request) -> str:
    query = v.query_params(request)
    v.reject_unknown(query, ("workspace_id",))
    workspace_id = v.query_string(query, "workspace_id", pattern=v.ACCOUNT_ID, required=True)
    assert workspace_id is not None
    return workspace_id


def _fail(error: Exception) -> HttpError:
    """Une erreur d'interface devient son statut HTTP ; le corps reste l'enveloppe neutre."""
    if isinstance(error, AgentUiError):
        return HttpError(error.status)
    raise error


@router.get("/catalog")
async def catalog(request: Request) -> Response:
    query = v.query_params(request)
    v.reject_unknown(query, ("workspace_id", "query", "capabilities", "limit"))
    workspace_id = v.query_string(query, "workspace_id", pattern=v.ACCOUNT_ID, required=True)
    assert workspace_id is not None
    text = v.query_string(query, "query", max_length=200)
    capabilities: list[str] | None = None
    if "capabilities" in query:
        raw = query["capabilities"]
        values = [entry.strip() for entry in raw.split(",") if entry.strip()] if isinstance(raw, str) else raw
        if len(values) > 12 or any(len(entry) > 64 for entry in values):
            raise v.invalid()
        capabilities = values
    limit = v.query_integer(query, "limit", 1, 50)
    session = await _session(request, workspace_id)
    unfiltered = text is None and capabilities is None and limit is None
    components = (
        catalog_for_permissions(session.permissions)
        if unfiltered
        else search_catalog(session.permissions, text, capabilities, limit)
    )
    return json_response({"protocolVersion": AGENT_UI_PROTOCOL_VERSION, "components": components})


@router.post("/render")
async def render(request: Request) -> Response:
    """Valide une proposition d'affichage de l'agent et frappe l'`instanceId`.

    L'agent n'obtient jamais d'instance qu'il aurait nommée lui-même.
    """
    workspace_id = _workspace_id(request)
    body = v.json_body(request)
    v.reject_unknown(body, ("componentId", "componentVersion", "props", "data", "fallbackText"))
    v.require_string(body, "componentId", pattern=v.TOKEN)
    v.optional_string(body, "componentVersion", pattern=v.TOKEN)
    v.optional_object(body, "props")
    v.require_string(body, "fallbackText", max_length=2_000)
    session = await _session(request, workspace_id)
    try:
        spec = build_render_spec(body, session)
    except AgentUiError as error:
        raise _fail(error) from error
    return json_response({"status": "success", "ui": spec.to_json()})


@router.post("/patch")
async def patch(request: Request) -> Response:
    workspace_id = _workspace_id(request)
    body = v.json_body(request)
    v.reject_unknown(body, ("instanceId", "componentId", "componentVersion", "props"))
    v.require_string(body, "instanceId", pattern=v.TOKEN)
    v.require_string(body, "componentId", pattern=v.TOKEN)
    v.optional_string(body, "componentVersion", pattern=v.TOKEN)
    v.require_object(body, "props")
    session = await _session(request, workspace_id)
    try:
        ui = build_patch(body, session)
    except AgentUiError as error:
        raise _fail(error) from error
    return json_response({"status": "success", "ui": ui})


@router.post("/resolve")
async def resolve(request: Request) -> Response:
    workspace_id = _workspace_id(request)
    body = v.json_body(request)
    v.reject_unknown(body, ("resolverId", "input", "componentId", "instanceId"))
    resolver_id = v.require_string(body, "resolverId", pattern=v.TOKEN)
    resolver_input = v.require_object(body, "input")
    component_id = v.require_string(body, "componentId", pattern=v.TOKEN)
    v.require_string(body, "instanceId", pattern=v.TOKEN)
    session = await _session(request, workspace_id)
    component = get_component_definition(component_id)
    if component is None:
        raise _fail(agent_ui_error("unknown_component", 400))
    if resolver_id not in component["allowedDataResolvers"]:
        raise _fail(agent_ui_error("unknown_resolver", 400))
    try:
        data = await _services(request).resolvers.execute(resolver_id, resolver_input, session, component_id)
    except AgentUiError as error:
        raise _fail(error) from error
    empty = isinstance(data, dict) and isinstance(data.get("items"), list) and len(data["items"]) == 0
    return json_response({"status": "empty" if empty else "success", "data": data})


@router.post("/action")
async def action(request: Request) -> Response:
    workspace_id = _workspace_id(request)
    body = v.json_body(request)
    v.reject_unknown(
        body,
        (
            "kind",
            "protocolVersion",
            "eventId",
            "messageId",
            "instanceId",
            "componentId",
            "componentVersion",
            "actionId",
            "values",
            "idempotencyKey",
            "createdAt",
        ),
    )
    v.require_string(body, "kind")
    if body.get("protocolVersion") is not None:
        v.require_string(body, "protocolVersion", pattern=_PROTOCOL_VERSION)
    for key in ("eventId", "messageId", "instanceId", "componentId", "componentVersion", "actionId"):
        v.require_string(body, key, pattern=v.TOKEN)
    v.require_object(body, "values")
    v.require_string(body, "idempotencyKey", pattern=v.TOKEN)
    v.optional_string(body, "createdAt", max_length=64)
    session = await _session(request, workspace_id)
    try:
        event = parse_action_event({**body, "kind": "ui.action"})
        result: Any = await _services(request).actions.dispatch(event, session)
    except ConfirmationRequiredError as challenge:
        return json_response(
            {
                "status": "confirmation_required",
                "result": None,
                "confirmation": challenge.confirmation.to_json(),
            }
        )
    except AgentUiError as error:
        raise _fail(error) from error
    return json_response({"status": "success", "result": result})
