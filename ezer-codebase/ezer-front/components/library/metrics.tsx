"use client";

import { EzCard, EzPill } from "./primitives";
import { asString, type AgentComponentProps } from "./agent-props";

export function MetricCard({
  value,
  label,
  hint,
  series,
}: {
  value: string;
  label: string;
  hint?: string;
  series?: number[];
}) {
  const max = series !== undefined && series.length > 0 ? Math.max(...series, 1) : 1;
  return (
    <EzCard style={{ padding: 16, display: "grid", gap: 4, minWidth: 130 }}>
      <span style={{ fontSize: 22, fontWeight: 600, color: "#f2eded" }}>{value}</span>
      <span
        className="ezc-mono"
        style={{ fontSize: 9.5, letterSpacing: "0.1em", color: "#6b5a5c", textTransform: "uppercase" }}
      >
        {label}
      </span>
      {hint !== undefined ? <span style={{ fontSize: 11, color: "#5c4d4f" }}>{hint}</span> : null}
      {series !== undefined && series.length > 0 ? (
        <span style={{ display: "flex", gap: 3, alignItems: "flex-end", height: 28, marginTop: 8 }}>
          {series.slice(0, 31).map((point, index) => (
            <span
              key={index}
              style={{
                width: 4,
                height: `${Math.max(12, Math.round((point / max) * 100))}%`,
                background: "#880d1e",
                borderRadius: 3,
              }}
            />
          ))}
        </span>
      ) : null}
    </EzCard>
  );
}

export function MorningBrief({
  title,
  body,
  when,
  onListen,
}: {
  title: string;
  body: string;
  when: string;
  onListen?: () => void;
}) {
  return (
    <EzCard style={{ padding: 20, display: "grid", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>{title}</span>
        <span className="ezc-mono" style={{ fontSize: 10, color: "#4d3f41" }}>
          {when}
        </span>
      </div>
      <span style={{ fontSize: 12.5, lineHeight: 1.6, color: "#a89a9c" }}>{body}</span>
      <EzPill variant="primary" onClick={onListen}>
        ▶ écouter le résumé
      </EzPill>
    </EzCard>
  );
}

export function MetricCardAgent(props: AgentComponentProps) {
  const record = props.data !== null && typeof props.data === "object" ? (props.data as Record<string, unknown>) : {};
  const series = Array.isArray(record.series)
    ? record.series.filter((entry): entry is number => typeof entry === "number")
    : undefined;
  return (
    <MetricCard
      value={asString(record.value, "—")}
      label={asString(record.label, asString(props.props.label, "valeur"))}
      hint={asString(record.hint, asString(props.props.hint)) || undefined}
      series={series}
    />
  );
}

export function MorningBriefAgent(props: AgentComponentProps) {
  const record = props.data !== null && typeof props.data === "object" ? (props.data as Record<string, unknown>) : {};
  return (
    <MorningBrief
      title={asString(record.title, asString(props.props.title, "Votre matinée"))}
      body={asString(record.body)}
      when={asString(record.when)}
    />
  );
}

export function SendersListAgent(props: AgentComponentProps) {
  const record = props.data !== null && typeof props.data === "object" ? (props.data as Record<string, unknown>) : {};
  const items = Array.isArray(record.items) ? record.items : [];
  const max = items.reduce((current, entry) => {
    const total = (entry as { total?: number }).total ?? 0;
    return total > current ? total : current;
  }, 1);
  return (
    <EzCard>
      {items.map((entry) => {
        const row = entry as Record<string, unknown>;
        const total = typeof row.total === "number" ? row.total : 0;
        return (
          <div className="ezc-row" key={asString(row.senderAddress, asString(row.sender))} style={{ cursor: "default" }}>
            <span style={{ display: "grid", gap: 5, flex: 1, minWidth: 0 }}>
              <span style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ fontSize: 12.5, color: "#d8cfd0" }}>{asString(row.sender)}</span>
                <span className="ezc-mono" style={{ fontSize: 10, color: "#5c4d4f" }}>
                  {total}
                </span>
              </span>
              <span style={{ display: "block", height: 3, borderRadius: 99, background: "#1a1314" }}>
                <span
                  style={{
                    display: "block",
                    height: "100%",
                    width: `${Math.round((total / max) * 100)}%`,
                    background: "#880d1e",
                    borderRadius: 99,
                  }}
                />
              </span>
            </span>
          </div>
        );
      })}
    </EzCard>
  );
}
