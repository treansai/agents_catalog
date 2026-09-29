import { randomUUID } from "node:crypto";

export const PROTOCOL_VERSION = "1.0";
export const MAX_UI_MESSAGES = 6;

const ID = /^[A-Za-z0-9._:-]{1,128}$/;
const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const TIMEOUT_MS = 20_000;
const MAX_CATALOG_ENTRIES = 20;

const SESSION_KEYS = new Set([
  "userId",
  "workspaceId",
  "tenantId",
  "permissions",
  "account_id",
  "accountId",
  "credentials"
]);

export class AgentUiError extends Error {
  constructor(
    readonly operation: string,
    readonly code: string,
    readonly detail = ""
  ) {
    super(`${operation} failed: ${code}`);
    this.name = "AgentUiError";
  }
}

export interface UiRenderSpec {
  instanceId: string;
  componentId: string;
  componentVersion: string;
  props: Record<string, unknown>;
  data: Record<string, unknown> | null;
  fallbackText: string;
}

export interface UiPatchSpec {
  instanceId: string;
  patch: Record<string, unknown>;
}

export interface UiRemoveSpec {
  instanceId: string;
}

export interface UiRenderMessage {
  kind: "ui.render";
  protocolVersion: string;
  id: string;
  role: "assistant";
  createdAt: string;
  ui: UiRenderSpec;
}

export interface UiPatchMessage {
  kind: "ui.patch";
  protocolVersion: string;
  id: string;
  role: "assistant";
  createdAt: string;
  ui: UiPatchSpec;
}

export interface UiRemoveMessage {
  kind: "ui.remove";
  protocolVersion: string;
  id: string;
  role: "assistant";
  createdAt: string;
  ui: UiRemoveSpec;
}

export type UiMessage = UiRenderMessage | UiPatchMessage | UiRemoveMessage;

export interface UiInstanceRef {
  instance_id: string;
  component_id: string;
  component_version: string;
}

export interface UiActionEvent {
  kind: "ui.action";
  event_id: string;
  message_id: string;
  instance_id: string;
  component_id: string;
  component_version: string;
  action_id: string;
  values: Record<string, unknown>;
  idempotency_key: string;
  result?: Record<string, unknown>;
}

export interface CatalogComponent {
  id: string;
  version: string;
  title: string;
  description: string;
  capabilities: string[];
  useWhen: string[];
  avoidWhen: string[];
  propsSchema: Record<string, unknown>;
  allowedDataResolvers: string[];
  allowedActions: string[];
}

export function catalogPromptPayload(component: CatalogComponent): Record<string, unknown> {
  return {
    id: component.id,
    version: component.version,
    description: component.description,
    useWhen: component.useWhen,
    avoidWhen: component.avoidWhen,
    propsSchema: component.propsSchema,
    allowedDataResolvers: component.allowedDataResolvers,
    allowedActions: component.allowedActions
  };
}

export interface AgentUiClient {
  catalog(
    workspaceId: string,
    options?: { query?: string; capabilities?: string[]; limit?: number }
  ): Promise<CatalogComponent[]>;
  render(
    workspaceId: string,
    options: {
      componentId: string;
      componentVersion?: string;
      props: Record<string, unknown>;
      data?: Record<string, unknown>;
      fallbackText: string;
    }
  ): Promise<UiRenderSpec>;
  patch(
    workspaceId: string,
    options: {
      instanceId: string;
      componentId: string;
      componentVersion?: string;
      props: Record<string, unknown>;
    }
  ): Promise<UiPatchSpec>;
}

export function stripSessionKeys(payload: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(payload).filter(([key]) => !SESSION_KEYS.has(key)));
}

