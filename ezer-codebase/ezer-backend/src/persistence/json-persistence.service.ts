import { randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { access, mkdir, open, readFile, rename, unlink } from "node:fs/promises";
import { dirname } from "node:path";

import { Injectable, type OnModuleInit } from "@nestjs/common";

import { AppConfigService } from "../config/app-config.service";
import { createDemoState } from "../demo/demo-data";
import {
  EMAIL_CATEGORIES,
  PRIORITIES,
  PROVIDERS,
  RISK_LEVELS,
  type AnalysisFilters,
  type EmailAnalysis,
  type PersistedState,
  type Provider
} from "../domain/models";

interface AnalysisPage {
  items: EmailAnalysis[];
  total: number;
}

interface SaveResult {
  inserted: EmailAnalysis[];
  skipped: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteProbability(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function assertAnalysis(value: unknown, index: number): asserts value is EmailAnalysis {
  if (!isRecord(value)) throw new Error(`analysis ${index} must be an object`);
  const requiredStrings = [
    "analysis_id",
    "message_ref",
    "content_hash",
    "pipeline_version",
    "model_id",
    "prompt_version",
    "created_at",
    "summary",
    "detected_language"
  ];
  if (requiredStrings.some((field) => typeof value[field] !== "string")) {
    throw new Error(`analysis ${index} has an invalid string field`);
  }
  if (!/^[a-f0-9]{64}$/.test(value.analysis_id as string)) {
    throw new Error(`analysis ${index} has an invalid id`);
  }
  if (!/^[a-f0-9]{64}$/.test(value.content_hash as string)) {
    throw new Error(`analysis ${index} has an invalid content hash`);
  }
  if (Number.isNaN(Date.parse(value.created_at as string))) {
    throw new Error(`analysis ${index} has an invalid date`);
  }
  if (!EMAIL_CATEGORIES.includes(value.category as (typeof EMAIL_CATEGORIES)[number])) {
    throw new Error(`analysis ${index} has an invalid category`);
  }
  if (!PRIORITIES.includes(value.priority as (typeof PRIORITIES)[number])) {
    throw new Error(`analysis ${index} has an invalid priority`);
  }
  if (typeof value.needs_human_review !== "boolean") {
    throw new Error(`analysis ${index} has an invalid review flag`);
  }
  if (!isStringArray(value.key_points) || !Array.isArray(value.action_items)) {
    throw new Error(`analysis ${index} has invalid extracted data`);
  }
  for (const action of value.action_items) {
    if (
      !isRecord(action) ||
      typeof action.description !== "string" ||
      (action.owner !== null && typeof action.owner !== "string") ||
      (action.due_date !== null && typeof action.due_date !== "string") ||
      !isFiniteProbability(action.confidence)
    ) {
      throw new Error(`analysis ${index} has an invalid action item`);
    }
  }
  const safety = value.safety;
  if (
    !isRecord(safety) ||
    !RISK_LEVELS.includes(safety.risk_level as (typeof RISK_LEVELS)[number]) ||
    typeof safety.prompt_injection_detected !== "boolean" ||
    !isFiniteProbability(safety.phishing_likelihood) ||
    !isStringArray(safety.indicators) ||
    typeof safety.rationale !== "string" ||
    !isFiniteProbability(safety.confidence)
  ) {
    throw new Error(`analysis ${index} has an invalid safety assessment`);
  }
  const triage = value.triage;
  if (
    !isRecord(triage) ||
    !EMAIL_CATEGORIES.includes(triage.category as (typeof EMAIL_CATEGORIES)[number]) ||
    !PRIORITIES.includes(triage.priority as (typeof PRIORITIES)[number]) ||
    typeof triage.needs_human_review !== "boolean" ||
    !isFiniteProbability(triage.confidence) ||
    typeof triage.rationale !== "string"
  ) {
    throw new Error(`analysis ${index} has invalid triage data`);
  }
}

function parseState(raw: string): PersistedState {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("the Ezer data file is not valid JSON");
  }
  if (!isRecord(value) || value.schema_version !== 1) {
    throw new Error("the Ezer data file has an unsupported schema");
  }
  if (!Array.isArray(value.accounts) || !Array.isArray(value.analyses) || !isRecord(value.cursors)) {
    throw new Error("the Ezer data file is incomplete");
  }
  const accountIds = new Set<string>();
  for (const [index, account] of value.accounts.entries()) {
    if (
      !isRecord(account) ||
      typeof account.id !== "string" ||
      !/^[A-Za-z0-9._-]{1,128}$/.test(account.id) ||
      typeof account.provider !== "string" ||
      !PROVIDERS.includes(account.provider as Provider) ||
      (account.mailbox !== undefined &&
        (typeof account.mailbox !== "string" || account.mailbox.length > 320)) ||
      accountIds.has(account.id)
    ) {
      throw new Error(`the Ezer data file has an invalid account at index ${index}`);
    }
    accountIds.add(account.id);
  }
  const analysisIds = new Set<string>();
  for (const [index, analysis] of value.analyses.entries()) {
    assertAnalysis(analysis, index);
    if (analysisIds.has(analysis.analysis_id)) {
      throw new Error(`the Ezer data file has a duplicate analysis at index ${index}`);
    }
    analysisIds.add(analysis.analysis_id);
  }
  for (const [accountId, cursor] of Object.entries(value.cursors)) {
    if (!/^[A-Za-z0-9._-]{1,128}$/.test(accountId) || (cursor !== null && typeof cursor !== "string")) {
      throw new Error("the Ezer data file has an invalid cursor");
    }
  }
  return value as unknown as PersistedState;
}

function accountIdFromAnalysis(analysis: EmailAnalysis): string | undefined {
  return analysis.message_ref.split(":", 3)[1];
}

@Injectable()
export class JsonPersistenceService implements OnModuleInit {
  private state: PersistedState | undefined;
  private queue: Promise<void> = Promise.resolve();

  constructor(private readonly config: AppConfigService) {}

  async onModuleInit(): Promise<void> {
    await this.serialized(async () => {
      await mkdir(dirname(this.config.dataFile), { recursive: true, mode: 0o700 });
      if (this.config.mode === "demo" && this.config.demoResetOnStart) {
        const initial = createDemoState();
        await this.writeAtomically(initial);
        this.state = initial;
        return;
      }

      try {
        const persisted = parseState(await readFile(this.config.dataFile, "utf8"));
        this.state = persisted;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
        const initial: PersistedState =
          this.config.mode === "demo"
            ? createDemoState()
            : {
                schema_version: 1,
                accounts: structuredClone(this.config.accounts),
                analyses: [],
                cursors: Object.fromEntries(this.config.accounts.map((account) => [account.id, null]))
              };
        await this.writeAtomically(initial);
        this.state = initial;
      }

      const current = this.requireState();
      if (JSON.stringify(current.accounts) !== JSON.stringify(this.config.accounts)) {
        const next = structuredClone(current);
        next.accounts = structuredClone(this.config.accounts);
        for (const account of this.config.accounts) next.cursors[account.id] ??= null;
        await this.writeAtomically(next);
        this.state = next;
      }
    });
  }

  async health(): Promise<boolean> {
    return this.serialized(async () => {
      if (this.state === undefined) return false;
      try {
        await access(this.config.dataFile, constants.R_OK | constants.W_OK);
        return true;
      } catch {
        return false;
      }
    });
  }

  async listAccounts(): Promise<PersistedState["accounts"]> {
    return this.serialized(async () => structuredClone(this.requireState().accounts));
  }

  async listAnalyses(
    limit: number,
    offset: number,
    filters: AnalysisFilters
  ): Promise<AnalysisPage> {
    return this.serialized(async () => {
      const configuredIds = new Set(this.requireState().accounts.map((account) => account.id));
      const matching = this.requireState()
        .analyses.filter((analysis) => {
          const accountId = accountIdFromAnalysis(analysis);
          if (accountId === undefined || !configuredIds.has(accountId)) return false;
          if (filters.accountId !== undefined && accountId !== filters.accountId) return false;
          if (filters.category !== undefined && analysis.category !== filters.category) return false;
          if (filters.priority !== undefined && analysis.priority !== filters.priority) return false;
          if (
            filters.needsHumanReview !== undefined &&
            analysis.needs_human_review !== filters.needsHumanReview
          ) {
            return false;
          }
          return true;
        })
        .sort((left, right) => {
          const byCreation = right.created_at.localeCompare(left.created_at);
          return byCreation !== 0 ? byCreation : right.analysis_id.localeCompare(left.analysis_id);
        });
      return {
        items: structuredClone(matching.slice(offset, offset + limit)),
        total: matching.length
      };
    });
  }

  async getAnalysis(analysisId: string): Promise<EmailAnalysis | undefined> {
    return this.serialized(async () => {
      const analysis = this.requireState().analyses.find((candidate) => candidate.analysis_id === analysisId);
      if (analysis === undefined) return undefined;
      const accountId = accountIdFromAnalysis(analysis);
      if (!this.requireState().accounts.some((account) => account.id === accountId)) return undefined;
      return structuredClone(analysis);
    });
  }

  async saveAnalyses(analyses: EmailAnalysis[]): Promise<SaveResult> {
    return this.serialized(async () => {
      const current = this.requireState();
      const known = new Set(current.analyses.map((analysis) => analysis.analysis_id));
      const inserted = analyses.filter((analysis) => {
        if (known.has(analysis.analysis_id)) return false;
        known.add(analysis.analysis_id);
        return true;
      });
      if (inserted.length === 0) return { inserted: [], skipped: analyses.length };
      const next = structuredClone(current);
      next.analyses = [...structuredClone(inserted), ...next.analyses];
      await this.writeAtomically(next);
      this.state = next;
      return { inserted: structuredClone(inserted), skipped: analyses.length - inserted.length };
    });
  }

  async getCursor(accountId: string): Promise<string | null> {
    return this.serialized(async () => this.requireState().cursors[accountId] ?? null);
  }

  async setCursor(accountId: string, expected: string | null, nextCursor: string | null): Promise<boolean> {
    return this.serialized(async () => {
      const current = this.requireState();
      const actual = current.cursors[accountId] ?? null;
      if (actual !== expected || actual === nextCursor) return false;
      const next = structuredClone(current);
      next.cursors[accountId] = nextCursor;
      await this.writeAtomically(next);
      this.state = next;
      return true;
    });
  }

  private requireState(): PersistedState {
    if (this.state === undefined) throw new Error("persistence is not initialized");
    return this.state;
  }

  private serialized<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.queue.then(operation, operation);
    this.queue = result.then(
      () => undefined,
      () => undefined
    );
    return result;
  }

  private async writeAtomically(state: PersistedState): Promise<void> {
    const target = this.config.dataFile;
    const temporary = `${target}.${process.pid}.${randomUUID()}.tmp`;
    let handle: Awaited<ReturnType<typeof open>> | undefined;
    try {
      handle = await open(temporary, "wx", 0o600);
      await handle.writeFile(`${JSON.stringify(state, null, 2)}\n`, "utf8");
      await handle.sync();
      await handle.close();
      handle = undefined;
      await rename(temporary, target);
      const directoryHandle = await open(dirname(target), "r");
      try {
        await directoryHandle.sync();
      } finally {
        await directoryHandle.close();
      }
    } catch (error) {
      await handle?.close().catch(() => undefined);
      await unlink(temporary).catch(() => undefined);
      throw error;
    }
  }
}
