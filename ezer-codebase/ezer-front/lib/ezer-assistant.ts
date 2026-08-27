import "server-only";

import { parseChatMessage, type ChatMessage } from "@/lib/agent-ui/contracts";

/**
 * Accès serveur à l'assistant multi-agents, qui vit dans ezer-bot.
 *
 * Le bot orchestre les agents ; il lit et supprime les messages en passant par le backend, jamais
 * par Microsoft directement. Le navigateur n'envoie qu'un historique de conversation et, le cas
 * échéant, les identifiants de messages que l'utilisateur vient de confirmer : aucune clé d'API ne
 * quitte le serveur.
 */

const REQUEST_TIMEOUT_MS = 120_000;
const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const MESSAGE_ID = /^[A-Za-z0-9_\-=+/]{1,512}$/;
const MAX_TURNS = 40;
const MAX_TURN_CHARS = 8_000;
const MAX_APPROVALS = 20;
const MAX_UI_MESSAGES = 6;
const MAX_UI_INSTANCES = 24;
const AGENT_UI_ID = /^[A-Za-z0-9._:-]{1,128}$/;
/** Identité de session : elle vient du serveur, jamais du navigateur ni du modèle. */
const SESSION_KEYS = ["userId", "workspaceId", "tenantId", "permissions", "account_id", "credentials"];

export interface AssistantTurn {
  role: "user" | "assistant";
  content: string;
}

export interface PendingDeletion {
  message_id: string;
  subject: string;
  sender_address: string;
  received_at: string;
}

export interface MessageHeaderView {
  message_id: string;
  subject: string;
  sender_name: string;
  sender_address: string;
  received_at: string;
  is_read: boolean;
  has_attachments: boolean;
  snippet: string;
}

/** Vues structurées : l'interface choisit un composant par forme de résultat. */
export type AssistantView =
  | { kind: "message"; message: MessageHeaderView; body_text: string }
  | { kind: "messages"; title: string; items: MessageHeaderView[] }
  | {
      kind: "senders";
      items: { sender_name: string; sender_address: string; total: number; unread: number }[];
    }
  | {
      kind: "stats";
      mailbox: string;
      total_messages: number;
      unread_messages: number;
      folders: { name: string; total: number; unread: number }[];
    }
  | {
      kind: "triage";
      total: number;
      items: {
        summary: string;
        category: string;
        priority: string;
        needs_human_review: boolean;
        created_at: string;
      }[];
    };

export interface AssistantAnswer {
  reply: string;
  pending_deletions: PendingDeletion[];
  deleted: PendingDeletion[];
  views: AssistantView[];
  /** Instructions d'interface déjà validées par le backend : rendu, patch, retrait. */
  ui_messages: ChatMessage[];
  tools_used: string[];
}

/** Une instance encore affichée, annoncée à l'agent pour qu'un patch puisse la viser. */
export interface UiInstanceRef {
  instance_id: string;
  component_id: string;
  component_version: string;
}

export interface UiActionRequest {
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

export class EzerAssistantError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "EzerAssistantError";
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Expected an object");
  }
  return value as Record<string, unknown>;
}

function asBoundedString(value: unknown, maximumLength: number): string {
  if (typeof value !== "string" || value.length > maximumLength) {
    throw new TypeError("Expected a bounded string");
  }
  return value;
}

function parseDeletions(value: unknown): PendingDeletion[] {
  if (!Array.isArray(value) || value.length > MAX_APPROVALS) {
    throw new TypeError("Expected a bounded array");
  }
  return value.map((entry) => {
    const deletion = asRecord(entry);
    const messageId = asBoundedString(deletion.message_id, 512);
    if (!MESSAGE_ID.test(messageId)) throw new TypeError("Invalid message identifier");
    return {
      message_id: messageId,
      subject: asBoundedString(deletion.subject, 400),
      sender_address: asBoundedString(deletion.sender_address, 320),
      received_at: asBoundedString(deletion.received_at, 64),
    };
  });
}

function asCount(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > 1_000_000) {
    throw new TypeError("Expected a bounded count");
  }
  return value;
}

