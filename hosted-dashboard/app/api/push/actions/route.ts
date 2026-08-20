import { NextResponse } from "next/server";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { MAX_PUSH_ACTION_BYTES } from "@/lib/dashboard/validation.ts";
import { isTrustedPushActionRequest } from "@/lib/push/http.ts";
import { performPushAction } from "@/lib/push/store.ts";
import { parsePushAction } from "@/lib/push/validation.ts";

export async function POST(request: Request) {
  const source = runtimeEnv();
  if (!isTrustedPushActionRequest(request, source)) {
    return noStore(NextResponse.json({ ok: false, error: "same_origin_required" }, { status: 403 }));
  }
  try {
    const contentLength = Number(request.headers.get("content-length") || "0");
    if (contentLength > MAX_PUSH_ACTION_BYTES) {
      return noStore(NextResponse.json({ ok: false, error: "action_body_too_large" }, { status: 413 }));
    }
    const body = await request.text();
    if (new TextEncoder().encode(body).byteLength > MAX_PUSH_ACTION_BYTES) {
      return noStore(NextResponse.json({ ok: false, error: "action_body_too_large" }, { status: 413 }));
    }
    const result = await performPushAction(source, parsePushAction(JSON.parse(body)));
    return noStore(NextResponse.json(result, { status: result.status === "research_queued" ? 202 : 200 }));
  } catch (error) {
    const candidate = error instanceof Error ? error.message : "";
    const status = candidate === "action_capability_expired" ? 410
      : candidate === "action_capability_invalid" ? 401
        : candidate === "push_not_configured" ? 503
          : 400;
    const code = status === 410 ? "action_capability_expired"
      : status === 401 ? "action_capability_invalid"
        : status === 503 ? "push_unavailable"
          : "push_action_invalid";
    return noStore(NextResponse.json({ ok: false, error: code }, { status }));
  }
}
