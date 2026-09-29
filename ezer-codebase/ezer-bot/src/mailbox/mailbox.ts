const MESSAGE_ID = /^[A-Za-z0-9_\-=+/]{1,512}$/;
const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const MAX_TOP = 25;
const MAX_QUERY_CHARS = 200;
const TIMEOUT_MS = 30_000;

export class MailboxUnavailableError extends Error {
  constructor(
    readonly operation: string,
    readonly code: string
  ) {
    super(`${operation} failed: ${code}`);
    this.name = "MailboxUnavailableError";
  }
}

export interface MessageHeader {
  message_id: string;
  subject: string;
  sender_name: string;
  sender_address: string;
  received_at: string;
  is_read: boolean;
  has_attachments: boolean;
  snippet: string;
}

export interface MessageBody {
  header: MessageHeader;
  body_text: string;
}

export interface SenderTally {
  sender_address: string;
  sender_name: string;
  total: number;
  unread: number;
}

export interface TriagedAnalysis {
  summary: string;
  category: string;
  priority: string;
  needs_human_review: boolean;
  created_at: string;
}

export interface MailboxStats {
  mailbox: string;
  total_messages: number;
  unread_messages: number;
  folders: [string, number, number][];
}

export interface MailboxClient {
  listRecent(accountId: string, top: number): Promise<MessageHeader[]>;
  listMessages(
    accountId: string,
    options: {
      top: number;
      unreadOnly?: boolean;
      fromAddress?: string;
      since?: string;
      until?: string;
      order?: string;
    }
  ): Promise<MessageHeader[]>;
  senders(accountId: string, sample: number): Promise<SenderTally[]>;
  analyses(
    accountId: string,
    options: {
      limit: number;
      category?: string;
      priority?: string;
      needsHumanReview?: boolean;
    }
  ): Promise<{ analyses: TriagedAnalysis[]; total: number }>;
  search(accountId: string, query: string, top: number): Promise<MessageHeader[]>;
  getMessage(accountId: string, messageId: string): Promise<MessageBody>;
  stats(accountId: string): Promise<MailboxStats>;
  moveToTrash(accountId: string, messageId: string): Promise<void>;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function text(payload: Record<string, unknown>, field: string, maximum: number): string {
  const value = payload[field];
  return typeof value === "string" ? value.slice(0, maximum) : "";
}

function headerFrom(payload: unknown): MessageHeader | undefined {
  const record = asRecord(payload);
  if (record === undefined) return undefined;
  const messageId = record.message_id;
  if (typeof messageId !== "string" || MESSAGE_ID.exec(messageId) === null) return undefined;
  return {
    message_id: messageId,
    subject: text(record, "subject", 400),
    sender_name: text(record, "sender_name", 320),
    sender_address: text(record, "sender_address", 320),
    received_at: text(record, "received_at", 64),
    is_read: record.is_read === true,
    has_attachments: record.has_attachments === true,
    snippet: text(record, "snippet", 600)
  };
}

export class HttpMailboxClient implements MailboxClient {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly fetchImpl: typeof fetch;

  constructor(baseUrl: string, apiKey: string, fetchImpl: typeof fetch = fetch) {
    const normalized = baseUrl.trim();
    if (normalized === "") throw new Error("backend base URL must not be empty");
    const withSlash = normalized.endsWith("/") ? normalized : `${normalized}/`;
    let parsed: URL;
    try {
      parsed = new URL(withSlash);
    } catch {
      throw new Error("backend base URL must be an absolute http(s) URL");
    }
    if ((parsed.protocol !== "http:" && parsed.protocol !== "https:") || parsed.hostname === "") {
      throw new Error("backend base URL must be an absolute http(s) URL");
    }
    if (apiKey.trim() === "") throw new Error("backend API key must not be empty");
    this.baseUrl = withSlash;
    this.apiKey = apiKey;
    this.fetchImpl = fetchImpl;
  }

  async listRecent(accountId: string, top: number): Promise<MessageHeader[]> {
    return this.listMessages(accountId, { top });
  }

