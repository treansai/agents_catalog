import type { ChatMessage, UiRenderSpec } from "./contracts.ts";

export interface InstanceRecord {
  spec: UiRenderSpec;
  messageId: string;
}

export function applyInstanceEvent(
  current: Map<string, InstanceRecord>,
  message: { kind: string; id: string; ui?: { instanceId?: string; patch?: Record<string, unknown> } & Partial<UiRenderSpec> },
): Map<string, InstanceRecord> {
  const next = new Map(current);
  if (message.kind === "ui.render" && message.ui !== undefined && typeof message.ui.instanceId === "string") {
    next.set(message.ui.instanceId, { spec: message.ui as UiRenderSpec, messageId: message.id });
  }
  if (message.kind === "ui.patch" && message.ui?.instanceId !== undefined) {
    const existing = next.get(message.ui.instanceId);
    if (existing !== undefined) {
      next.set(message.ui.instanceId, {
        ...existing,
        spec: { ...existing.spec, props: { ...existing.spec.props, ...(message.ui.patch ?? {}) } },
      });
    }
  }
  if (message.kind === "ui.remove" && message.ui?.instanceId !== undefined) {
    next.delete(message.ui.instanceId);
  }
  return next;
}

/**
 * Applique un tour d'instructions d'interface à la conversation affichée.
 *
 * Un `ui.patch` ou un `ui.remove` ne peut viser qu'une instance déjà rendue : elle est reprise du
 * tour précédent, mise à jour, puis conservée. Les instances anciennes que l'agent n'a pas touchées
 * disparaissent, comme auparavant, pour que l'écran suive la dernière réponse.
 */
export function mergeUiMessages(previous: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const carried = new Map<string, ChatMessage>();
  const targeted = new Set(
    incoming
      .filter((message) => message.kind === "ui.patch" || message.kind === "ui.remove")
      .map((message) => message.ui.instanceId),
  );
  for (const message of previous) {
    if (message.kind === "ui.render" && targeted.has(message.ui.instanceId)) {
      carried.set(message.ui.instanceId, message);
    }
  }

  const text: ChatMessage[] = [];
  for (const message of incoming) {
    if (message.kind === "text") {
      text.push(message);
      continue;
    }
    if (message.kind === "ui.render") {
      carried.set(message.ui.instanceId, message);
      continue;
    }
    if (message.kind === "ui.patch") {
      const existing = carried.get(message.ui.instanceId);
      if (existing !== undefined && existing.kind === "ui.render") {
        carried.set(message.ui.instanceId, {
          ...existing,
          ui: { ...existing.ui, props: { ...existing.ui.props, ...message.ui.patch } },
        });
      }
      continue;
    }
    carried.delete(message.ui.instanceId);
  }
  return [...text, ...carried.values()];
}

const STORAGE_PREFIX = "ezer-agent-ui:";

export function persistConversation(workspaceId: string, payload: unknown): void {
  try {
    sessionStorage.setItem(`${STORAGE_PREFIX}${workspaceId}`, JSON.stringify(payload));
  } catch {
    // Quota or private mode : l'historique reste en mémoire.
  }
}

export function restoreConversation(workspaceId: string): unknown | null {
  try {
    const raw = sessionStorage.getItem(`${STORAGE_PREFIX}${workspaceId}`);
    return raw === null ? null : JSON.parse(raw);
  } catch {
    return null;
  }
}
