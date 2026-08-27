import "dotenv/config";

import { resolve } from "node:path";

import { Injectable } from "@nestjs/common";

import { DEMO_ACCOUNTS } from "../demo/demo-data";
import { PROVIDERS, type Account, type Provider } from "../domain/models";

export type EzerMode = "demo" | "configured";

const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const MAILBOX = /^[^\s@]{1,64}@[^\s@]{1,255}$/;
const CLIENT_ID = /^[A-Za-z0-9-]{1,128}$/;
const MAP_KEY = /^[A-Za-z0-9._-]{8,128}$/;

/** Les hôtes cartographiques viennent de l'environnement : l'agent ne fournit jamais d'URL. */
function httpsUrlFromEnvironment(name: string, fallback: string | undefined): string | undefined {
  const raw = process.env[name]?.trim();
  if (raw === undefined || raw === "") return fallback;
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(`${name} must be an absolute URL`);
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new Error(`${name} must be an http(s) URL`);
  }
  return parsed.toString().replace(/\/$/, "");
}

function integerFromEnvironment(name: string, fallback: number, minimum: number, maximum: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return fallback;
  if (!/^\d+$/.test(raw)) throw new Error(`${name} must be an integer`);
  const parsed = Number(raw);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${name} must be between ${minimum} and ${maximum}`);
  }
  return parsed;
}

function booleanFromEnvironment(name: string, fallback: boolean): boolean {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return fallback;
  if (raw === "true") return true;
  if (raw === "false") return false;
  throw new Error(`${name} must be true or false`);
}

function parseAccount(value: unknown, index: number): Account {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`EZER_ACCOUNTS_JSON entry ${index} must be an object`);
  }
  const candidate = value as Record<string, unknown>;
  const keys = Object.keys(candidate);
  if (keys.some((key) => key !== "id" && key !== "provider" && key !== "mailbox")) {
    throw new Error(`EZER_ACCOUNTS_JSON entry ${index} has unknown fields`);
  }
  if (typeof candidate.id !== "string" || !ACCOUNT_ID.test(candidate.id)) {
    throw new Error(`EZER_ACCOUNTS_JSON entry ${index} has an invalid id`);
  }
  if (typeof candidate.provider !== "string" || !PROVIDERS.includes(candidate.provider as Provider)) {
    throw new Error(`EZER_ACCOUNTS_JSON entry ${index} has an invalid provider`);
  }
  if (
    candidate.mailbox !== undefined &&
    (typeof candidate.mailbox !== "string" ||
      candidate.mailbox.length > 320 ||
      !MAILBOX.test(candidate.mailbox))
  ) {
    throw new Error(`EZER_ACCOUNTS_JSON entry ${index} has an invalid mailbox`);
  }
  const account: Account = { id: candidate.id, provider: candidate.provider as Provider };
  if (typeof candidate.mailbox === "string") account.mailbox = candidate.mailbox.toLowerCase();
  return account;
}

function configuredAccounts(): Account[] {
  const raw = process.env.EZER_ACCOUNTS_JSON ?? "[]";
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("EZER_ACCOUNTS_JSON must contain valid JSON");
  }
  if (!Array.isArray(parsed) || parsed.length > 100) {
    throw new Error("EZER_ACCOUNTS_JSON must be an array with at most 100 entries");
  }
  const accounts = parsed.map(parseAccount);
  if (new Set(accounts.map((account) => account.id)).size !== accounts.length) {
    throw new Error("EZER_ACCOUNTS_JSON contains duplicate account ids");
  }
  return accounts;
}

@Injectable()
export class AppConfigService {
  readonly mode: EzerMode;
  readonly apiKey: string;
  readonly host: string;
  readonly port: number;
  readonly dataFile: string;
  readonly sourceFile: string | undefined;
  readonly demoResetOnStart: boolean;
  readonly syncDefaultLimit: number;
  readonly syncMaxLimit: number;
  readonly accounts: Account[];
  /** Application publique Entra utilisée par le flux device code; sans elle aucune connexion. */
  readonly outlookClientId: string | undefined;
  readonly tokenFile: string;
  /** Clé MapTiler : géocodage côté serveur, et fond de carte côté navigateur. */
  readonly maptilerKey: string | undefined;
  readonly maptilerStyle: string;
  /** Service de calcul d'itinéraire au protocole OSRM ; à héberger soi-même en production. */
  readonly routingUrl: string | undefined;
  /** Adresse de départ par défaut lorsqu'aucune n'est donnée. */
  readonly defaultOrigin: string;
  /** Géocodeur sans clé, replié quand MapTiler n'est pas configuré ; vide pour le désactiver. */
  readonly nominatimUrl: string | undefined;
  /** Contact envoyé en User-Agent, exigé par la politique d'usage de Nominatim. */
  readonly mapContact: string;

  constructor() {
    const mode = process.env.EZER_MODE ?? "demo";
    if (mode !== "demo" && mode !== "configured") {
      throw new Error("EZER_MODE must be demo or configured");
    }
    this.mode = mode;

    const configuredKey = process.env.EZER_API_KEY;
    if (configuredKey !== undefined && (configuredKey.length < 8 || configuredKey.length > 512)) {
      throw new Error("EZER_API_KEY must contain between 8 and 512 characters");
    }
    if (mode === "configured" && configuredKey === undefined) {
      throw new Error("EZER_API_KEY is required in configured mode");
    }
    this.apiKey = configuredKey ?? "ezer-demo-key";
    this.host = process.env.EZER_HOST || "127.0.0.1";
    this.port = integerFromEnvironment("EZER_PORT", 8080, 1, 65_535);
    this.dataFile = resolve(process.cwd(), process.env.EZER_DATA_FILE || "./data/ezer.json");
    this.sourceFile = process.env.EZER_SOURCE_FILE
      ? resolve(process.cwd(), process.env.EZER_SOURCE_FILE)
      : undefined;
    this.demoResetOnStart = booleanFromEnvironment("EZER_DEMO_RESET_ON_START", false);
    this.syncDefaultLimit = integerFromEnvironment("EZER_SYNC_DEFAULT_LIMIT", 50, 1, 500);
    this.syncMaxLimit = integerFromEnvironment("EZER_SYNC_MAX_LIMIT", 500, 1, 500);
    if (this.syncDefaultLimit > this.syncMaxLimit) {
      throw new Error("EZER_SYNC_DEFAULT_LIMIT cannot exceed EZER_SYNC_MAX_LIMIT");
    }
    const clientId = process.env.EZER_OUTLOOK_CLIENT_ID?.trim();
    if (clientId !== undefined && clientId !== "" && !CLIENT_ID.test(clientId)) {
      throw new Error("EZER_OUTLOOK_CLIENT_ID must be an application identifier");
    }
    this.outlookClientId = clientId === "" ? undefined : clientId;
    // Les jetons délégués ne partagent jamais le fichier de données du tableau de bord.
    this.tokenFile = resolve(process.cwd(), process.env.EZER_TOKEN_FILE || "./data/tokens.json");
    const mapKey = process.env.EZER_MAPTILER_KEY?.trim();
    if (mapKey !== undefined && mapKey !== "" && !MAP_KEY.test(mapKey)) {
      throw new Error("EZER_MAPTILER_KEY must be an API key");
    }
    this.maptilerKey = mapKey === "" ? undefined : mapKey;
    this.maptilerStyle = process.env.EZER_MAPTILER_STYLE?.trim() || "streets-v2-dark";
    this.routingUrl = httpsUrlFromEnvironment("EZER_ROUTING_URL", undefined);
    this.defaultOrigin = process.env.EZER_DEFAULT_ORIGIN?.trim() || "Paris, France";
    this.nominatimUrl =
      process.env.EZER_NOMINATIM_URL?.trim() === ""
        ? undefined
        : httpsUrlFromEnvironment("EZER_NOMINATIM_URL", "https://nominatim.openstreetmap.org");
    this.mapContact = (process.env.EZER_MAP_CONTACT?.trim() || "ezer-agent-ui").slice(0, 120);
    this.accounts = mode === "demo" ? structuredClone(DEMO_ACCOUNTS) : configuredAccounts();
  }
}
