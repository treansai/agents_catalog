"use client";

/**
 * Surfaces « liquides » : l'interface prend la forme de ce que la voix vient de demander.
 *
 * Chaque outil de l'assistant renvoie une donnée typée ; à chaque forme correspond un composant.
 * « Ouvre le dernier mail » fait apparaître le message entier, « qui m'écrit le plus » un
 * classement, « qu'est-ce qui demande une action » la file de triage.
 */

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

const categoryLabels: Record<string, string> = {
  action_required: "action requise",
  informational: "information",
  newsletter: "newsletter",
  other: "autre",
  receipt: "reçu",
  security: "sécurité",
  spam: "indésirable",
};

const priorityLabels: Record<string, string> = {
  critical: "critique",
  high: "haute",
  low: "basse",
  normal: "normale",
};

function formatMoment(value: string): string {
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

function HeaderLine({ header }: { header: MessageHeaderView }) {
  return (
    <>
      <span className="ezv-row__top">
        <span className="ezv-row__from">{header.sender_name || header.sender_address}</span>
        <span className="ezv-row__when">{formatMoment(header.received_at)}</span>
      </span>
      <span className="ezv-row__subject">
        {header.is_read ? null : <span className="ezv-unread" aria-label="non lu" />}
        {header.subject || "(sans objet)"}
        {header.has_attachments ? <span className="ezv-clip" aria-label="pièce jointe">↗</span> : null}
      </span>
      <span className="ezv-row__snippet">{header.snippet}</span>
    </>
  );
}

function MessagePanel({ view }: { view: Extract<AssistantView, { kind: "message" }> }) {
  const { message } = view;
  return (
    <article className="ezv-message">
      <p className="ezv-message__subject">{message.subject || "(sans objet)"}</p>
      <p className="ezv-message__meta">
        {message.sender_name || "expéditeur inconnu"}
        <span className="ezv-message__address"> &lt;{message.sender_address}&gt;</span>
        {" · "}
        {formatMoment(message.received_at)}
      </p>
      {/* Contenu d'expéditeur : affiché tel quel, jamais interprété. */}
      <pre className="ezv-message__body">{view.body_text}</pre>
    </article>
  );
}

function MessageListPanel({ view }: { view: Extract<AssistantView, { kind: "messages" }> }) {
  return (
    <div className="ezv-block">
      <p className="ezv-block__title">
        {view.title}
        <span className="ezv-block__count">{view.items.length}</span>
      </p>
      {view.items.length === 0 ? (
        <p className="ezv-empty">Aucun message.</p>
      ) : (
        <ol className="ezv-list">
          {view.items.map((header) => (
            <li className="ezv-row" key={header.message_id}>
              <HeaderLine header={header} />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function SenderPanel({ view }: { view: Extract<AssistantView, { kind: "senders" }> }) {
  const busiest = view.items[0]?.total ?? 1;
  return (
    <div className="ezv-block">
      <p className="ezv-block__title">
        Expéditeurs
        <span className="ezv-block__count">{view.items.length}</span>
      </p>
      <ol className="ezv-list">
        {view.items.map((sender) => (
          <li className="ezv-sender" key={sender.sender_address}>
            <span className="ezv-sender__head">
              <span className="ezv-sender__name">{sender.sender_name || sender.sender_address}</span>
              <span className="ezv-sender__count">
                {sender.total}
                {sender.unread > 0 ? ` · ${sender.unread} non lus` : ""}
              </span>
            </span>
            <span className="ezv-bar" aria-hidden="true">
              <span
                className="ezv-bar__fill"
                style={{ width: `${Math.round((sender.total / busiest) * 100)}%` }}
              />
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function StatsPanel({ view }: { view: Extract<AssistantView, { kind: "stats" }> }) {
  return (
    <div className="ezv-block">
      <p className="ezv-block__title">{view.mailbox}</p>
      <div className="ezv-metrics">
        <span className="ezv-metric">
          <span className="ezv-metric__value">{view.total_messages}</span>
          <span className="ezv-metric__label">messages</span>
        </span>
        <span className="ezv-metric">
          <span className="ezv-metric__value">{view.unread_messages}</span>
          <span className="ezv-metric__label">non lus</span>
        </span>
      </div>
      <ol className="ezv-list">
        {view.folders.map((folder) => (
          <li className="ezv-folder" key={folder.name}>
            <span className="ezv-folder__name">{folder.name}</span>
            <span className="ezv-folder__count">
              {folder.total}
              {folder.unread > 0 ? ` · ${folder.unread} non lus` : ""}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function TriagePanel({ view }: { view: Extract<AssistantView, { kind: "triage" }> }) {
  return (
    <div className="ezv-block">
      <p className="ezv-block__title">
        Triage
        <span className="ezv-block__count">{view.total}</span>
      </p>
      <ol className="ezv-list">
        {view.items.map((item) => (
          <li className="ezv-triage" key={`${item.created_at}-${item.summary.slice(0, 24)}`}>
            <span className="ezv-tags">
              <span className={`ezv-tag ezv-tag--${item.priority}`}>
                {priorityLabels[item.priority] ?? item.priority}
              </span>
              <span className="ezv-tag">{categoryLabels[item.category] ?? item.category}</span>
              {item.needs_human_review ? <span className="ezv-tag">à relire</span> : null}
            </span>
            <span className="ezv-triage__summary">{item.summary}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function AssistantViews({ views }: { views: AssistantView[] }) {
  if (views.length === 0) return null;
  return (
    <div className="ezv-surface">
      <style>{VIEW_STYLES}</style>
      {views.map((view) => {
        switch (view.kind) {
          case "message":
            return <MessagePanel key="message" view={view} />;
          case "messages":
            return <MessageListPanel key="messages" view={view} />;
          case "senders":
            return <SenderPanel key="senders" view={view} />;
          case "stats":
            return <StatsPanel key="stats" view={view} />;
          case "triage":
            return <TriagePanel key="triage" view={view} />;
        }
      })}
    </div>
  );
}

const VIEW_STYLES = `
.ezv-surface {
  display: grid;
  gap: 14px;
  min-height: 0;
  overflow-y: auto;
  animation: ezv-in 0.28s ease;
}
@keyframes ezv-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
.ezv-block { display: grid; gap: 8px; min-width: 0; }
.ezv-block__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 0;
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: 0.16em;
  color: #4d3f41;
  text-transform: uppercase;
  padding-bottom: 8px;
  border-bottom: 1px solid #1a1314;
}
.ezv-block__count { color: #8a7679; letter-spacing: 0.08em; }
.ezv-empty { margin: 0; font-size: 13px; color: #5c4d4f; }
.ezv-list { margin: 0; padding: 0; list-style: none; display: grid; gap: 2px; }
.ezv-row {
  display: grid;
  gap: 3px;
  padding: 10px 0;
  border-bottom: 1px solid #140f10;
  min-width: 0;
}
.ezv-row__top { display: flex; justify-content: space-between; gap: 10px; }
.ezv-row__from {
  font-size: 12.5px;
  color: #d8cfd0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ezv-row__when {
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 10px;
  color: #5c4d4f;
  flex-shrink: 0;
}
.ezv-row__subject {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #f2eded;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ezv-row__snippet {
  font-size: 11.5px;
  color: #6b5a5c;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ezv-unread {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #880d1e;
  flex-shrink: 0;
}
.ezv-clip { color: #6b5a5c; font-size: 11px; }
.ezv-message { display: grid; gap: 8px; min-width: 0; }
.ezv-message__subject { margin: 0; font-size: 15px; font-weight: 600; color: #f2eded; }
.ezv-message__meta { margin: 0; font-size: 11.5px; color: #6b5a5c; }
.ezv-message__address { color: #4d3f41; }
.ezv-message__body {
  margin: 0;
  padding: 12px 14px;
  border: 1px solid #1a1314;
  border-radius: 12px;
  background: #0e0c0d;
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #cbc0c1;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 15rem;
  overflow-y: auto;
}
.ezv-sender { display: grid; gap: 5px; padding: 9px 0; }
.ezv-sender__head { display: flex; justify-content: space-between; gap: 10px; }
.ezv-sender__name {
  font-size: 12.5px;
  color: #d8cfd0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ezv-sender__count {
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 10px;
  color: #5c4d4f;
  flex-shrink: 0;
}
.ezv-bar { display: block; height: 3px; border-radius: 99px; background: #1a1314; }
.ezv-bar__fill { display: block; height: 100%; border-radius: 99px; background: #880d1e; }
.ezv-metrics { display: flex; gap: 22px; }
.ezv-metric { display: grid; gap: 2px; }
.ezv-metric__value { font-size: 22px; font-weight: 600; color: #f2eded; }
.ezv-metric__label {
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: 0.1em;
  color: #5c4d4f;
}
.ezv-folder {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid #140f10;
}
.ezv-folder__name {
  font-size: 12.5px;
  color: #d8cfd0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ezv-folder__count {
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 10px;
  color: #5c4d4f;
  flex-shrink: 0;
}
.ezv-triage { display: grid; gap: 5px; padding: 10px 0; border-bottom: 1px solid #140f10; }
.ezv-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.ezv-tag {
  font-family: var(--font-voice-mono), ui-monospace, monospace;
  font-size: 9.5px;
  letter-spacing: 0.06em;
  padding: 2px 8px;
  border-radius: 99px;
  border: 1px solid #2a1f20;
  color: #8a7679;
}
.ezv-tag--critical, .ezv-tag--high { border-color: #6d1622; color: #e0808b; }
.ezv-triage__summary { font-size: 12.5px; line-height: 1.5; color: #d8cfd0; }
`;
