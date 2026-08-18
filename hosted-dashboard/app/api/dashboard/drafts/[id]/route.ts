import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { readDetail, readSnapshot, requestDetail } from "@/lib/dashboard/store.ts";

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  const { id } = await context.params;
  if (!/^\d+$/u.test(id)) return noStore(NextResponse.json({ error: "draft_not_found" }, { status: 404 }));
  const source = runtimeEnv();
  const detail = await readDetail(source.DB, "draft", id);
  if (detail) return noStore(NextResponse.json({ ok: true, draft: detail }));
  const stored = await readSnapshot(source.DB);
  if (!stored?.bridgeConnected) {
    return noStore(NextResponse.json({ ok: false, error: "draft_detail_unavailable_offline" }, { status: 503 }));
  }
  const queued = await requestDetail(source.DB, {
    resourceType: "draft",
    resourceId: id,
    requestedBy: authorization.session.actorId,
  });
  return noStore(NextResponse.json({ ok: true, pending: true, request: queued }, { status: 202 }));
}
