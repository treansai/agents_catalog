import { Injectable } from "@nestjs/common";

const TTL_MS = 15 * 60_000;
const MAX_ENTRIES = 512;

interface CacheEntry {
  value: unknown;
  expiresAt: number;
  workspaceId: string;
}

@Injectable()
export class ResolverCache {
  private readonly entries = new Map<string, CacheEntry>();

  get(key: string, workspaceId: string): unknown | undefined {
    const entry = this.entries.get(key);
    if (entry === undefined) return undefined;
    if (entry.workspaceId !== workspaceId || entry.expiresAt < Date.now()) {
      this.entries.delete(key);
      return undefined;
    }
    return entry.value;
  }

  set(key: string, workspaceId: string, value: unknown, ttlMs = TTL_MS): void {
    if (this.entries.size >= MAX_ENTRIES) {
      const first = this.entries.keys().next().value;
      if (first !== undefined) this.entries.delete(first);
    }
    this.entries.set(key, { value, workspaceId, expiresAt: Date.now() + ttlMs });
  }
}

@Injectable()
export class IdempotencyStore {
  private readonly entries = new Map<string, { result: unknown; expiresAt: number }>();

  async remember<T>(key: string, produce: () => Promise<T>): Promise<T> {
    const existing = this.entries.get(key);
    if (existing !== undefined && existing.expiresAt >= Date.now()) {
      if (existing.result instanceof Error) throw existing.result;
      return existing.result as T;
    }
    try {
      const result = await produce();
      this.entries.set(key, { result, expiresAt: Date.now() + 5 * 60_000 });
      return result;
    } catch (error) {
      if (error instanceof Error && error.name === "ConfirmationRequiredError") {
        this.entries.set(key, { result: error, expiresAt: Date.now() + 5 * 60_000 });
      }
      throw error;
    }
  }
}
