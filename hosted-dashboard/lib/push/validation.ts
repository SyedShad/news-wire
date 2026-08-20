import { isRecord } from "../dashboard/validation.ts";
import type { PushAction, PushSubscriptionInput } from "./types.ts";

function requiredString(value: Record<string, unknown>, key: string, maximum: number): string {
  const candidate = value[key];
  if (typeof candidate !== "string" || !candidate.trim() || candidate.length > maximum) {
    throw new TypeError(`${key} is invalid`);
  }
  return candidate.trim();
}

function canonicalBase64Url(value: string, minimum: number, maximum: number): boolean {
  return value.length >= minimum && value.length <= maximum && /^[A-Za-z0-9_-]+$/u.test(value);
}

const PUSH_PROVIDER_HOSTS = new Set([
  "fcm.googleapis.com",
  "android.googleapis.com",
  "web.push.apple.com",
  "notify.windows.com",
]);
const PUSH_PROVIDER_SUFFIXES = [
  ".push.services.mozilla.com",
  ".notify.windows.com",
];

export function validatePushEndpoint(endpoint: string): URL {
  let endpointUrl: URL;
  try { endpointUrl = new URL(endpoint); } catch { throw new TypeError("endpoint is invalid"); }
  const hostname = endpointUrl.hostname.toLowerCase();
  const supportedProvider = PUSH_PROVIDER_HOSTS.has(hostname) ||
    PUSH_PROVIDER_SUFFIXES.some((suffix) => hostname.endsWith(suffix));
  if (
    endpointUrl.protocol !== "https:" || endpointUrl.username || endpointUrl.password ||
    endpointUrl.hash || (endpointUrl.port && endpointUrl.port !== "443") ||
    !supportedProvider || !endpointUrl.pathname.startsWith("/") || endpointUrl.pathname === "/"
  ) {
    throw new TypeError("endpoint is not a supported Web Push provider");
  }
  return endpointUrl;
}

export function parsePushSubscription(value: unknown): PushSubscriptionInput {
  if (!isRecord(value)) throw new TypeError("subscription body must be an object");
  const candidate = isRecord(value.subscription) ? value.subscription : value;
  const endpoint = requiredString(candidate, "endpoint", 2_048);
  validatePushEndpoint(endpoint);
  if (!isRecord(candidate.keys)) throw new TypeError("subscription keys are required");
  const p256dh = requiredString(candidate.keys, "p256dh", 200);
  const auth = requiredString(candidate.keys, "auth", 100);
  if (!canonicalBase64Url(p256dh, 80, 100) || !canonicalBase64Url(auth, 20, 40)) {
    throw new TypeError("subscription keys are invalid");
  }
  const expirationTime = candidate.expirationTime;
  if (expirationTime !== null && expirationTime !== undefined &&
      (!Number.isSafeInteger(expirationTime) || Number(expirationTime) <= Date.now())) {
    throw new TypeError("expirationTime is invalid");
  }
  return { endpoint, expirationTime: expirationTime == null ? null : Number(expirationTime), keys: { p256dh, auth } };
}

export function parseSubscriptionRevocation(value: unknown): { scope: "device" | "all"; endpoint?: string } {
  if (!isRecord(value) || (value.scope !== "device" && value.scope !== "all")) {
    throw new TypeError("scope is invalid");
  }
  if (value.scope === "all") return { scope: "all" };
  const endpoint = requiredString(value, "endpoint", 2_048);
  validatePushEndpoint(endpoint);
  return { scope: "device", endpoint };
}

export function parsePushAction(value: unknown): {
  eventId: string; action: PushAction; capability: string;
} {
  if (!isRecord(value) || value.schemaVersion !== 1) throw new TypeError("action schema is invalid");
  const eventId = requiredString(value, "eventId", 100);
  const capability = requiredString(value, "capability", 200);
  if (!/^[A-Za-z0-9_-]{40,200}$/u.test(capability)) throw new TypeError("capability is invalid");
  if (value.action !== "start_research" && value.action !== "dismiss") {
    throw new TypeError("action is invalid");
  }
  return { eventId, action: value.action, capability };
}

export function parseCanaryRequest(value: unknown): { endpoint: string } {
  if (!isRecord(value)) throw new TypeError("canary body must be an object");
  const endpoint = requiredString(value, "endpoint", 2_048);
  validatePushEndpoint(endpoint);
  return { endpoint };
}

export function parsePushRuntimeAction(value: unknown): { action: "activate" | "pause" | "shadow" } {
  if (!isRecord(value) || !new Set(["activate", "pause", "shadow"]).has(String(value.action))) {
    throw new TypeError("runtime action is invalid");
  }
  return { action: value.action as "activate" | "pause" | "shadow" };
}
