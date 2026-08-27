import { readFile } from "node:fs/promises";

import { Inject, Injectable } from "@nestjs/common";

import { AppConfigService } from "../config/app-config.service";
import { DEMO_MESSAGES } from "../demo/demo-data";
import { PROVIDERS, type Account, type MailMessage, type Provider } from "../domain/models";
import { GraphMailConnector } from "../mail/graph.connector";
import { HTTP_FETCH, OutlookAuthService, type HttpFetch } from "../mail/outlook-auth.service";
import { TokenStoreService } from "../mail/token-store.service";
import { CatalogConnector } from "./catalog.connector";
import type { MailConnector } from "./mail-connector";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function optionalString(
  value: Record<string, unknown>,
  field: string,
  maximum: number,
  fallback = ""
): string {
  const candidate = value[field];
  if (candidate === undefined) return fallback;
  if (typeof candidate !== "string" || candidate.length > maximum) {
    throw new Error(`message field ${field} is invalid`);
  }
  return candidate;
}

function parseMessage(value: unknown, index: number): MailMessage {
  if (!isRecord(value)) throw new Error(`source message ${index} must be an object`);
  const allowed = new Set([
    "account_id",
    "provider",
    "provider_message_id",
    "thread_id",
    "subject",
    "sender_name",
    "sender_address",
    "received_at",
    "body_text",
    "snippet"
  ]);
  if (Object.keys(value).some((key) => !allowed.has(key))) {
    throw new Error(`source message ${index} has unknown fields`);
  }
  if (typeof value.account_id !== "string" || !/^[A-Za-z0-9._-]{1,128}$/.test(value.account_id)) {
    throw new Error(`source message ${index} has an invalid account_id`);
  }
  if (typeof value.provider !== "string" || !PROVIDERS.includes(value.provider as Provider)) {
    throw new Error(`source message ${index} has an invalid provider`);
  }
  if (
    typeof value.provider_message_id !== "string" ||
    value.provider_message_id.length < 1 ||
    value.provider_message_id.length > 1024
  ) {
    throw new Error(`source message ${index} has an invalid provider_message_id`);
  }
  if (typeof value.received_at !== "string" || Number.isNaN(Date.parse(value.received_at))) {
    throw new Error(`source message ${index} has an invalid received_at`);
  }
  const thread = value.thread_id;
  if (thread !== undefined && thread !== null && (typeof thread !== "string" || thread.length > 1024)) {
    throw new Error(`source message ${index} has an invalid thread_id`);
  }
  return {
    account_id: value.account_id,
    provider: value.provider as Provider,
    provider_message_id: value.provider_message_id,
    thread_id: thread as string | null | undefined,
    subject: optionalString(value, "subject", 998),
    sender_name: optionalString(value, "sender_name", 320),
    sender_address: optionalString(value, "sender_address", 320),
    received_at: new Date(value.received_at).toISOString(),
    body_text: optionalString(value, "body_text", 500_000),
    snippet: optionalString(value, "snippet", 2_000)
  };
}

@Injectable()
export class ConnectorRegistryService {
  constructor(
    private readonly config: AppConfigService,
    private readonly tokens: TokenStoreService,
    private readonly outlookAuth: OutlookAuthService,
    @Inject(HTTP_FETCH) private readonly httpFetch: HttpFetch
  ) {}

  /**
   * Un compte Outlook réellement connecté est lu depuis Microsoft Graph. Les autres comptes
   * conservent le connecteur catalogue, qui alimente la démonstration et les tests.
   */
  async connectorFor(account: Account): Promise<MailConnector> {
    if (account.provider === "outlook" && account.mailbox !== undefined) {
      const stored = await this.tokens.get(account.id);
      if (stored !== undefined) {
        return new GraphMailConnector(
          account,
          () => this.outlookAuth.accessTokenFor(account),
          this.httpFetch
        );
      }
    }
    return new CatalogConnector(account, () => this.loadCatalog());
  }

  private async loadCatalog(): Promise<MailMessage[]> {
    if (this.config.mode === "demo") return structuredClone(DEMO_MESSAGES);
    if (this.config.sourceFile === undefined) return [];
    let parsed: unknown;
    try {
      parsed = JSON.parse(await readFile(this.config.sourceFile, "utf8"));
    } catch (error) {
      if (error instanceof SyntaxError) throw new Error("EZER_SOURCE_FILE is not valid JSON");
      throw error;
    }
    const values = isRecord(parsed) ? parsed.messages : parsed;
    if (!Array.isArray(values) || values.length > 10_000) {
      throw new Error("EZER_SOURCE_FILE must contain at most 10000 messages");
    }
    return values.map(parseMessage);
  }
}
