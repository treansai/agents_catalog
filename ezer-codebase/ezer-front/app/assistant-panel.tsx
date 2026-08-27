"use client";

import { useCallback, useEffect, useState } from "react";

import { AgentMessageRenderer } from "@/components/agent-ui/message-renderer";
import {
  AGENT_UI_PROTOCOL_VERSION,
  newId,
  parseChatMessage,
  type ChatMessage,
} from "@/lib/agent-ui/contracts";
import {
  liveInstances,
  messagesFromAssistantAnswer,
  shouldAskAgent,
} from "@/lib/agent-ui/from-assistant";
import {
  mergeUiMessages,
  persistConversation,
  restoreConversation,
} from "@/lib/agent-ui/instance-store";
import type { AssistantView } from "./assistant-views";

/**
 * Conversation avec l'assistant multi-agents d'Ezer.
 *
 * Le panneau n'exécute rien : il envoie l'historique, affiche la réponse, et ne transmet un
 * identifiant de message à supprimer qu'après un clic explicite de l'utilisateur sur « Confirmer ».
 */

interface Turn {
  role: "user" | "assistant";
  content: string;
}

interface Deletion {
  message_id: string;
  subject: string;
  sender_address: string;
  received_at: string;
}

interface Answer {
  reply: string;
  pending_deletions: Deletion[];
  deleted: Deletion[];
  views: AssistantView[];
  ui_messages?: ChatMessage[];
  tools_used: string[];
}

/** Une interaction déclarative renvoyée à l'agent, sans identité ni jeton côté navigateur. */
interface UiActionForAgent {
  kind: "ui.action";
  event_id: string;
  message_id: string;
  instance_id: string;
  component_id: string;
  component_version: string;
  action_id: string;
  values: Record<string, unknown>;
  idempotency_key: string;
}

interface ConnectedMailbox {
  account_id: string;
  mailbox: string | null;
  status: string;
}

const toolLabels: Record<string, string> = {
  analyze_message: "analyse d’un message",
  list_recent_messages: "messages récents",
  read_message: "lecture d’un message",
  request_delete_message: "demande de suppression",
  search_messages: "recherche",
  summarize_mailbox: "synthèse de la boîte",
};

const suggestions = [
  "Résume l’état de ma boîte.",
  "Quels sont mes 5 messages les plus récents ?",
  "Y a-t-il un message qui demande une action ?",
];

function formatMoment(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "date inconnue";
  return new Intl.DateTimeFormat("fr-FR", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Paris",
  }).format(date);
}

async function readError(response: Response, fallback: string): Promise<string> {
  const payload: unknown = await response.json().catch(() => null);
  if (payload !== null && typeof payload === "object" && "message" in payload) {
    const message = (payload as { message?: unknown }).message;
    if (typeof message === "string" && message.length > 0) return message;
  }
  return fallback;
}

