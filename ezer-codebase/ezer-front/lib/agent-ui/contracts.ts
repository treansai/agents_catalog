import { assertSchema, jsonSize, type JsonSchema } from "./schema.ts";

export const AGENT_UI_PROTOCOL_VERSION = "1.0" as const;
export const MAX_INLINE_BYTES = 32 * 1024;
export const MAX_INPUT_BYTES = 8 * 1024;

const ID = /^[A-Za-z0-9._:-]{1,128}$/;

export type ChatRole = "user" | "assistant" | "system";

export type AgentUiDataSource =
  | { mode: "inline"; value: unknown }
  | { mode: "resolver"; resolverId: string; input: Record<string, unknown> };

export interface UiRenderSpec {
  instanceId: string;
  componentId: string;
  componentVersion: string;
  props: Record<string, unknown>;
  data?: AgentUiDataSource;
  fallbackText: string;
}

export type ChatMessage =
  | {
      kind: "text";
      protocolVersion: typeof AGENT_UI_PROTOCOL_VERSION;
      id: string;
      role: ChatRole;
      content: string;
      createdAt: string;
    }
  | {
      kind: "ui.render";
      protocolVersion: typeof AGENT_UI_PROTOCOL_VERSION;
      id: string;
      role: "assistant";
      createdAt: string;
      ui: UiRenderSpec;
    }
  | {
      kind: "ui.patch";
      protocolVersion: typeof AGENT_UI_PROTOCOL_VERSION;
      id: string;
      role: "assistant";
      createdAt: string;
      ui: { instanceId: string; patch: Record<string, unknown> };
    }
  | {
      kind: "ui.remove";
      protocolVersion: typeof AGENT_UI_PROTOCOL_VERSION;
      id: string;
      role: "assistant";
      createdAt: string;
      ui: { instanceId: string };
    };

export type DataLoadState =
  | "idle"
  | "loading"
  | "success"
  | "empty"
  | "error"
  | "permission_denied"
  | "stale";

export interface AgentUiActionEvent {
  kind: "ui.action";
  eventId: string;
  messageId: string;
  instanceId: string;
  componentId: string;
  componentVersion: string;
  actionId: string;
  values: Record<string, unknown>;
  idempotencyKey: string;
  createdAt: string;
}

export interface CatalogEntry {
  id: string;
  version: string;
  title: string;
  description: string;
  capabilities: string[];
  useWhen: string[];
  avoidWhen?: string[];
  propsSchema: JsonSchema;
  dataSchema?: JsonSchema;
  allowedDataResolvers: string[];
  allowedActions: string[];
  examples: Record<string, unknown>[];
}

export class AgentUiClientError extends Error {
  readonly code:
    | "unknown_component"
    | "version_mismatch"
    | "invalid_payload"
    | "unknown_resolver"
    | "payload_too_large"
    | "permission_denied"
    | "render_failed";

