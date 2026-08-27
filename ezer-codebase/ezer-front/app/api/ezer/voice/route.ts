import { VoiceApiError, interpretVoiceCommand, isVoiceConfigured } from "@/lib/openai-voice";

export const dynamic = "force-dynamic";

// Audio is orders of magnitude larger than the JSON the sync route carries:
// ~15 s of 16 kHz mono PCM is about 480 KB of WAV, roughly 640 KB once base64-encoded.
const MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024;
const MAX_ACCOUNTS = 100;
const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/;
const NO_STORE_HEADERS = {
  "Cache-Control": "no-store, max-age=0",
} as const;

interface VoiceRequest {
  audio: string;
  account_ids: string[];
}

class RequestBodyError extends Error {
  constructor(readonly status: 400 | 413 | 415) {
    super(
      status === 413
        ? "Request body too large"
        : status === 415
          ? "Unsupported media type"
          : "Invalid request body",
    );
  }
}

async function readBoundedBody(request: Request): Promise<string> {
  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    if (!/^\d+$/.test(declaredLength)) {
      throw new RequestBodyError(400);
    }
    if (Number(declaredLength) > MAX_REQUEST_BODY_BYTES) {
      throw new RequestBodyError(413);
    }
  }

  if (request.body === null) {
    return "";
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    received += value.byteLength;
    if (received > MAX_REQUEST_BODY_BYTES) {
      await reader.cancel();
      throw new RequestBodyError(413);
    }
    chunks.push(value);
  }

  const body = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }

  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(body);
  } catch {
    throw new RequestBodyError(400);
  }
}

function validateVoiceRequest(payload: unknown): VoiceRequest {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new RequestBodyError(400);
  }

  const body = payload as Record<string, unknown>;
  if (Object.keys(body).some((key) => key !== "audio" && key !== "account_ids")) {
    throw new RequestBodyError(400);
  }

  if (
    typeof body.audio !== "string" ||
    body.audio.length === 0 ||
    body.audio.length > MAX_REQUEST_BODY_BYTES ||
    !BASE64.test(body.audio)
  ) {
    throw new RequestBodyError(400);
  }

  let accountIds: string[] = [];
  if (body.account_ids !== undefined) {
    if (
      !Array.isArray(body.account_ids) ||
      body.account_ids.length > MAX_ACCOUNTS ||
      !body.account_ids.every(
        (accountId) => typeof accountId === "string" && ACCOUNT_ID.test(accountId),
      )
    ) {
      throw new RequestBodyError(400);
    }
    accountIds = body.account_ids as string[];
  }

  return { audio: body.audio, account_ids: accountIds };
}

async function parseRequest(request: Request): Promise<VoiceRequest> {
  const contentType = request.headers
    .get("content-type")
    ?.split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (contentType !== "application/json") {
    throw new RequestBodyError(415);
  }

  const rawBody = await readBoundedBody(request);
  let payload: unknown;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    throw new RequestBodyError(400);
  }

  return validateVoiceRequest(payload);
}

function errorResponse(status: number, message: string): Response {
  return Response.json({ message }, { status, headers: NO_STORE_HEADERS });
}

export async function POST(request: Request): Promise<Response> {
  if (!isVoiceConfigured()) {
    return errorResponse(
      503,
      "La commande vocale n’est pas configurée — renseignez OPENAI_API_KEY.",
    );
  }

  try {
    const payload = await parseRequest(request);
    const result = await interpretVoiceCommand(payload.audio, payload.account_ids);
    return Response.json(result, { headers: NO_STORE_HEADERS });
  } catch (error) {
    if (error instanceof RequestBodyError) {
      return errorResponse(
        error.status,
        error.status === 413
          ? "L’enregistrement est trop long."
          : error.status === 415
            ? "Le corps de la requête doit être au format JSON."
            : "La commande vocale est invalide.",
      );
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