function parseHeader(value: unknown): MessageHeaderView {
  const header = asRecord(value);
  const messageId = asBoundedString(header.message_id, 512);
  if (!MESSAGE_ID.test(messageId)) throw new TypeError("Invalid message identifier");
  return {
    message_id: messageId,
    subject: asBoundedString(header.subject, 400),
    sender_name: asBoundedString(header.sender_name, 320),
    sender_address: asBoundedString(header.sender_address, 320),
    received_at: asBoundedString(header.received_at, 64),
    is_read: header.is_read === true,
    has_attachments: header.has_attachments === true,
    snippet: asBoundedString(header.snippet, 600),
  };
}

function asBoundedArray(value: unknown, maximumLength: number): unknown[] {
  if (!Array.isArray(value) || value.length > maximumLength) {
    throw new TypeError("Expected a bounded array");
  }
  return value;
}

function parseView(value: unknown): AssistantView {
  const view = asRecord(value);
  switch (view.kind) {
    case "message":
      return {
        kind: "message",
        message: parseHeader(view.message),
        body_text: asBoundedString(view.body_text, 20_000),
      };
    case "messages":
      return {
        kind: "messages",
        title: asBoundedString(view.title, 200),
        items: asBoundedArray(view.items, 25).map(parseHeader),
      };
    case "senders":
      return {
        kind: "senders",
        items: asBoundedArray(view.items, 25).map((entry) => {
          const sender = asRecord(entry);
          return {
            sender_name: asBoundedString(sender.sender_name, 320),
            sender_address: asBoundedString(sender.sender_address, 320),
            total: asCount(sender.total),
            unread: asCount(sender.unread),
          };
        }),
      };
    case "stats":
      return {
        kind: "stats",
        mailbox: asBoundedString(view.mailbox, 320),
        total_messages: asCount(view.total_messages),
        unread_messages: asCount(view.unread_messages),
        folders: asBoundedArray(view.folders, 20).map((entry) => {
          const folder = asRecord(entry);
          return {
            name: asBoundedString(folder.name, 120),
            total: asCount(folder.total),
            unread: asCount(folder.unread),
          };
        }),
      };
    case "triage":
      return {
        kind: "triage",
        total: asCount(view.total),
        items: asBoundedArray(view.items, 100).map((entry) => {
          const item = asRecord(entry);
          return {
            summary: asBoundedString(item.summary, 2_000),
            category: asBoundedString(item.category, 64),
            priority: asBoundedString(item.priority, 32),
            needs_human_review: item.needs_human_review === true,
            created_at: asBoundedString(item.created_at, 64),
          };
        }),
      };
    default:
      throw new TypeError("Unknown view kind");
  }
}

function parseAnswer(payload: unknown): AssistantAnswer {
  const answer = asRecord(payload);
  if (!Array.isArray(answer.tools_used) || answer.tools_used.length > 40) {
    throw new TypeError("Expected a bounded array");
  }
  return {
    reply: asBoundedString(answer.reply, 8_000),
    pending_deletions: parseDeletions(answer.pending_deletions),
    deleted: parseDeletions(answer.deleted),
    views: asBoundedArray(answer.views ?? [], 4).map(parseView),
    // Le contrat Agent UI est revalidé ici : un message hors protocole invalide la réponse.
    ui_messages: asBoundedArray(answer.ui_messages ?? [], MAX_UI_MESSAGES).map(parseChatMessage),
    tools_used: answer.tools_used.map((tool) => asBoundedString(tool, 64)),
  };
}

/** Valide la demande du navigateur avant qu'elle n'atteigne le backend. */
function asAgentUiId(value: unknown, maximumLength = 128): string {
  const identifier = asBoundedString(value, maximumLength);
  if (!AGENT_UI_ID.test(identifier)) throw new TypeError("Invalid agent UI identifier");
  return identifier;
}

function parseUiInstances(value: unknown): UiInstanceRef[] {
  if (!Array.isArray(value) || value.length > MAX_UI_INSTANCES) {
    throw new TypeError("Expected a bounded array");
  }
  return value.map((entry) => {
    const instance = asRecord(entry);
    return {
      instance_id: asAgentUiId(instance.instance_id),
      component_id: asAgentUiId(instance.component_id),
      component_version: asAgentUiId(instance.component_version ?? "1.0", 32),
    };
  });
}

