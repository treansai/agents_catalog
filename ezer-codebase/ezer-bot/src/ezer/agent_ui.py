"""Agent UI runtime client: the assistant's only door to the component catalogue.

The agent never invents a component, a version, a prop, a resolver or an instance identifier. It
proposes; the backend validates against the catalogue and mints the `instanceId`. Everything this
module returns is therefore server-approved, and no session identity (userId, workspaceId,
permissions) is ever accepted from the model.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION: Final = "1.0"

_ID: Final = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_ACCOUNT_ID: Final = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_TIMEOUT: Final = httpx.Timeout(20.0, connect=10.0)
_MAX_CATALOG_ENTRIES: Final = 20
MAX_UI_MESSAGES: Final = 6

# Identifiants de session : le modèle n'a pas à les fournir, ils sont retirés de ses entrées.
_SESSION_KEYS: Final = frozenset(
    {"userId", "workspaceId", "tenantId", "permissions", "account_id", "accountId", "credentials"}
)


class AgentUiError(RuntimeError):
    """A content-free failure, carrying the catalogue error code the model may act on."""

    def __init__(self, operation: str, code: str, detail: str = "") -> None:
        self.operation = operation
        self.code = code
        self.detail = detail
        super().__init__(f"{operation} failed: {code}")


class UiRenderSpec(BaseModel):
    """Une instance validée par le serveur : rien ici ne vient tel quel du modèle."""

    model_config = ConfigDict(extra="forbid")

    instanceId: str
    componentId: str
    componentVersion: str
    props: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] | None = None
    fallbackText: str


class UiPatchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instanceId: str
    patch: dict[str, Any]


class UiRemoveSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instanceId: str


class UiRenderMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ui.render"] = "ui.render"
    protocolVersion: str = PROTOCOL_VERSION
    id: str
    role: Literal["assistant"] = "assistant"
    createdAt: str
    ui: UiRenderSpec


class UiPatchMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ui.patch"] = "ui.patch"
    protocolVersion: str = PROTOCOL_VERSION
    id: str
    role: Literal["assistant"] = "assistant"
    createdAt: str
    ui: UiPatchSpec


class UiRemoveMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ui.remove"] = "ui.remove"
    protocolVersion: str = PROTOCOL_VERSION
    id: str
    role: Literal["assistant"] = "assistant"
    createdAt: str
    ui: UiRemoveSpec


UiMessage = UiRenderMessage | UiPatchMessage | UiRemoveMessage


class UiInstanceRef(BaseModel):
    """Une instance encore affichée, annoncée par l'interface pour autoriser un patch."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    component_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    component_version: str = Field(default="1.0", pattern=r"^[A-Za-z0-9._:-]{1,32}$")


class UiActionEvent(BaseModel):
    """Un clic ou une soumission venant d'un composant : entrée utilisateur non fiable."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ui.action"] = "ui.action"
    event_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    message_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    instance_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    component_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    component_version: str = Field(default="1.0", pattern=r"^[A-Za-z0-9._:-]{1,32}$")
    action_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    values: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    # Résultat déjà obtenu côté serveur, s'il y en a un : l'agent réagit, il ne rejoue pas.
    result: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CatalogComponent:
    """Ce que l'agent voit d'un composant : jamais son code, jamais son chemin d'import."""

    id: str
    version: str
    title: str
    description: str
    capabilities: tuple[str, ...]
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    props_schema: dict[str, Any]
    allowed_data_resolvers: tuple[str, ...]
    allowed_actions: tuple[str, ...]

    def as_prompt_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "description": self.description,
            "useWhen": list(self.use_when),
            "avoidWhen": list(self.avoid_when),
            "propsSchema": self.props_schema,
            "allowedDataResolvers": list(self.allowed_data_resolvers),
            "allowedActions": list(self.allowed_actions),
        }


class AgentUiClient(Protocol):
    """Injectable surface, so the assistant is testable without a backend."""

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
        component_version: str | None,
        props: dict[str, Any],
        data: dict[str, Any] | None,
        fallback_text: str,
    ) -> UiRenderSpec: ...

    async def patch(
        self,
        workspace_id: str,
        *,
        instance_id: str,
        component_id: str,
        component_version: str | None,
        props: dict[str, Any],
    ) -> UiPatchSpec: ...


def strip_session_keys(payload: dict[str, Any]) -> dict[str, Any]:
    """Le modèle ne fournit jamais d'identité : elle est retirée avant d'atteindre le serveur."""

    return {key: value for key, value in payload.items() if key not in _SESSION_KEYS}


def new_message_id() -> str:
    return f"ui-{uuid.uuid4().hex}"


def _text(payload: dict[str, Any], key: str, maximum: int) -> str:
    value = payload.get(key)
    return value[:maximum] if isinstance(value, str) else ""


def _strings(payload: dict[str, Any], key: str, maximum: int) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(entry[:200] for entry in value[:maximum] if isinstance(entry, str))


