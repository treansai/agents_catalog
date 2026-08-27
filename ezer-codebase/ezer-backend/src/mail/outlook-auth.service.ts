import { Inject, Injectable, Logger } from "@nestjs/common";

import { AppConfigService } from "../config/app-config.service";
import type { Account, ConnectionState, ConnectionStatus } from "../domain/models";
import { TokenStoreService } from "./token-store.service";

export const HTTP_FETCH = Symbol("HTTP_FETCH");
export const SLEEPER = Symbol("SLEEPER");

export type HttpFetch = (input: string, init: RequestInit) => Promise<Response>;
export type Sleeper = (milliseconds: number) => Promise<void>;

/** Autorité grand public : les boîtes outlook.com/hotmail ne vivent dans aucun tenant Entra. */
const AUTHORITY = "https://login.microsoftonline.com/consumers";
const DEVICE_CODE_ENDPOINT = `${AUTHORITY}/oauth2/v2.0/devicecode`;
const TOKEN_ENDPOINT = `${AUTHORITY}/oauth2/v2.0/token`;
const GRAPH_ME_ENDPOINT = "https://graph.microsoft.com/v1.0/me";
// Mail.ReadWrite couvre la lecture et le déplacement vers la corbeille demandé par l'assistant.
const SCOPES = "offline_access Mail.ReadWrite User.Read";
const WRITE_SCOPE = "mail.readwrite";
const DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code";

// La page à ouvrir dépend de l'autorité : Entra renvoie /devicelogin, l'autorité grand public
// renvoie /link. L'URI de Microsoft est utilisée telle quelle après contrôle de cette liste.
const DEVICE_LOGIN_URIS = new Set([
  "https://microsoft.com/devicelogin",
  "https://www.microsoft.com/devicelogin",
  "https://microsoft.com/link",
  "https://www.microsoft.com/link"
]);

/**
 * Seules ces réponses signifient que le refresh token est définitivement mort. Toute autre erreur
 * (réseau, indisponibilité, portée refusée) laisse la connexion en place : jeter un jeton encore
 * valable imposerait à l'utilisateur une reconnexion qu'aucun incident ne justifie.
 */
const DEAD_GRANT_ERRORS = new Set(["invalid_grant", "invalid_client", "unauthorized_client"]);

/**
 * Microsoft renvoie les portées accordées sans `offline_access` et parfois avec des portées OIDC
 * (`profile` seule est refusée : elle exige `openid`). Un renouvellement ne rejoue donc pas la
 * chaîne telle quelle — il garde les portées de ressource consenties et remet `offline_access`.
 */
const OIDC_SCOPES = new Set(["openid", "profile", "email", "offline_access"]);

export function refreshScopesFor(grantedScopes: string | undefined, fallback: string): string {
  const granted = (grantedScopes ?? "")
    .split(/\s+/)
    .map((scope) => scope.trim())
    .filter((scope) => scope !== "" && !OIDC_SCOPES.has(scope.toLowerCase()));
  if (granted.length === 0) return fallback;
  return ["offline_access", ...granted].join(" ");
}

const USER_CODE = /^[A-Za-z0-9-]{4,32}$/;
const REQUEST_TIMEOUT_MS = 15_000;
const MAX_RESPONSE_BYTES = 256 * 1024;
const MIN_POLL_INTERVAL_MS = 1_000;
const MAX_POLL_INTERVAL_MS = 60_000;
const MAX_FLOW_LIFETIME_MS = 20 * 60 * 1_000;
const ACCESS_TOKEN_SAFETY_MARGIN_MS = 60_000;

interface DeviceCodeFlow {
  status: ConnectionStatus;
  code: string | null;
  verificationUri: string;
  userCode: string;
  expiresAt: number;
}

interface CachedAccessToken {
  value: string;
  expiresAt: number;
}

/** Erreur interne : seul son `code` stable sort de ce service. */
export class ConnectionError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "ConnectionError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeDeviceLoginUri(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 2_048) return null;
  const trimmed = value.trim().replace(/\/+$/, "");
  return DEVICE_LOGIN_URIS.has(trimmed) ? trimmed : null;
}

