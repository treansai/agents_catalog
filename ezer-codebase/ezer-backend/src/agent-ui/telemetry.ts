import { Logger } from "@nestjs/common";

export type AgentUiEventName =
  | "component_selected"
  | "component_validation_failed"
  | "component_render_started"
  | "component_render_succeeded"
  | "component_render_failed"
  | "data_resolver_started"
  | "data_resolver_succeeded"
  | "data_resolver_failed"
  | "ui_action_received"
  | "ui_action_rejected"
  | "ui_action_succeeded";

export interface AgentUiTelemetry {
  event: AgentUiEventName;
  traceId: string;
  conversationId?: string;
  messageId?: string;
  instanceId?: string;
  componentId?: string;
  componentVersion?: string;
  resolverId?: string;
  actionId?: string;
  durationMs?: number;
  status: "ok" | "error" | "denied";
  code?: string;
}

const SENSITIVE_KEYS = new Set(["email", "token", "secret", "password", "body", "snippet", "subject", "address", "props", "data"]);
const logger = new Logger("AgentUi");

export function emitAgentUiEvent(entry: AgentUiTelemetry): void {
  const redacted: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(entry)) {
    if (value === undefined) continue;
    redacted[key] = SENSITIVE_KEYS.has(key.toLowerCase()) ? "[redacted]" : value;
  }
  logger.log(JSON.stringify(redacted));
}
