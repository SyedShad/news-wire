import { NextResponse } from "next/server";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { randomToken, sha256 } from "@/lib/auth/crypto.ts";
import { googleAuthorizationUrl } from "@/lib/auth/google.ts";
import { OAUTH_STATE_COOKIE, secureCookie } from "@/lib/auth/http.ts";
import { errorRedirect, noStore } from "@/lib/auth/responses.ts";
import { storeOauthTransaction } from "@/lib/auth/store.ts";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<NextResponse> {
  const source = runtimeEnv();
  try {
    const state = randomToken(32);
    const nonce = randomToken(32);
    const verifier = randomToken(64);
    const codeChallenge = await sha256(verifier);
    await storeOauthTransaction(source, { state, nonce, verifier });

    const response = noStore(
      NextResponse.redirect(
        googleAuthorizationUrl(request, source, { state, nonce, codeChallenge }),
        302,
      ),
    );
    response.cookies.set(OAUTH_STATE_COOKIE, state, {
      httpOnly: true,
      secure: secureCookie(request, source),
      sameSite: "lax",
      path: "/api/auth/google/callback",
      maxAge: 600,
    });
    return response;
  } catch {
    return errorRedirect(request, "google_not_configured");
  }
}
