import {
  ElevenLabsError,
  MAX_SPEECH_CHARS,
  SPEECH_CONTENT_TYPE,
  isElevenLabsConfigured,
  synthesizeSpeech,
} from "@/lib/elevenlabs";

export const dynamic = "force-dynamic";

const MAX_REQUEST_BODY_BYTES = 64 * 1024;
const NO_STORE_HEADERS = {
  "Cache-Control": "no-store, max-age=0",
} as const;

class RequestBodyError extends Error {
  constructor(readonly status: 400 | 413 | 415) {
    super("Invalid speech request");
    this.name = "RequestBodyError";
  }
}

function errorResponse(status: number, message: string): Response {
  return Response.json({ message }, { status, headers: NO_STORE_HEADERS });
}

async function readText(request: Request): Promise<string> {
  const contentType = request.headers
    .get("content-type")
    ?.split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (contentType !== "application/json") {
    throw new RequestBodyError(415);
  }

  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    if (!/^\d+$/.test(declaredLength)) throw new RequestBodyError(400);
    if (Number(declaredLength) > MAX_REQUEST_BODY_BYTES) throw new RequestBodyError(413);
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    throw new RequestBodyError(400);
  }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new RequestBodyError(400);
  }

  const { text } = payload as Record<string, unknown>;
  if (typeof text !== "string") throw new RequestBodyError(400);
  const spoken = text.trim();
  if (spoken === "") throw new RequestBodyError(400);
  if (spoken.length > MAX_SPEECH_CHARS) throw new RequestBodyError(413);
  return spoken;
}

/**
 * Lit un texte à voix haute. Le flux d'ElevenLabs est relayé sans être accumulé ici, et la clé
 * d'API reste sur le serveur : le navigateur ne reçoit que des octets audio.
 */
export async function POST(request: Request): Promise<Response> {
  if (!isElevenLabsConfigured()) {
    return errorResponse(503, "La voix n’est pas configurée — renseignez ELEVENLABS_API_KEY.");
  }

  try {
    const audio = await synthesizeSpeech(await readText(request));
    return new Response(audio, {
      headers: { ...NO_STORE_HEADERS, "Content-Type": SPEECH_CONTENT_TYPE },
    });
  } catch (error) {
    if (error instanceof RequestBodyError) {
      return errorResponse(
        error.status,
        error.status === 413
          ? "La réponse à lire est trop longue."
          : error.status === 415
            ? "Le corps de la requête doit être au format JSON."
            : "La demande de synthèse est invalide.",
      );
    }
    if (error instanceof ElevenLabsError) {
      if (error.status === 401) {
        return errorResponse(503, "La clé ElevenLabs ne permet pas la synthèse vocale.");
      }
      if (error.status === 402) {
        return errorResponse(503, "Le crédit ElevenLabs est épuisé.");
      }
      if (error.status === 429) {
        return errorResponse(429, "Trop de demandes vocales. Réessayez dans un instant.");
      }
      return errorResponse(502, "La synthèse vocale n’a pas abouti.");
    }
    return errorResponse(500, "Une erreur inattendue est survenue.");
  }
}
