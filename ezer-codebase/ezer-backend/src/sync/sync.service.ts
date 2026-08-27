import { Injectable, UnprocessableEntityException } from "@nestjs/common";

import { analyseMessage } from "../analysis/analyse-message";
import { AppConfigService } from "../config/app-config.service";
import type { Account, SyncReport } from "../domain/models";
import { JsonPersistenceService } from "../persistence/json-persistence.service";
import { ConnectorRegistryService } from "./connector-registry.service";

@Injectable()
export class SyncService {
  constructor(
    private readonly config: AppConfigService,
    private readonly persistence: JsonPersistenceService,
    private readonly connectors: ConnectorRegistryService
  ) {}

  async sync(accountIds?: string[], requestedLimit?: number): Promise<SyncReport[]> {
    const limit = requestedLimit ?? this.config.syncDefaultLimit;
    if (!Number.isInteger(limit) || limit < 1 || limit > this.config.syncMaxLimit) {
      throw new UnprocessableEntityException();
    }
    const accounts = await this.selectAccounts(accountIds);
    return Promise.all(accounts.map((account) => this.syncAccount(account, limit)));
  }

  private async selectAccounts(accountIds?: string[]): Promise<Account[]> {
    const accounts = await this.persistence.listAccounts();
    if (accountIds === undefined) return accounts;
    const byId = new Map(accounts.map((account) => [account.id, account]));
    const selected: Account[] = [];
    for (const accountId of accountIds) {
      const account = byId.get(accountId);
      if (account === undefined) throw new UnprocessableEntityException();
      selected.push(account);
    }
    return selected;
  }

  private async syncAccount(account: Account, limit: number): Promise<SyncReport> {
    const connector = await this.connectors.connectorFor(account);
    const cursor = await this.persistence.getCursor(account.id);
    const batch = await connector.fetch(cursor, limit);
    const analyses = [];
    let failed = 0;
    for (const message of batch.messages) {
      if (message.account_id !== account.id || message.provider !== account.provider) {
        failed += 1;
        continue;
      }
      try {
        analyses.push(analyseMessage(message));
      } catch {
        failed += 1;
      }
    }
    const saved = await this.persistence.saveAnalyses(analyses);
    const cursorAdvanced =
      failed === 0
        ? await this.persistence.setCursor(account.id, cursor, batch.next_cursor)
        : false;
    return {
      account_id: account.id,
      provider: account.provider,
      fetched: batch.messages.length,
      processed: saved.inserted.length,
      skipped: saved.skipped,
      failed,
      dead_lettered: 0,
      cursor_advanced: cursorAdvanced,
      cursor_reset: batch.cursor_reset,
      analyses: saved.inserted
    };
  }
}
