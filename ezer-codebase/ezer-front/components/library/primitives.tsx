"use client";

import type { CSSProperties, ReactNode } from "react";

export function EzCard({
  children,
  className = "",
  style,
  role,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  role?: string;
}) {
  return (
    <div className={`ezc ezc-card ${className}`.trim()} role={role} style={style}>
      {children}
    </div>
  );
}

export function EzPill({
  children,
  variant = "ghost",
  disabled,
  onClick,
  type = "button",
}: {
  children: ReactNode;
  variant?: "ghost" | "primary" | "danger";
  disabled?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
}) {
  const extra = variant === "primary" ? " is-primary" : variant === "danger" ? " is-danger" : "";
  return (
    <button className={`ezc-pill${extra}`} disabled={disabled} onClick={onClick} type={type}>
      {children}
    </button>
  );
}

export function EzTag({ children, urgent }: { children: ReactNode; urgent?: boolean }) {
  return <span className={`ezc-tag${urgent === true ? " is-urgent" : ""}`}>{children}</span>;
}

export function EzAvatar({ initials }: { initials: string }) {
  return (
    <span
      style={{
        width: 42,
        height: 42,
        borderRadius: "50%",
        background: "rgba(136,13,30,0.3)",
        border: "1px solid rgba(136,13,30,0.6)",
        display: "grid",
        placeItems: "center",
        fontSize: 14,
        fontWeight: 600,
        color: "#e8b7bd",
        flexShrink: 0,
      }}
    >
      {initials.slice(0, 2).toUpperCase()}
    </span>
  );
}

export function EzLabel({ children }: { children: ReactNode }) {
  return (
    <span
      className="ezc-mono"
      style={{
        fontSize: 10,
        letterSpacing: "0.18em",
        color: "#6b5a5c",
        textTransform: "uppercase",
      }}
    >
      {children}
    </span>
  );
}
