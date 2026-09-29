import { recordingFilename } from "@/lib/voice-capture";

/**
 * Un tour de parole, côté navigateur.
 *
 * Là où la session temps réel tenait le micro et le haut-parleur d'un bout à l'autre, le tour est
 * maintenant explicite : on transcrit ce qui vient d'être dit, Ezer répond en texte, puis on lit sa
 * réponse à voix haute. Les deux appels traversent notre BFF, jamais ElevenLabs directement — la
 * clé d'API reste sur le serveur.
 */

const TRANSCRIBE_URL = "/api/ezer/voice/transcribe";
const SPEAK_URL = "/api/ezer/voice/speak";

/**
 * WAV muet et vide. Safari ne laisse parler qu'un élément déjà démarré par un geste : on l'amorce
 * au clic, bien avant que la réponse d'Ezer n'existe.
 */
const SILENCE =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=";

export class VoiceSessionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "VoiceSessionError";
  }
}

async function readMessage(response: Response, fallback: string): Promise<string> {
  const payload: unknown = await response.json().catch(() => null);
  if (payload !== null && typeof payload === "object" && "message" in payload) {
    const { message } = payload as { message?: unknown };
    if (typeof message === "string" && message !== "") return message;
  }
  return fallback;
}

/** Transcrit l'enregistrement. Une chaîne vide signifie que rien d'intelligible n'a été capté. */
export async function transcribeRecording(audio: Blob): Promise<string> {
  const form = new FormData();
  form.append("audio", audio, recordingFilename(audio));

  let response: Response;
  try {
    response = await fetch(TRANSCRIBE_URL, { method: "POST", body: form });
  } catch {
    throw new VoiceSessionError("Ezer est injoignable. Vérifiez que le service tourne.");
  }
  if (!response.ok) {
    throw new VoiceSessionError(await readMessage(response, "La transcription n’a pas abouti."));
  }

  const payload = (await response.json()) as { text?: unknown };
  return typeof payload.text === "string" ? payload.text.trim() : "";
}

export interface SpeechPlayer {
  /** Lit le texte à voix haute et résout à la fin — ou dès qu'on coupe la parole. */
  speak: (text: string) => Promise<void>;
  /** Interrompt la lecture en cours et libère l'audio retenu. */
  stop: () => void;
}

/**
 * Crée le lecteur d'un tour. À appeler pendant le clic de l'utilisateur : l'élément audio hérite
 * ainsi de son autorisation de lecture, que la réponse d'Ezer arrive une seconde ou dix plus tard.
 */
export function createSpeechPlayer(): SpeechPlayer {
  const element = new Audio(SILENCE);
  void element.play().catch(() => undefined);

  let objectUrl: string | null = null;
  let end: (() => void) | null = null;

  const release = () => {
    if (objectUrl !== null) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    }
  };

  return {
    speak: async (text) => {
      let response: Response;
      try {
        response = await fetch(SPEAK_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
      } catch {
        throw new VoiceSessionError("Ezer est injoignable. Vérifiez que le service tourne.");
      }
      if (!response.ok) {
        throw new VoiceSessionError(await readMessage(response, "Ezer n’a pas pu parler."));
      }

      const audio = await response.blob();
      release();
      objectUrl = URL.createObjectURL(audio);
      element.src = objectUrl;

      await new Promise<void>((resolve) => {
        const done = () => {
          element.removeEventListener("ended", done);
          element.removeEventListener("error", done);
          end = null;
          resolve();
        };
        // Retenu pour que `stop` puisse rendre la main tout de suite : une pause ne produit
        // aucun événement de fin, et le tour resterait suspendu.
        end = done;
        element.addEventListener("ended", done);
        element.addEventListener("error", done);
        void element.play().catch(done);
      });

      release();
    },
    stop: () => {
      element.pause();
      end?.();
      release();
    },
  };
}
