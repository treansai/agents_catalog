import "dotenv/config";

import { Injectable } from "@nestjs/common";

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

function httpUrl(name: string, raw: string | undefined): string | undefined {
  const trimmed = raw?.trim();
  if (trimmed === undefined || trimmed === "") return undefined;
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new Error(`${name} must be an absolute URL`);
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(`${name} must be an http(s) URL`);
  }
  return parsed.toString().replace(/\/$/, "");
}

@Injectable()
export class AppConfigService {
  readonly apiKey: string;
  readonly host: string;
  readonly port: number;
  readonly anthropicApiKey: string | undefined;
  readonly anthropicModel: string;
  readonly llmMaxTokens: number;
  readonly llmTimeoutSeconds: number;
  readonly backendUrl: string | undefined;
  readonly backendApiKey: string | undefined;

  constructor() {
    const configuredKey = process.env.EZER_API_KEY;
    if (configuredKey === undefined || configuredKey.length < 8 || configuredKey.length > 512) {
      throw new Error("EZER_API_KEY must contain between 8 and 512 characters");
    }
    this.apiKey = configuredKey;
    this.host = process.env.EZER_HOST || "127.0.0.1";
    this.port = integerFromEnvironment("EZER_PORT", 8080, 1, 65_535);

    const anthropicKey = process.env.EZER_ANTHROPIC_API_KEY?.trim();
    this.anthropicApiKey = anthropicKey === undefined || anthropicKey === "" ? undefined : anthropicKey;
    const model = process.env.EZER_ANTHROPIC_MODEL?.trim() || "claude-sonnet-5";
    if (!model.startsWith("claude-")) {
      throw new Error("EZER_ANTHROPIC_MODEL must be an Anthropic Claude API model ID");
    }
    this.anthropicModel = model;
    this.llmMaxTokens = integerFromEnvironment("EZER_LLM_MAX_TOKENS", 4096, 256, 128_000);
    this.llmTimeoutSeconds = integerFromEnvironment("EZER_LLM_TIMEOUT_SECONDS", 60, 1, 600);
    this.backendUrl = httpUrl("EZER_BACKEND_URL", process.env.EZER_BACKEND_URL);
    const backendKey = process.env.EZER_BACKEND_API_KEY?.trim();
    this.backendApiKey = backendKey === undefined || backendKey === "" ? undefined : backendKey;
  }

  get assistantConfigured(): boolean {
    return (
      this.anthropicApiKey !== undefined &&
      this.backendUrl !== undefined &&
      this.backendApiKey !== undefined
    );
  }
}
