import { createRemoteJWKSet, jwtVerify } from "jose";
import type { AuthRuntimeEnv, GoogleIdentity } from "./types.ts";
import { authBaseUrl, requireEnv } from "./config.ts";
import { constantTimeEqual } from "./crypto.ts";
import { approvedEmailDomain } from "./policy.ts";

const GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth";
const GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token";
const GOOGLE_JWKS = createRemoteJWKSet(
  new URL("https://www.googleapis.com/oauth2/v3/certs"),
);

export class GoogleAuthError extends Error {
  constructor(public readonly code: string) {
    super(code);
  }
}

export function googleCallbackUrl(request: Request, source: AuthRuntimeEnv): string {
  return new URL("/api/auth/google/callback", authBaseUrl(request, source)).toString();
}

export function googleAuthorizationUrl(
  request: Request,
  source: AuthRuntimeEnv,
  input: { state: string; nonce: string; codeChallenge: string },
): URL {
  const url = new URL(GOOGLE_AUTHORIZATION_ENDPOINT);
  url.searchParams.set("client_id", requireEnv(source, "GOOGLE_CLIENT_ID"));
  url.searchParams.set("redirect_uri", googleCallbackUrl(request, source));
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", "openid email profile");
  url.searchParams.set("state", input.state);
  url.searchParams.set("nonce", input.nonce);
  url.searchParams.set("code_challenge", input.codeChallenge);
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("prompt", "select_account");
  return url;
}

export async function exchangeGoogleCode(
  request: Request,
  source: AuthRuntimeEnv,
  code: string,
  verifier: string,
): Promise<string> {
  const response = await fetch(GOOGLE_TOKEN_ENDPOINT, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: requireEnv(source, "GOOGLE_CLIENT_ID"),
      client_secret: requireEnv(source, "GOOGLE_CLIENT_SECRET"),
      code,
      code_verifier: verifier,
      grant_type: "authorization_code",
      redirect_uri: googleCallbackUrl(request, source),
    }),
  });
  if (!response.ok) throw new GoogleAuthError("token_exchange_failed");
  const payload = (await response.json()) as { id_token?: unknown };
  if (typeof payload.id_token !== "string") {
    throw new GoogleAuthError("missing_identity_token");
  }
  return payload.id_token;
}

export async function verifyGoogleIdentity(
  source: AuthRuntimeEnv,
  idToken: string,
  expectedNonce: string,
): Promise<GoogleIdentity> {
  const clientId = requireEnv(source, "GOOGLE_CLIENT_ID");
  let payload;
  try {
    ({ payload } = await jwtVerify(idToken, GOOGLE_JWKS, {
      issuer: ["https://accounts.google.com", "accounts.google.com"],
      audience: clientId,
      algorithms: ["RS256"],
      clockTolerance: 5,
    }));
  } catch {
    throw new GoogleAuthError("invalid_identity_token");
  }

  if (
    typeof payload.nonce !== "string" ||
    !constantTimeEqual(
      new TextEncoder().encode(payload.nonce),
      new TextEncoder().encode(expectedNonce),
    )
  ) {
    throw new GoogleAuthError("invalid_nonce");
  }
  if (payload.email_verified !== true || typeof payload.email !== "string") {
    throw new GoogleAuthError("email_not_verified");
  }
  if (typeof payload.sub !== "string" || !payload.sub) {
    throw new GoogleAuthError("invalid_subject");
  }
  if (typeof payload.azp === "string" && payload.azp !== clientId) {
    throw new GoogleAuthError("invalid_authorized_party");
  }
  const domain = approvedEmailDomain(payload.email);
  if (!domain) throw new GoogleAuthError("domain_not_allowed");
  if (typeof payload.hd !== "string" || !approvedEmailDomain(`member@${payload.hd}`)) {
    throw new GoogleAuthError("workspace_not_allowed");
  }
  return { subject: payload.sub, email: payload.email, domain };
}