/** Un jeton consenti avant l'élargissement des portées ne peut pas supprimer. */
export function grantsWriteAccess(scopes: string | undefined): boolean {
  if (scopes === undefined) return false;
  return scopes
    .split(/\s+/)
    .some((scope) => scope.trim().toLowerCase().endsWith(WRITE_SCOPE));
}

function positiveSeconds(value: unknown, fallback: number, maximum: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return fallback;
  return Math.min(value, maximum);
}

function realSleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, milliseconds);
    // Un flux en attente ne doit jamais empêcher l'arrêt du processus.
    timer.unref?.();
  });
}

/**
 * Authentification déléguée Microsoft par device code, entièrement côté backend.
 *
 * Le navigateur de l'utilisateur ne reçoit que l'URI publique et le code à saisir ; le device code,
 * les jetons d'accès et le refresh token restent dans ce processus et dans le magasin de jetons.
 */
@Injectable()
export class OutlookAuthService {
  private readonly logger = new Logger(OutlookAuthService.name);
  private readonly flows = new Map<string, DeviceCodeFlow>();
  private readonly accessTokens = new Map<string, CachedAccessToken>();

  constructor(
    private readonly config: AppConfigService,
    private readonly tokens: TokenStoreService,
    @Inject(HTTP_FETCH) private readonly httpFetch: HttpFetch,
    @Inject(SLEEPER) private readonly sleep: Sleeper
  ) {}

  /** Vérifie qu'un compte peut être connecté, sans révéler la configuration au client. */
  assertConnectable(account: Account): { mailbox: string; clientId: string } {
    if (account.provider !== "outlook") throw new ConnectionError("provider_not_supported");
    if (account.mailbox === undefined || account.mailbox === "") {
      throw new ConnectionError("mailbox_not_configured");
    }
    if (this.config.outlookClientId === undefined) {
      throw new ConnectionError("client_not_configured");
    }
    return { mailbox: account.mailbox, clientId: this.config.outlookClientId };
  }

  async state(account: Account): Promise<ConnectionState> {
    const stored = await this.tokens.get(account.id);
    const flow = this.currentFlow(account.id);
    const base = {
      account_id: account.id,
      provider: account.provider,
      mailbox: account.mailbox ?? null,
      write_enabled: grantsWriteAccess(stored?.scopes)
    };
    if (stored !== undefined && (flow === undefined || flow.status !== "pending")) {
      return {
        ...base,
        status: "connected" as const,
        code: null,
        verification_uri: null,
        user_code: null,
        expires_at: null,
        connected_at: stored.connected_at
      };
    }
    if (flow === undefined) {
      return {
        ...base,
        status: "disconnected" as const,
        code: null,
        verification_uri: null,
        user_code: null,
        expires_at: null,
        connected_at: null
      };
    }
    return {
      ...base,
      status: flow.status,
      code: flow.code,
      verification_uri: flow.status === "pending" ? flow.verificationUri : null,
      user_code: flow.status === "pending" ? flow.userCode : null,
      expires_at: flow.status === "pending" ? new Date(flow.expiresAt).toISOString() : null,
      connected_at: stored?.connected_at ?? null
    };
  }

  /**
   * Démarre un flux device code, ou renvoie celui déjà en cours pour ce compte. La complétion est
   * poursuivie en tâche de fond : la requête HTTP retourne dès que le code est connu.
   */
  async connect(account: Account): Promise<ConnectionState> {
    const { mailbox, clientId } = this.assertConnectable(account);
    const pending = this.currentFlow(account.id);
    if (pending !== undefined && pending.status === "pending") return this.state(account);

    const payload = await this.postForm(DEVICE_CODE_ENDPOINT, {
      client_id: clientId,
      scope: SCOPES
    });
    const verificationUri = normalizeDeviceLoginUri(
      payload.verification_uri ?? payload.verification_url
    );
    const userCode = typeof payload.user_code === "string" ? payload.user_code.trim() : "";
    const deviceCode = typeof payload.device_code === "string" ? payload.device_code : "";
    if (verificationUri === null || !USER_CODE.test(userCode) || deviceCode === "") {
      throw new ConnectionError(
        payload.error === "invalid_client" ? "public_client_flow_not_enabled" : "device_flow_failed"
      );
    }

    const lifetimeMs = positiveSeconds(payload.expires_in, 900, 1_800) * 1_000;
    const flow: DeviceCodeFlow = {
      status: "pending",
      code: null,
      verificationUri,
      userCode,
      expiresAt: Date.now() + Math.min(lifetimeMs, MAX_FLOW_LIFETIME_MS)
    };
    this.flows.set(account.id, flow);

    const intervalMs = Math.min(
      Math.max(positiveSeconds(payload.interval, 5, 60) * 1_000, MIN_POLL_INTERVAL_MS),
      MAX_POLL_INTERVAL_MS
    );
    void this.completeFlow(account.id, mailbox, clientId, deviceCode, flow, intervalMs);

    return this.state(account);
  }