  async listMessages(
    accountId: string,
    options: {
      top: number;
      unreadOnly?: boolean;
      fromAddress?: string;
      since?: string;
      until?: string;
      order?: string;
    }
  ): Promise<MessageHeader[]> {
    const params: Record<string, string> = {
      top: String(this.top(options.top)),
      order: options.order === "asc" ? "asc" : "desc"
    };
    if (options.unreadOnly === true) params.unread_only = "true";
    if (options.fromAddress) params.from_address = options.fromAddress.trim().slice(0, 320);
    if (options.since) params.since = options.since.trim().slice(0, 64);
    if (options.until) params.until = options.until.trim().slice(0, 64);
    const payload = await this.request("GET", `v1/accounts/${this.account(accountId)}/messages`, {
      operation: "list_messages",
      params
    });
    return this.headersFrom(payload);
  }

  async senders(accountId: string, sample: number): Promise<SenderTally[]> {
    const payload = await this.request("GET", `v1/accounts/${this.account(accountId)}/senders`, {
      operation: "senders",
      params: { sample: String(this.top(sample)) }
    });
    const entries = payload.senders;
    if (!Array.isArray(entries) || entries.length > MAX_TOP) {
      throw new MailboxUnavailableError("senders", "invalid_backend_payload");
    }
    const tallies: SenderTally[] = [];
    for (const entry of entries) {
      const record = asRecord(entry);
      if (record === undefined) continue;
      const total = record.total;
      const unread = record.unread;
      tallies.push({
        sender_address: text(record, "sender_address", 320),
        sender_name: text(record, "sender_name", 320),
        total: typeof total === "number" && Number.isInteger(total) ? total : 0,
        unread: typeof unread === "number" && Number.isInteger(unread) ? unread : 0
      });
    }
    return tallies;
  }

  async analyses(
    accountId: string,
    options: {
      limit: number;
      category?: string;
      priority?: string;
      needsHumanReview?: boolean;
    }
  ): Promise<{ analyses: TriagedAnalysis[]; total: number }> {
    const params: Record<string, string> = {
      account_id: this.account(accountId),
      limit: String(Math.max(1, Math.min(options.limit, 100))),
      offset: "0"
    };
    if (options.category) params.category = options.category;
    if (options.priority) params.priority = options.priority;
    if (options.needsHumanReview !== undefined) {
      params.needs_human_review = options.needsHumanReview ? "true" : "false";
    }
    const payload = await this.request("GET", "v1/analyses", { operation: "analyses", params });
    const items = payload.items;
    if (!Array.isArray(items) || items.length > 100) {
      throw new MailboxUnavailableError("analyses", "invalid_backend_payload");
    }
    const analyses = items.flatMap((item) => {
      const record = asRecord(item);
      if (record === undefined) return [];
      return [
        {
          summary: text(record, "summary", 2_000),
          category: text(record, "category", 64),
          priority: text(record, "priority", 32),
          needs_human_review: record.needs_human_review === true,
          created_at: text(record, "created_at", 64)
        }
      ];
    });
    const total = payload.total;
    return {
      analyses,
      total: typeof total === "number" && Number.isInteger(total) ? total : analyses.length
    };
  }

  async search(accountId: string, query: string, top: number): Promise<MessageHeader[]> {
    const trimmed = query.trim().slice(0, MAX_QUERY_CHARS);
    if (trimmed === "") return [];
    const payload = await this.request("GET", `v1/accounts/${this.account(accountId)}/messages`, {
      operation: "search",
      params: { top: String(this.top(top)), query: trimmed }
    });
    return this.headersFrom(payload);
  }

  async getMessage(accountId: string, messageId: string): Promise<MessageBody> {
    const payload = await this.request("GET", `v1/accounts/${this.account(accountId)}/message`, {
      operation: "get_message",
      params: { message_id: this.message(messageId) }
    });
    const header = headerFrom(payload);
    if (header === undefined) {
      throw new MailboxUnavailableError("get_message", "invalid_backend_payload");
    }
    return { header, body_text: text(payload, "body_text", 20_000) };
  }