  constructor(
    code:
      | "unknown_component"
      | "version_mismatch"
      | "invalid_payload"
      | "unknown_resolver"
      | "payload_too_large"
      | "permission_denied"
      | "render_failed",
    message: string,
  ) {
    super(message);
    this.name = "AgentUiClientError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asId(value: unknown, field: string): string {
  if (typeof value !== "string" || !ID.test(value)) {
    throw new AgentUiClientError("invalid_payload", `invalid ${field}`);
  }
  return value;
}

export function parseDataSource(value: unknown): AgentUiDataSource | undefined {
  if (value === undefined) return undefined;
  if (!isRecord(value) || typeof value.mode !== "string") {
    throw new AgentUiClientError("invalid_payload", "invalid data source");
  }
  if (value.mode === "inline") {
    if (jsonSize(value.value) > MAX_INLINE_BYTES) {
      throw new AgentUiClientError("payload_too_large", "inline data too large");
    }
    return { mode: "inline", value: value.value };
  }
  if (value.mode === "resolver") {
    if (typeof value.resolverId !== "string" || !ID.test(value.resolverId)) {
      throw new AgentUiClientError("unknown_resolver", "invalid resolverId");
    }
    if (!isRecord(value.input)) {
      throw new AgentUiClientError("invalid_payload", "invalid resolver input");
    }
    if (jsonSize(value.input) > MAX_INPUT_BYTES) {
      throw new AgentUiClientError("payload_too_large", "resolver input too large");
    }
    const input = { ...value.input };
    delete input.userId;
    delete input.workspaceId;
    delete input.permissions;
    delete input.account_id;
    return { mode: "resolver", resolverId: value.resolverId, input };
  }
  throw new AgentUiClientError("invalid_payload", "invalid data mode");
}

export function parseRenderSpec(value: unknown): UiRenderSpec {
  if (!isRecord(value)) throw new AgentUiClientError("invalid_payload", "invalid ui spec");
  const fallbackText = value.fallbackText;
  if (typeof fallbackText !== "string" || fallbackText.length < 1 || fallbackText.length > 2_000) {
    throw new AgentUiClientError("invalid_payload", "fallbackText is required");
  }
  const props = isRecord(value.props) ? value.props : {};
  if (jsonSize(props) > MAX_INPUT_BYTES) {
    throw new AgentUiClientError("payload_too_large", "props too large");
  }
  return {
    instanceId: asId(value.instanceId, "instanceId"),
    componentId: asId(value.componentId, "componentId"),
    componentVersion: asId(value.componentVersion, "componentVersion"),
    props,
    data: parseDataSource(value.data),
    fallbackText,
  };
}

export function parseChatMessage(value: unknown): ChatMessage {
  if (!isRecord(value) || typeof value.kind !== "string") {
    throw new AgentUiClientError("invalid_payload", "invalid message");
  }
  const protocolVersion =
    value.protocolVersion === AGENT_UI_PROTOCOL_VERSION ? AGENT_UI_PROTOCOL_VERSION : AGENT_UI_PROTOCOL_VERSION;
  const id = asId(value.id, "id");
  const createdAt = typeof value.createdAt === "string" ? value.createdAt : new Date().toISOString();
  if (value.kind === "text") {
    const role = value.role;
    if (role !== "user" && role !== "assistant" && role !== "system") {
      throw new AgentUiClientError("invalid_payload", "invalid role");
    }
    if (typeof value.content !== "string" || value.content.length > 8_000) {
      throw new AgentUiClientError("invalid_payload", "invalid content");
    }
    return { kind: "text", protocolVersion, id, role, content: value.content, createdAt };
  }
  if (value.role !== "assistant") {
    throw new AgentUiClientError("invalid_payload", "ui messages are assistant-only");
  }
  if (value.kind === "ui.render") {
    return { kind: "ui.render", protocolVersion, id, role: "assistant", createdAt, ui: parseRenderSpec(value.ui) };
  }
  if (value.kind === "ui.patch") {
    if (!isRecord(value.ui) || !isRecord(value.ui.patch)) {
      throw new AgentUiClientError("invalid_payload", "invalid patch");
    }
    return {
      kind: "ui.patch",
      protocolVersion,
      id,
      role: "assistant",
      createdAt,
      ui: { instanceId: asId(value.ui.instanceId, "instanceId"), patch: value.ui.patch },
    };
  }
  if (value.kind === "ui.remove") {
    if (!isRecord(value.ui)) throw new AgentUiClientError("invalid_payload", "invalid remove");
    return {
      kind: "ui.remove",
      protocolVersion,
      id,
      role: "assistant",
      createdAt,
      ui: { instanceId: asId(value.ui.instanceId, "instanceId") },
    };
  }
  throw new AgentUiClientError("invalid_payload", "unknown message kind");
}

export function validateAgainst(schema: JsonSchema | undefined, value: unknown): void {
  if (schema === undefined) return;
  assertSchema(value, schema);
}

export function newId(prefix: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}
