import "server-only";

/**
 * L'oreille et la bouche d'Ezer, fournies par ElevenLabs.
 *
 * ElevenLabs ne fait que transcrire et synthétiser : le raisonnement reste à Ezer, qui répond en
 * texte. Un tour de parole passe donc par trois étapes explicites — transcription, réponse, lecture
 * — là où une session temps réel les fondait en une seule. `ELEVENLABS_API_KEY` ne quitte jamais le
 * serveur : le navigateur envoie des octets audio et en reçoit, sans jamais voir de jeton.
 */

const SPEECH_TO_TEXT_URL = "https://api.elevenlabs.io/v1/speech-to-text";
const TEXT_TO_SPEECH_URL = "https://api.elevenlabs.io/v1/text-to-speech";

/** Rachel, la voix par défaut du catalogue public d'ElevenLabs. */
const DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM";
/** Flash est le modèle le moins latent : en conversation, l'attente pèse plus lourd que le grain. */
const DEFAULT_TTS_MODEL = "eleven_flash_v2_5";
/** Scribe v1 est disponible sur toutes les formules ; `scribe_v2` s'active par variable. */
const DEFAULT_STT_MODEL = "scribe_v1";
/** Ezer parle français : l'annoncer évite que Scribe se trompe de langue sur une phrase courte. */
const DEFAULT_STT_LANGUAGE = "fra";
/** 64 kbit/s : la parole y est intacte et le premier octet arrive plus tôt qu'en 128. */
const OUTPUT_FORMAT = "mp3_44100_64";

export const SPEECH_CONTENT_TYPE = "audio/mpeg";
/** Une réponse d'Ezer tient largement dedans ; au-delà, c'est une anomalie, pas une phrase. */
export const MAX_SPEECH_CHARS = 5_000;
const MAX_TRANSCRIPT_CHARS = 5_000;

const MODEL_ID = /^[A-Za-z0-9._-]{1,128}$/;
const VOICE_ID = /^[A-Za-z0-9]{1,64}$/;
const LANGUAGE_CODE = /^[a-z]{2,3}$/;

const TRANSCRIBE_TIMEOUT_MS = 30_000;
const SPEAK_TIMEOUT_MS = 30_000;

export class ElevenLabsError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ElevenLabsError";
  }
}

export function isElevenLabsConfigured(): boolean {
  return (process.env.ELEVENLABS_API_KEY?.trim().length ?? 0) > 0;
}

function apiKey(): string {
  const key = process.env.ELEVENLABS_API_KEY?.trim();
  if (!key) {
    throw new ElevenLabsError("ElevenLabs voice is not configured", 503);
  }
  return key;
}

/** Une variable mal formée est une erreur de déploiement : elle échoue ici, pas chez ElevenLabs. */
function configured(name: string, fallback: string, shape: RegExp): string {
  const value = process.env[name]?.trim() || fallback;
  if (!shape.test(value)) {
    throw new ElevenLabsError(`${name} must be a valid identifier`);
  }
  return value;
}

/** Vide = détection automatique, utile si l'utilisateur mêle plusieurs langues. */
function transcriptionLanguage(): string | null {
  const raw = process.env.ELEVENLABS_STT_LANGUAGE?.trim();
  if (raw === "") return null;
  const language = raw || DEFAULT_STT_LANGUAGE;
  if (!LANGUAGE_CODE.test(language)) {
    throw new ElevenLabsError("ELEVENLABS_STT_LANGUAGE must be an ISO-639 code");
  }
  return language;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ElevenLabsError("Unexpected ElevenLabs payload");
  }
  return value as Record<string, unknown>;
}

/**
 * Transcrit un enregistrement. Le blob arrive tel que le navigateur l'a produit — webm/opus le plus
 * souvent, mp4 sur Safari — car Scribe accepte les formats courants sans réencodage préalable.
 */
export async function transcribeSpeech(audio: Blob, filename = "speech.webm"): Promise<string> {
  const key = apiKey();
  const form = new FormData();
  form.append("model_id", configured("ELEVENLABS_STT_MODEL", DEFAULT_STT_MODEL, MODEL_ID));
  form.append("file", audio, filename);
  const language = transcriptionLanguage();
  if (language !== null) {
    form.append("language_code", language);
  }

  let response: Response;
  try {
    response = await fetch(SPEECH_TO_TEXT_URL, {
      method: "POST",
      cache: "no-store",
      headers: { "xi-api-key": key, Accept: "application/json" },
      body: form,
      signal: AbortSignal.timeout(TRANSCRIBE_TIMEOUT_MS),
    });
  } catch {
    throw new ElevenLabsError("Speech-to-text is unreachable");
  }
  if (!response.ok) {
    throw new ElevenLabsError("Speech-to-text request failed", response.status);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ElevenLabsError("Speech-to-text returned invalid JSON", response.status);
  }

  const { text } = asRecord(payload);
  if (typeof text !== "string") {
    throw new ElevenLabsError("Speech-to-text returned no transcript", response.status);
  }
  // Un enregistrement silencieux transcrit en chaîne vide : l'appelant décidera quoi en dire.
  return text.trim().slice(0, MAX_TRANSCRIPT_CHARS);
}

/**
 * Synthétise une réponse et renvoie le flux tel quel : les octets traversent le BFF sans y être
 * accumulés, ce qui laisse la mémoire du serveur indépendante de la longueur de la réponse.
 */
export async function synthesizeSpeech(text: string): Promise<ReadableStream<Uint8Array>> {
  const key = apiKey();
  const spoken = text.trim();
  if (spoken === "") {
    throw new ElevenLabsError("Nothing to speak");
  }

  const voiceId = configured("ELEVENLABS_VOICE_ID", DEFAULT_VOICE_ID, VOICE_ID);
  const url = `${TEXT_TO_SPEECH_URL}/${voiceId}/stream?output_format=${OUTPUT_FORMAT}`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      cache: "no-store",
      headers: { "xi-api-key": key, "Content-Type": "application/json" },
      body: JSON.stringify({
        text: spoken.slice(0, MAX_SPEECH_CHARS),
        model_id: configured("ELEVENLABS_TTS_MODEL", DEFAULT_TTS_MODEL, MODEL_ID),
      }),
      signal: AbortSignal.timeout(SPEAK_TIMEOUT_MS),
    });
  } catch {
    throw new ElevenLabsError("Text-to-speech is unreachable");
  }
  if (!response.ok) {
    throw new ElevenLabsError("Text-to-speech request failed", response.status);
  }
  if (response.body === null) {
    throw new ElevenLabsError("Text-to-speech returned no audio", response.status);
  }
  return response.body;
}
