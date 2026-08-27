import { AgentUiBffError, assertWorkspaceId, fetchCatalog } from "@/lib/agent-ui/bff";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store, max-age=0" } as const;

export async function GET(request: Request): Promise<Response> {
  try {
    const workspaceId = assertWorkspaceId(new URL(request.url).searchParams.get("workspace_id"));
    return Response.json(await fetchCatalog(workspaceId), { headers: NO_STORE });
  } catch (error) {
    const status = error instanceof AgentUiBffError ? error.status : 500;
    return Response.json(
      { message: "Le catalogue n'est pas disponible." },
      { status: status === 204 ? 200 : status, headers: NO_STORE },
    );
  }
}
