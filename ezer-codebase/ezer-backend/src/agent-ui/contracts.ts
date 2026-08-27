import { assertSchema, jsonSize, type JsonSchema } from "./schema";
import { agentUiError } from "./errors";

export const AGENT_UI_PROTOCOL_VERSION = "1.0" as const;
export const MAX_INLINE_BYTES = 32 * 1024;
export const MAX_INPUT_BYTES = 8 * 1024;
export const MAX_ACTION_VALUES_BYTES = 8 * 1024;

export const ID_PATTERN = "^[A-Za-z0-9._:-]{1,128}$";
const ID = new RegExp(ID_PATTERN);

export type AgentUiDataSource =
  | { mode: "inline"; value: unknown }
  | { mode: "resolver"; resolverId: string; input: Record<string, unknown> };

export interface AgentUiRenderSpec {
  instanceId: string;
  componentId: string;
  componentVersion: string;
  props: Record<string, unknown>;
  data?: AgentUiDataSource;
  fallbackText: string;
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

export interface DataResolverContext {
  userId: string;
  workspaceId: string;
  permissions: string[];
  traceId: string;
  signal?: AbortSignal;
}

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && Array.isArray(value) === false;
}

function asId(value: unknown, field: string): string {
  if (typeof value !== "string" || !ID.test(value)) {
    throw agentUiError("invalid_payload", 400, `invalid ${field}`);
  }
  return value;
}

export function parseDataSource(value: unknown): AgentUiDataSource | undefined {
  if (value === undefined) return undefined;
  if (!isRecord(value) || typeof value.mode !== "string") {
    throw agentUiError("invalid_payload", 400, "invalid data source");
  }
  if (value.mode === "inline") {
    if (jsonSize(value.value) > MAX_INLINE_BYTES) {
      throw agentUiError("payload_too_large", 413, "inline data too large");
    }
    return { mode: "inline", value: value.value };
  }
  if (value.mode === "resolver") {
    if (typeof value.resolverId !== "string" || !ID.test(value.resolverId)) {
      throw agentUiError("unknown_resolver", 400, "invalid resolverId");
    }
    if (!isRecord(value.input)) throw agentUiError("invalid_payload", 400, "invalid resolver input");
    if (jsonSize(value.input) > MAX_INPUT_BYTES) {
      throw agentUiError("payload_too_large", 413, "resolver input too large");
    }
    const input = { ...value.input };
    delete input.userId;
    delete input.workspaceId;
    delete input.permissions;
    delete input.account_id;
    return { mode: "resolver", resolverId: value.resolverId, input };
  }
  throw agentUiError("invalid_payload", 400, "invalid data mode");
}

export function parseRenderSpec(value: unknown): AgentUiRenderSpec {
  if (!isRecord(value)) throw agentUiError("invalid_payload", 400, "invalid ui spec");
  const fallbackText = value.fallbackText;
  if (typeof fallbackText !== "string" || fallbackText.length < 1 || fallbackText.length > 2_000) {
    throw agentUiError("invalid_payload", 400, "fallbackText is required");
  }
  const props = isRecord(value.props) ? value.props : {};
  if (jsonSize(props) > MAX_INPUT_BYTES) throw agentUiError("payload_too_large", 413, "props too large");
  return {
    instanceId: asId(value.instanceId, "instanceId"),
    componentId: asId(value.componentId, "componentId"),
    componentVersion: asId(value.componentVersion, "componentVersion"),
    props,
    data: parseDataSource(value.data),
    fallbackText
  };
}

export function parseActionEvent(value: unknown): AgentUiActionEvent {
  if (!isRecord(value) || value.kind !== "ui.action") {
    throw agentUiError("invalid_payload", 400, "invalid action event");
  }
  const values = isRecord(value.values) ? value.values : {};
  if (jsonSize(values) > MAX_ACTION_VALUES_BYTES) {
    throw agentUiError("payload_too_large", 413, "action values too large");
  }
  const createdAt = typeof value.createdAt === "string" ? value.createdAt : new Date().toISOString();
  return {
    kind: "ui.action",
    eventId: asId(value.eventId, "eventId"),
    messageId: asId(value.messageId, "messageId"),
    instanceId: asId(value.instanceId, "instanceId"),
    componentId: asId(value.componentId, "componentId"),
    componentVersion: asId(value.componentVersion, "componentVersion"),
    actionId: asId(value.actionId, "actionId"),
    values,
    idempotencyKey: asId(value.idempotencyKey, "idempotencyKey"),
    createdAt
  };
}

export const MAIL_ROW_SCHEMA: JsonSchema = {
  type: "object",
  additionalProperties: false,
  required: ["id", "sender", "subject", "snippet", "receivedAt", "unread"],
  properties: {
    id: { type: "string", minLength: 1, maxLength: 512 },
    sender: { type: "string", maxLength: 320 },
    senderAddress: { type: "string", maxLength: 320 },
    subject: { type: "string", maxLength: 400 },
    snippet: { type: "string", maxLength: 600 },
    receivedAt: { type: "string", maxLength: 64 },
    unread: { type: "boolean" },
    hasAttachments: { type: "boolean" },
    tag: { type: "string", maxLength: 32 }
  }
};

export const MAIL_PAGE_SCHEMA: JsonSchema = {
  type: "object",
  additionalProperties: false,
  required: ["items", "total", "limit", "offset"],
  properties: {
    items: { type: "array", maxItems: 25, items: MAIL_ROW_SCHEMA },
    total: { type: "integer", minimum: 0, maximum: 1_000_000 },
    limit: { type: "integer", minimum: 1, maximum: 25 },
    offset: { type: "integer", minimum: 0, maximum: 1_000_000 }
  }
};

export function assertOutput(value: unknown, schema: JsonSchema | undefined): unknown {
  if (schema === undefined) return value;
  assertSchema(value, schema);
  return value;
}