function parseUiAction(value: unknown): UiActionRequest {
  const event = asRecord(value);
  if (event.kind !== "ui.action") throw new TypeError("Invalid action event");
  const values = asRecord(event.values ?? {});
  for (const key of SESSION_KEYS) {
    delete values[key];
  }
  if (JSON.stringify(values).length > 8_000) throw new TypeError("Action values too large");
  return {
    kind: "ui.action",
    event_id: asAgentUiId(event.event_id),
    message_id: asAgentUiId(event.message_id),
    instance_id: asAgentUiId(event.instance_id),
    component_id: asAgentUiId(event.component_id),
    component_version: asAgentUiId(event.component_version ?? "1.0", 32),
    action_id: asAgentUiId(event.action_id),
    values,
    idempotency_key: asAgentUiId(event.idempotency_key),
    ...(event.result === undefined ? {} : { result: asRecord(event.result) }),
  };
}

export function validateAssistantRequest(payload: unknown): {
  account_id: string;
  messages: AssistantTurn[];
  approved_deletions: string[];
  ui_action?: UiActionRequest;
  ui_instances?: UiInstanceRef[];
} {
  const body = asRecord(payload);
  const accountId = asBoundedString(body.account_id, 128);
  if (!ACCOUNT_ID.test(accountId)) throw new TypeError("Invalid account identifier");

  if (!Array.isArray(body.messages) || body.messages.length < 1 || body.messages.length > MAX_TURNS) {
    throw new TypeError("Expected a bounded array");
  }
  const messages: AssistantTurn[] = body.messages.map((entry) => {
    const turn = asRecord(entry);
    const role = turn.role;
    if (role !== "user" && role !== "assistant") {
      throw new TypeError("Invalid turn role");
    }
    const content = asBoundedString(turn.content, MAX_TURN_CHARS);
    if (content.length === 0) throw new TypeError("Empty turn");
    return { role, content };
  });

  let approved: string[] = [];
  if (body.approved_deletions !== undefined) {
    if (!Array.isArray(body.approved_deletions) || body.approved_deletions.length > MAX_APPROVALS) {
      throw new TypeError("Expected a bounded array");
    }
    approved = body.approved_deletions.map((value) => {
      const messageId = asBoundedString(value, 512);
      if (!MESSAGE_ID.test(messageId)) throw new TypeError("Invalid message identifier");
      return messageId;
    });
  }

  return {
    account_id: accountId,
    messages,
    approved_deletions: approved,
    ...(body.ui_action === undefined ? {} : { ui_action: parseUiAction(body.ui_action) }),
    ...(body.ui_instances === undefined
      ? {}
      : { ui_instances: parseUiInstances(body.ui_instances) }),
  };
}

export async function askAssistant(request: {
  account_id: string;
  messages: AssistantTurn[];
  approved_deletions: string[];
  ui_action?: UiActionRequest;
  ui_instances?: UiInstanceRef[];
}): Promise<AssistantAnswer> {
  const apiUrl = process.env.EZER_BOT_URL?.trim();
  const apiKey = process.env.EZER_BOT_API_KEY?.trim();
  if (!apiUrl || !apiKey) {
    throw new EzerAssistantError("Ezer bot is not configured", 503);
  }

  let url: URL;
  try {
    url = new URL("/v1/assistant", apiUrl);
  } catch {
    throw new EzerAssistantError("Invalid Ezer bot URL");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new EzerAssistantError("Invalid Ezer bot URL protocol");
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
      },
      body: JSON.stringify(request),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    throw new EzerAssistantError("Ezer API is unreachable");
  }
  if (!response.ok) {
    throw new EzerAssistantError("Ezer API request failed", response.status);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new EzerAssistantError("Ezer API returned invalid JSON", response.status);
  }
  try {
    return parseAnswer(payload);
  } catch {
    throw new EzerAssistantError("Ezer API returned an invalid payload", response.status);
  }
}
