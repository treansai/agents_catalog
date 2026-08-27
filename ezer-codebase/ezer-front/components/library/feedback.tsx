"use client";

import { EzCard, EzPill } from "./primitives";
import { asBoolean, asString, type AgentComponentProps } from "./agent-props";

export function ConfirmationDialog({
  title,
  body,
  confirmLabel = "confirmer",
  cancelLabel = "annuler",
  reversible,
  targetLabel,
  busy,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  reversible: boolean;
  targetLabel?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <EzCard
      role="alertdialog"
      style={{
        padding: 22,
        display: "grid",
        gap: 14,
        background: "#0f0d0e",
        boxShadow: "0 30px 80px rgba(0,0,0,0.8)",
      }}
    >
      <span style={{ fontSize: 15, fontWeight: 600 }}>{title}</span>
      {targetLabel !== undefined ? (
        <span className="ezc-mono" style={{ fontSize: 11, color: "#d97883" }}>
          cible : {targetLabel}
        </span>
      ) : null}
      <span style={{ fontSize: 12.5, lineHeight: 1.55, color: "#8a7679" }}>{body}</span>
      <span className="ezc-mono" style={{ fontSize: 10, color: reversible ? "#7da57d" : "#d97883" }}>
        {reversible ? "action réversible" : "action non réversible"}
      </span>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <EzPill disabled={busy} onClick={onCancel}>
          {cancelLabel}
        </EzPill>
        <EzPill disabled={busy} variant="primary" onClick={onConfirm}>
          {confirmLabel}
        </EzPill>
      </div>
    </EzCard>
  );
}

export function EmptyStateView({ title, detail }: { title: string; detail?: string }) {
  return (
    <EzCard
      style={{
        minHeight: 170,
        display: "grid",
        placeItems: "center",
        gap: 10,
        textAlign: "center",
        padding: 24,
      }}
    >
      <span className="ezc-dot is-unread" />
      <span style={{ fontSize: 13, color: "#a89a9c" }}>{title}</span>
      {detail !== undefined ? (
        <span className="ezc-mono" style={{ fontSize: 10, color: "#4d3f41" }}>
          {detail}
        </span>
      ) : null}
    </EzCard>
  );
}

export function SkeletonView({ label = "synchronisation…" }: { label?: string }) {
  return (
    <EzCard style={{ padding: 18, display: "grid", gap: 14 }}>
      <span className="ezc-skel" style={{ width: "60%" }} />
      <span className="ezc-skel" style={{ width: "85%", animationDelay: "0.2s" }} />
      <span className="ezc-skel" style={{ width: "45%", animationDelay: "0.4s" }} />
      <span className="ezc-mono" style={{ fontSize: 10, color: "#4d3f41" }}>
        {label}
      </span>
    </EzCard>
  );
}

export function StatusBanner({
  title,
  detail,
  tone = "neutral",
  actionLabel,
  onAction,
}: {
  title: string;
  detail?: string;
  tone?: "neutral" | "danger";
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <EzCard
      style={{
        padding: 18,
        display: "grid",
        gap: 8,
        background: tone === "danger" ? "rgba(136,13,30,0.1)" : undefined,
        borderColor: tone === "danger" ? "rgba(136,13,30,0.45)" : undefined,
      }}
    >
      <span style={{ fontSize: 13, fontWeight: 600, color: tone === "danger" ? "#e8b7bd" : "#d8cfd0" }}>{title}</span>
      {detail !== undefined ? (
        <span style={{ fontSize: 12, lineHeight: 1.5, color: "#a08488" }}>{detail}</span>
      ) : null}
      {actionLabel !== undefined ? (
        <EzPill variant="primary" onClick={onAction}>
          {actionLabel}
        </EzPill>
      ) : null}
    </EzCard>
  );
}

