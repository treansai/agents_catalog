import type { Account, FetchBatch, MailMessage } from "../domain/models";
import type { MailConnector } from "./mail-connector";

type MessageLoader = () => Promise<MailMessage[]>;

export class CatalogConnector implements MailConnector {
  readonly accountId: string;
  readonly provider: Account["provider"];

  constructor(
    account: Account,
    private readonly loadMessages: MessageLoader
  ) {
    this.accountId = account.id;
    this.provider = account.provider;
  }

  async fetch(cursor: string | null, limit: number): Promise<FetchBatch> {
    const available = (await this.loadMessages()).filter(
      (message) => message.account_id === this.accountId && message.provider === this.provider
    );
    let start = 0;
    let cursorReset = false;
    if (cursor !== null) {
      if (/^\d+$/.test(cursor) && Number(cursor) <= available.length) {
        start = Number(cursor);
      } else {
        cursorReset = true;
      }
    }
    const end = Math.min(start + limit, available.length);
    return {
      messages: structuredClone(available.slice(start, end)),
      next_cursor: String(end),
      cursor_reset: cursorReset
    };
  }
}
