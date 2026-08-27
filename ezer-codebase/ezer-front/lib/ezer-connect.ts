import "server-only";

/**
 * Accès serveur aux routes de connexion de boîte du backend Ezer.
 *
 * L'authentification et la lecture des messages vivent entièrement dans le backend : ce module ne
 * transporte que l'état public d'une connexion (URI Microsoft et code à saisir) et n'expose jamais
 * `EZER_API_KEY` au navigateur.
 */

const REQUEST_TIMEOUT_MS = 15_000;
const MAX_ACCOUNTS = 100;
const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;

const STATUSES = ["disconnected", "pending", "connected", "failed"] as const;
const PROVIDERS = ["gmail", "outlook"] as const;

// Même liste que le backend : la page à ouvrir dépend de l'autorité Microsoft.
const DEVICE_LOGIN_URIS = new Set([
  "https://microsoft.com/devicelogin",
  "https://www.microsoft.com/devicelogin",
  "https://microsoft.com/link",
  "https://www.microsoft.com/link",
]);

export type ConnectionStatus = (typeof STATUSES)[number];
export type ConnectProvider = (typeof PROVIDERS)[number];

export interface MailboxConnection {
  account_id: string;
  provider: ConnectProvider;
  mailbox: string | null;
  status: ConnectionStatus;
  code: string | null;
  verification_uri: string | null;
  user_code: string | null;
  connected_at: string | null;
}

export interface ConnectPanelState {
  /** Faux quand le frontend tourne en démonstration : aucune boîte réelle n'est joignable. */
  configured: boolean;
  mailboxes: MailboxConnection[];
}

export class EzerConnectError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "EzerConnectError";
  }
}

interface Config {
  apiUrl: URL;
  apiKey: string;
}

function getConfig(): Config | null {
  const apiUrl = process.env.EZER_API_URL?.trim();
  const apiKey = process.env.EZER_API_KEY?.trim();
  if (!apiUrl || !apiKey) {
    return null;
  }

  let parsedUrl: URL;
  try {
    parsedUrl = new URL(apiUrl);
  } catch {
    throw new EzerConnectError("Invalid Ezer API URL");
  }
  if (parsedUrl.protocol !== "http:" && parsedUrl.protocol !== "https:") {
    throw new EzerConnectError("Invalid Ezer API URL protocol");
  }
  return { apiUrl: parsedUrl, apiKey };
}

async function request(config: Config, path: string, method: string): Promise<unknown> {
  const url = new URL(path, config.apiUrl);
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      cache: "no-store",
      headers: { Accept: "application/json", "X-API-Key": config.apiKey },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    throw new EzerConnectError("Ezer API is unreachable");
  }
  if (!response.ok) {
    throw new EzerConnectError("Ezer API request failed", response.status);
  }
  try {
    return await response.json();
  } catch {
    throw new EzerConnectError("Ezer API returned invalid JSON", response.status);
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Expected an object");
  }
  return value as Record<string, unknown>;
}

function asBoundedString(value: unknown, maximumLength: number): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string" || value.length === 0 || value.length > maximumLength) {
    throw new TypeError("Expected a bounded string");
  }
  return value;
}

function asEnum<T extends string>(value: unknown, values: readonly T[]): T {
  if (typeof value !== "string" || !values.includes(value as T)) {
    throw new TypeError("Expected an enum member");
  }
  return value as T;
}

/** N'accepte qu'une page de connexion Microsoft connue : un lien affiché est un lien cliqué. */
function asDeviceLoginUri(value: unknown): string | null {
  const uri = asBoundedString(value, 2_048);
  if (uri === null) return null;
  if (!DEVICE_LOGIN_URIS.has(uri.replace(/\/+$/, ""))) {
    throw new TypeError("Expected a Microsoft device-login URI");
  }
  return uri.replace(/\/+$/, "");
}

function parseConnection(value: unknown, accountId?: string): MailboxConnection {
  const connection = asRecord(value);
  const id = accountId ?? asBoundedString(connection.account_id, 128);
  if (id === null || !ACCOUNT_ID.test(id)) {
    throw new TypeError("Invalid account identifier");
  }
  return {
    account_id: id,
    provider: asEnum<ConnectProvider>(connection.provider, PROVIDERS),
    mailbox: asBoundedString(connection.mailbox, 320),
    status: asEnum<ConnectionStatus>(connection.status, STATUSES),
    code: asBoundedString(connection.code, 128),
    verification_uri: asDeviceLoginUri(connection.verification_uri),
    user_code: asBoundedString(connection.user_code, 32),
    connected_at: asBoundedString(connection.connected_at, 64),
  };
}

function parseAccounts(payload: unknown): MailboxConnection[] {
  const response = asRecord(payload);
  if (!Array.isArray(response.accounts) || response.accounts.length > MAX_ACCOUNTS) {
    throw new TypeError("Expected a bounded array");
  }
  return response.accounts.map((account) => {
    const record = asRecord(account);
    const id = asBoundedString(record.id, 128);
    if (id === null || !ACCOUNT_ID.test(id)) throw new TypeError("Invalid account identifier");
    return {
      account_id: id,
      provider: asEnum<ConnectProvider>(record.provider, PROVIDERS),
      mailbox: asBoundedString(record.mailbox, 320),
      status: asEnum<ConnectionStatus>(record.status, STATUSES),
      code: null,
      verification_uri: null,
      user_code: null,
      connected_at: asBoundedString(record.connected_at, 64),
    };
  });
}

/** Boîtes déclarées côté backend qui peuvent réellement être connectées. */
export async function listConnectableMailboxes(): Promise<ConnectPanelState> {
  const config = getConfig();
  if (config === null) return { configured: false, mailboxes: [] };
  const accounts = parseAccounts(await request(config, "/v1/accounts", "GET"));
  return {
    configured: true,
    mailboxes: accounts.filter(
      (account) => account.provider === "outlook" && account.mailbox !== null,
    ),
  };
}

export async function readConnection(accountId: string): Promise<MailboxConnection> {
  return connectionRequest(accountId, "GET");
}

export async function startConnection(accountId: string): Promise<MailboxConnection> {
  return connectionRequest(accountId, "POST");
}

export async function endConnection(accountId: string): Promise<MailboxConnection> {
  return connectionRequest(accountId, "DELETE");
}

async function connectionRequest(accountId: string, method: string): Promise<MailboxConnection> {
  if (!ACCOUNT_ID.test(accountId)) {
    throw new EzerConnectError("Invalid account identifier", 400);
  }
  const config = getConfig();
  if (config === null) {
    throw new EzerConnectError("Ezer backend is not configured", 503);
  }
  const payload = await request(
    config,
    `/v1/accounts/${encodeURIComponent(accountId)}/connection`,
    method,
  );
  try {
    return parseConnection(payload, accountId);
  } catch {
    throw new EzerConnectError("Ezer API returned an invalid payload");
  }
}