export function ProgressBar({ label, current, total }: { label: string; current: number; total: number }) {
  const ratio = total === 0 ? 0 : Math.round((current / total) * 100);
  return (
    <EzCard style={{ padding: "16px 18px", display: "grid", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span className="ezc-mono" style={{ fontSize: 10, letterSpacing: "0.12em", color: "#6b5a5c", textTransform: "uppercase" }}>
          {label}
        </span>
        <span className="ezc-mono" style={{ fontSize: 11, color: "#d97883" }}>
          {current} / {total}
        </span>
      </div>
      <div style={{ height: 3, borderRadius: 99, background: "#1a1314", overflow: "hidden" }}>
        <div style={{ width: `${ratio}%`, height: "100%", background: "#880d1e", borderRadius: 99 }} />
      </div>
    </EzCard>
  );
}

export function ActionFeed({
  items,
}: {
  items: Array<{ text: string; meta: string; live?: boolean }>;
}) {
  return (
    <EzCard style={{ padding: "6px 18px" }}>
      {items.map((item, index) => (
        <div
          key={`${item.text}-${index}`}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "13px 0",
            borderBottom: index === items.length - 1 ? 0 : "1px solid #140f10",
          }}
        >
          <span className={`ezc-dot${item.live === true ? " is-live" : ""}`} style={{ background: item.live === true ? undefined : "#3a2c2e" }} />
          <span style={{ display: "grid", gap: 2 }}>
            <span style={{ fontSize: 13, color: "#d8cfd0" }}>{item.text}</span>
            <span className="ezc-mono" style={{ fontSize: 10, color: item.live === true ? "#d97883" : "#5c4d4f" }}>
              {item.meta}
            </span>
          </span>
        </div>
      ))}
    </EzCard>
  );
}

export function ConfirmAgent(props: AgentComponentProps) {
  const record = props.data !== null && typeof props.data === "object" ? (props.data as Record<string, unknown>) : {};
  return (
    <ConfirmationDialog
      title={asString(props.props.title, "Confirmer ?")}
      body={asString(props.props.body)}
      confirmLabel={asString(props.props.confirmLabel, "confirmer")}
      cancelLabel={asString(props.props.cancelLabel, "annuler")}
      reversible={asBoolean(props.props.reversible) || asBoolean(record.reversible)}
      targetLabel={asString(props.props.targetLabel, asString(record.target)) || undefined}
      onConfirm={() =>
        props.onAction("confirmation.confirm", {
          confirmationId: record.confirmationId,
          confirmationToken: record.token,
        })
      }
      onCancel={() => props.onAction("confirmation.cancel", { confirmationId: record.confirmationId })}
    />
  );
}

export function EmptyStateAgent(props: AgentComponentProps) {
  return <EmptyStateView title={asString(props.props.title, "Rien à afficher")} detail={asString(props.props.detail) || undefined} />;
}

export function ActionFeedAgent(props: AgentComponentProps) {
  const record = props.data !== null && typeof props.data === "object" ? (props.data as Record<string, unknown>) : {};
  const items = Array.isArray(record.items)
    ? record.items.map((entry) => {
        const row = entry as Record<string, unknown>;
        return { text: asString(row.text), meta: asString(row.meta), live: asBoolean(row.live) };
      })
    : [];
  return <ActionFeed items={items} />;
}

export function StatusBannerAgent(props: AgentComponentProps) {
  return (
    <StatusBanner
      title={asString(props.props.title, "État")}
      detail={asString(props.props.detail) || undefined}
      tone={props.props.tone === "danger" ? "danger" : "neutral"}
    />
  );
}

export function ProgressAgent(props: AgentComponentProps) {
  const current = typeof props.props.current === "number" ? props.props.current : 0;
  const total = typeof props.props.total === "number" ? props.props.total : 1;
  return <ProgressBar label={asString(props.props.label, "en cours")} current={current} total={total} />;
}

export function ChoicesAgent(props: AgentComponentProps) {
  const options = Array.isArray(props.props.options) ? props.props.options : [];
  return (
    <div className="ezc" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      {options.map((entry) => {
        const option = entry as Record<string, unknown>;
        const selected = asBoolean(option.selected);
        return (
          <EzPill
            key={asString(option.id)}
            variant={selected ? "primary" : "ghost"}
            onClick={() => props.onAction("table.filter", { optionId: asString(option.id) })}
          >
            {asString(option.label)}
          </EzPill>
        );
      })}
    </div>
  );
}
