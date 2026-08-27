import type { FetchBatch, Provider } from "../domain/models";

export interface MailConnector {
  readonly accountId: string;
  readonly provider: Provider;

  fetch(cursor: string | null, limit: number): Promise<FetchBatch>;
}