def _component_from(payload: object) -> CatalogComponent | None:
    if not isinstance(payload, dict):
        return None
    identifier = payload.get("id")
    version = payload.get("version")
    if not isinstance(identifier, str) or _ID.fullmatch(identifier) is None:
        return None
    if not isinstance(version, str) or _ID.fullmatch(version) is None:
        return None
    schema = payload.get("propsSchema")
    return CatalogComponent(
        id=identifier,
        version=version,
        title=_text(payload, "title", 120),
        description=_text(payload, "description", 400),
        capabilities=_strings(payload, "capabilities", 12),
        use_when=_strings(payload, "useWhen", 8),
        avoid_when=_strings(payload, "avoidWhen", 8),
        props_schema=schema if isinstance(schema, dict) else {},
        allowed_data_resolvers=_strings(payload, "allowedDataResolvers", 12),
        allowed_actions=_strings(payload, "allowedActions", 12),
    )


class HttpAgentUiClient:
    """`AgentUiClient` backed by the Ezer backend's `/v1/agent-ui` endpoints."""

    def __init__(
        self, base_url: str, api_key: str, client: httpx.AsyncClient | None = None
    ) -> None:
        normalized = base_url.strip()
        if not normalized:
            raise ValueError("backend base URL must not be empty")
        if not normalized.endswith("/"):
            normalized = f"{normalized}/"
        parsed = httpx.URL(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("backend base URL must be an absolute http(s) URL")
        if not api_key.strip():
            raise ValueError("backend API key must not be empty")
        self._base_url = normalized
        self._api_key = api_key
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None

    async def catalog(
        self,
        workspace_id: str,
        *,
        query: str | None = None,
        capabilities: list[str] | None = None,
        limit: int | None = None,
    ) -> list[CatalogComponent]:
        params: dict[str, Any] = {"workspace_id": self._workspace(workspace_id)}
        if query:
            params["query"] = query.strip()[:200]
        if capabilities:
            params["capabilities"] = ",".join(
                entry.strip()[:64] for entry in capabilities[:12] if isinstance(entry, str)
            )
        params["limit"] = max(1, min(limit or _MAX_CATALOG_ENTRIES, 50))
        payload = await self._request("GET", "v1/agent-ui/catalog", "catalog", params=params)
        entries = payload.get("components")
        if not isinstance(entries, list):
            raise AgentUiError("catalog", "invalid_backend_payload")
        return [
            component for component in map(_component_from, entries[:50]) if component is not None
        ]

    async def render(
        self,
        workspace_id: str,
        *,
        component_id: str,
        component_version: str | None,
        props: dict[str, Any],
        data: dict[str, Any] | None,
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
            json=body,
        )
        return self._spec(payload, UiRenderSpec, "render")

    async def patch(
        self,
        workspace_id: str,
        *,
        instance_id: str,
        component_id: str,
        component_version: str | None,
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
            json=body,
        )
        return self._spec(payload, UiPatchSpec, "patch")

    @staticmethod
    def _spec[T: BaseModel](payload: dict[str, Any], model: type[T], operation: str) -> T:
        ui = payload.get("ui")
        if not isinstance(ui, dict):
            raise AgentUiError(operation, "invalid_backend_payload")
        try:
            return model.model_validate(ui)
        except ValueError:
            raise AgentUiError(operation, "invalid_backend_payload") from None

    @staticmethod
    def _workspace(workspace_id: str) -> str:
        if _ACCOUNT_ID.fullmatch(workspace_id) is None:
            raise AgentUiError("request", "invalid_workspace_id")
        return workspace_id

    async def _request(
        self,
        method: str,
        path: str,
        operation: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._client
        if client is None:
            if self._owned_client is None:
                self._owned_client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
            client = self._owned_client
        try:
            response = await client.request(
                method,
                urljoin(self._base_url, path),
                params=params,
                json=json,
                headers={"Accept": "application/json", "X-API-Key": self._api_key},
            )
        except httpx.HTTPError:
            raise AgentUiError(operation, "backend_unreachable") from None

        if response.status_code >= 400:
            raise AgentUiError(operation, _error_code(response))

        try:
            payload = response.json()
        except ValueError:
            raise AgentUiError(operation, "invalid_backend_payload") from None
        if not isinstance(payload, dict):
            raise AgentUiError(operation, "invalid_backend_payload")
        return payload


def _error_code(response: httpx.Response) -> str:
    """Le code du catalogue quand le backend le donne ; sinon un code générique par statut."""

    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("detail", "message"):
            value = payload.get(key)
            if isinstance(value, str) and _ID.fullmatch(value) is not None:
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


@dataclass(slots=True)
class UiInstanceLedger:
    """Les instances qu'un patch peut viser : celles du tour, plus celles encore à l'écran."""

    known: dict[str, tuple[str, str]] = field(default_factory=dict)

    def declare(self, instance_id: str, component_id: str, component_version: str) -> None:
        self.known[instance_id] = (component_id, component_version)

    def lookup(self, instance_id: str) -> tuple[str, str] | None:
        return self.known.get(instance_id)


__all__ = [
    "MAX_UI_MESSAGES",
    "PROTOCOL_VERSION",
    "AgentUiClient",
    "AgentUiError",
    "CatalogComponent",
    "HttpAgentUiClient",
    "UiActionEvent",
    "UiInstanceLedger",
    "UiInstanceRef",
    "UiMessage",
    "UiPatchMessage",
    "UiPatchSpec",
    "UiRemoveMessage",
    "UiRemoveSpec",
    "UiRenderMessage",
    "UiRenderSpec",
    "new_message_id",
    "strip_session_keys",
]
