import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { isSameOriginRequest } from "@/lib/auth/http.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { readPushStatus, setPushRuntimeMode } from "@/lib/push/store.ts";
import { parsePushRuntimeAction } from "@/lib/push/validation.ts";

export async function GET(request: Request) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  return noStore(NextResponse.json(await readPushStatus(runtimeEnv())));
}

export async function POST(request: Request) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  const source = runtimeEnv();
  if (!isSameOriginRequest(request, source)) {
    return noStore(NextResponse.json({ ok: false, error: "same_origin_required" }, { status: 403 }));
  }
  try {
    const { action } = parsePushRuntimeAction(await request.json());
    const mode = action === "activate" ? "active" : action === "pause" ? "paused" : "shadow";
    const status = await setPushRuntimeMode(source, mode, authorization.session.actorId);
    return noStore(NextResponse.json({ ok: true, ...status }));
  } catch {
    return noStore(NextResponse.json({ ok: false, error: "push_runtime_action_invalid" }, { status: 400 }));
  }
}
