"use client";

import { EzCard, EzPill, EzTag } from "./primitives";
import { asBoolean, asString, type AgentComponentProps } from "./agent-props";

export interface MailRow {
  id: string;
  sender: string;
  senderAddress?: string;
  subject: string;
  snippet: string;
  receivedAt: string;
  unread: boolean;
  hasAttachments?: boolean;
  tag?: string;
}

function formatWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Paris",
  }).format(date);
}

export function MailListView({
  title,
  items,
  onOpen,
  onTrash,
}: {
  title?: string;
  items: MailRow[];
  onOpen?: (id: string) => void;
  onTrash?: (id: string) => void;
}) {
  return (
    <EzCard>
      {title !== undefined ? (
        <div
          className="ezc-mono"
          style={{
            padding: "12px 16px 0",
            fontSize: 10,
            letterSpacing: "0.12em",
            color: "#b0525e",
            textTransform: "uppercase",
          }}
        >
          {title}
        </div>
      ) : null}
      {items.map((item) => (
        <div className="ezc-row" key={item.id} style={{ cursor: "default" }}>
          <button
            className="ezc-row"
            onClick={() => onOpen?.(item.id)}
            style={{ padding: 0, border: 0, flex: 1, minWidth: 0 }}
            type="button"
          >
            <span className={`ezc-dot${item.unread ? " is-unread" : ""}`} />
            <span style={{ display: "grid", gap: 2, minWidth: 0, flex: 1 }}>
              <span style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                <span style={{ fontSize: 13, fontWeight: item.unread ? 600 : 500, color: item.unread ? "#f2eded" : "#a89a9c" }}>
                  {item.sender}
                </span>
                <span className="ezc-mono" style={{ fontSize: 10, color: "#4d3f41" }}>
                  {formatWhen(item.receivedAt)}
                </span>
              </span>
              <span
                style={{
                  fontSize: 12.5,
                  color: "#c9bfc0",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {item.subject}
              </span>
              <span
                style={{
                  fontSize: 11.5,
                  color: "#5c4d4f",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {item.snippet}
              </span>
            </span>
            {item.tag !== undefined ? (
              <EzTag urgent={item.tag === "urgent" || item.tag === "action_required"}>{item.tag}</EzTag>
            ) : null}
          </button>
          {onTrash !== undefined ? (
            <EzPill variant="danger" onClick={() => onTrash(item.id)}>
              corbeille
            </EzPill>
          ) : null}
        </div>
      ))}
    </EzCard>
  );
}

export function MailDetailView({
  subject,
  sender,
  senderAddress,
  receivedAt,
  body,
  onReply,
  onFile,
  onLater,
}: {
  subject: string;
  sender: string;
  senderAddress?: string;
  receivedAt: string;
  body: string;
  onReply?: () => void;
  onFile?: () => void;
  onLater?: () => void;
}) {
  return (
    <EzCard style={{ padding: 20, display: "grid", gap: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>{subject}</span>
        <span className="ezc-mono" style={{ fontSize: 10, color: "#4d3f41" }}>
          {formatWhen(receivedAt)}
        </span>
      </div>
      <div className="ezc-mono" style={{ fontSize: 10, color: "#6b5a5c" }}>
        de {senderAddress || sender}
      </div>
      <pre
        style={{
          margin: 0,
          font: "inherit",
          fontSize: 13,
          lineHeight: 1.6,
          color: "#c9bfc0",
          whiteSpace: "pre-wrap",
        }}
      >
        {body}
      </pre>
      <div style={{ display: "flex", gap: 8, paddingTop: 4, borderTop: "1px solid #140f10" }}>
        <EzPill variant="primary" onClick={onReply}>
          répondre par ezer
        </EzPill>
        <EzPill onClick={onFile}>classer</EzPill>
        <EzPill onClick={onLater}>plus tard</EzPill>
      </div>
    </EzCard>
  );
}

export function MailListAgent(props: AgentComponentProps) {
  const record = props.data !== null && typeof props.data === "object" ? (props.data as Record<string, unknown>) : {};
  const items = Array.isArray(record.items)
    ? record.items.map((entry) => {
        const row = entry as Record<string, unknown>;
        return {
          id: asString(row.id),
          sender: asString(row.sender),
          senderAddress: asString(row.senderAddress),
          subject: asString(row.subject),
          snippet: asString(row.snippet),
          receivedAt: asString(row.receivedAt),
          unread: asBoolean(row.unread),
          hasAttachments: asBoolean(row.hasAttachments),
          tag: typeof row.tag === "string" ? row.tag : undefined,
        };
      })
    : [];
  const total = typeof record.total === "number" ? record.total : items.length;
  const limit = typeof record.limit === "number" ? record.limit : items.length || 20;
  const offset = typeof record.offset === "number" ? record.offset : 0;
  const canPrev = offset > 0;
  const canNext = offset + items.length < total;
  return (
    <div className="ezc" style={{ display: "grid", gap: 8 }}>
      <MailListView
        title={asString(props.props.title) || undefined}
        items={items}
        onOpen={(id) => props.onAction("messages.open", { targetId: id })}
        onTrash={(id) => props.onAction("messages.trash", { targetId: id })}
      />
      {total > limit ? (
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0 4px" }}>
          <span className="ezc-mono" style={{ fontSize: 10, color: "#5c4d4f" }}>
            {offset + 1}–{offset + items.length} / {total}
          </span>
          <span style={{ display: "flex", gap: 8 }}>
            <EzPill
              disabled={!canPrev}
              onClick={() =>
                props.onAction("table.page", { offset: Math.max(0, offset - limit), limit })
              }
            >
              précédent
            </EzPill>
            <EzPill
              disabled={!canNext}
              onClick={() => props.onAction("table.page", { offset: offset + limit, limit })}
            >
              suivant
            </EzPill>
          </span>
        </div>
      ) : null}
    </div>
  );
}

export function MailDetailAgent(props: AgentComponentProps) {
  const record = props.data !== null && typeof props.data === "object" ? (props.data as Record<string, unknown>) : {};
  return (
    <MailDetailView
      subject={asString(record.subject, asString(props.props.title, "Message"))}
      sender={asString(record.sender)}
      senderAddress={asString(record.senderAddress)}
      receivedAt={asString(record.receivedAt)}
      body={asString(record.body)}
      onReply={() => props.onAction("draft.reply", { targetId: asString(record.id) })}
    />
  );
}
