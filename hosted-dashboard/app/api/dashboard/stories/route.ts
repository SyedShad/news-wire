import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { listStoryPage } from "@/lib/dashboard/store.ts";

export async function GET(request: Request) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  const query = new URL(request.url).searchParams;
  const page = await listStoryPage(runtimeEnv().DB, {
    status: query.get("status") || "all",
    lane: query.get("lane") || "all",
    kind: query.get("kind") || "all",
    window: query.get("window") || "review_now",
    sort: query.get("sort") || "priority",
    cursor: query.get("cursor") || "",
    pageSize: Number(query.get("page_size") || 25),
  });
  return noStore(NextResponse.json({ ok: true, ...page }));
}
