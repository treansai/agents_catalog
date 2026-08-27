import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

import { Injectable } from "@nestjs/common";

const TTL_MS = 10 * 60_000;

export interface ConfirmationRecord {
  id: string;
  token: string;
  workspaceId: string;
  actionId: string;
  targetId: string;
  targetLabel: string;
  impact: string;
  reversible: boolean;
  expiresAt: number;
}

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
}

@Injectable()
export class ConfirmationStore {
  private readonly records = new Map<string, ConfirmationRecord>();

  issue(input: Omit<ConfirmationRecord, "id" | "token" | "expiresAt">): ConfirmationRecord {
    this.gc();
    const record: ConfirmationRecord = {
      ...input,
      id: `cnf_${randomBytes(12).toString("hex")}`,
      token: randomBytes(24).toString("base64url"),
      expiresAt: Date.now() + TTL_MS
    };
    this.records.set(record.id, record);
    return record;
  }

  consume(id: string, token: string, workspaceId: string, actionId: string): ConfirmationRecord | undefined {
    this.gc();
    const record = this.records.get(id);
    if (record === undefined) return undefined;
    if (record.workspaceId !== workspaceId || record.actionId !== actionId) return undefined;
    if (record.expiresAt < Date.now()) {
      this.records.delete(id);
      return undefined;
    }
    if (!timingSafeEqual(digest(record.token), digest(token))) return undefined;
    this.records.delete(id);
    return record;
  }

  peek(id: string, workspaceId: string): ConfirmationRecord | undefined {
    const record = this.records.get(id);
    if (record === undefined || record.workspaceId !== workspaceId) return undefined;
    return record;
  }

  private gc(): void {
    const now = Date.now();
    for (const [id, record] of this.records) {
      if (record.expiresAt < now) this.records.delete(id);
    }
  }
}