export function newMessageId(): string {
  return `ui-${randomUUID().replaceAll("-", "")}`;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function text(payload: Record<string, unknown>, key: string, maximum: number): string {
  const value = payload[key];
  return typeof value === "string" ? value.slice(0, maximum) : "";
}

function strings(payload: Record<string, unknown>, key: string, maximum: number): string[] {
  const value = payload[key];
  if (!Array.isArray(value)) return [];
  return value.filter((entry): entry is string => typeof entry === "string").slice(0, maximum).map((entry) => entry.slice(0, 200));
}

function componentFrom(payload: unknown): CatalogComponent | undefined {
  const record = asRecord(payload);
  if (record === undefined) return undefined;
  const identifier = record.id;
  const version = record.version;
  if (typeof identifier !== "string" || ID.exec(identifier) === null) return undefined;
  if (typeof version !== "string" || ID.exec(version) === null) return undefined;
  const schema = record.propsSchema;
  return {
    id: identifier,
    version,
    title: text(record, "title", 120),
    description: text(record, "description", 400),
    capabilities: strings(record, "capabilities", 12),
    useWhen: strings(record, "useWhen", 8),
    avoidWhen: strings(record, "avoidWhen", 8),
    propsSchema: asRecord(schema) ?? {},
    allowedDataResolvers: strings(record, "allowedDataResolvers", 12),
    allowedActions: strings(record, "allowedActions", 12)
  };
}

export class HttpAgentUiClient implements AgentUiClient {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly fetchImpl: typeof fetch;

  constructor(baseUrl: string, apiKey: string, fetchImpl: typeof fetch = fetch) {
    const normalized = baseUrl.trim();
    if (normalized === "") throw new Error("backend base URL must not be empty");
    const withSlash = normalized.endsWith("/") ? normalized : `${normalized}/`;
    let parsed: URL;
    try {
      parsed = new URL(withSlash);
    } catch {
      throw new Error("backend base URL must be an absolute http(s) URL");
    }
    if ((parsed.protocol !== "http:" && parsed.protocol !== "https:") || parsed.hostname === "") {
      throw new Error("backend base URL must be an absolute http(s) URL");
    }
    if (apiKey.trim() === "") throw new Error("backend API key must not be empty");
    this.baseUrl = withSlash;
    this.apiKey = apiKey;
    this.fetchImpl = fetchImpl;
  }

  async catalog(
    workspaceId: string,
    options: { query?: string; capabilities?: string[]; limit?: number } = {}
  ): Promise<CatalogComponent[]> {
    const params: Record<string, string> = { workspace_id: this.workspace(workspaceId) };
    if (options.query) params.query = options.query.trim().slice(0, 200);
    if (options.capabilities) {
      params.capabilities = options.capabilities
        .filter((entry): entry is string => typeof entry === "string")
        .slice(0, 12)
        .map((entry) => entry.trim().slice(0, 64))
        .join(",");
    }
    params.limit = String(Math.max(1, Math.min(options.limit ?? MAX_CATALOG_ENTRIES, 50)));
    const payload = await this.request("GET", "v1/agent-ui/catalog", "catalog", { params });
    const entries = payload.components;
    if (!Array.isArray(entries)) throw new AgentUiError("catalog", "invalid_backend_payload");
    return entries.slice(0, 50).flatMap((entry) => {
      const component = componentFrom(entry);
      return component === undefined ? [] : [component];
    });
  }

  async render(
    workspaceId: string,
    options: {
      componentId: string;
      componentVersion?: string;
      props: Record<string, unknown>;
      data?: Record<string, unknown>;
      fallbackText: string;
    }
  ): Promise<UiRenderSpec> {
    const body: Record<string, unknown> = {
      componentId: options.componentId,
      props: stripSessionKeys(options.props),
      fallbackText: options.fallbackText
    };
    if (options.componentVersion) body.componentVersion = options.componentVersion;
    if (options.data !== undefined) body.data = options.data;
    const payload = await this.request("POST", "v1/agent-ui/render", "render", {
      params: { workspace_id: this.workspace(workspaceId) },
      json: body
    });
    return this.spec(payload, "render", parseRenderSpec);
  }

  async patch(
    workspaceId: string,
    options: {
      instanceId: string;
      componentId: string;
      componentVersion?: string;
      props: Record<string, unknown>;
    }
  ): Promise<UiPatchSpec> {
    const body: Record<string, unknown> = {
      instanceId: options.instanceId,
      componentId: options.componentId,
      props: stripSessionKeys(options.props)
    };
    if (options.componentVersion) body.componentVersion = options.componentVersion;
    const payload = await this.request("POST", "v1/agent-ui/patch", "patch", {
      params: { workspace_id: this.workspace(workspaceId) },
      json: body
    });
    return this.spec(payload, "patch", parsePatchSpec);
  }

  private spec<T>(
    payload: Record<string, unknown>,
    operation: string,
    parse: (ui: Record<string, unknown>) => T | undefined
  ): T {
    const ui = asRecord(payload.ui);
    if (ui === undefined) throw new AgentUiError(operation, "invalid_backend_payload");
    const parsed = parse(ui);
    if (parsed === undefined) throw new AgentUiError(operation, "invalid_backend_payload");
    return parsed;
  }

  private workspace(workspaceId: string): string {
    if (ACCOUNT_ID.exec(workspaceId) === null) {
      throw new AgentUiError("request", "invalid_workspace_id");
    }
    return workspaceId;
  }

  private async request(
    method: string,
    path: string,
    operation: string,
    options: { params?: Record<string, string>; json?: Record<string, unknown> } = {}
  ): Promise<Record<string, unknown>> {
    const url = new URL(path, this.baseUrl);
    if (options.params !== undefined) {
      for (const [key, value] of Object.entries(options.params)) {
        url.searchParams.set(key, value);
      }
    }
    let response: Response;
    try {
      response = await this.fetchImpl(url, {
        method,
        headers: {
          Accept: "application/json",
          "X-API-Key": this.apiKey,
          ...(options.json === undefined ? {} : { "Content-Type": "application/json" })
        },
        body: options.json === undefined ? undefined : JSON.stringify(options.json),
        signal: AbortSignal.timeout(TIMEOUT_MS),
        redirect: "manual"
      });
    } catch {
      throw new AgentUiError(operation, "backend_unreachable");
    }
    if (response.status >= 400) {
      throw new AgentUiError(operation, await errorCode(response));
    }
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new AgentUiError(operation, "invalid_backend_payload");
    }
    const record = asRecord(payload);
    if (record === undefined) throw new AgentUiError(operation, "invalid_backend_payload");
    return record;
  }
}

