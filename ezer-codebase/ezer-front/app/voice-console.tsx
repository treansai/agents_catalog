"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AssistantViews, type AssistantView } from "./assistant-views";
import { AgentMessageRenderer } from "@/components/agent-ui/message-renderer";
import { VoiceOrb, type OrbSpeaker } from "./voice-orb";
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
import {
  connectRealtime,
  isRealtimeSupported,
  type RealtimeConnection,
} from "@/lib/realtime-session";

/**
 * Surface principale d'Ezer : on lui parle.
 *
 * La voix est le mode par défaut de bout en bout : le micro et le haut-parleur sont reliés au
 * modèle temps réel par WebRTC, sans étape de transcription puis de synthèse. Le modèle vocal ne
 * connaît rien de la boîte : il interroge Ezer par son unique outil et lit la réponse.
 * Le clavier reste disponible mais volontairement replié derrière l'icône message : il sert aux
 * environnements bruyants, aux identifiants difficiles à dicter et à l'accessibilité.
 */

type Mode = "opaque" | "copilote";

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

interface Turn {
  role: "user" | "assistant";
  content: string;
}

interface Action {
  id: string;
  text: string;
  meta: string;
  live: boolean;
}

interface Mailbox {
  account_id: string;
  mailbox: string | null;
  status: string;
}

const ACCENT = "#880d1e";
/** Fond unique : page, carte et contrôles partagent la même surface. */
const SURFACE = "#050505";
const MAX_ACTIONS = 24;
const MAX_HISTORY_TURNS = 20;

const toolLabels: Record<string, string> = {
  analyze_message: "analyse d’un message",
  list_recent_messages: "lecture des messages récents",
  read_message: "lecture d’un message",
  request_delete_message: "demande de suppression",
  search_messages: "recherche dans la boîte",
  summarize_mailbox: "synthèse de la boîte",
};

/**
 * Un `fetch` qui n'atteint pas le serveur lève un TypeError dont le message (« Failed to fetch »)
 * ne veut rien dire pour l'utilisateur. Nos propres refus, eux, portent déjà un message clair.
 */
function humanError(reason: unknown, fallback: string): string {
  if (reason instanceof TypeError) {
    return "Ezer est injoignable. Vérifiez que le service tourne, puis réessayez.";
  }
  return reason instanceof Error && reason.message !== "" ? reason.message : fallback;
}

async function readError(response: Response, fallback: string): Promise<string> {
  const payload: unknown = await response.json().catch(() => null);
  if (payload !== null && typeof payload === "object" && "message" in payload) {
    const message = (payload as { message?: unknown }).message;
    if (typeof message === "string" && message.length > 0) return message;
  }
  return fallback;
}

