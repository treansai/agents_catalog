import {
  AGENT_UI_PROTOCOL_VERSION,
  type ChatMessage,
  newId,
} from "./contracts.ts";

interface MessageHeaderView {
  message_id: string;
  subject: string;
  sender_name: string;
  sender_address: string;
  received_at: string;
  is_read: boolean;
  has_attachments: boolean;
  snippet: string;
}

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

export interface AssistantAnswerLike {
  reply: string;
  /** Instructions émises par l'agent lui-même, déjà validées par le backend. */
  ui_messages?: ChatMessage[];
  pending_deletions?: Array<{
    message_id: string;
    subject: string;
    sender_address: string;
    received_at: string;
  }>;
  views: AssistantView[];
}

function textMessage(content: string): ChatMessage {
  return {
    kind: "text",
    protocolVersion: AGENT_UI_PROTOCOL_VERSION,
    id: newId("txt"),
    role: "assistant",
    content,
    createdAt: new Date().toISOString(),
  };
}

function renderMessage(
  componentId: string,
  fallbackText: string,
  data: unknown,
  props: Record<string, unknown> = {},
): ChatMessage {
  return {
    kind: "ui.render",
    protocolVersion: AGENT_UI_PROTOCOL_VERSION,
    id: newId("ui"),
    role: "assistant",
    createdAt: new Date().toISOString(),
    ui: {
      instanceId: newId("inst"),
      componentId,
      componentVersion: "1.0",
      props,
      data: { mode: "inline", value: data },
      fallbackText,
    },
  };
}

function fromView(view: AssistantView): ChatMessage {
  switch (view.kind) {
    case "messages":
      return renderMessage(
        "mail.list",
        view.title,
        {
          items: view.items.map((item) => ({
            id: item.message_id,
            sender: item.sender_name || item.sender_address,
            senderAddress: item.sender_address,
            subject: item.subject,
            snippet: item.snippet,
            receivedAt: item.received_at,
            unread: item.is_read === false,
            hasAttachments: item.has_attachments,
          })),
          total: view.items.length,
          limit: view.items.length || 1,
          offset: 0,
        },
        { title: view.title },
      );
    case "message":
      return renderMessage("mail.detail", view.message.subject || "Message", {
        id: view.message.message_id,
        subject: view.message.subject,
        sender: view.message.sender_name,
        senderAddress: view.message.sender_address,
        receivedAt: view.message.received_at,
        body: view.body_text,
      });
    case "senders":
      return renderMessage("senders.list", "Expéditeurs", {
        items: view.items.map((item) => ({
          sender: item.sender_name,
          senderAddress: item.sender_address,
          total: item.total,
          unread: item.unread,
        })),
      });
    case "stats":
      return renderMessage("metric.card", `${view.unread_messages} non lus`, {
        value: String(view.unread_messages),
        label: "non lus",
        hint: `${view.total_messages} messages`,
      });
    case "triage":
      return renderMessage(
        "mail.list",
        "Triage",
        {
          items: view.items.map((item, index) => ({
            id: `triage-${index}`,
            sender: item.category,
            subject: item.summary,
            snippet: item.priority,
            receivedAt: item.created_at,
            unread: item.needs_human_review,
            tag: item.priority,
          })),
          total: view.total,
          limit: view.items.length || 1,
          offset: 0,
        },
        { title: "Triage" },
      );
  }
}

/**
 * Convertit la réponse HTTP de l'assistant en événements Agent UI.
 *
 * Quand l'agent a lui-même choisi un composant, ses instructions font foi : leurs `instanceId`
 * viennent du backend et doivent survivre au tour suivant pour qu'un patch les retrouve. Les
 * `views` historiques ne servent que de repli lorsqu'aucune instruction n'a été émise.
 */
export function messagesFromAssistantAnswer(answer: AssistantAnswerLike): ChatMessage[] {
  const messages: ChatMessage[] = [textMessage(answer.reply)];
  const emitted = answer.ui_messages ?? [];
  if (emitted.length > 0) {
    return [...messages, ...emitted];
  }
  for (const view of answer.views ?? []) {
    messages.push(fromView(view));
  }
  return messages;
}

/** Les instances encore à l'écran, annoncées à l'agent pour qu'il puisse les mettre à jour. */
export function liveInstances(
  messages: ChatMessage[],
): { instance_id: string; component_id: string; component_version: string }[] {
  const live = new Map<string, { instance_id: string; component_id: string; component_version: string }>();
  for (const message of messages) {
    if (message.kind === "ui.render") {
      live.set(message.ui.instanceId, {
        instance_id: message.ui.instanceId,
        component_id: message.ui.componentId,
        component_version: message.ui.componentVersion,
      });
    }
    if (message.kind === "ui.remove") {
      live.delete(message.ui.instanceId);
    }
  }
  return [...live.values()].slice(-24);
}

/**
 * Une pagination ou un filtre se règle dans le composant ; une action porteuse de sens (ouvrir,
 * répondre, choisir) mérite un tour d'agent.
 */
const AGENT_FORWARDED_ACTIONS = new Set([
  "messages.open",
  "invoices.open",
  "draft.reply",
  "form.submit",
]);

export function shouldAskAgent(actionId: string): boolean {
  return AGENT_FORWARDED_ACTIONS.has(actionId);
}
