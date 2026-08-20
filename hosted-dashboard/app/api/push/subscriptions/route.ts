import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { isSameOriginRequest } from "@/lib/auth/http.ts";
import { noStore } from "@/lib/auth/responses.ts";
import {
  readPushStatus,
  registerPushSubscription,
  revokePushSubscriptions,
} from "@/lib/push/store.ts";
import { parsePushSubscription, parseSubscriptionRevocation } from "@/lib/push/validation.ts";

export async function GET(request: Request) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  try {
    return noStore(NextResponse.json(await readPushStatus(runtimeEnv())));
  } catch {
    return noStore(NextResponse.json({ ok: false, error: "push_status_unavailable" }, { status: 503 }));
  }
}

export async function POST(request: Request) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  const source = runtimeEnv();
  if (!isSameOriginRequest(request, source)) {
    return noStore(NextResponse.json({ ok: false, error: "same_origin_required" }, { status: 403 }));
  }
  try {
    const subscription = parsePushSubscription(await request.json());
    const deviceCount = await registerPushSubscription(source, subscription, authorization.session.actorId);
    return noStore(NextResponse.json({ enabled: true, deviceCount }, { status: 201 }));
  } catch (error) {
    const code = error instanceof Error && error.message === "push_not_configured"
      ? "push_not_configured"
      : "push_subscription_invalid";
    return noStore(NextResponse.json({ ok: false, error: code }, { status: code === "push_not_configured" ? 503 : 400 }));
  }
}

export async function DELETE(request: Request) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  const source = runtimeEnv();
  if (!isSameOriginRequest(request, source)) {
    return noStore(NextResponse.json({ ok: false, error: "same_origin_required" }, { status: 403 }));
  }
  try {
    const input = parseSubscriptionRevocation(await request.json());
    const deviceCount = await revokePushSubscriptions(source, input);
    return noStore(NextResponse.json({ enabled: false, deviceCount }));
  } catch {
    return noStore(NextResponse.json({ ok: false, error: "push_revocation_invalid" }, { status: 400 }));
  }
}
