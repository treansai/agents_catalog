import { ElevenLabsError, isElevenLabsConfigured, transcribeSpeech } from "@/lib/elevenlabs";
import { VoiceApiError, interpretVoiceCommand, isVoiceConfigured } from "@/lib/openai-voice";
import { AudioUploadError, readAudioForm, uploadFilename } from "@/lib/voice-upload";

export const dynamic = "force-dynamic";

const MAX_ACCOUNTS = 100;
const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const NO_STORE_HEADERS = {
  "Cache-Control": "no-store, max-age=0",
} as const;

function errorResponse(status: number, message: string): Response {
  return Response.json({ message }, { status, headers: NO_STORE_HEADERS });
}

/** Les comptes voyagent à côté de l'audio : ils bornent ce que le modèle peut nommer. */
function readAccountIds(form: FormData): string[] {
  const accountIds = form.getAll("account_ids");
  if (accountIds.length > MAX_ACCOUNTS) {
    throw new AudioUploadError(400);
  }
  return accountIds.map((accountId) => {
    if (typeof accountId !== "string" || !ACCOUNT_ID.test(accountId)) {
      throw new AudioUploadError(400);
    }
    return accountId;
  });
}

/**
 * Ordre vocal du tableau de bord, en deux temps : ElevenLabs transcrit, puis un modèle de texte
 * réduit la phrase à une intention. Aucune des deux clés ne quitte le serveur.
 */
export async function POST(request: Request): Promise<Response> {
  if (!isElevenLabsConfigured()) {
    return errorResponse(
      503,
      "La commande vocale n’est pas configurée — renseignez ELEVENLABS_API_KEY.",
    );
  }
  if (!isVoiceConfigured()) {
    return errorResponse(
      503,
      "La commande vocale n’est pas configurée — renseignez OPENAI_API_KEY.",
    );
  }

  try {
    const { audio, form } = await readAudioForm(request);
    const accountIds = readAccountIds(form);
    const transcript = await transcribeSpeech(audio, uploadFilename(audio));
    if (transcript === "") {
      return errorResponse(422, "Rien n’a été entendu.");
    }
    const result = await interpretVoiceCommand(transcript, accountIds);
    return Response.json(result, { headers: NO_STORE_HEADERS });
  } catch (error) {
    if (error instanceof AudioUploadError) {
      return errorResponse(
        error.status,
        error.status === 413
          ? "L’enregistrement est trop long."
          : error.status === 415
            ? "L’enregistrement doit être envoyé en multipart."
            : "La commande vocale est invalide.",
      );
    }

    if (error instanceof ElevenLabsError) {
      if (error.status === 401) {
        return errorResponse(503, "La clé ElevenLabs ne permet pas la transcription.");
      }
      if (error.status === 402) {
        return errorResponse(503, "Le crédit ElevenLabs est épuisé.");
      }
      if (error.status === 429) {
        return errorResponse(429, "Trop de commandes vocales. Réessayez dans un instant.");
      }
      return errorResponse(502, "La transcription n’a pas abouti.");
    }

    if (error instanceof VoiceApiError) {
      if (error.status === 429) {
        return errorResponse(429, "Trop de commandes vocales. Réessayez dans un instant.");
      }
      return errorResponse(502, "La commande vocale n’a pas pu être interprétée.");
    }

    return errorResponse(500, "Une erreur inattendue est survenue.");
  }
}
