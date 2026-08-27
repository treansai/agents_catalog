import "server-only";

import { createDemoSnapshot, createDemoSyncResponse } from "@/lib/demo-data";
import type {
  AccountSummary,
  AnalysesPage,
  DashboardSnapshot,
  EmailAnalysis,
  EmailCategory,
  EmailPriority,
  MailProvider,
  RiskLevel,
  SyncRequest,
  SyncResponse,
} from "@/lib/ezer-types";

const REQUEST_TIMEOUT_MS = 10_000;
const SNAPSHOT_ANALYSIS_LIMIT = 100;

const PROVIDERS = ["gmail", "outlook"] as const;
const CATEGORIES = [
  "action_required",
  "informational",
  "newsletter",
  "receipt",
  "security",
  "spam",
  "other",
] as const;
const PRIORITIES = ["low", "normal", "high", "critical"] as const;
const RISK_LEVELS = ["none", "low", "medium", "high"] as const;

interface EzerConfig {
  apiUrl: URL;
  apiKey: string;
}

export class EzerApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "EzerApiError";
  }
}

function getEzerConfig(): EzerConfig | null {
  const apiUrl = process.env.EZER_API_URL?.trim();
  const apiKey = process.env.EZER_API_KEY?.trim();

  // An intentionally unconfigured frontend is the only situation that enables
  // demo mode. A configured backend that fails never falls back to demo data.
  if (!apiUrl || !apiKey) {
    return null;
  }

  let parsedUrl: URL;
  try {
    parsedUrl = new URL(apiUrl);
  } catch {
    throw new EzerApiError("Invalid Ezer API URL");
  }

  if (parsedUrl.protocol !== "http:" && parsedUrl.protocol !== "https:") {
    throw new EzerApiError("Invalid Ezer API URL protocol");
  }

  return { apiUrl: parsedUrl, apiKey };
}

async function requestJson<T>(
  config: EzerConfig,
  path: string,
  init: RequestInit,
  parse: (payload: unknown) => T,
): Promise<T> {
  const url = new URL(path, config.apiUrl);
  let response: Response;

  try {
    response = await fetch(url, {
      ...init,
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "X-API-Key": config.apiKey,
        ...init.headers,
      },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    throw new EzerApiError("Ezer API is unreachable");
  }

  if (!response.ok) {
    throw new EzerApiError("Ezer API request failed", response.status);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new EzerApiError("Ezer API returned invalid JSON", response.status);
  }

  try {
    return parse(payload);
  } catch {
    throw new EzerApiError("Ezer API returned an invalid payload", response.status);
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Expected an object");
  }
  return value as Record<string, unknown>;
}

function asArray(value: unknown, maximumLength: number): unknown[] {
  if (!Array.isArray(value) || value.length > maximumLength) {
    throw new TypeError("Expected a bounded array");
  }
  return value;
}

function asString(value: unknown, maximumLength: number): string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > maximumLength
  ) {
    throw new TypeError("Expected a bounded string");
  }
  return value;
}

function asNullableString(value: unknown, maximumLength: number): string | null {
  return value === null ? null : asString(value, maximumLength);
}

function asBoolean(value: unknown): boolean {
  if (typeof value !== "boolean") {
    throw new TypeError("Expected a boolean");
  }
  return value;
}

function asInteger(value: unknown, maximum = Number.MAX_SAFE_INTEGER): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < 0 ||
    value > maximum
  ) {
    throw new TypeError("Expected a non-negative integer");
  }
  return value;
}

function asProbability(value: unknown): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > 1
  ) {
    throw new TypeError("Expected a probability");
  }
  return value;
}

function asEnum<T extends string>(value: unknown, values: readonly T[]): T {
  if (typeof value !== "string" || !values.includes(value as T)) {
    throw new TypeError("Expected an enum member");
  }
  return value as T;
}

function asStringArray(
  value: unknown,
  maximumLength: number,
  maximumItemLength: number,
): string[] {
  return asArray(value, maximumLength).map((item) =>
    asString(item, maximumItemLength),
  );
}

function parseAccount(value: unknown): AccountSummary {
  const account = asRecord(value);
  const id = asString(account.id, 128);
  if (!/^[A-Za-z0-9._-]+$/.test(id)) {
    throw new TypeError("Invalid account identifier");
  }

  return {
    id,
    provider: asEnum<MailProvider>(account.provider, PROVIDERS),
  };
}

