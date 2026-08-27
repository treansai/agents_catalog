import type { Account, FetchBatch, MailMessage, Provider } from "../domain/models";
import type { MailConnector } from "../sync/mail-connector";
import { ConnectionError, type HttpFetch } from "./outlook-auth.service";

const GRAPH_ORIGIN = "https://graph.microsoft.com";
const MESSAGES_PATH = "/v1.0/me/mailFolders/inbox/messages";
const SELECT_FIELDS = "id,conversationId,subject,from,receivedDateTime,bodyPreview,body";
const REQUEST_TIMEOUT_MS = 20_000;
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const MAX_PAGE_SIZE = 50;
const MAX_SUBJECT_CHARS = 998;
const MAX_ADDRESS_CHARS = 320;
const MAX_SNIPPET_CHARS = 2_000;
const MAX_BODY_CHARS = 500_000;

type AccessTokenProvider = () => Promise<string>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function boundedString(value: unknown, maximum: number): string {
  return typeof value === "string" ? value.slice(0, maximum) : "";
}

/**
 * Un `@odata.nextLink` est une valeur opaque, mais elle est suivie comme une URL : elle est donc
 * acceptée uniquement si elle pointe encore vers Microsoft Graph.
 */
function trustedGraphUrl(value: string): string | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.origin !== GRAPH_ORIGIN || !url.pathname.startsWith("/v1.0/")) return null;
  return url.toString();
}

/** Lecture seule d'une boîte Outlook via Microsoft Graph, page par page. */
export class GraphMailConnector implements MailConnector {
  readonly accountId: string;
  readonly provider: Provider;

  constructor(
    account: Account,
    private readonly accessToken: AccessTokenProvider,
    private readonly httpFetch: HttpFetch
  ) {
    this.accountId = account.id;
    this.provider = account.provider;
  }

  async fetch(cursor: string | null, limit: number): Promise<FetchBatch> {
    const pageSize = Math.min(Math.max(limit, 1), MAX_PAGE_SIZE);
    let cursorReset = false;
    let url: string;
    if (cursor === null) {
      url = this.firstPageUrl(pageSize);
    } else {
      const trusted = trustedGraphUrl(cursor);
      if (trusted === null) {
        cursorReset = true;
        url = this.firstPageUrl(pageSize);
      } else {
        url = trusted;
      }
    }

    const payload = await this.request(url);
    const values = payload.value;
    if (!Array.isArray(values)) throw new ConnectionError("graph_response_invalid");
    const messages = values
      .map((value) => this.toMailMessage(value))
      .filter((message): message is MailMessage => message !== null);

    const nextLink = payload["@odata.nextLink"];
    const nextCursor = typeof nextLink === "string" ? trustedGraphUrl(nextLink) : null;
    return {
      messages,
      // Sans page suivante, la page courante est rejouée au prochain tour : Graph ne renvoie alors
      // que les messages arrivés depuis, l'identité de message évitant les doublons.
      next_cursor: nextCursor ?? (cursorReset ? null : cursor),
      cursor_reset: cursorReset
    };
  }

  private firstPageUrl(pageSize: number): string {
    const url = new URL(MESSAGES_PATH, GRAPH_ORIGIN);
    url.searchParams.set("$select", SELECT_FIELDS);
    // Ordre croissant : le curseur progresse dans le sens d'arrivée des messages.
    url.searchParams.set("$orderby", "receivedDateTime asc");
    url.searchParams.set("$top", String(pageSize));
    return url.toString();
  }

  private async request(url: string): Promise<Record<string, unknown>> {
    const token = await this.accessToken();
    let response: Response;
    try {
      response = await this.httpFetch(url, {
        method: "GET",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
          // Graph renvoie alors un corps texte : aucun HTML n'a besoin d'être nettoyé ici.
          Prefer: 'outlook.body-content-type="text"'
        },
        redirect: "error",
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
      });
    } catch {
      throw new ConnectionError("graph_unreachable");
    }
    if (response.status === 401 || response.status === 403) {
      throw new ConnectionError("reauthentication_required");
    }
    if (response.status === 429) throw new ConnectionError("graph_rate_limited");
    if (!response.ok) throw new ConnectionError("graph_request_failed");

    const raw = await response.text();
    if (raw.length > MAX_RESPONSE_BYTES) throw new ConnectionError("graph_response_too_large");
    let payload: unknown;
    try {
      payload = JSON.parse(raw);
    } catch {
      throw new ConnectionError("graph_response_invalid");
    }
    if (!isRecord(payload)) throw new ConnectionError("graph_response_invalid");
    return payload;
  }

  private toMailMessage(value: unknown): MailMessage | null {
    if (!isRecord(value)) return null;
    const id = value.id;
    if (typeof id !== "string" || id.length === 0 || id.length > 1_024) return null;
    const receivedAt = value.receivedDateTime;
    if (typeof receivedAt !== "string" || Number.isNaN(Date.parse(receivedAt))) return null;

    const sender = isRecord(value.from) && isRecord(value.from.emailAddress)
      ? value.from.emailAddress
      : {};
    const body = isRecord(value.body) ? value.body : {};
    const conversation = value.conversationId;

    return {
      account_id: this.accountId,
      provider: this.provider,
      provider_message_id: id,
      thread_id:
        typeof conversation === "string" && conversation.length <= 1_024 ? conversation : null,
      subject: boundedString(value.subject, MAX_SUBJECT_CHARS),
      sender_name: boundedString(sender.name, MAX_ADDRESS_CHARS),
      sender_address: boundedString(sender.address, MAX_ADDRESS_CHARS),
      received_at: new Date(receivedAt).toISOString(),
      body_text: boundedString(body.content, MAX_BODY_CHARS),
      snippet: boundedString(value.bodyPreview, MAX_SNIPPET_CHARS)
    };
  }
}