  async stats(accountId: string): Promise<MailboxStats> {
    const payload = await this.request("GET", `v1/accounts/${this.account(accountId)}/mailbox-stats`, {
      operation: "stats"
    });
    const foldersPayload = payload.folders;
    const folders: [string, number, number][] = [];
    if (Array.isArray(foldersPayload)) {
      for (const folder of foldersPayload.slice(0, 20)) {
        const record = asRecord(folder);
        if (record === undefined) continue;
        const name = record.name;
        const total = record.total;
        const unread = record.unread;
        if (typeof name === "string" && typeof total === "number" && typeof unread === "number") {
          folders.push([name.slice(0, 120), total, unread]);
        }
      }
    }
    const totalMessages = payload.total_messages;
    const unreadMessages = payload.unread_messages;
    return {
      mailbox: text(payload, "mailbox", 320),
      total_messages: typeof totalMessages === "number" && Number.isInteger(totalMessages) ? totalMessages : 0,
      unread_messages: typeof unreadMessages === "number" && Number.isInteger(unreadMessages) ? unreadMessages : 0,
      folders
    };
  }

  async moveToTrash(accountId: string, messageId: string): Promise<void> {
    await this.request("POST", `v1/accounts/${this.account(accountId)}/message/trash`, {
      operation: "move_to_trash",
      json: { message_id: this.message(messageId) }
    });
  }

  private account(accountId: string): string {
    if (ACCOUNT_ID.exec(accountId) === null) {
      throw new MailboxUnavailableError("request", "invalid_account_id");
    }
    return accountId;
  }

  private message(messageId: string): string {
    if (MESSAGE_ID.exec(messageId) === null) {
      throw new MailboxUnavailableError("request", "invalid_message_id");
    }
    return messageId;
  }

  private top(top: number): number {
    return Math.max(1, Math.min(Math.trunc(top), MAX_TOP));
  }

  private headersFrom(payload: Record<string, unknown>): MessageHeader[] {
    const messages = payload.messages;
    if (!Array.isArray(messages) || messages.length > MAX_TOP) {
      throw new MailboxUnavailableError("list", "invalid_backend_payload");
    }
    return messages.flatMap((entry) => {
      const header = headerFrom(entry);
      return header === undefined ? [] : [header];
    });
  }

  private async request(
    method: string,
    path: string,
    options: { operation: string; params?: Record<string, string>; json?: Record<string, unknown> }
  ): Promise<Record<string, unknown>> {
    const url = new URL(path, this.baseUrl);
    if (options.params !== undefined) {
      for (const [key, value] of Object.entries(options.params)) {
        url.searchParams.set(key, value);
      }
    }
    let response: Response;
    try {
      response = await this.fetchImpl(url, {
        method,
        headers: {
          Accept: "application/json",
          "X-API-Key": this.apiKey,
          ...(options.json === undefined ? {} : { "Content-Type": "application/json" })
        },
        body: options.json === undefined ? undefined : JSON.stringify(options.json),
        signal: AbortSignal.timeout(TIMEOUT_MS),
        redirect: "manual"
      });
    } catch {
      throw new MailboxUnavailableError(options.operation, "backend_unreachable");
    }
    if (response.status === 401) {
      throw new MailboxUnavailableError(options.operation, "backend_unauthorized");
    }
    if (response.status === 404) {
      throw new MailboxUnavailableError(options.operation, "account_not_found");
    }
    if (response.status === 422) {
      throw new MailboxUnavailableError(options.operation, "mailbox_not_available");
    }
    if (response.status >= 400) {
      throw new MailboxUnavailableError(options.operation, "backend_request_failed");
    }
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new MailboxUnavailableError(options.operation, "invalid_backend_payload");
    }
    const record = asRecord(payload);
    if (record === undefined) {
      throw new MailboxUnavailableError(options.operation, "invalid_backend_payload");
    }
    return record;
  }
}
