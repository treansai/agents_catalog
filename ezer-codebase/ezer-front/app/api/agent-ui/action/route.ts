import { AgentUiBffError, assertWorkspaceId, fetchAction } from "@/lib/agent-ui/bff";
import { getRegisteredComponent } from "@/lib/agent-ui/registry";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store, max-age=0" } as const;
const MAX_BYTES = 16 * 1024;
const ID = /^[A-Za-z0-9._:-]{1,128}$/;

export async function POST(request: Request): Promise<Response> {
  try {
    const workspaceId = assertWorkspaceId(new URL(request.url).searchParams.get("workspace_id"));
    const raw = await request.text();
    if (raw.length > MAX_BYTES) {
      return Response.json({ message: "payload trop volumineux" }, { status: 413, headers: NO_STORE });
    }
    const body = JSON.parse(raw) as Record<string, unknown>;
    if (body.kind !== "ui.action" || typeof body.actionId !== "string" || !ID.test(body.actionId)) {
      return Response.json({ message: "action invalide" }, { status: 400, headers: NO_STORE });
    }
    if (typeof body.componentId !== "string" || !ID.test(body.componentId)) {
      return Response.json({ message: "composant invalide" }, { status: 400, headers: NO_STORE });
    }
    const component = getRegisteredComponent(body.componentId);
    if (component === undefined || !component.allowedActions.includes(body.actionId)) {
      return Response.json({ message: "action non autorisée" }, { status: 400, headers: NO_STORE });
    }
    if (typeof body.idempotencyKey !== "string" || !ID.test(body.idempotencyKey)) {
      return Response.json({ message: "idempotence invalide" }, { status: 400, headers: NO_STORE });
    }
    return Response.json(await fetchAction(workspaceId, body), { headers: NO_STORE });
  } catch (error) {
    const status = error instanceof AgentUiBffError ? error.status : 500;
    return Response.json({ message: "action indisponible" }, { status, headers: NO_STORE });
  }
}
