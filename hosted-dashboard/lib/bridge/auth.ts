import type { AuthRuntimeEnv } from "../auth/types.ts";
import { consumeBridgeNonce, ensureDashboardSchema } from "../dashboard/store.ts";

const MAX_CLOCK_SKEW_MS = 5 * 60_000;
const encoder = new TextEncoder();

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function hexToBytes(value: string): Uint8Array | null {
  if (!/^[a-f0-9]{64}$/i.test(value)) return null;
  return new Uint8Array(value.match(/../g)!.map((part) => Number.parseInt(part, 16)));
}

async function sha256Hex(value: ArrayBuffer): Promise<string> {
  return bytesToHex(new Uint8Array(await crypto.subtle.digest("SHA-256", value)));
}

export type VerifiedBridgeRequest = { body: ArrayBuffer; bodyText: string };

function supportedRuntimeVersion(value: string): boolean {
  const match = /^(\d+)\.(\d+)\.(\d+)/u.exec(value);
  if (!match) return false;
  const version = match.slice(1).map(Number);
  return version[0] > 0 || version[1] > 4 || (version[1] === 4 && version[2] >= 1);
}

export async function verifyBridgeRequest(
  request: Request,
  source: AuthRuntimeEnv,
): Promise<VerifiedBridgeRequest> {
  const secret = source.BRIDGE_SECRET?.trim();
  if (!secret || secret.length < 32) throw new Error("bridge_not_configured");

  const timestampValue = request.headers.get("x-news-wire-timestamp") || "";
  const nonce = request.headers.get("x-news-wire-nonce") || "";
  const signatureValue = request.headers.get("x-news-wire-signature") || "";
  const bridgeVersion = request.headers.get("x-news-wire-bridge-version")?.trim() || "";
  const runtimeVersion = request.headers.get("x-news-wire-runtime-version")?.trim() || "";
  const timestamp = Number(timestampValue);
  if (!Number.isSafeInteger(timestamp) || Math.abs(Date.now() - timestamp) > MAX_CLOCK_SKEW_MS) {
    throw new Error("bridge_timestamp_invalid");
  }
  if (!/^[A-Za-z0-9_-]{24,100}$/.test(nonce)) throw new Error("bridge_nonce_invalid");
  const signature = hexToBytes(signatureValue);
  if (!signature) throw new Error("bridge_signature_invalid");

  const body = await request.arrayBuffer();
  const bodyDigest = await sha256Hex(body);
  const canonical = [
    "v1",
    timestampValue,
    nonce,
    request.method.toUpperCase(),
    new URL(request.url).pathname,
    bodyDigest,
  ].join("\n");
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const valid = await crypto.subtle.verify("HMAC", key, signature, encoder.encode(canonical));
  if (!valid) throw new Error("bridge_signature_invalid");
  if (!bridgeVersion.startsWith("2.")) throw new Error("bridge_version_unsupported");
  if (!supportedRuntimeVersion(runtimeVersion)) throw new Error("runtime_version_unsupported");

  await ensureDashboardSchema(source.DB);
  if (!(await consumeBridgeNonce(source.DB, nonce, timestamp))) throw new Error("bridge_replay_detected");
  return { body, bodyText: new TextDecoder().decode(body) };
}
