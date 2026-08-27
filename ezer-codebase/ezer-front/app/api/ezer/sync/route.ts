import { EzerApiError, syncMailboxes } from "@/lib/ezer-api";
import type { SyncRequest } from "@/lib/ezer-types";

export const dynamic = "force-dynamic";

const MAX_REQUEST_BODY_BYTES = 64 * 1024;
const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const NO_STORE_HEADERS = {
  "Cache-Control": "no-store, max-age=0",
} as const;

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

function validateSyncRequest(payload: unknown): SyncRequest {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new RequestBodyError(400);
  }

  const body = payload as Record<string, unknown>;
  if (Object.keys(body).some((key) => key !== "account_ids" && key !== "limit")) {
    throw new RequestBodyError(400);
  }

  const result: SyncRequest = {};

  if (body.account_ids !== undefined) {
    if (
      !Array.isArray(body.account_ids) ||
      body.account_ids.length < 1 ||
      body.account_ids.length > 100 ||
      !body.account_ids.every(
        (accountId) => typeof accountId === "string" && ACCOUNT_ID.test(accountId),
      ) ||
      new Set(body.account_ids).size !== body.account_ids.length
    ) {
      throw new RequestBodyError(400);
    }
    result.account_ids = body.account_ids as string[];
  }

  if (body.limit !== undefined) {
    if (
      typeof body.limit !== "number" ||
      !Number.isSafeInteger(body.limit) ||
      body.limit < 1 ||
      body.limit > 500
    ) {
      throw new RequestBodyError(400);
    }
    result.limit = body.limit;
  }

  return result;
}

async function parseRequest(request: Request): Promise<SyncRequest> {
  const rawBody = await readBoundedBody(request);
  if (rawBody.trim() === "") {
    return {};
  }

  const contentType = request.headers
    .get("content-type")
    ?.split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (contentType !== "application/json") {
    throw new RequestBodyError(415);
  }

  let payload: unknown;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    throw new RequestBodyError(400);
  }

  return validateSyncRequest(payload);
}

function errorResponse(status: number, message: string): Response {
  return Response.json(
    { message },
    { status, headers: NO_STORE_HEADERS },
  );
}

export async function POST(request: Request): Promise<Response> {
  try {
    const payload = await parseRequest(request);
    const response = await syncMailboxes(payload);
    return Response.json(response, { headers: NO_STORE_HEADERS });
  } catch (error) {
    if (error instanceof RequestBodyError) {
      return errorResponse(
        error.status,
        error.status === 413
          ? "Le corps de la requête est trop volumineux."
          : error.status === 415
            ? "Le corps de la requête doit être au format JSON."
          : "La demande de synchronisation est invalide.",
      );
    }

    if (error instanceof EzerApiError) {
      if (error.status === 422) {
        return errorResponse(422, "La synchronisation a été refusée par Ezer.");
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
