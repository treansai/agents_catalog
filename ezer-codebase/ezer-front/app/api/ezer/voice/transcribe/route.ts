import { ElevenLabsError, isElevenLabsConfigured, transcribeSpeech } from "@/lib/elevenlabs";
import { AudioUploadError, readAudioForm, uploadFilename } from "@/lib/voice-upload";

export const dynamic = "force-dynamic";

const NO_STORE_HEADERS = {
  "Cache-Control": "no-store, max-age=0",
} as const;

function errorResponse(status: number, message: string): Response {
  return Response.json({ message }, { status, headers: NO_STORE_HEADERS });
}

/** Transcrit un tour de parole. Le navigateur envoie du son, il reçoit du texte — rien d'autre. */
export async function POST(request: Request): Promise<Response> {
  if (!isElevenLabsConfigured()) {
    return errorResponse(503, "La voix n’est pas configurée — renseignez ELEVENLABS_API_KEY.");
  }

  try {
    const { audio } = await readAudioForm(request);
    return Response.json(
      { text: await transcribeSpeech(audio, uploadFilename(audio)) },
      { headers: NO_STORE_HEADERS },
    );
  } catch (error) {
    if (error instanceof AudioUploadError) {
      return errorResponse(
        error.status,
        error.status === 413
          ? "L’enregistrement est trop long."
          : error.status === 415
            ? "L’enregistrement doit être envoyé en multipart."
            : "L’enregistrement est invalide.",
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
        return errorResponse(429, "Trop de demandes vocales. Réessayez dans un instant.");
      }
      return errorResponse(502, "La transcription n’a pas abouti.");
    }
    return errorResponse(500, "Une erreur inattendue est survenue.");
  }
}
