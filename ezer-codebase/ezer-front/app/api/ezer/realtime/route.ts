import { RealtimeError, createRealtimeSession } from "@/lib/openai-realtime";

export const dynamic = "force-dynamic";

const NO_STORE_HEADERS = {
  "Cache-Control": "no-store, max-age=0",
} as const;

function errorResponse(status: number, message: string): Response {
  return Response.json({ message }, { status, headers: NO_STORE_HEADERS });
}

/** Délivre un secret éphémère au navigateur pour qu'il ouvre sa session vocale. */
export async function POST(): Promise<Response> {
  try {
    return Response.json(await createRealtimeSession(), { headers: NO_STORE_HEADERS });
  } catch (error) {
    if (error instanceof RealtimeError) {
      if (error.status === 503) {
        return errorResponse(503, "La voix n’est pas configurée sur ce serveur.");
      }
      if (error.status === 429) {
        return errorResponse(429, "Trop de sessions vocales. Réessayez dans un instant.");
      }
      return errorResponse(502, "La session vocale n’a pas pu être ouverte.");
    }
    return errorResponse(500, "Une erreur inattendue est survenue.");
  }
}