export function VoiceConsole() {
  const [mode, setMode] = useState<Mode>("opaque");
  const [account, setAccount] = useState<Mailbox | null>(null);
  const [ready, setReady] = useState(false);
  const [speaker, setSpeaker] = useState<OrbSpeaker>("idle");
  const [status, setStatus] = useState("parler à ezer");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [actions, setActions] = useState<Action[]>([]);
  const [pending, setPending] = useState<Deletion[]>([]);
  const [views, setViews] = useState<AssistantView[]>([]);
  const [uiMessages, setUiMessages] = useState<ChatMessage[]>([]);
  const [composerOpen, setComposerOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sessionRef = useRef<RealtimeConnection | null>(null);
  const counterRef = useRef(0);

  const pushAction = useCallback((text: string, meta: string, live = false): string => {
    counterRef.current += 1;
    const id = `a${counterRef.current}`;
    setActions((current) => [{ id, text, meta, live }, ...current].slice(0, MAX_ACTIONS));
    return id;
  }, []);

  const settleAction = useCallback((id: string, meta: string) => {
    setActions((current) =>
      current.map((action) => (action.id === id ? { ...action, meta, live: false } : action)),
    );
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch("/api/ezer/connect", { cache: "no-store" });
        if (!response.ok) {
          if (!cancelled) setReady(true);
          return;
        }
        const payload = (await response.json()) as { mailboxes: Mailbox[] };
        if (cancelled) return;
        const connected = payload.mailboxes.find((mailbox) => mailbox.status === "connected") ?? null;
        setAccount(connected);
        if (connected === null) return;
        const restored = restoreConversation(connected.account_id);
        if (!Array.isArray(restored)) return;
        const messages: ChatMessage[] = [];
        for (const entry of restored) {
          try {
            messages.push(parseChatMessage(entry));
          } catch {
            // Historique corrompu : on ignore l'entrée plutôt que de rejouer une mutation.
          }
        }
        if (messages.length > 0) setUiMessages(messages);
      } catch {
        // Une boîte injoignable laisse la console en place, avec son message d'état.
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
      sessionRef.current?.close();
      sessionRef.current = null;
    };
  }, []);

  /**
   * Un tour auprès d'Ezer. Utilisé par l'outil `ask_ezer` du modèle vocal, par la saisie écrite et
   * par la confirmation de suppression ; renvoie le texte qu'il faut restituer à l'utilisateur.
   */
  const converse = useCallback(
    async (
      question: string,
      approvedDeletions: string[],
      uiAction?: UiActionForAgent,
    ): Promise<string> => {
      if (account === null) return "Aucune boîte n'est connectée.";
      // Une interaction dans un composant ne crée pas de tour écrit : elle voyage dans ui_action.
      const asked = question.trim();
      const history = (
        asked === "" ? [...turns] : [...turns, { role: "user" as const, content: asked }]
      ).slice(-MAX_HISTORY_TURNS);
      if (history.length === 0) return "Posez d'abord une question à Ezer.";
      setTurns(history);
      setPending([]);
      setBusy(true);
      setError(null);
      setStatus("ezer consulte la boîte");
      const thinking = pushAction(asked === "" ? "interaction" : asked, "en cours…", true);

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
          throw new Error(await readError(response, "Ezer n’a pas pu répondre."));
        }
        const answer = (await response.json()) as Answer;
        setTurns([...history, { role: "assistant", content: answer.reply }]);
        setPending(answer.pending_deletions);
        // L'interface prend la forme du résultat : le composant adapté remplace le précédent.
        setViews(answer.views);
        const converted = messagesFromAssistantAnswer(answer);
        setUiMessages((current) => {
          const next = mergeUiMessages(current, converted);
          persistConversation(account.account_id, next);
          return next;
        });
        settleAction(
          thinking,
          answer.tools_used.length === 0
            ? "répondu"
            : answer.tools_used.map((tool) => toolLabels[tool] ?? tool).join(" · "),
        );
        for (const deletion of answer.deleted) {
          pushAction(deletion.subject || "message sans objet", "déplacé → corbeille");
        }
        for (const deletion of answer.pending_deletions) {
          pushAction(deletion.subject || "message sans objet", "attend votre confirmation");
        }

        setStatus("ezer répond");
        return answer.reply;
      } catch (reason) {
        settleAction(thinking, "échec");
        const message = humanError(reason, "Ezer n’a pas pu répondre.");
        setError(message);
        setStatus("parler à ezer");
        return message;
      } finally {
        setBusy(false);
      }
    },
    [account, pushAction, settleAction, turns, uiMessages],
  );

  async function toggleListening() {
    setError(null);

    // Deuxième appui : on raccroche. Une session ouverte consomme du temps d'antenne.
    if (sessionRef.current !== null) {
      sessionRef.current.close();
      return;
    }

    if (!isRealtimeSupported()) {
      setError("Ce navigateur ne permet pas la conversation vocale.");
      return;
    }
    if (account === null) return;

    setBusy(true);
    setStatus("connexion…");
    try {
      sessionRef.current = await connectRealtime({
        onSpeaker: (next) => {
          setSpeaker(next);
          setStatus(
            next === "user" ? "vous parlez" : next === "assistant" ? "ezer répond" : "à l’écoute",
          );
        },
        onUserTranscript: (text) => {
          setTurns((current) => [...current, { role: "user" as const, content: text }].slice(-MAX_HISTORY_TURNS));
        },
        onAssistantTranscript: (text) => {
          setTurns((current) =>
            [...current, { role: "assistant" as const, content: text }].slice(-MAX_HISTORY_TURNS),
          );
        },
        onToolCall: async (name, args) => {
          if (name !== "ask_ezer") return "Outil inconnu.";
          const question = typeof args.question === "string" ? args.question : "";
          if (question.trim() === "") return "La question était vide.";
          return converse(question, []);
        },
        onError: (message) => setError(message),
        onClosed: () => {
          sessionRef.current = null;
          setListening(false);
          setSpeaker("idle");
          setStatus("parler à ezer");
        },
      });
      setListening(true);
      setSpeaker("idle");
      setStatus("à l’écoute");
    } catch (reason) {
      sessionRef.current = null;
      setListening(false);
      setSpeaker("idle");
      setStatus("parler à ezer");
      setError(humanError(reason, "La session vocale n’a pas pu être ouverte."));
    } finally {
      setBusy(false);
    }
  }

  async function submitDraft() {
    const question = draft.trim();
    if (question === "" || busy) return;
    setDraft("");
    const reply = await converse(question, []);
    setTurns((current) => [...current, { role: "assistant" as const, content: reply }].slice(-MAX_HISTORY_TURNS));
  }

  async function confirmDeletion(deletion: Deletion) {
    const reply = await converse(
      `Je confirme la mise à la corbeille du message « ${deletion.subject} ».`,
      [deletion.message_id],
    );
    setTurns((current) => [...current, { role: "assistant" as const, content: reply }].slice(-MAX_HISTORY_TURNS));
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
    if (payload.status === "success" && shouldAskAgent(actionId)) {
      await converse("", [], {
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

  // Le chrome s'efface quand le curseur ne bouge plus : la console redevient une surface nue.
  const [chromeIdle, setChromeIdle] = useState(false);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    function reveal() {
      setChromeIdle(false);
      if (idleTimerRef.current !== null) clearTimeout(idleTimerRef.current);
      idleTimerRef.current = setTimeout(() => setChromeIdle(true), CHROME_IDLE_MS);
    }
    const events = ["pointermove", "pointerdown", "keydown", "wheel", "touchstart", "focus"] as const;
    for (const event of events) window.addEventListener(event, reveal, { passive: true });
    reveal();
    return () => {
      for (const event of events) window.removeEventListener(event, reveal);
      if (idleTimerRef.current !== null) clearTimeout(idleTimerRef.current);
    };
  }, []);

  /**
   * L'effacement ne vaut que pour la console au repos : tant qu'un appel est en cours, qu'Ezer
   * travaille, ou qu'un panneau attend une réponse, les commandes restent à portée de clic.
   */
  const chromeHidden =
    chromeIdle && !listening && !busy && !composerOpen && !historyOpen && pending.length === 0;

  const copilot = mode === "copilote";
  const renderedUi = uiMessages.filter((message) => message.kind === "ui.render");
  const hasViews = views.length > 0 || renderedUi.length > 0;
  const compact = copilot || hasViews;
  const lastReply = [...turns].reverse().find((turn) => turn.role === "assistant");

  return (
    <div className="ezer-voice">
      <style>{CONSOLE_STYLES}</style>

      <section
        aria-label="Assistant vocal Ezer"
        className={`ezer-card${chromeHidden ? " is-idle" : ""}`}
      >
        <header className="ezer-head">
          <div className="ezer-brand">
            <span className="ezer-dot" aria-hidden="true" />
            <span className="ezer-name">ezer</span>
          </div>
          <div className="ezer-modes ezer-chrome" role="group" aria-label="Mode d’affichage">
            <button
              aria-pressed={!copilot}
              className={`ezer-mode${copilot ? "" : " is-on"}`}
              onClick={() => setMode("opaque")}
              type="button"
            >
              opaque
            </button>
            <button
              aria-pressed={copilot}
              className={`ezer-mode${copilot ? " is-on" : ""}`}
              onClick={() => setMode("copilote")}
              type="button"
            >
              copilote
            </button>
          </div>
        </header>

        <div className={`ezer-stage${compact ? " is-compact" : ""}`}>
          <VoiceOrb
            accent={ACCENT}
            onClick={() => void toggleListening()}
            size={compact ? 120 : 240}
            speaker={speaker}
          />
          <p className="ezer-status">{ready && account === null ? "aucune boîte connectée" : status}</p>
          {!compact && lastReply !== undefined ? (
            <p className="ezer-reply">{lastReply.content}</p>
          ) : null}
        </div>

        {historyOpen ? (
          <div className="ezer-history">
            <p className="ezer-feed__title">conversation</p>
            <ol className="ezer-history__list">
              {turns.length === 0 ? (
                <li className="ezer-feed__empty">Rien encore. Parlez à Ezer ou écrivez-lui.</li>
              ) : (
                turns.map((turn, index) => (
                  <li className={`ezer-turn is-${turn.role}`} key={`${index}-${turn.content.slice(0, 24)}`}>
                    <span className="ezer-turn__who">{turn.role === "user" ? "vous" : "ezer"}</span>
                    <span className="ezer-turn__text">{turn.content}</span>
                  </li>
                ))
              )}
            </ol>
          </div>
        ) : hasViews ? (
          <div className="ezer-views">
            {renderedUi.length > 0
              ? renderedUi.map((message) => (
                  <AgentMessageRenderer
                    conversationId={account?.account_id ?? "local"}
                    key={message.id}
                    message={message}
                    onAction={(actionId, values, source) => void dispatchUiAction(actionId, values, source)}
                    workspaceId={account?.account_id ?? ""}
                  />
                ))
              : <AssistantViews views={views} />}
            <button
              className="ezer-dismiss"
              onClick={() => {
                setViews([]);
                setUiMessages([]);
              }}
              type="button"
            >
              fermer
            </button>
          </div>
        ) : copilot ? (
          <div className="ezer-feed">
            <p className="ezer-feed__title">actions en direct</p>
            <ol className="ezer-feed__list">
              {actions.length === 0 ? (
                <li className="ezer-feed__empty">Rien encore. Parlez à Ezer.</li>
              ) : (
                actions.map((action) => (
                  <li className="ezer-act" key={action.id}>
                    <span
                      className="ezer-act__dot"
                      style={{ background: action.live ? ACCENT : "#3a2c2e" }}
                      aria-hidden="true"
                    />
                    <span className="ezer-act__body">
                      <span className="ezer-act__text">{action.text}</span>
                      <span className="ezer-act__meta">{action.meta}</span>
                    </span>
                  </li>
                ))
              )}
            </ol>
          </div>
        ) : null}

        {pending.map((deletion) => (
          <div className="ezer-confirm" key={deletion.message_id} role="alertdialog">
            <p className="ezer-confirm__text">
              Mettre à la corbeille «&nbsp;{deletion.subject || "sans objet"}&nbsp;»
              {deletion.sender_address === "" ? null : <> de {deletion.sender_address}</>} ?
              Le message restera récupérable.
            </p>
            <div className="ezer-confirm__actions">
              <button
                className="ezer-pill is-danger"
                disabled={busy}
                onClick={() => void confirmDeletion(deletion)}
                type="button"
              >
                confirmer
              </button>
              <button
                className="ezer-pill"
                disabled={busy}
                onClick={() => setPending([])}
                type="button"
              >
                annuler
              </button>
            </div>
          </div>
        ))}

        {error !== null ? (
          <p className="ezer-error" role="alert">
            {error}
          </p>
        ) : null}

        {composerOpen ? (
          <form
            className="ezer-composer"
            onSubmit={(event) => {
              event.preventDefault();
              void submitDraft();
            }}
          >
            <label className="ezer-sr" htmlFor="ezer-draft">
              Écrire à Ezer
            </label>
            <input
              autoComplete="off"
              autoFocus
              className="ezer-input"
              disabled={busy}
              id="ezer-draft"
              maxLength={2_000}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Écrire à Ezer…"
              value={draft}
            />
            <button
              className="ezer-pill is-primary"
              disabled={busy || draft.trim() === ""}
              type="submit"
            >
              envoyer
            </button>
          </form>
        ) : null}

        <footer className="ezer-foot">
          {/* Sans boîte connectée, la voix n'a rien à lire : on renvoie là où on la connecte. */}
          {account === null && ready ? (
            <a className="ezer-link" href="/analyses">
              connecter une boîte
            </a>
          ) : (
            <span className="ezer-count">{account?.mailbox ?? "…"}</span>
          )}
          <div className="ezer-foot__actions ezer-chrome">
            <button
              aria-expanded={historyOpen}
              aria-label={historyOpen ? "Masquer la conversation" : "Afficher la conversation"}
              className={`ezer-icon${historyOpen ? " is-on" : ""}`}
              disabled={turns.length === 0}
              onClick={() => setHistoryOpen((open) => !open)}
              type="button"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7">
                <path d="M4 6h16M4 12h10M4 18h13" strokeLinecap="round" />
              </svg>
            </button>
            <button
              aria-expanded={composerOpen}
              aria-label={composerOpen ? "Fermer la saisie écrite" : "Écrire à Ezer"}
              className={`ezer-icon${composerOpen ? " is-on" : ""}`}
              onClick={() => setComposerOpen((open) => !open)}
              type="button"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7">
                <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z" strokeLinejoin="round" />
              </svg>
            </button>
            <button
              className={`ezer-pill${listening ? "" : " is-primary"}`}
              disabled={busy || account === null}
              onClick={() => void toggleListening()}
              type="button"
            >
              {listening ? "⏹ raccrocher" : "● parler"}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}

const CHROME_IDLE_MS = 4_000;

const CONSOLE_STYLES = `
.ezer-voice {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: ${SURFACE};
  padding: 24px;
  font-family: var(--font-voice), system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.ezer-card {
  width: min(420px, 100%);
  height: min(660px, 92vh);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: ${SURFACE};
}
.ezer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 22px 0;
}
.ezer-brand { display: flex; align-items: center; gap: 9px; }
.ezer-dot { width: 8px; height: 8px; border-radius: 50%; background: ${ACCENT}; }
.ezer-name { font-size: 15px; font-weight: 600; letter-spacing: 0.04em; color: #f2eded; }
.ezer-modes {
  display: flex;
  gap: 2px;
  padding: 3px;
  background: transparent;
  border-radius: 99px;
}
.ezer-mode {
  border: none;
  cursor: pointer;
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 10.5px;
  letter-spacing: 0.06em;
  padding: 5px 12px;
  border-radius: 99px;
  background: transparent;
  color: #6b5a5c;
}
.ezer-mode.is-on { background: ${ACCENT}; color: #f5eaea; }
.ezer-stage {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 18px;
  padding: 28px 22px;
  transition: flex 0.4s ease;
  min-height: 0;
}
.ezer-stage.is-compact { flex: 0 0 auto; padding-bottom: 12px; }
.ezer-orb { cursor: pointer; transition: width 0.4s ease, height 0.4s ease; }
.ezer-status {
  margin: 0;
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 11px;
  letter-spacing: 0.14em;
  color: #6b5a5c;
  text-transform: uppercase;
}
.ezer-reply {
  margin: 0;
  max-height: 8.5rem;
  overflow-y: auto;
  text-align: center;
  font-size: 14px;
  line-height: 1.55;
  color: #d8cfd0;
}
.ezer-views {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0 22px 12px;
}
.ezer-dismiss {
  align-self: flex-start;
  border: none;
  background: transparent;
  color: #8a7679;
  cursor: pointer;
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: 0.08em;
  padding: 5px 12px;
  border-radius: 99px;
  flex-shrink: 0;
}
.ezer-feed {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 0 22px 12px;
}
.ezer-feed__title {
  margin: 0;
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: 0.16em;
  color: #4d3f41;
  text-transform: uppercase;
  padding-bottom: 12px;
}
.ezer-feed__list { margin: 0; padding: 0; list-style: none; overflow-y: auto; }
.ezer-feed__empty { padding: 14px 0; font-size: 13px; color: #5c4d4f; }
.ezer-act {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 0;
}
.ezer-act__dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.ezer-act__body { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.ezer-act__text {
  font-size: 13px;
  color: #d8cfd0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ezer-act__meta {
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 10px;
  color: #5c4d4f;
}
.ezer-confirm {
  margin: 0 22px 12px;
  padding: 12px 14px;
  background: rgba(109, 22, 34, 0.16);
  border-radius: 12px;
  display: grid;
  gap: 10px;
}
.ezer-confirm__text { margin: 0; font-size: 13px; line-height: 1.5; color: #e2d6d7; }
.ezer-confirm__actions { display: flex; gap: 8px; }
.ezer-error {
  margin: 0 22px 12px;
  font-size: 12.5px;
  color: #e0808b;
}
.ezer-composer {
  display: flex;
  gap: 8px;
  padding: 0 22px 14px;
}
.ezer-input {
  flex: 1;
  min-width: 0;
  font: inherit;
  font-size: 13px;
  padding: 9px 14px;
  border-radius: 99px;
  border: none;
  background: #0d0c0c;
  color: #e8dedf;
}
.ezer-input::placeholder { color: #5c4d4f; }
.ezer-input:focus { outline: 1px solid ${ACCENT}; }
.ezer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 22px;
}
.ezer-count {
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: 0.1em;
  color: #4d3f41;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ezer-foot__actions { display: flex; align-items: center; gap: 8px; }
.ezer-link {
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: 0.1em;
  color: #b0525e;
  text-decoration: none;
}
.ezer-link:hover { color: #d97883; }
.ezer-icon {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 99px;
  border: none;
  background: transparent;
  color: #8a7679;
  cursor: pointer;
  padding: 0;
}
.ezer-icon svg { width: 17px; height: 17px; }
.ezer-icon.is-on { color: #f5eaea; }
.ezer-pill {
  border: none;
  background: transparent;
  color: #8a7679;
  cursor: pointer;
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 10.5px;
  letter-spacing: 0.08em;
  padding: 7px 16px;
  border-radius: 99px;
}
.ezer-pill.is-primary { background: ${ACCENT}; color: #f5eaea; }
.ezer-pill.is-danger { color: #e0808b; }
.ezer-pill[disabled], .ezer-icon[disabled] { cursor: not-allowed; opacity: 0.5; }
.ezer-history {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 0 22px 12px;
}
.ezer-history__list {
  margin: 0;
  padding: 0;
  list-style: none;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.ezer-turn {
  display: grid;
  gap: 4px;
  padding-top: 12px;
}
.ezer-turn__who {
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 9.5px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: #4d3f41;
}
.ezer-turn.is-assistant .ezer-turn__who { color: ${ACCENT}; }
.ezer-turn__text {
  font-size: 13.5px;
  line-height: 1.55;
  color: #d8cfd0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.ezer-turn.is-user .ezer-turn__text { color: #8a7679; }
.ezer-chrome {
  transition: opacity 500ms ease;
}
.ezer-card.is-idle .ezer-history {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 0 22px 12px;
}
.ezer-history__list {
  margin: 0;
  padding: 0;
  list-style: none;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.ezer-turn {
  display: grid;
  gap: 4px;
  padding-top: 12px;
}
.ezer-turn__who {
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 9.5px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: #4d3f41;
}
.ezer-turn.is-assistant .ezer-turn__who { color: ${ACCENT}; }
.ezer-turn__text {
  font-size: 13.5px;
  line-height: 1.55;
  color: #d8cfd0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.ezer-turn.is-user .ezer-turn__text { color: #8a7679; }
.ezer-chrome {
  opacity: 0;
  pointer-events: none;
}
/* Le focus clavier ramène les commandes : elles restent atteignables sans souris. */
.ezer-card.is-idle .ezer-chrome:focus-within {
  opacity: 1;
  pointer-events: auto;
}
@media (prefers-reduced-motion: reduce) {
  .ezer-history {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 0 22px 12px;
}
.ezer-history__list {
  margin: 0;
  padding: 0;
  list-style: none;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.ezer-turn {
  display: grid;
  gap: 4px;
  padding-top: 12px;
}
.ezer-turn__who {
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 9.5px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: #4d3f41;
}
.ezer-turn.is-assistant .ezer-turn__who { color: ${ACCENT}; }
.ezer-turn__text {
  font-size: 13.5px;
  line-height: 1.55;
  color: #d8cfd0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.ezer-turn.is-user .ezer-turn__text { color: #8a7679; }
.ezer-chrome { transition: none; }
}
.ezer-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
`;
