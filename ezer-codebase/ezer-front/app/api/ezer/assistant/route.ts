import {
  EzerAssistantError,
  askAssistant,
  validateAssistantRequest,
} from "@/lib/ezer-assistant";

export const dynamic = "force-dynamic";

const MAX_REQUEST_BODY_BYTES = 256 * 1024;
const NO_STORE_HEADERS = {
  "Cache-Control": "no-store, max-age=0",
} as const;

function errorResponse(status: number, message: string): Response {
  return Response.json({ message }, { status, headers: NO_STORE_HEADERS });
}

export async function POST(request: Request): Promise<Response> {
  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    if (!/^\d+$/.test(declaredLength)) {
      return errorResponse(400, "La demande envoyée à l’assistant est invalide.");
    }
    if (Number(declaredLength) > MAX_REQUEST_BODY_BYTES) {
      return errorResponse(413, "La conversation est trop volumineuse.");
    }
  }

  const contentType = request.headers
    .get("content-type")
    ?.split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (contentType !== "application/json") {
    return errorResponse(415, "Le corps de la requête doit être au format JSON.");
  }

  let payload: unknown;
  try {
    const raw = await request.text();
    if (raw.length > MAX_REQUEST_BODY_BYTES) {
      return errorResponse(413, "La conversation est trop volumineuse.");
    }
    payload = JSON.parse(raw);
  } catch {
    return errorResponse(400, "La demande envoyée à l’assistant est invalide.");
  }

  let validated: ReturnType<typeof validateAssistantRequest>;
  try {
    validated = validateAssistantRequest(payload);
  } catch {
    return errorResponse(400, "La demande envoyée à l’assistant est invalide.");
  }

  try {
    return Response.json(await askAssistant(validated), { headers: NO_STORE_HEADERS });
  } catch (error) {
    if (error instanceof EzerAssistantError) {
      if (error.status === 404) {
        return errorResponse(404, "Ce compte n’est pas configuré côté Ezer.");
      }
      if (error.status === 422) {
        return errorResponse(
          422,
          "L’assistant n’a pas pu répondre : boîte non connectée, ou assistant non configuré.",
        );
      }
      if (error.status === 429) {
        return errorResponse(429, "Ezer reçoit trop de demandes. Réessayez plus tard.");
      }
      return errorResponse(
        error.status === undefined || error.status === 503 ? 503 : 502,
        "Le service Ezer est temporairement indisponible.",
      );
    }
    return errorResponse(500, "Une erreur inattendue est survenue.");
  }
}
