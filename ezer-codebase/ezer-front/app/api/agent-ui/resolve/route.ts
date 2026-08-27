import { AgentUiBffError, assertWorkspaceId, fetchResolve } from "@/lib/agent-ui/bff";
import { getRegisteredComponent } from "@/lib/agent-ui/registry";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store, max-age=0" } as const;
const MAX_BYTES = 32 * 1024;

export async function POST(request: Request): Promise<Response> {
  try {
    const workspaceId = assertWorkspaceId(new URL(request.url).searchParams.get("workspace_id"));
    const raw = await request.text();
    if (raw.length > MAX_BYTES) {
      return Response.json({ message: "payload trop volumineux" }, { status: 413, headers: NO_STORE });
    }
    const body = JSON.parse(raw) as {
      resolverId?: string;
      input?: Record<string, unknown>;
      componentId?: string;
      instanceId?: string;
    };
    if (
      typeof body.resolverId !== "string" ||
      typeof body.componentId !== "string" ||
      typeof body.instanceId !== "string" ||
      typeof body.input !== "object" ||
      body.input === null
    ) {
      return Response.json({ message: "demande invalide" }, { status: 400, headers: NO_STORE });
    }
    const component = getRegisteredComponent(body.componentId);
    if (component === undefined || !component.allowedDataResolvers.includes(body.resolverId)) {
      return Response.json({ message: "résolveur non autorisé" }, { status: 400, headers: NO_STORE });
    }
    const input = { ...body.input };
    delete input.userId;
    delete input.workspaceId;
    delete input.account_id;
    delete input.permissions;
    return Response.json(
      await fetchResolve(workspaceId, {
        resolverId: body.resolverId,
        input,
        componentId: body.componentId,
        instanceId: body.instanceId,
      }),
      { headers: NO_STORE },
    );
  } catch (error) {
    const status = error instanceof AgentUiBffError ? error.status : 500;
    return Response.json({ message: "résolveur indisponible" }, { status, headers: NO_STORE });
  }
}
