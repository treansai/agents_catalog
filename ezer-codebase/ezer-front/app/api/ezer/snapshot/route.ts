import { getDashboardSnapshot } from "@/lib/ezer-api";

export const dynamic = "force-dynamic";

const NO_STORE_HEADERS = {
  "Cache-Control": "no-store, max-age=0",
} as const;

export async function GET(): Promise<Response> {
  const snapshot = await getDashboardSnapshot();

  return Response.json(snapshot, {
    status: snapshot.health === "ready" ? 200 : 503,
    headers: NO_STORE_HEADERS,
  });
}
