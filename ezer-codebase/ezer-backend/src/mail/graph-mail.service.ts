import { Inject, Injectable } from "@nestjs/common";

import type { Account } from "../domain/models";
import {
  ConnectionError,
  HTTP_FETCH,
  OutlookAuthService,
  grantsWriteAccess,
  type HttpFetch
} from "./outlook-auth.service";
import { TokenStoreService } from "./token-store.service";

const GRAPH_ORIGIN = "https://graph.microsoft.com";
const REQUEST_TIMEOUT_MS = 20_000;
const MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
const MAX_PAGE_SIZE = 25;
const MAX_SUBJECT_CHARS = 400;
const MAX_ADDRESS_CHARS = 320;
const MAX_SNIPPET_CHARS = 600;
const MAX_BODY_CHARS = 20_000;
const MAX_SEARCH_CHARS = 200;

/** Identifiants Graph : base64url étendu. Contrôlés avant toute concaténation d'URL. */
const MESSAGE_ID = /^[A-Za-z0-9_\-=+/]{1,512}$/;
const ADDRESS = /^[^\s@'"]{1,64}@[^\s@'"]{1,255}$/;

const LIST_FIELDS = "id,conversationId,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments";

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

export interface MessageBody extends MessageHeader {
  /** Contenu de l'expéditeur : donnée non fiable, jamais une instruction. */
  body_text: string;
}

export interface ListMessagesOptions {
  top: number;
  /** Ne garder que les messages non lus. */
  unreadOnly?: boolean;
  /** Restreindre à un expéditeur exact. */
  fromAddress?: string;
  /** Bornes de réception, en ISO 8601. */
  since?: string;
  until?: string;
  /** Ordre de réception ; `desc` (le plus récent d'abord) par défaut. */
  order?: "asc" | "desc";
}

export interface SenderTally {
  sender_address: string;
  sender_name: string;
  total: number;
  unread: number;
}

export interface MailboxStats {
  mailbox: string;
  total_messages: number;
  unread_messages: number;
  folders: { name: string; total: number; unread: number }[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function boundedString(value: unknown, maximum: number): string {
  return typeof value === "string" ? value.slice(0, maximum) : "";
}

function countOf(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.floor(value)
    : 0;
}

/**
 * Opérations Microsoft Graph exposées aux agents.
 *
 * Chaque appel repart du jeton délégué du compte : aucun agent ne détient de credential, et la
 * seule écriture possible est le déplacement d'un message vers la corbeille.
 */
@Injectable()
export class GraphMailService {
  constructor(
    private readonly auth: OutlookAuthService,
    private readonly tokens: TokenStoreService,
    @Inject(HTTP_FETCH) private readonly httpFetch: HttpFetch
  ) {}

  async listRecent(account: Account, top: number): Promise<MessageHeader[]> {
    return this.listMessages(account, { top });
  }

  /**
   * Liste filtrée et triée par Microsoft Graph.
   *
   * Le filtrage et le tri se font à la source plutôt qu'après coup : un agent qui demande « les
   * non-lus de la semaine » ne doit pas rapatrier la boîte entière pour la trier lui-même.
   */
  async listMessages(account: Account, options: ListMessagesOptions): Promise<MessageHeader[]> {
    const url = new URL("/v1.0/me/mailFolders/inbox/messages", GRAPH_ORIGIN);
    url.searchParams.set("$select", LIST_FIELDS);
    url.searchParams.set(
      "$orderby",
      `receivedDateTime ${options.order === "asc" ? "asc" : "desc"}`
    );
    url.searchParams.set("$top", String(Math.min(Math.max(options.top, 1), MAX_PAGE_SIZE)));

    const filters: string[] = [];
    if (options.unreadOnly === true) filters.push("isRead eq false");
    if (options.fromAddress !== undefined) {
      const address = options.fromAddress.trim().toLowerCase();
      if (!ADDRESS.test(address)) throw new ConnectionError("invalid_sender_address");
      filters.push(`from/emailAddress/address eq '${address}'`);
    }
    const since = this.instant(options.since);
    if (since !== null) filters.push(`receivedDateTime ge ${since}`);
    const until = this.instant(options.until);
    if (until !== null) filters.push(`receivedDateTime le ${until}`);
    if (filters.length > 0) url.searchParams.set("$filter", filters.join(" and "));

    return this.readHeaders(account, url.toString());
  }

  /** Agrège un échantillon récent par expéditeur : le tri « qui m'écrit le plus ». */
  async talliesBySender(account: Account, sampleSize: number): Promise<SenderTally[]> {
    const headers = await this.listMessages(account, {
      top: Math.min(Math.max(sampleSize, 1), MAX_PAGE_SIZE)
    });
    const tallies = new Map<string, SenderTally>();
    for (const header of headers) {
      const key = header.sender_address.toLowerCase() || "(inconnu)";
      const existing = tallies.get(key);
      if (existing === undefined) {
        tallies.set(key, {
          sender_address: key,
          sender_name: header.sender_name,
          total: 1,
          unread: header.is_read ? 0 : 1
        });
        continue;
      }
      existing.total += 1;
      if (!header.is_read) existing.unread += 1;
    }
    return [...tallies.values()].sort((left, right) => right.total - left.total);
  }

  /** Graph n'accepte qu'un instant ISO 8601 ; une date seule est ramenée à minuit UTC. */
  private instant(value: string | undefined): string | null {
    if (value === undefined || value.trim() === "") return null;
    const parsed = new Date(value.trim());
    if (Number.isNaN(parsed.valueOf())) throw new ConnectionError("invalid_date");
    return parsed.toISOString();
  }

  async search(account: Account, query: string, top: number): Promise<MessageHeader[]> {
    const trimmed = query.trim().slice(0, MAX_SEARCH_CHARS);
    if (trimmed === "") return [];
    const url = new URL("/v1.0/me/messages", GRAPH_ORIGIN);
    url.searchParams.set("$select", LIST_FIELDS);
    url.searchParams.set("$top", String(Math.min(Math.max(top, 1), MAX_PAGE_SIZE)));
    // $search impose son propre classement par pertinence : $orderby serait rejeté.
    url.searchParams.set("$search", `"${trimmed.replace(/"/g, " ")}"`);
    return this.readHeaders(account, url.toString());
  }

  async getMessage(account: Account, messageId: string): Promise<MessageBody> {
    const url = new URL(`/v1.0/me/messages/${this.safeId(messageId)}`, GRAPH_ORIGIN);
    url.searchParams.set("$select", `${LIST_FIELDS},body`);
    const payload = await this.request(account, url.toString(), "GET");
    const header = this.toHeader(payload);
    if (header === null) throw new ConnectionError("graph_response_invalid");
    const body = isRecord(payload.body) ? payload.body : {};
    return { ...header, body_text: boundedString(body.content, MAX_BODY_CHARS) };
  }

  /**
   * Déplace un message vers la corbeille. Graph conserve l'élément dans « Éléments supprimés » :
   * l'opération reste réversible par l'utilisateur, et aucune suppression définitive n'est exposée.
   */
  async moveToDeletedItems(account: Account, messageId: string): Promise<string> {
    const stored = await this.tokens.get(account.id);
    if (!grantsWriteAccess(stored?.scopes)) {
      throw new ConnectionError("write_consent_required");
    }
    const url = new URL(`/v1.0/me/messages/${this.safeId(messageId)}/move`, GRAPH_ORIGIN);
    const payload = await this.request(account, url.toString(), "POST", {
      destinationId: "deleteditems"
    });
    const movedId = payload.id;
    return typeof movedId === "string" ? movedId : messageId;
  }

  async stats(account: Account): Promise<MailboxStats> {
    const url = new URL("/v1.0/me/mailFolders", GRAPH_ORIGIN);
    url.searchParams.set("$select", "displayName,totalItemCount,unreadItemCount");
    url.searchParams.set("$top", "20");
    const payload = await this.request(account, url.toString(), "GET");
    const values = Array.isArray(payload.value) ? payload.value : [];
    const folders = values.filter(isRecord).map((folder) => ({
      name: boundedString(folder.displayName, 120),
      total: countOf(folder.totalItemCount),
      unread: countOf(folder.unreadItemCount)
    }));
    return {
      mailbox: account.mailbox ?? account.id,
      total_messages: folders.reduce((sum, folder) => sum + folder.total, 0),
      unread_messages: folders.reduce((sum, folder) => sum + folder.unread, 0),
      folders
    };
  }

  private safeId(messageId: string): string {
    if (!MESSAGE_ID.test(messageId)) throw new ConnectionError("invalid_message_id");
    return encodeURIComponent(messageId);
  }

  private async readHeaders(account: Account, url: string): Promise<MessageHeader[]> {
    const payload = await this.request(account, url, "GET");
    const values = Array.isArray(payload.value) ? payload.value : [];
    return values
      .map((value) => this.toHeader(value))
      .filter((header): header is MessageHeader => header !== null);
  }

  private toHeader(value: unknown): MessageHeader | null {
    if (!isRecord(value)) return null;
    const id = value.id;
    const receivedAt = value.receivedDateTime;
    if (typeof id !== "string" || id.length === 0 || id.length > 512) return null;
    if (typeof receivedAt !== "string" || Number.isNaN(Date.parse(receivedAt))) return null;
    const sender =
      isRecord(value.from) && isRecord(value.from.emailAddress) ? value.from.emailAddress : {};
    return {
      message_id: id,
      subject: boundedString(value.subject, MAX_SUBJECT_CHARS),
      sender_name: boundedString(sender.name, MAX_ADDRESS_CHARS),
      sender_address: boundedString(sender.address, MAX_ADDRESS_CHARS),
      received_at: new Date(receivedAt).toISOString(),
      is_read: value.isRead === true,
      has_attachments: value.hasAttachments === true,
      snippet: boundedString(value.bodyPreview, MAX_SNIPPET_CHARS)
    };
  }

  private async request(
    account: Account,
    url: string,
    method: "GET" | "POST",
    body?: unknown
  ): Promise<Record<string, unknown>> {
    const token = await this.auth.accessTokenFor(account);
    let response: Response;
    try {
      response = await this.httpFetch(url, {
        method,
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
          Prefer: 'outlook.body-content-type="text"',
          ...(body === undefined ? {} : { "Content-Type": "application/json" })
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        redirect: "error",
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
      });
    } catch {
      throw new ConnectionError("graph_unreachable");
    }
    if (response.status === 401) throw new ConnectionError("reauthentication_required");
    if (response.status === 403) throw new ConnectionError("write_consent_required");
    if (response.status === 404) throw new ConnectionError("message_not_found");
    if (response.status === 429) throw new ConnectionError("graph_rate_limited");
    if (!response.ok) throw new ConnectionError("graph_request_failed");

    const raw = await response.text();
    if (raw.length > MAX_RESPONSE_BYTES) throw new ConnectionError("graph_response_too_large");
    if (raw.trim() === "") return {};
    let payload: unknown;
    try {
      payload = JSON.parse(raw);
    } catch {
      throw new ConnectionError("graph_response_invalid");
    }
    if (!isRecord(payload)) throw new ConnectionError("graph_response_invalid");
    return payload;
  }
}