function parseAnalysis(value: unknown): EmailAnalysis {
  const analysis = asRecord(value);
  const safety = asRecord(analysis.safety);
  const triage = analysis.triage === null ? null : asRecord(analysis.triage);

  return {
    analysis_id: asString(analysis.analysis_id, 64),
    message_ref: asString(analysis.message_ref, 1_400),
    content_hash: asString(analysis.content_hash, 64),
    pipeline_version: asString(analysis.pipeline_version, 64),
    model_id: asString(analysis.model_id, 128),
    prompt_version: asString(analysis.prompt_version, 64),
    created_at: asString(analysis.created_at, 64),
    category: asEnum<EmailCategory>(analysis.category, CATEGORIES),
    priority: asEnum<EmailPriority>(analysis.priority, PRIORITIES),
    needs_human_review: asBoolean(analysis.needs_human_review),
    summary: asString(analysis.summary, 4_000),
    key_points: asStringArray(analysis.key_points, 12, 1_000),
    action_items: asArray(analysis.action_items, 20).map((item) => {
      const action = asRecord(item);
      return {
        description: asString(action.description, 1_200),
        owner: asNullableString(action.owner, 320),
        due_date: asNullableString(action.due_date, 64),
        confidence: asProbability(action.confidence),
      };
    }),
    safety: {
      risk_level: asEnum<RiskLevel>(safety.risk_level, RISK_LEVELS),
      prompt_injection_detected: asBoolean(safety.prompt_injection_detected),
      phishing_likelihood: asProbability(safety.phishing_likelihood),
      indicators: asStringArray(safety.indicators, 12, 240),
      rationale: asString(safety.rationale, 1_200),
      confidence: asProbability(safety.confidence),
    },
    triage:
      triage === null
        ? null
        : {
            category: asEnum<EmailCategory>(triage.category, CATEGORIES),
            priority: asEnum<EmailPriority>(triage.priority, PRIORITIES),
            needs_human_review: asBoolean(triage.needs_human_review),
            confidence: asProbability(triage.confidence),
            rationale: asString(triage.rationale, 1_200),
          },
    detected_language: asString(analysis.detected_language, 32),
  };
}

function parseAccountsResponse(payload: unknown): AccountSummary[] {
  const response = asRecord(payload);
  return asArray(response.accounts, 100).map(parseAccount);
}

function parseAnalysesPage(payload: unknown): AnalysesPage {
  const response = asRecord(payload);
  return {
    items: asArray(response.items, SNAPSHOT_ANALYSIS_LIMIT).map(parseAnalysis),
    total: asInteger(response.total),
    limit: asInteger(response.limit, SNAPSHOT_ANALYSIS_LIMIT),
    offset: asInteger(response.offset),
  };
}

function parseSyncResponse(payload: unknown): SyncResponse {
  const response = asRecord(payload);
  return {
    reports: asArray(response.reports, 100).map((value) => {
      const report = asRecord(value);
      return {
        account_id: asString(report.account_id, 128),
        provider: asEnum<MailProvider>(report.provider, PROVIDERS),
        fetched: asInteger(report.fetched, 500),
        processed: asInteger(report.processed, 500),
        skipped: asInteger(report.skipped, 500),
        failed: asInteger(report.failed, 500),
        dead_lettered: asInteger(report.dead_lettered, 500),
        cursor_advanced: asBoolean(report.cursor_advanced),
        cursor_reset: asBoolean(report.cursor_reset),
        analyses: asArray(report.analyses, 500).map(parseAnalysis),
      };
    }),
  };
}

function unavailableSnapshot(): DashboardSnapshot {
  return {
    mode: "live",
    health: "unavailable",
    accounts: [],
    analyses: [],
    total: 0,
    generatedAt: new Date().toISOString(),
  };
}

/**
 * Builds the server-side dashboard view without ever exposing EZER_API_KEY.
 * An unavailable configured backend yields an empty live snapshot for resilient
 * SSR; it deliberately never substitutes demo content.
 */
export async function getDashboardSnapshot(): Promise<DashboardSnapshot> {
  let config: EzerConfig | null;
  try {
    config = getEzerConfig();
  } catch {
    return unavailableSnapshot();
  }

  if (config === null) {
    return createDemoSnapshot();
  }

  try {
    const [accounts, page] = await Promise.all([
      requestJson(config, "/v1/accounts", { method: "GET" }, parseAccountsResponse),
      requestJson(
        config,
        `/v1/analyses?limit=${SNAPSHOT_ANALYSIS_LIMIT}`,
        { method: "GET" },
        parseAnalysesPage,
      ),
    ]);

    return {
      mode: "live",
      health: "ready",
      accounts,
      analyses: page.items,
      total: page.total,
      generatedAt: new Date().toISOString(),
    };
  } catch {
    return unavailableSnapshot();
  }
}

export async function syncMailboxes(request: SyncRequest): Promise<SyncResponse> {
  const config = getEzerConfig();
  if (config === null) {
    return createDemoSyncResponse(request);
  }

  return requestJson(
    config,
    "/v1/sync",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
    parseSyncResponse,
  );
}
