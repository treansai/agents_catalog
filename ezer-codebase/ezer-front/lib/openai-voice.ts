import "server-only";

import type { VoiceAction, VoiceCommandResponse, VoiceIntent } from "@/lib/ezer-types";

const OPENAI_URL = "https://api.openai.com/v1/chat/completions";
const AUDIO_MODEL = "gpt-audio-1.5";
const REQUEST_TIMEOUT_MS = 30_000;
const MAX_SEARCH_LENGTH = 120;
const MAX_REPLY_LENGTH = 240;
const MAX_TRANSCRIPT_LENGTH = 2_000;

const ACTIONS: readonly VoiceAction[] = [
  "sync",
  "set_view_filter",
  "set_account_filter",
  "set_search",
  "refresh",
  "none",
];
const VIEW_FILTERS = ["all", "focus", "actions", "risk"] as const;
const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;

export class VoiceApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "VoiceApiError";
  }
}

/** Absent key means the voice feature is simply off, exactly like EZER_API_URL for demo mode. */
export function isVoiceConfigured(): boolean {
  return (process.env.OPENAI_API_KEY?.trim().length ?? 0) > 0;
}

function instructions(accountIds: string[]): string {
  const accounts = accountIds.length > 0 ? accountIds.join(", ") : "aucun";
  return [
    "Tu pilotes le tableau de bord Ezer, qui trie des e-mails analysés.",
    "Écoute l'ordre vocal de l'utilisateur et renvoie UNIQUEMENT un objet JSON, sans texte autour.",
    "",
    "Schéma exact :",
    '{"action": "sync"|"set_view_filter"|"set_account_filter"|"set_search"|"refresh"|"none",',
    ' "account_id": string|null, "view_filter": "all"|"focus"|"actions"|"risk"|null,',
    ' "search": string|null, "reply": string}',
    "",
    "Sémantique des actions :",
    '- sync : lancer une synchronisation. account_id = "all" ou un identifiant de compte.',
    "- set_view_filter : changer la vue. all = tous, focus = à regarder, actions = avec actions, risk = à risque.",
    '- set_account_filter : filtrer par compte. account_id = "all" ou un identifiant de compte.',
    "- set_search : filtrer par texte libre. search = les termes entendus.",
    "- refresh : recharger les données sans synchroniser.",
    "- none : l'ordre est incompris ou hors périmètre.",
    "",
    `Comptes disponibles : ${accounts}.`,
    "Mets à null tout champ inutile pour l'action choisie.",
    "reply : une phrase courte en français confirmant ce que tu as compris.",
  ].join("\n");
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Expected an object");
  }
  return value as Record<string, unknown>;
}

function boundedString(value: unknown, maximum: number): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length === 0 ? null : trimmed.slice(0, maximum);
}

/** The model is untrusted input: every field is narrowed before it can reach the dashboard. */
function parseIntent(raw: string): VoiceIntent {
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start === -1 || end <= start) {
    throw new VoiceApiError("Voice model returned no intent");
  }

  let payload: unknown;
  try {
    payload = JSON.parse(raw.slice(start, end + 1));
  } catch {
    throw new VoiceApiError("Voice model returned invalid JSON");
  }

  const candidate = asRecord(payload);
  const action = ACTIONS.includes(candidate.action as VoiceAction)
    ? (candidate.action as VoiceAction)
    : "none";

  const accountId = boundedString(candidate.account_id, 128);
  const viewFilter = VIEW_FILTERS.includes(
    candidate.view_filter as (typeof VIEW_FILTERS)[number],
  )
    ? (candidate.view_filter as VoiceIntent["view_filter"])
    : null;

  return {
    action,
    account_id:
      accountId !== null && (accountId === "all" || ACCOUNT_ID.test(accountId))
        ? accountId
        : null,
    view_filter: viewFilter,
    search: boundedString(candidate.search, MAX_SEARCH_LENGTH),
    reply: boundedString(candidate.reply, MAX_REPLY_LENGTH) ?? "Commande reçue.",
  };
}

/**
 * Sends one push-to-talk recording to the audio model and narrows its answer into
 * a dashboard command. The API key never leaves the server.
 */
export async function interpretVoiceCommand(
  wavBase64: string,
  accountIds: string[],
): Promise<VoiceCommandResponse> {
  const apiKey = process.env.OPENAI_API_KEY?.trim();
  if (!apiKey) {
    throw new VoiceApiError("Voice commands are not configured");
  }

  let response: Response;
  try {
    response = await fetch(OPENAI_URL, {
      method: "POST",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: AUDIO_MODEL,
        modalities: ["text"],
        messages: [
          { role: "system", content: instructions(accountIds) },
          {
            role: "user",
            content: [
              { type: "text", text: "Transcris cet ordre et renvoie le JSON demandé." },
              { type: "input_audio", input_audio: { data: wavBase64, format: "wav" } },
            ],
          },
        ],
      }),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    throw new VoiceApiError("Voice model is unreachable");
  }

  if (!response.ok) {
    throw new VoiceApiError("Voice model request failed", response.status);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new VoiceApiError("Voice model returned invalid JSON", response.status);
  }

  const choices = asRecord(payload).choices;
  if (!Array.isArray(choices) || choices.length === 0) {
    throw new VoiceApiError("Voice model returned no choice", response.status);
  }
  const message = asRecord(asRecord(choices[0]).message);
  const audio = message.audio === undefined ? null : asRecord(message.audio);
  const text =
    boundedString(message.content, MAX_TRANSCRIPT_LENGTH) ??
    boundedString(audio?.transcript, MAX_TRANSCRIPT_LENGTH);

  if (text === null) {
    throw new VoiceApiError("Voice model returned an empty answer", response.status);
  }

  const intent = parseIntent(text);
  return { transcript: intent.reply, intent };
}