export function AssistantPanel() {
  const [account, setAccount] = useState<ConnectedMailbox | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<Deletion[]>([]);
  const [toolsUsed, setToolsUsed] = useState<string[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [uiMessages, setUiMessages] = useState<ChatMessage[]>([]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch("/api/ezer/connect", { cache: "no-store" });
        if (!response.ok) return;
        const payload = (await response.json()) as {
          configured: boolean;
          mailboxes: ConnectedMailbox[];
        };
        if (cancelled) return;
        setAccount(payload.mailboxes.find((mailbox) => mailbox.status === "connected") ?? null);
        const connected = payload.mailboxes.find((mailbox) => mailbox.status === "connected");
        if (connected === undefined) return;
        const restored = restoreConversation(connected.account_id);
        if (!Array.isArray(restored)) return;
        const messages: ChatMessage[] = [];
        for (const entry of restored) {
          try {
            messages.push(parseChatMessage(entry));
          } catch {
            // Historique corrompu : on n'exécute aucune mutation.
          }
        }
        if (messages.length > 0) setUiMessages(messages);
      } catch {
        // Panneau simplement absent si l'état des boîtes est indisponible.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const send = useCallback(
    async (history: Turn[], approvedDeletions: string[], uiAction?: UiActionForAgent) => {
      if (account === null) return;
      setIsThinking(true);
      setError(null);
      try {
        const response = await fetch("/api/ezer/assistant", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            account_id: account.account_id,
            messages: history,
            ...(approvedDeletions.length > 0 ? { approved_deletions: approvedDeletions } : {}),
            ...(uiAction === undefined ? {} : { ui_action: uiAction }),
            ui_instances: liveInstances(uiMessages),
          }),
        });
        if (!response.ok) {
          throw new Error(await readError(response, "L’assistant n’a pas pu répondre."));
        }
        const answer = (await response.json()) as Answer;
        setTurns([...history, { role: "assistant", content: answer.reply }]);
        setPending(answer.pending_deletions);
        setToolsUsed(answer.tools_used);
        const converted = messagesFromAssistantAnswer({
          reply: answer.reply,
          views: answer.views ?? [],
          ui_messages: answer.ui_messages ?? [],
        });
        setUiMessages((current) => {
          const next = mergeUiMessages(current, converted);
          persistConversation(account.account_id, next);
          return next;
        });
        setNotice(
          answer.deleted.length === 0
            ? null
            : `${answer.deleted.length} message(s) déplacé(s) vers la corbeille — récupérables depuis Outlook.`,
        );
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "L’assistant n’a pas pu répondre.");
      } finally {
        setIsThinking(false);
      }
    },
    [account, uiMessages],
  );

  async function ask(question: string) {
    const trimmed = question.trim();
    if (trimmed === "" || isThinking) return;
    const history: Turn[] = [...turns, { role: "user", content: trimmed }];
    setTurns(history);
    setDraft("");
    setPending([]);
    setNotice(null);
    await send(history, []);
  }

  async function confirmDeletion(deletion: Deletion) {
    setNotice(null);
    const history: Turn[] = [
      ...turns,
      { role: "user", content: `Je confirme la mise à la corbeille du message « ${deletion.subject} ».` },
    ];
    setTurns(history);
    setPending([]);
    await send(history, [deletion.message_id]);
  }

  async function dispatchUiAction(
    actionId: string,
    values: Record<string, unknown>,
    message: ChatMessage,
  ) {
    if (account === null || message.kind !== "ui.render") return;

    // Une confirmation portée par un composant que l'agent a affiché lui-même n'a pas de jeton
    // serveur : elle rejoint le protocole de suppression existant plutôt que d'en ouvrir un second.
    if (message.ui.componentId === "confirm.dialog" && message.ui.data === undefined) {
      if (actionId === "confirmation.confirm" && pending.length > 0) {
        await confirmDeletion(pending[0]);
        return;
      }
      if (actionId === "confirmation.cancel") {
        setPending([]);
        setUiMessages((current) => {
          const next = current.filter(
            (entry) => entry.kind !== "ui.render" || entry.ui.instanceId !== message.ui.instanceId,
          );
          persistConversation(account.account_id, next);
          return next;
        });
      }
      return;
    }

    const target =
      typeof values.targetId === "string"
        ? values.targetId
        : typeof values.offset === "number"
          ? String(values.offset)
          : "na";
    const event = {
      kind: "ui.action" as const,
      protocolVersion: AGENT_UI_PROTOCOL_VERSION,
      eventId: newId("evt"),
      messageId: message.id,
      instanceId: message.ui.instanceId,
      componentId: message.ui.componentId,
      componentVersion: message.ui.componentVersion,
      actionId,
      values,
      idempotencyKey: `${message.ui.instanceId}:${actionId}:${target}`.replace(/[^A-Za-z0-9._:-]/g, "-").slice(0, 128),
      createdAt: new Date().toISOString(),
    };
    const response = await fetch(
      `/api/agent-ui/action?workspace_id=${encodeURIComponent(account.account_id)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(event),
      },
    );
    if (!response.ok) return;
    const payload = (await response.json()) as {
      status: string;
      confirmation?: {
        confirmationId: string;
        token: string;
        action: string;
        target: string;
        impact: string;
        reversible: boolean;
      };
    };
    if (payload.status === "confirmation_required" && payload.confirmation !== undefined) {
      const confirm: ChatMessage = {
        kind: "ui.render",
        protocolVersion: AGENT_UI_PROTOCOL_VERSION,
        id: newId("ui"),
        role: "assistant",
        createdAt: new Date().toISOString(),
        ui: {
          instanceId: newId("inst"),
          componentId: "confirm.dialog",
          componentVersion: "1.0",
          props: {
            title: `${payload.confirmation.action} ?`,
            body: payload.confirmation.impact,
            confirmLabel: "confirmer",
            reversible: payload.confirmation.reversible,
            targetLabel: payload.confirmation.target,
          },
          data: { mode: "inline", value: payload.confirmation },
          fallbackText: payload.confirmation.impact,
        },
      };
      setUiMessages((current) => {
        const next = [...current, confirm];
        persistConversation(account.account_id, next);
        return next;
      });
      return;
    }

    // Pagination et filtres restent locaux ; une action porteuse de sens rend la main à l'agent.
    if (payload.status === "success" && shouldAskAgent(actionId) && turns.length > 0) {
      await send(turns, [], {
        kind: "ui.action",
        event_id: event.eventId,
        message_id: event.messageId,
        instance_id: event.instanceId,
        component_id: event.componentId,
        component_version: event.componentVersion,
        action_id: event.actionId,
        values: event.values,
        idempotency_key: event.idempotencyKey,
      });
    }
  }

  if (account === null) return null;

  return (
    <section className="assistant-panel" aria-labelledby="assistant-heading">
      <style>{PANEL_STYLES}</style>
      <div className="assistant-panel__head">
        <p className="assistant-panel__eyebrow">Agents</p>
        <h2 className="assistant-panel__title" id="assistant-heading">
          Demander à Ezer
        </h2>
        <p className="assistant-panel__mailbox">{account.mailbox}</p>
      </div>

      {turns.length === 0 ? (
        <div className="assistant-panel__suggestions">
          {suggestions.map((suggestion) => (
            <button
              className="assistant-chip"
              key={suggestion}
              onClick={() => void ask(suggestion)}
              type="button"
            >
              {suggestion}
            </button>
          ))}
        </div>
      ) : (
        <ol className="assistant-thread">
          {turns.map((turn, index) => (
            <li
              className={`assistant-turn assistant-turn--${turn.role}`}
              key={`${turn.role}-${index}`}
            >
              {turn.content}
            </li>
          ))}
          {isThinking ? <li className="assistant-turn assistant-turn--thinking">…</li> : null}
        </ol>
      )}

      {uiMessages.some((message) => message.kind === "ui.render") ? (
        <div className="assistant-panel__views">
          {uiMessages
            .filter((message) => message.kind === "ui.render")
            .map((message) => (
              <AgentMessageRenderer
                conversationId={account.account_id}
                key={message.id}
                message={message}
                onAction={(actionId, values, source) => void dispatchUiAction(actionId, values, source)}
                workspaceId={account.account_id}
              />
            ))}
        </div>
      ) : null}

      {toolsUsed.length > 0 ? (
        <p className="assistant-panel__tools">
          Outils utilisés : {toolsUsed.map((tool) => toolLabels[tool] ?? tool).join(", ")}
        </p>
      ) : null}

      {pending.map((deletion) => (
        <div className="assistant-confirm" key={deletion.message_id} role="alertdialog">
          <p className="assistant-confirm__text">
            Mettre à la corbeille «&nbsp;<strong>{deletion.subject || "sans objet"}</strong>&nbsp;»
            {deletion.sender_address === "" ? null : <> de {deletion.sender_address}</>}
            {deletion.received_at === "" ? null : <> ({formatMoment(deletion.received_at)})</>} ?
            Le message restera récupérable dans les éléments supprimés.
          </p>
          <div className="assistant-confirm__actions">
            <button
              className="assistant-button assistant-button--danger"
              disabled={isThinking}
              onClick={() => void confirmDeletion(deletion)}
              type="button"
            >
              Confirmer
            </button>
            <button
              className="assistant-button"
              disabled={isThinking}
              onClick={() => setPending([])}
              type="button"
            >
              Annuler
            </button>
          </div>
        </div>
      ))}

      {notice !== null ? <p className="assistant-panel__notice">{notice}</p> : null}
      {error !== null ? (
        <p className="assistant-panel__error" role="alert">
          {error}
        </p>
      ) : null}

      <form
        className="assistant-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void ask(draft);
        }}
      >
        <label className="assistant-composer__label" htmlFor="assistant-input">
          Votre demande
        </label>
        <input
          autoComplete="off"
          className="assistant-composer__input"
          disabled={isThinking}
          id="assistant-input"
          maxLength={2_000}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Résume ma boîte, cherche un message, …"
          value={draft}
        />
        <button
          className="assistant-button assistant-button--primary"
          disabled={isThinking || draft.trim() === ""}
          type="submit"
        >
          {isThinking ? "…" : "Envoyer"}
        </button>
      </form>
    </section>
  );
}

const PANEL_STYLES = `
.assistant-panel {
  display: grid;
  gap: 0.9rem;
  padding: 1.25rem 1.4rem;
  border: 1px solid color-mix(in srgb, currentColor 12%, transparent);
  border-radius: 18px;
  background: color-mix(in srgb, currentColor 3%, transparent);
}
.assistant-panel__eyebrow {
  margin: 0;
  font-size: 0.72rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  opacity: 0.6;
}
.assistant-panel__title { margin: 0.15rem 0 0; font-size: 1.15rem; }
.assistant-panel__mailbox { margin: 0.15rem 0 0; font-size: 0.85rem; opacity: 0.65; }
.assistant-panel__views { display: grid; gap: 0.75rem; }
.assistant-panel__suggestions { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.assistant-chip {
  font: inherit;
  font-size: 0.85rem;
  padding: 0.4rem 0.9rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, currentColor 20%, transparent);
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.assistant-thread {
  display: grid;
  gap: 0.55rem;
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 22rem;
  overflow-y: auto;
}
.assistant-turn {
  padding: 0.6rem 0.85rem;
  border-radius: 14px;
  font-size: 0.92rem;
  line-height: 1.5;
  white-space: pre-wrap;
  border: 1px solid color-mix(in srgb, currentColor 10%, transparent);
}
.assistant-turn--user { background: color-mix(in srgb, currentColor 8%, transparent); }
.assistant-turn--thinking { opacity: 0.6; }
.assistant-panel__tools { margin: 0; font-size: 0.78rem; opacity: 0.6; }
.assistant-panel__notice, .assistant-panel__error { margin: 0; font-size: 0.88rem; }
.assistant-panel__error { color: #b4232b; }
.assistant-confirm {
  display: grid;
  gap: 0.6rem;
  padding: 0.85rem 1rem;
  border-radius: 14px;
  border: 1px solid #b4232b;
}
.assistant-confirm__text { margin: 0; font-size: 0.9rem; line-height: 1.5; }
.assistant-confirm__actions { display: flex; gap: 0.5rem; }
.assistant-composer {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.5rem;
  align-items: center;
}
.assistant-composer__label {
  grid-column: 1 / -1;
  font-size: 0.75rem;
  opacity: 0.6;
}
.assistant-composer__input {
  font: inherit;
  padding: 0.55rem 0.9rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, currentColor 20%, transparent);
  background: transparent;
  color: inherit;
}
.assistant-button {
  font: inherit;
  font-size: 0.88rem;
  padding: 0.45rem 1rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, currentColor 25%, transparent);
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.assistant-button--primary { background: color-mix(in srgb, currentColor 12%, transparent); }
.assistant-button--danger { border-color: #b4232b; color: #b4232b; }
.assistant-button[disabled] { cursor: not-allowed; opacity: 0.55; }
`;
