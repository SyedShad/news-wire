import { NextResponse } from "next/server";
import {
  masterPasswordVersion,
  requireEnv,
  runtimeEnv,
} from "@/lib/auth/config.ts";
import { verifyPassword } from "@/lib/auth/crypto.ts";
import {
  SESSION_COOKIE,
  assertSameOrigin,
  requestFingerprint,
  secureCookie,
} from "@/lib/auth/http.ts";
import { noStore } from "@/lib/auth/responses.ts";
import {
  audit,
  clearMasterFailures,
  createSession,
  readMasterAttemptState,
  recordMasterFailure,
} from "@/lib/auth/store.ts";

export const dynamic = "force-dynamic";

function wantsJson(request: Request): boolean {
  return request.headers.get("accept")?.includes("application/json") ?? false;
}

function failure(
  request: Request,
  status: number,
  code: string,
  retryAfter?: number,
): NextResponse {
  let response: NextResponse;
  if (wantsJson(request)) {
    response = NextResponse.json({ ok: false, error: code }, { status });
  } else {
    const url = new URL("/owner", request.url);
    url.searchParams.set("error", code);
    response = NextResponse.redirect(url, 303);
  }
  if (retryAfter) response.headers.set("retry-after", String(retryAfter));
  return noStore(response);
}

export async function POST(request: Request): Promise<NextResponse> {
  const source = runtimeEnv();
  let fingerprint = "unavailable";
  let failureStage = "origin_check";
  try {
    assertSameOrigin(request, source);
    failureStage = "request_fingerprint";
    fingerprint = await requestFingerprint(request, source);
    failureStage = "rate_limit_read";
    const state = await readMasterAttemptState(source, fingerprint);
    const now = Date.now();
    if (state.lockedUntil > now) {
      await audit(source, {
        action: "auth.master.login",
        outcome: "locked",
        ipHash: fingerprint,
      });
      return failure(
        request,
        423,
        "temporarily_locked",
        Math.ceil((state.lockedUntil - now) / 1_000),
      );
    }
    if (state.nextAllowedAt > now) {
      return failure(
        request,
        429,
        "try_again_later",
        Math.max(1, Math.ceil((state.nextAllowedAt - now) / 1_000)),
      );
    }

    failureStage = "form_parse";
    const form = await request.formData();
    const password = form.get("password");
    const validInput = typeof password === "string" && password.length <= 4096;
    failureStage = "password_verify";
    const valid = validInput
      ? await verifyPassword(password, requireEnv(source, "MASTER_PASSWORD_VERIFIER"))
      : false;
    if (!valid) {
      failureStage = "failure_record";
      const next = await recordMasterFailure(source, fingerprint, state.failures);
      failureStage = "failure_audit";
      await audit(source, {
        action: "auth.master.login",
        outcome: next.lockedUntil ? "failure_lockout" : "failure",
        ipHash: fingerprint,
      });
      return failure(
        request,
        next.lockedUntil ? 423 : 401,
        next.lockedUntil ? "temporarily_locked" : "invalid_credentials",
        next.lockedUntil ? 15 * 60 : undefined,
      );
    }

    failureStage = "failure_clear";
    await clearMasterFailures(source, fingerprint);
    const version = masterPasswordVersion(source);
    failureStage = "session_create";
    const token = await createSession(source, {
      actorId: "master",
      role: "master",
      email: null,
      credentialVersion: version,
      lifetimeSeconds: 8 * 60 * 60,
    });
    failureStage = "success_audit";
    await audit(source, {
      actorId: "master",
      role: "master",
      action: "auth.master.login",
      outcome: "success",
      ipHash: fingerprint,
      detail: { credentialVersion: version },
    });

    const response = wantsJson(request)
      ? NextResponse.json({ ok: true }, { status: 200 })
      : NextResponse.redirect(new URL("/dashboard", request.url), 303);
    response.cookies.set(SESSION_COOKIE, token, {
      httpOnly: true,
      secure: secureCookie(request, source),
      sameSite: "lax",
      path: "/",
      maxAge: 8 * 60 * 60,
    });
    return noStore(response);
  } catch {
    try {
      await audit(source, {
        action: "auth.master.login",
        outcome: "configuration_or_storage_error",
        ipHash: fingerprint,
        detail: { stage: failureStage },
      });
    } catch {
      // Authentication still fails closed if its audit store is unavailable.
    }
    return failure(request, 503, "owner_access_unavailable");
  }
}
