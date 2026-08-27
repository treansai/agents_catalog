"use client";

import { EzCard, EzPill } from "./primitives";
import { asString, type AgentComponentProps } from "./agent-props";

export function ModeToggle({ value }: { value: "opaque" | "copilote" }) {
  return (
    <div
      className="ezc"
      style={{
        display: "flex",
        gap: 2,
        background: "#131011",
        border: "1px solid #201819",
        borderRadius: 99,
        padding: 3,
        width: "fit-content",
      }}
    >
      {(["opaque", "copilote"] as const).map((mode) => (
        <span
          key={mode}
          className="ezc-mono"
          style={{
            fontSize: 10.5,
            letterSpacing: "0.06em",
            padding: "6px 14px",
            borderRadius: 99,
            background: value === mode ? "#880d1e" : "transparent",
            color: value === mode ? "#f5eaea" : "#6b5a5c",
          }}
        >
          {mode}
        </span>
      ))}
    </div>
  );
}

export function VoiceStatus({ label, tone = "idle" }: { label: string; tone?: "idle" | "live" | "paused" }) {
  const color = tone === "live" ? "#d97883" : tone === "paused" ? "#4d3f41" : "#6b5a5c";
  return (
    <span
      className="ezc-mono"
      style={{
        fontSize: 10,
        letterSpacing: "0.14em",
        color,
        textTransform: "uppercase",
        background: tone === "live" ? "rgba(136,13,30,0.16)" : "#0b0a0a",
        border: `1px solid ${tone === "live" ? "rgba(136,13,30,0.45)" : "#1c1516"}`,
        borderRadius: 99,
        padding: "7px 14px",
      }}
    >
      {label}
    </span>
  );
}

export function SearchField({ placeholder, liveQuery }: { placeholder: string; liveQuery?: string }) {
  if (liveQuery !== undefined) {
    return (
      <div
        className="ezc"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          background: "#0b0a0a",
          border: "1px solid rgba(136,13,30,0.5)",
          borderRadius: 12,
          padding: "11px 14px",
        }}
      >
        <span className="ezc-dot is-live" />
        <span style={{ fontSize: 13, color: "#d8cfd0" }}>« {liveQuery} »</span>
        <span className="ezc-mono" style={{ marginLeft: "auto", fontSize: 9, letterSpacing: "0.1em", color: "#b0525e" }}>
          VOIX
        </span>
      </div>
    );
  }
  return (
    <div
      className="ezc"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        background: "#0b0a0a",
        border: "1px solid #1c1516",
        borderRadius: 12,
        padding: "11px 14px",
      }}
    >
      <span style={{ color: "#4d3f41", fontSize: 13 }}>⌕</span>
      <span style={{ fontSize: 13, color: "#5c4d4f" }}>{placeholder}</span>
    </div>
  );
}

export function FilterChips({ options }: { options: Array<{ id: string; label: string; selected?: boolean }> }) {
  return (
    <div className="ezc" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      {options.map((option) => (
        <EzPill key={option.id} variant={option.selected === true ? "primary" : "ghost"}>
          {option.label}
        </EzPill>
      ))}
    </div>
  );
}

export function ContextMenu({ items }: { items: Array<{ label: string; danger?: boolean; active?: boolean }> }) {
  return (
    <EzCard style={{ width: 210, padding: 6, background: "#0f0d0e" }}>
      {items.map((item) => (
        <div
          key={item.label}
          style={{
            fontSize: 12.5,
            color: item.danger === true ? "#d97883" : item.active === true ? "#d8cfd0" : "#a89a9c",
            padding: "9px 12px",
            borderRadius: 9,
            background: item.active === true ? "#1a1314" : "transparent",
          }}
        >
          {item.label}
        </div>
      ))}
    </EzCard>
  );
}

export function SettingsPanel() {
  return (
    <EzCard style={{ padding: "6px 18px", width: 360 }}>
      {[
        { title: "Tri automatique", detail: "classer sans demander", on: true },
        { title: "Envoi sans confirmation", detail: "réponses envoyées directement", on: false },
      ].map((row) => (
        <div
          key={row.title}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "14px 0",
            borderBottom: "1px solid #140f10",
          }}
        >
          <span style={{ display: "grid", gap: 2 }}>
            <span style={{ fontSize: 13, color: "#d8cfd0" }}>{row.title}</span>
            <span className="ezc-mono" style={{ fontSize: 10, color: "#5c4d4f" }}>
              {row.detail}
            </span>
          </span>
          <span
            style={{
              width: 38,
              height: 22,
              borderRadius: 99,
              background: row.on ? "#880d1e" : "#1a1314",
              border: row.on ? 0 : "1px solid #201819",
              position: "relative",
            }}
          >
            <span
              style={{
                position: "absolute",
                top: row.on ? 3 : 2,
                right: row.on ? 3 : undefined,
                left: row.on ? undefined : 3,
                width: 16,
                height: 16,
                borderRadius: "50%",
                background: row.on ? "#f5eaea" : "#4d3f41",
              }}
            />
          </span>
        </div>
      ))}
    </EzCard>
  );
}