function parseRenderSpec(ui: Record<string, unknown>): UiRenderSpec | undefined {
  const instanceId = ui.instanceId;
  const componentId = ui.componentId;
  const componentVersion = ui.componentVersion;
  const fallbackText = ui.fallbackText;
  if (typeof instanceId !== "string" || typeof componentId !== "string") return undefined;
  if (typeof componentVersion !== "string" || typeof fallbackText !== "string") return undefined;
  return {
    instanceId,
    componentId,
    componentVersion,
    props: asRecord(ui.props) ?? {},
    data: asRecord(ui.data) ?? null,
    fallbackText
  };
}

function parsePatchSpec(ui: Record<string, unknown>): UiPatchSpec | undefined {
  const instanceId = ui.instanceId;
  const patch = asRecord(ui.patch);
  if (typeof instanceId !== "string" || patch === undefined) return undefined;
  return { instanceId, patch };
}

async function errorCode(response: Response): Promise<string> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = undefined;
  }
  const record = asRecord(payload);
  if (record !== undefined) {
    for (const key of ["detail", "message"] as const) {
      const value = record[key];
      if (typeof value === "string" && ID.exec(value) !== null) return value;
    }
  }
  if (response.status === 401) return "backend_unauthorized";
  if (response.status === 403) return "permission_denied";
  if (response.status === 409) return "component_version_mismatch";
  if (response.status === 413) return "payload_too_large";
  return "backend_request_failed";
}

export class UiInstanceLedger {
  readonly known = new Map<string, [string, string]>();

  declare(instanceId: string, componentId: string, componentVersion: string): void {
    this.known.set(instanceId, [componentId, componentVersion]);
  }

  lookup(instanceId: string): [string, string] | undefined {
    return this.known.get(instanceId);
  }
}
