import "server-only";

import { AGENT_UI_PROTOCOL_VERSION } from "@/lib/agent-ui/contracts";
import { toPublicCatalog } from "@/lib/agent-ui/registry";
import { DEMO_ANALYSES } from "@/lib/demo-data";

const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const REQUEST_TIMEOUT_MS = 15_000;

export class AgentUiBffError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AgentUiBffError";
    this.status = status;
  }
}

function backendConfig(): { apiUrl: URL; apiKey: string } | null {
  const apiUrl = process.env.EZER_API_URL?.trim();
  const apiKey = process.env.EZER_API_KEY?.trim();
  if (!apiUrl || !apiKey) return null;
  const parsed = new URL(apiUrl);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new AgentUiBffError("Invalid Ezer API URL protocol", 500);
  }
  return { apiUrl: parsed, apiKey };
}

export function assertWorkspaceId(value: string | null): string {
  if (value === null || !ACCOUNT_ID.test(value)) {
    throw new AgentUiBffError("workspace invalide", 400);
  }
  return value;
}

async function backend(path: string, init: RequestInit): Promise<Response> {
  const config = backendConfig();
  if (config === null) {
    throw new AgentUiBffError("demo", 204);
  }
  const url = new URL(path, config.apiUrl);
  return fetch(url, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "X-API-Key": config.apiKey,
      ...init.headers,
    },
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
}

export async function fetchCatalog(workspaceId: string): Promise<unknown> {
  try {
    const response = await backend(`/v1/agent-ui/catalog?workspace_id=${encodeURIComponent(workspaceId)}`, {
      method: "GET",
    });
    if (!response.ok) throw new AgentUiBffError("catalogue indisponible", response.status);
    return await response.json();
  } catch (error) {
    if (error instanceof AgentUiBffError && error.status === 204) {
      return { protocolVersion: AGENT_UI_PROTOCOL_VERSION, components: toPublicCatalog() };
    }
    throw error;
  }
}

function demoInvoices(workspaceId: string, limit: number, offset: number, query: string) {
  const items = DEMO_ANALYSES.filter((analysis) => analysis.message_ref.includes(workspaceId) || workspaceId.startsWith("work"))
    .filter((analysis) => analysis.category === "receipt" || /facture|reçu|paiement/i.test(analysis.summary))
    .map((analysis) => ({
      id: analysis.message_ref.split(":")[2] ?? analysis.analysis_id,
      sender: analysis.summary.slice(0, 40),
      subject: analysis.summary,
      snippet: analysis.key_points[0] ?? "",
      receivedAt: analysis.created_at,
      unread: analysis.needs_human_review,
      tag: "finances",
    }))
    .filter((row) => query === "" || `${row.subject} ${row.snippet}`.toLowerCase().includes(query.toLowerCase()));
  return {
    status: items.length === 0 ? "empty" : "success",
    data: { items: items.slice(offset, offset + limit), total: items.length, limit, offset },
  };
}

export async function fetchResolve(
  workspaceId: string,
  body: { resolverId: string; input: Record<string, unknown>; componentId: string; instanceId: string },
): Promise<unknown> {
  try {
    const response = await backend(`/v1/agent-ui/resolve?workspace_id=${encodeURIComponent(workspaceId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new AgentUiBffError("résolveur refusé", response.status);
    return await response.json();
  } catch (error) {
    if (error instanceof AgentUiBffError && error.status === 204) {
      const limit = typeof body.input.limit === "number" ? body.input.limit : 20;
      const offset = typeof body.input.offset === "number" ? body.input.offset : 0;
      const query = typeof body.input.query === "string" ? body.input.query : "";
      if (body.resolverId === "invoices.search" || body.resolverId === "messages.search") {
        return demoInvoices(workspaceId, limit, offset, query);
      }
      if (body.resolverId === "metrics.receipts") {
        const page = demoInvoices(workspaceId, 25, 0, "");
        const data = page.data as { total: number };
        return { status: "success", data: { value: String(data.total), label: "reçus ce mois", hint: "mode démonstration" } };
      }
      if (body.resolverId === "mailbox.stats") {
        return {
          status: "success",
          data: { value: String(DEMO_ANALYSES.length), label: "analyses", hint: "mode démonstration" },
        };
      }
      throw new AgentUiBffError("résolveur inconnu", 400);
    }
    throw error;
  }
}

export async function fetchAction(workspaceId: string, body: Record<string, unknown>): Promise<unknown> {
  try {
    const response = await backend(`/v1/agent-ui/action?workspace_id=${encodeURIComponent(workspaceId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new AgentUiBffError("action refusée", response.status);
    return await response.json();
  } catch (error) {
    if (error instanceof AgentUiBffError && error.status === 204) {
      if (body.actionId === "messages.trash" && (body.values as { confirmationToken?: string } | undefined)?.confirmationToken === undefined) {
        return {
          status: "confirmation_required",
          result: null,
          confirmation: {
            confirmationId: "demo-cnf",
            action: "mettre à la corbeille",
            target: "message",
            impact: "Le message restera récupérable.",
            reversible: true,
            token: "demo-token",
          },
        };
      }
      return { status: "success", result: { kind: "ok" } };
    }
    throw error;
  }
}
