import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { isSameOriginRequest } from "@/lib/auth/http.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { queueCanary } from "@/lib/push/store.ts";
import { parseCanaryRequest } from "@/lib/push/validation.ts";

export async function POST(request: Request) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  const source = runtimeEnv();
  if (!isSameOriginRequest(request, source)) {
    return noStore(NextResponse.json({ ok: false, error: "same_origin_required" }, { status: 403 }));
  }
  try {
    const { endpoint } = parseCanaryRequest(await request.json());
    await queueCanary(source, endpoint);
    return noStore(NextResponse.json({ ok: true, queued: true }, { status: 202 }));
  } catch (error) {
    const unavailable = error instanceof Error && new Set([
      "push_delivery_disabled", "push_subscription_unavailable",
    ]).has(error.message);
    return noStore(NextResponse.json(
      { ok: false, error: unavailable ? "push_canary_unavailable" : "push_canary_invalid" },
      { status: unavailable ? 409 : 400 },
    ));
  }
}
