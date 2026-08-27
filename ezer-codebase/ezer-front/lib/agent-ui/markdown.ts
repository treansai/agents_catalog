/**
 * Markdown borné : pas de HTML brut, pas de javascript:, pas de scripts.
 * Le texte d'un outil ou d'un mail est toujours traité comme non fiable.
 */

const ALLOWED_SCHEMES = /^(https?:|mailto:)/i;

export function sanitizeText(value: string, maximum = 8_000): string {
  return value.replace(/\u0000/g, "").slice(0, maximum);
}

export function sanitizeHref(href: string): string | null {
  const trimmed = href.trim();
  if (!ALLOWED_SCHEMES.test(trimmed)) return null;
  if (/javascript:|data:|vbscript:/i.test(trimmed)) return null;
  return trimmed.slice(0, 2_048);
}

/** Rend un texte assistant en nœuds sûrs : gras/italique simples, liens https uniquement. */
export function renderSafeMarkdown(content: string): Array<{ type: "text" | "link"; text: string; href?: string }> {
  const source = sanitizeText(content);
  const parts: Array<{ type: "text" | "link"; text: string; href?: string }> = [];
  const pattern = /\[([^\]]{1,200})\]\(([^)]{1,500})\)/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(source)) !== null) {
    if (match.index > cursor) {
      parts.push({ type: "text", text: source.slice(cursor, match.index) });
    }
    const href = sanitizeHref(match[2] ?? "");
    if (href === null) {
      parts.push({ type: "text", text: match[1] ?? "" });
    } else {
      parts.push({ type: "link", text: match[1] ?? "", href });
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < source.length) parts.push({ type: "text", text: source.slice(cursor) });
  return parts;
}

export function looksLikeCode(value: unknown): boolean {
  if (typeof value !== "string") return false;
  return /<\s*script|eval\(|new Function|javascript:/i.test(value);
}
