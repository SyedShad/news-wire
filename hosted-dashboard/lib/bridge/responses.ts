import { NextResponse } from "next/server";
import { noStore } from "../auth/responses.ts";

export function bridgeError(error: unknown): NextResponse {
  const candidate = error instanceof Error ? error.message : "";
  const code = new Set([
    "bridge_not_configured",
    "bridge_timestamp_invalid",
    "bridge_nonce_invalid",
    "bridge_signature_invalid",
    "bridge_replay_detected",
    "bridge_version_unsupported",
    "runtime_version_unsupported",
  ]).has(candidate) ? candidate : "bridge_request_invalid";
  const status = code.endsWith("_unsupported") ? 426 : code === "bridge_not_configured" || code === "bridge_request_invalid" ? 503 : 401;
  return noStore(NextResponse.json({ ok: false, error: code }, { status }));
}
