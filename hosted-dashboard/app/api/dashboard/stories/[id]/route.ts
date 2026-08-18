import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { readDetail, readSnapshot, requestDetail } from "@/lib/dashboard/store.ts";

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  const { id } = await context.params;
  const source = runtimeEnv();
  const stored = await readSnapshot(source.DB);
  const current = stored?.snapshot.stories.find((story) => String(story.id) === id) || null;
  const detail = current || await readDetail(source.DB, "story", id);
  if (detail) return noStore(NextResponse.json({ ok: true, story: detail }));
  if (!stored?.bridgeConnected) {
    return noStore(NextResponse.json({ ok: false, error: "story_detail_unavailable_offline" }, { status: 503 }));
  }
  const queued = await requestDetail(source.DB, {
    resourceType: "story",
    resourceId: id,
    requestedBy: authorization.session.actorId,
  });
  return noStore(NextResponse.json({ ok: true, pending: true, request: queued }, { status: 202 }));
}
