import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { readDetail, readSnapshot, requestDetail } from "@/lib/dashboard/store.ts";

export async function GET(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const authorization = await authorizeApiRequest(request);
  if (authorization.response) return authorization.response;
  const { id } = await context.params;
  const source = runtimeEnv();
  const stored = await readSnapshot(source.DB);
  const story = stored?.snapshot.stories.find((item) => String(item.id) === id)
    || await readDetail(source.DB, "story", id);
  if (!story) {
    if (stored?.bridgeConnected) {
      await requestDetail(source.DB, {
        resourceType: "story", resourceId: id, requestedBy: authorization.session.actorId,
      });
      return noStore(NextResponse.json({ error: "story_detail_pending" }, { status: 202 }));
    }
    return noStore(NextResponse.json({ error: "story_not_found" }, { status: 404 }));
  }
  const payload = {
    schema_version: 1,
    exported_at: new Date().toISOString(),
    story,
  };
  return noStore(NextResponse.json(payload, {
    headers: {
      "content-disposition": `attachment; filename="news-wire-evidence-${encodeURIComponent(id)}.json"`,
    },
  }));
}