export function MailboxList({
  items,
}: {
  items: Array<{ address: string; status: "sync" | "paused" }>;
}) {
  return (
    <EzCard style={{ padding: "6px 18px", width: 280 }}>
      <span
        className="ezc-mono"
        style={{ fontSize: 10, letterSpacing: "0.12em", color: "#6b5a5c", textTransform: "uppercase", padding: "14px 0 10px" }}
      >
        boîtes connectées
      </span>
      {items.map((item) => (
        <div
          key={item.address}
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "11px 0",
            borderTop: "1px solid #140f10",
          }}
        >
          <span style={{ fontSize: 13, color: item.status === "sync" ? "#d8cfd0" : "#8a7679" }}>{item.address}</span>
          <span className="ezc-mono" style={{ fontSize: 9.5, color: item.status === "sync" ? "#7da57d" : "#4d3f41" }}>
            {item.status === "sync" ? "● SYNC" : "EN PAUSE"}
          </span>
        </div>
      ))}
    </EzCard>
  );
}

export function DraftReply({
  title,
  tone,
  body,
  onSend,
  onRewrite,
}: {
  title: string;
  tone: string;
  body: string;
  onSend?: () => void;
  onRewrite?: () => void;
}) {
  return (
    <EzCard style={{ padding: 20, display: "grid", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span className="ezc-mono" style={{ fontSize: 10, letterSpacing: "0.12em", color: "#b0525e", textTransform: "uppercase" }}>
          {title}
        </span>
        <span className="ezc-mono" style={{ fontSize: 10, color: "#4d3f41" }}>
          ton : {tone}
        </span>
      </div>
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "#c9bfc0" }}>{body}</p>
      <div style={{ display: "flex", gap: 8, paddingTop: 10, borderTop: "1px solid #140f10" }}>
        <EzPill variant="primary" onClick={onSend}>
          envoyer
        </EzPill>
        <EzPill onClick={onRewrite}>reformuler</EzPill>
        <EzPill>dicter des retouches</EzPill>
      </div>
    </EzCard>
  );
}

export function DraftReplyAgent(props: AgentComponentProps) {
  return (
    <DraftReply
      title={asString(props.props.title, "brouillon")}
      tone={asString(props.props.tone, "professionnel")}
      body={asString(props.props.body)}
      onSend={() => props.onAction("draft.send", {})}
    />
  );
}

export function CalendarEvent({
  weekday,
  day,
  title,
  when,
  source,
}: {
  weekday: string;
  day: string;
  title: string;
  when: string;
  source?: string;
}) {
  return (
    <EzCard style={{ padding: 18, display: "flex", gap: 14, width: 280 }}>
      <div
        style={{
          display: "grid",
          justifyItems: "center",
          gap: 2,
          paddingRight: 14,
          borderRight: "1px solid #1a1314",
        }}
      >
        <span className="ezc-mono" style={{ fontSize: 10, color: "#6b5a5c" }}>
          {weekday}
        </span>
        <span style={{ fontSize: 20, fontWeight: 600 }}>{day}</span>
      </div>
      <div style={{ display: "grid", gap: 4 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "#d8cfd0" }}>{title}</span>
        <span className="ezc-mono" style={{ fontSize: 10, color: "#6b5a5c" }}>
          {when}
        </span>
        {source !== undefined ? (
          <span className="ezc-mono" style={{ fontSize: 10, color: "#b0525e" }}>
            {source}
          </span>
        ) : null}
      </div>
    </EzCard>
  );
}

export function CalendarEventAgent(props: AgentComponentProps) {
  return (
    <CalendarEvent
      weekday={asString(props.props.weekday, "")}
      day={asString(props.props.day, "")}
      title={asString(props.props.title)}
      when={asString(props.props.when)}
      source={asString(props.props.source) || undefined}
    />
  );
}

export function ContactCard({ name, meta }: { name: string; meta: string }) {
  const initials = name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
  return (
    <EzCard style={{ padding: 18, display: "flex", gap: 14, alignItems: "center", width: 260 }}>
      <span
        style={{
          width: 42,
          height: 42,
          borderRadius: "50%",
          background: "rgba(136,13,30,0.3)",
          border: "1px solid rgba(136,13,30,0.6)",
          display: "grid",
          placeItems: "center",
          fontWeight: 600,
          color: "#e8b7bd",
        }}
      >
        {initials}
      </span>
      <span style={{ display: "grid", gap: 2 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{name}</span>
        <span className="ezc-mono" style={{ fontSize: 10, color: "#6b5a5c" }}>
          {meta}
        </span>
      </span>
    </EzCard>
  );
}

export function ContactCardAgent(props: AgentComponentProps) {
  return <ContactCard name={asString(props.props.name)} meta={asString(props.props.meta)} />;
}

export function ChatBubbles({
  user,
  assistant,
}: {
  user: string;
  assistant: string;
}) {
  return (
    <div className="ezc" style={{ display: "grid", gap: 10, width: 340 }}>
      <div
        style={{
          alignSelf: "end",
          maxWidth: "85%",
          background: "#131011",
          border: "1px solid #201819",
          borderRadius: "16px 16px 4px 16px",
          padding: "10px 14px",
        }}
      >
        <span style={{ fontSize: 13, color: "#d8cfd0" }}>{user}</span>
      </div>
      <div
        style={{
          alignSelf: "start",
          maxWidth: "85%",
          background: "rgba(136,13,30,0.16)",
          border: "1px solid rgba(136,13,30,0.45)",
          borderRadius: "16px 16px 16px 4px",
          padding: "10px 14px",
          display: "grid",
          gap: 4,
        }}
      >
        <span style={{ fontSize: 13, color: "#f0dfe1" }}>{assistant}</span>
        <span className="ezc-mono" style={{ fontSize: 9.5, letterSpacing: "0.1em", color: "#b0525e", textTransform: "uppercase" }}>
          ezer
        </span>
      </div>
    </div>
  );
}