  async disconnect(account: Account): Promise<boolean> {
    this.flows.delete(account.id);
    this.accessTokens.delete(account.id);
    return this.tokens.remove(account.id);
  }

  /** Jeton d'accès Graph pour un compte connecté ; échange le refresh token si nécessaire. */
  async accessTokenFor(account: Account): Promise<string> {
    const cached = this.accessTokens.get(account.id);
    if (cached !== undefined && cached.expiresAt > Date.now()) return cached.value;

    const { clientId } = this.assertConnectable(account);
    const stored = await this.tokens.get(account.id);
    if (stored === undefined) throw new ConnectionError("account_not_connected");

    let payload: Record<string, unknown>;
    try {
      payload = await this.postForm(TOKEN_ENDPOINT, {
        client_id: clientId,
        grant_type: "refresh_token",
        refresh_token: stored.refresh_token,
        // Renouveler sur les portées réellement consenties, pas sur celles que demande la version
        // courante du code : élargir SCOPES ne doit jamais invalider une connexion existante.
        scope: refreshScopesFor(stored.scopes, SCOPES)
      });
    } catch (error) {
      throw error instanceof ConnectionError ? error : new ConnectionError("token_refresh_failed");
    }

    const accessToken = payload.access_token;
    if (typeof accessToken !== "string" || accessToken === "") {
      const providerError =
        typeof payload.error === "string" ? payload.error.toLowerCase() : "";
      if (!DEAD_GRANT_ERRORS.has(providerError)) {
        // La connexion est conservée : l'échec peut être passager et le jeton rester utilisable.
        throw new ConnectionError("token_refresh_failed");
      }
      await this.tokens.remove(account.id);
      throw new ConnectionError("reauthentication_required");
    }
    if (typeof payload.refresh_token === "string" && payload.refresh_token !== "") {
      await this.tokens.rotate(account.id, payload.refresh_token);
    }
    const lifetimeMs = positiveSeconds(payload.expires_in, 3_600, 86_400) * 1_000;
    this.accessTokens.set(account.id, {
      value: accessToken,
      expiresAt: Date.now() + Math.max(lifetimeMs - ACCESS_TOKEN_SAFETY_MARGIN_MS, 0)
    });
    return accessToken;
  }

  private currentFlow(accountId: string): DeviceCodeFlow | undefined {
    const flow = this.flows.get(accountId);
    if (flow === undefined) return undefined;
    if (flow.status === "pending" && flow.expiresAt <= Date.now()) {
      flow.status = "failed";
      flow.code = "device_code_expired";
    }
    return flow;
  }

