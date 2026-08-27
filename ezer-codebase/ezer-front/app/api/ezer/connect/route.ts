import {
  EzerConnectError,
  endConnection,
  listConnectableMailboxes,
  readConnection,
  startConnection,
} from "@/lib/ezer-connect";

export const dynamic = "force-dynamic";

const ACCOUNT_ID = /^[A-Za-z0-9._-]{1,128}$/;
const NO_STORE_HEADERS = {
  "Cache-Control": "no-store, max-age=0",
} as const;

function errorResponse(status: number, message: string): Response {
  return Response.json({ message }, { status, headers: NO_STORE_HEADERS });
}

function failure(error: unknown): Response {
  if (error instanceof EzerConnectError) {
    if (error.status === 400) {
      return errorResponse(400, "Le compte demandé est invalide.");
    }
    if (error.status === 404) {
      return errorResponse(404, "Ce compte n’est pas configuré côté Ezer.");
    }
    if (error.status === 422) {
      return errorResponse(422, "Ezer a refusé la connexion de cette boîte.");
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

function accountIdFrom(request: Request): string | null {
  const accountId = new URL(request.url).searchParams.get("account_id");
  return accountId !== null && ACCOUNT_ID.test(accountId) ? accountId : null;
}

/** Sans `account_id`, liste les boîtes connectables ; avec, renvoie l'état d'une connexion. */
export async function GET(request: Request): Promise<Response> {
  const accountId = accountIdFrom(request);
  try {
    const payload =
      accountId === null
        ? await listConnectableMailboxes()
        : await readConnection(accountId);
    return Response.json(payload, { headers: NO_STORE_HEADERS });
  } catch (error) {
    return failure(error);
  }
}

export async function POST(request: Request): Promise<Response> {
  const accountId = accountIdFrom(request);
  if (accountId === null) {
    return errorResponse(400, "Le compte demandé est invalide.");
  }
  try {
    return Response.json(await startConnection(accountId), { headers: NO_STORE_HEADERS });
  } catch (error) {
    return failure(error);
  }
}

export async function DELETE(request: Request): Promise<Response> {
  const accountId = accountIdFrom(request);
  if (accountId === null) {
    return errorResponse(400, "Le compte demandé est invalide.");
  }
  try {
    return Response.json(await endConnection(accountId), { headers: NO_STORE_HEADERS });
  } catch (error) {
    return failure(error);
  }
}
