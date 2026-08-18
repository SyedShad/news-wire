import type { AuthRuntimeEnv } from "./types.ts";
import { authBaseUrl, requireEnv } from "./config.ts";
import { hmacSha256 } from "./crypto.ts";

export const SESSION_COOKIE = "osainw_session";
export const OAUTH_STATE_COOKIE = "osainw_oauth_state";

export function cookieValue(request: Request, name: string): string | undefined {
  const cookieHeader = request.headers.get("cookie") || "";
  for (const part of cookieHeader.split(";")) {
    const separator = part.indexOf("=");
    if (separator < 0) continue;
    const key = part.slice(0, separator).trim();
    if (key === name) return decodeURIComponent(part.slice(separator + 1).trim());
  }
  return undefined;
}

export function secureCookie(request: Request, source: AuthRuntimeEnv): boolean {
  return authBaseUrl(request, source).protocol === "https:";
}

export function isSameOriginRequest(
  request: Request,
  source: AuthRuntimeEnv,
): boolean {
  const expected = authBaseUrl(request, source).origin;
  const origin = request.headers.get("origin");
  if (origin === expected) return true;

  // Sites serves owner pages inside an authenticated sandbox that serializes
  // form and fetch initiators as the opaque `null` origin. Accept that case
  // only for the configured Sites origin, trusted browser fetch metadata, and
  // the internal authenticated dispatch headers. Cross-site forms report
  // Sec-Fetch-Site: cross-site and continue to fail closed.
  if (
    origin !== "null" ||
    !expected.endsWith(".chatgpt.site") ||
    new URL(request.url).origin !== expected ||
    request.headers.get("sec-fetch-site") !== "same-origin" ||
    !request.headers.get("x-dispatched-app")?.trim() ||
    !request.headers.get("oai-authenticated-user-id")?.trim()
  ) {
    return false;
  }
  return true;
}

export function assertSameOrigin(request: Request, source: AuthRuntimeEnv): void {
  if (!isSameOriginRequest(request, source)) {
    throw new Error("Cross-origin request rejected");
  }
}

export async function requestFingerprint(
  request: Request,
  source: AuthRuntimeEnv,
): Promise<string> {
  const forwarded = request.headers.get("cf-connecting-ip")?.trim();
  const address = forwarded || "address-unavailable";
  return hmacSha256(requireEnv(source, "AUTH_SECRET"), `ip:${address}`);
}