  private async completeFlow(
    accountId: string,
    mailbox: string,
    clientId: string,
    deviceCode: string,
    flow: DeviceCodeFlow,
    initialIntervalMs: number
  ): Promise<void> {
    let intervalMs = initialIntervalMs;
    try {
      while (flow.status === "pending") {
        if (flow.expiresAt <= Date.now()) throw new ConnectionError("device_code_expired");
        await this.sleep(intervalMs);
        if (this.flows.get(accountId) !== flow) return;

        const payload = await this.postForm(TOKEN_ENDPOINT, {
          client_id: clientId,
          grant_type: DEVICE_CODE_GRANT,
          device_code: deviceCode
        });
        const error = typeof payload.error === "string" ? payload.error : "";
        if (error === "authorization_pending") continue;
        if (error === "slow_down") {
          intervalMs = Math.min(intervalMs + 5_000, MAX_POLL_INTERVAL_MS);
          continue;
        }
        if (error !== "") {
          throw new ConnectionError(
            error === "authorization_declined" || error === "expired_token"
              ? error
              : "device_flow_failed"
          );
        }

        const accessToken = payload.access_token;
        const refreshToken = payload.refresh_token;
        if (typeof accessToken !== "string" || accessToken === "") {
          throw new ConnectionError("device_flow_failed");
        }
        if (typeof refreshToken !== "string" || refreshToken === "") {
          // Sans refresh token la connexion ne survivrait pas à une heure : la refuser est plus
          // honnête qu'un compte qui se déconnecte seul.
          throw new ConnectionError("offline_access_denied");
        }

        const signedIn = await this.signedInMailbox(accessToken);
        if (signedIn !== mailbox) {
          // L'adresse principale d'un compte Microsoft diffère souvent de l'alias saisi. Elle est
          // tracée ici, pour l'opérateur seul : l'API ne renvoie que le code `mailbox_mismatch`.
          this.logger.warn(
            JSON.stringify({
              event: "connection_mailbox_mismatch",
              account_id: accountId,
              configured_mailbox: mailbox,
              signed_in_mailbox: signedIn
            })
          );
          throw new ConnectionError("mailbox_mismatch");
        }

        await this.tokens.save({
          account_id: accountId,
          mailbox,
          refresh_token: refreshToken,
          connected_at: new Date().toISOString(),
          scopes: typeof payload.scope === "string" ? payload.scope.slice(0, 2_048) : ""
        });
        const lifetimeMs = positiveSeconds(payload.expires_in, 3_600, 86_400) * 1_000;
        this.accessTokens.set(accountId, {
          value: accessToken,
          expiresAt: Date.now() + Math.max(lifetimeMs - ACCESS_TOKEN_SAFETY_MARGIN_MS, 0)
        });
        flow.status = "connected";
        flow.code = null;
        return;
      }
    } catch (error) {
      if (this.flows.get(accountId) !== flow) return;
      flow.status = "failed";
      flow.code = error instanceof ConnectionError ? error.code : "device_flow_failed";
    }
  }

  private async signedInMailbox(accessToken: string): Promise<string> {
    let response: Response;
    try {
      response = await this.httpFetch(GRAPH_ME_ENDPOINT, {
        method: "GET",
        headers: { Accept: "application/json", Authorization: `Bearer ${accessToken}` },
        redirect: "error",
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
      });
    } catch {
      throw new ConnectionError("microsoft_unreachable");
    }
    if (!response.ok) throw new ConnectionError("mailbox_lookup_failed");
    const payload: unknown = await this.readJson(response);
    if (!isRecord(payload)) throw new ConnectionError("mailbox_lookup_failed");
    const address = payload.mail ?? payload.userPrincipalName;
    if (typeof address !== "string" || address.length > 320) {
      throw new ConnectionError("mailbox_lookup_failed");
    }
    return address.trim().toLowerCase();
  }

  private async postForm(
    endpoint: string,
    fields: Record<string, string>
  ): Promise<Record<string, unknown>> {
    let response: Response;
    try {
      response = await this.httpFetch(endpoint, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/x-www-form-urlencoded"
        },
        body: new URLSearchParams(fields).toString(),
        redirect: "error",
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
      });
    } catch {
      throw new ConnectionError("microsoft_unreachable");
    }
    // Un refus OAuth utilise un 400 porteur d'un corps JSON exploitable ; seul un 5xx est opaque.
    if (response.status >= 500) throw new ConnectionError("microsoft_unavailable");
    const payload: unknown = await this.readJson(response);
    if (!isRecord(payload)) throw new ConnectionError("device_flow_failed");
    return payload;
  }

  private async readJson(response: Response): Promise<unknown> {
    const raw = await response.text();
    if (raw.length > MAX_RESPONSE_BYTES) throw new ConnectionError("microsoft_response_too_large");
    try {
      return JSON.parse(raw);
    } catch {
      throw new ConnectionError("microsoft_response_invalid");
    }
  }
}

export const HTTP_FETCH_PROVIDER = {
  provide: HTTP_FETCH,
  useValue: ((input: string, init: RequestInit) => fetch(input, init)) satisfies HttpFetch
};

export const SLEEPER_PROVIDER = { provide: SLEEPER, useValue: realSleep satisfies Sleeper };
