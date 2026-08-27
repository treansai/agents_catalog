import { randomUUID } from "node:crypto";
import { mkdir, open, readFile, rename, unlink } from "node:fs/promises";
import { dirname } from "node:path";

import { Injectable } from "@nestjs/common";

import { AppConfigService } from "../config/app-config.service";

/** Jetons délégués d'un compte. Ils ne transitent jamais par l'API publique. */
export interface StoredToken {
  account_id: string;
  mailbox: string;
  refresh_token: string;
  connected_at: string;
  /** Portées réellement accordées. Absente pour un jeton écrit avant leur suivi. */
  scopes?: string;
}

interface TokenFile {
  schema_version: 1;
  tokens: StoredToken[];
}

const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const MAX_TOKEN_FILE_BYTES = 1024 * 1024;

function parseTokenFile(raw: string): TokenFile {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("the Ezer token file is not valid JSON");
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("the Ezer token file is invalid");
  }
  const candidate = value as Record<string, unknown>;
  if (candidate.schema_version !== 1 || !Array.isArray(candidate.tokens)) {
    throw new Error("the Ezer token file has an unsupported schema");
  }
  const tokens: StoredToken[] = [];
  const seen = new Set<string>();
  for (const entry of candidate.tokens) {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      throw new Error("the Ezer token file has an invalid entry");
    }
    const token = entry as Record<string, unknown>;
    if (
      typeof token.account_id !== "string" ||
      !ACCOUNT_ID.test(token.account_id) ||
      typeof token.mailbox !== "string" ||
      token.mailbox.length > 320 ||
      typeof token.refresh_token !== "string" ||
      token.refresh_token.length === 0 ||
      token.refresh_token.length > 32_768 ||
      typeof token.connected_at !== "string" ||
      Number.isNaN(Date.parse(token.connected_at)) ||
      (token.scopes !== undefined &&
        (typeof token.scopes !== "string" || token.scopes.length > 2_048)) ||
      seen.has(token.account_id)
    ) {
      throw new Error("the Ezer token file has an invalid entry");
    }
    seen.add(token.account_id);
    tokens.push({
      account_id: token.account_id,
      mailbox: token.mailbox,
      refresh_token: token.refresh_token,
      connected_at: token.connected_at,
      ...(typeof token.scopes === "string" ? { scopes: token.scopes } : {})
    });
  }
  return { schema_version: 1, tokens };
}

/**
 * Conserve les refresh tokens Microsoft dans un fichier dédié, distinct du fichier de données du
 * tableau de bord, écrit atomiquement et lisible par le seul processus backend.
 */
@Injectable()
export class TokenStoreService {
  private cache: TokenFile | undefined;
  private queue: Promise<void> = Promise.resolve();

  constructor(private readonly config: AppConfigService) {}

  async get(accountId: string): Promise<StoredToken | undefined> {
    return this.serialized(async () => {
      const file = await this.load();
      const token = file.tokens.find((entry) => entry.account_id === accountId);
      return token === undefined ? undefined : { ...token };
    });
  }

  async save(token: StoredToken): Promise<void> {
    await this.serialized(async () => {
      const file = await this.load();
      const tokens = file.tokens.filter((entry) => entry.account_id !== token.account_id);
      tokens.push({ ...token });
      await this.write({ schema_version: 1, tokens });
    });
  }

  /** Remplace le seul refresh token d'un compte lors d'une rotation, sans toucher aux autres. */
  async rotate(accountId: string, refreshToken: string): Promise<void> {
    await this.serialized(async () => {
      const file = await this.load();
      const existing = file.tokens.find((entry) => entry.account_id === accountId);
      if (existing === undefined) return;
      const tokens = file.tokens.map((entry) =>
        entry.account_id === accountId ? { ...entry, refresh_token: refreshToken } : entry
      );
      await this.write({ schema_version: 1, tokens });
    });
  }

  async remove(accountId: string): Promise<boolean> {
    return this.serialized(async () => {
      const file = await this.load();
      const tokens = file.tokens.filter((entry) => entry.account_id !== accountId);
      if (tokens.length === file.tokens.length) return false;
      await this.write({ schema_version: 1, tokens });
      return true;
    });
  }

  private async load(): Promise<TokenFile> {
    if (this.cache !== undefined) return this.cache;
    try {
      const raw = await readFile(this.config.tokenFile, "utf8");
      if (raw.length > MAX_TOKEN_FILE_BYTES) throw new Error("the Ezer token file is too large");
      this.cache = parseTokenFile(raw);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      this.cache = { schema_version: 1, tokens: [] };
    }
    return this.cache;
  }

  private async write(file: TokenFile): Promise<void> {
    const target = this.config.tokenFile;
    await mkdir(dirname(target), { recursive: true, mode: 0o700 });
    const temporary = `${target}.${process.pid}.${randomUUID()}.tmp`;
    let handle: Awaited<ReturnType<typeof open>> | undefined;
    try {
      handle = await open(temporary, "wx", 0o600);
      await handle.writeFile(`${JSON.stringify(file, null, 2)}\n`, "utf8");
      await handle.sync();
      await handle.close();
      handle = undefined;
      await rename(temporary, target);
    } catch (error) {
      await handle?.close().catch(() => undefined);
      await unlink(temporary).catch(() => undefined);
      throw error;
    }
    this.cache = file;
  }

  private serialized<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.queue.then(operation, operation);
    this.queue = result.then(
      () => undefined,
      () => undefined
    );
    return result;
  }
}
