import type { DataLoadState } from "@/lib/agent-ui/contracts";

export interface AgentComponentProps {
  instanceId: string;
  messageId: string;
  componentId: string;
  componentVersion: string;
  props: Record<string, unknown>;
  data: unknown;
  dataStatus: DataLoadState;
  onAction: (actionId: string, values: Record<string, unknown>) => void;
}

export function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function asBoolean(value: unknown): boolean {
  return value === true;
}

export function initialsFrom(name: string): string {
  const parts = name.trim().split(/\s+/).slice(0, 2);
  return parts.map((part) => part.slice(0, 1)).join("") || "?";
}
