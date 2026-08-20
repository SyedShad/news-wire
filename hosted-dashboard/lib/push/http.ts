import { authBaseUrl } from "../auth/config.ts";
import type { AuthRuntimeEnv } from "../auth/types.ts";

export function isTrustedPushActionRequest(request: Request, source: AuthRuntimeEnv): boolean {
  const expected = authBaseUrl(request, source).origin;
  if (new URL(request.url).origin !== expected) return false;
  const origin = request.headers.get("origin");
  if (origin === expected) return true;
  // Same-origin service-worker fetches may omit Origin. The capability remains
  // the authorization credential; Fetch Metadata still rejects cross-site use.
  return (origin === null || origin === "null") && request.headers.get("sec-fetch-site") !== "cross-site";
}
