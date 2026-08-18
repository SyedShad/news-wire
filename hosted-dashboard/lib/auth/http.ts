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

export function assertSameOrigin(request: Request, source: AuthRuntimeEnv): void {
  const origin = request.headers.get("origin");
  const expected = authBaseUrl(request, source).origin;
  if (!origin || origin !== expected) throw new Error("Cross-origin request rejected");
}

export async function requestFingerprint(
  request: Request,
  source: AuthRuntimeEnv,
): Promise<string> {
  const forwarded = request.headers.get("cf-connecting-ip")?.trim();
  const address = forwarded || "address-unavailable";
  return hmacSha256(requireEnv(source, "AUTH_SECRET"), `ip:${address}`);
}
