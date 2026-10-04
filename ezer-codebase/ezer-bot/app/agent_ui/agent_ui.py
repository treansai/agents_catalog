"""Agent UI: component catalog, render/patch calls to the backend, and the instance ledger.

UI messages exchanged with the front end keep the camelCase field names of the HTTP contract
(``protocolVersion``, ``instanceId``, ...); they are plain dictionaries.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.mailbox.mailbox import as_record, normalize_base_url

PROTOCOL_VERSION = "1.0"
MAX_UI_MESSAGES = 6

ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
ACCOUNT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
TIMEOUT_SECONDS = 20.0
MAX_CATALOG_ENTRIES = 20

SESSION_KEYS = frozenset(
    {
        "userId",
        "workspaceId",
        "tenantId",
        "permissions",
        "account_id",
        "accountId",
        "credentials",
    }
)


class AgentUiError(Exception):
    def __init__(self, operation: str, code: str, detail: str = "") -> None:
        super().__init__(f"{operation} failed: {code}")
        self.operation = operation
        self.code = code
        self.detail = detail


@dataclass
class UiRenderSpec:
    instance_id: str
    component_id: str
    component_version: str
    props: dict[str, Any]
    data: dict[str, Any] | None
    fallback_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "instanceId": self.instance_id,
            "componentId": self.component_id,
            "componentVersion": self.component_version,
            "props": self.props,
            "data": self.data,
            "fallbackText": self.fallback_text,
        }


@dataclass
class UiPatchSpec:
    instance_id: str
    patch: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"instanceId": self.instance_id, "patch": self.patch}


@dataclass
class UiInstanceRef:
    instance_id: str
    component_id: str
    component_version: str = "1.0"


@dataclass
class UiActionEvent:
    event_id: str
    message_id: str
    instance_id: str
    component_id: str
    action_id: str
    idempotency_key: str
    component_version: str = "1.0"
    values: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    kind: str = "ui.action"


@dataclass
class CatalogComponent:
    id: str
    version: str
    title: str
    description: str
    capabilities: list[str]
    use_when: list[str]
    avoid_when: list[str]
    props_schema: dict[str, Any]
    allowed_data_resolvers: list[str]
    allowed_actions: list[str]


def catalog_prompt_payload(component: CatalogComponent) -> dict[str, Any]:
    return {
        "id": component.id,
        "version": component.version,
        "description": component.description,
        "useWhen": component.use_when,
        "avoidWhen": component.avoid_when,
        "propsSchema": component.props_schema,
        "allowedDataResolvers": component.allowed_data_resolvers,
        "allowedActions": component.allowed_actions,
    }


class AgentUiClient(Protocol):
    async def catalog(
        self,
        workspace_id: str,
        *,
        query: str | None = None,
        capabilities: list[str] | None = None,
        limit: int | None = None,
    ) -> list[CatalogComponent]: ...

    async def render(
        self,
        workspace_id: str,
        *,
        component_id: str,
        component_version: str | None = None,
        props: dict[str, Any],
        data: dict[str, Any] | None = None,
        fallback_text: str,
    ) -> UiRenderSpec: ...

    async def patch(
        self,
        workspace_id: str,
        *,
        instance_id: str,
        component_id: str,
        component_version: str | None = None,
        props: dict[str, Any],
    ) -> UiPatchSpec: ...


def strip_session_keys(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in SESSION_KEYS}


def new_message_id() -> str:
    return f"ui-{uuid.uuid4().hex}"


def _text(payload: dict[str, Any], key: str, maximum: int) -> str:
    value = payload.get(key)
    return value[:maximum] if isinstance(value, str) else ""


def _strings(payload: dict[str, Any], key: str, maximum: int) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [entry[:200] for entry in [e for e in value if isinstance(e, str)][:maximum]]


def _component_from(payload: Any) -> CatalogComponent | None:
    record = as_record(payload)
    if record is None:
        return None
    identifier = record.get("id")
    version = record.get("version")
    if not isinstance(identifier, str) or ID.fullmatch(identifier) is None:
        return None
    if not isinstance(version, str) or ID.fullmatch(version) is None:
        return None
    return CatalogComponent(
        id=identifier,
        version=version,
        title=_text(record, "title", 120),
        description=_text(record, "description", 400),
        capabilities=_strings(record, "capabilities", 12),
        use_when=_strings(record, "useWhen", 8),
        avoid_when=_strings(record, "avoidWhen", 8),
        props_schema=as_record(record.get("propsSchema")) or {},
        allowed_data_resolvers=_strings(record, "allowedDataResolvers", 12),
        allowed_actions=_strings(record, "allowedActions", 12),
    )


def _parse_render_spec(ui: dict[str, Any]) -> UiRenderSpec | None:
    instance_id = ui.get("instanceId")
    component_id = ui.get("componentId")
    component_version = ui.get("componentVersion")
    fallback_text = ui.get("fallbackText")
    if not isinstance(instance_id, str) or not isinstance(component_id, str):
        return None
    if not isinstance(component_version, str) or not isinstance(fallback_text, str):
        return None
    return UiRenderSpec(
        instance_id=instance_id,
        component_id=component_id,
        component_version=component_version,
        props=as_record(ui.get("props")) or {},
        data=as_record(ui.get("data")),
        fallback_text=fallback_text,
    )


def _parse_patch_spec(ui: dict[str, Any]) -> UiPatchSpec | None:
    instance_id = ui.get("instanceId")
    patch = as_record(ui.get("patch"))
    if not isinstance(instance_id, str) or patch is None:
        return None
    return UiPatchSpec(instance_id=instance_id, patch=patch)


def _error_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    record = as_record(payload)
    if record is not None:
        for key in ("detail", "message"):
            value = record.get(key)
            if isinstance(value, str) and ID.fullmatch(value) is not None:
                return value
    if response.status_code == 401:
        return "backend_unauthorized"
    if response.status_code == 403:
        return "permission_denied"
    if response.status_code == 409:
        return "component_version_mismatch"
    if response.status_code == 413:
        return "payload_too_large"
    return "backend_request_failed"


class HttpAgentUiClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = normalize_base_url(base_url)
        if api_key.strip() == "":
            raise ValueError("backend API key must not be empty")
        self._api_key = api_key
        self._transport = transport

    async def catalog(
        self,
        workspace_id: str,
        *,
        query: str | None = None,
        capabilities: list[str] | None = None,
        limit: int | None = None,
    ) -> list[CatalogComponent]:
        params: dict[str, str] = {"workspace_id": self._workspace(workspace_id)}
        if query:
            params["query"] = query.strip()[:200]
        if capabilities is not None:
            params["capabilities"] = ",".join(
                entry.strip()[:64] for entry in [c for c in capabilities if isinstance(c, str)][:12]
            )
        params["limit"] = str(max(1, min(limit if limit is not None else MAX_CATALOG_ENTRIES, 50)))
        payload = await self._request("GET", "v1/agent-ui/catalog", "catalog", params=params)
        entries = payload.get("components")
        if not isinstance(entries, list):
            raise AgentUiError("catalog", "invalid_backend_payload")
        components: list[CatalogComponent] = []
        for entry in entries[:50]:
            component = _component_from(entry)
            if component is not None:
                components.append(component)
        return components

    async def render(
        self,
        workspace_id: str,
        *,
        component_id: str,
        component_version: str | None = None,
        props: dict[str, Any],
        data: dict[str, Any] | None = None,
        fallback_text: str,
    ) -> UiRenderSpec:
        body: dict[str, Any] = {
            "componentId": component_id,
            "props": strip_session_keys(props),
            "fallbackText": fallback_text,
        }
        if component_version:
            body["componentVersion"] = component_version
        if data is not None:
            body["data"] = data
        payload = await self._request(
            "POST",
            "v1/agent-ui/render",
            "render",
            params={"workspace_id": self._workspace(workspace_id)},
            json_body=body,
        )
        return self._spec(payload, "render", _parse_render_spec)

    async def patch(
        self,
        workspace_id: str,
        *,
        instance_id: str,
        component_id: str,
        component_version: str | None = None,
        props: dict[str, Any],
    ) -> UiPatchSpec:
        body: dict[str, Any] = {
            "instanceId": instance_id,
            "componentId": component_id,
            "props": strip_session_keys(props),
        }
        if component_version:
            body["componentVersion"] = component_version
        payload = await self._request(
            "POST",
            "v1/agent-ui/patch",
            "patch",
            params={"workspace_id": self._workspace(workspace_id)},
            json_body=body,
        )
        return self._spec(payload, "patch", _parse_patch_spec)

    @staticmethod
    def _spec(payload: dict[str, Any], operation: str, parse: Any) -> Any:
        ui = as_record(payload.get("ui"))
        if ui is None:
            raise AgentUiError(operation, "invalid_backend_payload")
        parsed = parse(ui)
        if parsed is None:
            raise AgentUiError(operation, "invalid_backend_payload")
        return parsed

    @staticmethod
    def _workspace(workspace_id: str) -> str:
        if ACCOUNT_ID.fullmatch(workspace_id) is None:
            raise AgentUiError("request", "invalid_workspace_id")
        return workspace_id

    async def _request(
        self,
        method: str,
        path: str,
        operation: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json", "X-API-Key": self._api_key}
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                response = await client.request(
                    method,
                    self._base_url + path,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
        except Exception as error:
            raise AgentUiError(operation, "backend_unreachable") from error
        if response.status_code >= 400:
            raise AgentUiError(operation, _error_code(response))
        try:
            payload = response.json()
        except ValueError as error:
            raise AgentUiError(operation, "invalid_backend_payload") from error
        record = as_record(payload)
        if record is None:
            raise AgentUiError(operation, "invalid_backend_payload")
        return record


class UiInstanceLedger:
    def __init__(self) -> None:
        self.known: dict[str, tuple[str, str]] = {}

    def declare(self, instance_id: str, component_id: str, component_version: str) -> None:
        self.known[instance_id] = (component_id, component_version)

    def lookup(self, instance_id: str) -> tuple[str, str] | None:
        return self.known.get(instance_id)
