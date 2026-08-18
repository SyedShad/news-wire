import { NextResponse } from "next/server";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { constantTimeEqual } from "@/lib/auth/crypto.ts";
import {
  exchangeGoogleCode,
  GoogleAuthError,
  verifyGoogleIdentity,
} from "@/lib/auth/google.ts";
import {
  OAUTH_STATE_COOKIE,
  SESSION_COOKIE,
  cookieValue,
  requestFingerprint,
  secureCookie,
} from "@/lib/auth/http.ts";
import { errorRedirect, noStore } from "@/lib/auth/responses.ts";
import {
  audit,
  consumeOauthTransaction,
  createSession,
} from "@/lib/auth/store.ts";

export const dynamic = "force-dynamic";

function equalState(left: string, right: string): boolean {
  return constantTimeEqual(new TextEncoder().encode(left), new TextEncoder().encode(right));
}

export async function GET(request: Request): Promise<NextResponse> {
  const source = runtimeEnv();
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const cookieState = cookieValue(request, OAUTH_STATE_COOKIE);
  let errorCode = "google_sign_in_failed";
  let ipHash: string | null = null;

  try {
    ipHash = await requestFingerprint(request, source);
    if (url.searchParams.has("error")) throw new GoogleAuthError("provider_denied");
    if (!code || !state || !cookieState || !equalState(state, cookieState)) {
      throw new GoogleAuthError("invalid_state");
    }
    const transaction = await consumeOauthTransaction(source, state);
    if (!transaction) throw new GoogleAuthError("invalid_state");

    const idToken = await exchangeGoogleCode(request, source, code, transaction.verifier);
    const identity = await verifyGoogleIdentity(source, idToken, transaction.nonce);
    const token = await createSession(source, {
      actorId: `google:${identity.subject}`,
      role: "editor",
      email: identity.email,
      credentialVersion: "google-v1",
      lifetimeSeconds: 12 * 60 * 60,
    });
    await audit(source, {
      actorId: `google:${identity.subject}`,
      role: "editor",
      action: "auth.google.login",
      outcome: "success",
      ipHash,
      detail: { domain: identity.domain },
    });

    const response = noStore(
      NextResponse.redirect(new URL("/dashboard", request.url), 303),
    );
    response.cookies.set(SESSION_COOKIE, token, {
      httpOnly: true,
      secure: secureCookie(request, source),
      sameSite: "lax",
      path: "/",
      maxAge: 12 * 60 * 60,
    });
    response.cookies.set(OAUTH_STATE_COOKIE, "", {
      httpOnly: true,
      secure: secureCookie(request, source),
      sameSite: "lax",
      path: "/api/auth/google/callback",
      maxAge: 0,
    });
    return response;
  } catch (error) {
    if (error instanceof GoogleAuthError) errorCode = error.code;
    try {
      await audit(source, {
        action: "auth.google.login",
        outcome: errorCode,
        ipHash,
      });
    } catch {
      errorCode = "authentication_storage_unavailable";
    }
    const response = errorRedirect(request, errorCode);
    response.cookies.set(OAUTH_STATE_COOKIE, "", {
      httpOnly: true,
      secure: new URL(request.url).protocol === "https:",
      sameSite: "lax",
      path: "/api/auth/google/callback",
      maxAge: 0,
    });
    return response;
  }
}
