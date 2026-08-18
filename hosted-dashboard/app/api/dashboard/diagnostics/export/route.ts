import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { readSnapshot } from "@/lib/dashboard/store.ts";

export async function GET(request: Request) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  const stored = await readSnapshot(runtimeEnv().DB);
  const body = JSON.stringify({
    schema_version: 1,
    exported_at: new Date().toISOString(),
    synchronized_at: stored?.receivedAt || null,
    bridge: { version: stored?.bridgeVersion || null, last_seen_at: stored?.bridgeLastSeenAt || null, error: stored?.bridgeLastError || null },
    diagnostics: stored?.snapshot.diagnostics || [],
  }, null, 2);
  return noStore(new NextResponse(body, { headers: { "content-type": "application/json; charset=utf-8", "content-disposition": "attachment; filename=news-wire-diagnostics.json" } }));
}
