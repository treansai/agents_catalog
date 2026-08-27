/**
 * Validation serveur des instructions d'interface produites par l'agent.
 *
 * L'agent ne fabrique jamais une instance : il propose un `componentId`, une version, des props et
 * une source de données. Le serveur vérifie le tout contre le catalogue, puis frappe lui-même
 * l'`instanceId`. Aucun identifiant de session (userId, workspaceId, permissions) ne peut venir du
 * modèle : il est retiré des entrées avant validation.
 */

import { randomBytes } from "node:crypto";

import { getComponentDefinition, type ComponentCatalogDefinition } from "./catalog";
import {
  parseDataSource,
  type AgentUiDataSource,
  type AgentUiRenderSpec,
  ID_PATTERN,
  MAX_INPUT_BYTES
} from "./contracts";
import { agentUiError } from "./errors";
import { assertSchema, jsonSize, SchemaValidationError, type JsonSchema } from "./schema";

const ID = new RegExp(ID_PATTERN);

export interface AgentUiSession {
  userId: string;
  workspaceId: string;
  permissions: string[];
  traceId: string;
}

export interface RenderRequest {
  componentId: string;
  componentVersion?: string;
  props?: Record<string, unknown>;
  data?: unknown;
  fallbackText: string;
}

export interface PatchRequest {
  instanceId: string;
  componentId: string;
  componentVersion?: string;
  props: Record<string, unknown>;
}

export function newInstanceId(): string {
  return `ui_${randomBytes(12).toString("hex")}`;
}

/** Le composant existe, la version correspond, et la session a les permissions requises. */
export function requireComponent(
  componentId: string,
  componentVersion: string | undefined,
  session: AgentUiSession
): ComponentCatalogDefinition {
  const component = getComponentDefinition(componentId);
  if (component === undefined) {
    throw agentUiError("unknown_component", 400, "unknown_component");
  }
  const granted = new Set(session.permissions);
  if (!component.requiredPermissions.every((permission) => granted.has(permission))) {
    throw agentUiError("permission_denied", 403, "permission_denied");
  }
  if (componentVersion !== undefined && componentVersion !== component.version) {
    throw agentUiError("component_version_mismatch", 409, "component_version_mismatch");
  }
  return component;
}

function assertProps(props: Record<string, unknown>, schema: JsonSchema): void {
  if (jsonSize(props) > MAX_INPUT_BYTES) {
    throw agentUiError("payload_too_large", 413, "props too large");
  }
  try {
    assertSchema(props, schema);
  } catch (error) {
    if (error instanceof SchemaValidationError) {
      throw agentUiError("invalid_props", 400, error.message);
    }
    throw error;
  }
}

function assertDataSource(
  data: AgentUiDataSource | undefined,
  component: ComponentCatalogDefinition
): void {
  if (data === undefined || data.mode !== "resolver") return;
  if (!component.allowedDataResolvers.includes(data.resolverId)) {
    throw agentUiError("unknown_resolver", 400, "unauthorized_resolver");
  }
}

/** Rend une instruction `ui.render` sûre à partir d'une proposition du modèle. */
export function buildRenderSpec(request: RenderRequest, session: AgentUiSession): AgentUiRenderSpec {
  const component = requireComponent(request.componentId, request.componentVersion, session);
  const fallbackText = request.fallbackText;
  if (typeof fallbackText !== "string" || fallbackText.length < 1 || fallbackText.length > 2_000) {
    throw agentUiError("invalid_payload", 400, "fallbackText is required");
  }
  const props = { ...(request.props ?? {}) };
  assertProps(props, component.propsSchema);
  const data = parseDataSource(request.data);
  assertDataSource(data, component);
  return {
    instanceId: newInstanceId(),
    componentId: component.id,
    componentVersion: component.version,
    props,
    ...(data === undefined ? {} : { data }),
    fallbackText
  };
}

/**
 * Un patch ne porte que des props : il est validé contre le schéma du composant privé de ses
 * `required`, puisqu'il est partiel par nature.
 */
export function buildPatch(
  request: PatchRequest,
  session: AgentUiSession
): { instanceId: string; patch: Record<string, unknown> } {
  const component = requireComponent(request.componentId, request.componentVersion, session);
  if (typeof request.instanceId !== "string" || !ID.test(request.instanceId)) {
    throw agentUiError("invalid_payload", 400, "invalid instanceId");
  }
  const patch = { ...request.props };
  if (Object.keys(patch).length === 0) {
    throw agentUiError("invalid_payload", 400, "empty patch");
  }
  const partial: JsonSchema = { ...component.propsSchema, required: [] };
  assertProps(patch, partial);
  return { instanceId: request.instanceId, patch };
}
