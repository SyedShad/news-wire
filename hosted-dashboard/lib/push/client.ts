"use client";

export const PUSH_PAYLOAD_SCHEMA_VERSION = 1 as const;
export const PUSH_PAYLOAD_CACHE = "news-wire-push-payload-v1";

export type PushAction = "start_research" | "dismiss";
export type PushPayloadKind = "article_alert" | "research_result" | "canary";
export type PushRuntimeMode = "shadow" | "active" | "paused";
export type PushRuntimeAction = "activate" | "pause" | "shadow";

export interface NotificationArticlePayload {
  schemaVersion: typeof PUSH_PAYLOAD_SCHEMA_VERSION;
  kind: PushPayloadKind;
  eventId: string;
  storyId: string;
  title: string;
  publisher: string;
  category: string;
  context: string;
  publishedAt?: string | number | null;
  detectedAt: string | number;
  provenance: string;
  sourceType?: string;
  canonicalUrl?: string;
  actions?: {
    startResearch: { capability: string };
    dismiss: { capability: string };
  };
  actionState?: {
    action: PushAction;
    status: "submitting" | "completed" | "failed";
    message?: string;
  };
}

export interface PushSubscriptionStatus {
  enabled: boolean;
  vapidPublicKey: string;
  deviceCount: number;
}

export interface PushRuntimeStatus {
  enabled: boolean;
  configured: boolean;
  mode: PushRuntimeMode;
  activationWatermark: number;
}

function responseMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "error" in payload && typeof payload.error === "string") {
    return payload.error.replaceAll("_", " ");
  }
  return fallback;
}

async function readJson(response: Response, fallback: string): Promise<Record<string, unknown>> {
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(responseMessage(payload, fallback));
  }
  return payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
}

export function pushSupported(): boolean {
  return typeof window !== "undefined"
    && window.isSecureContext
    && "serviceWorker" in navigator
    && "PushManager" in window
    && "Notification" in window;
}

export function isIosWithoutHomeScreen(): boolean {
  if (typeof navigator === "undefined" || typeof window === "undefined") return false;
  const ios = /iPad|iPhone|iPod/u.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia?.("(display-mode: standalone)").matches
    || ("standalone" in navigator && (navigator as Navigator & { standalone?: boolean }).standalone === true);
  return ios && !standalone;
}

export async function readPushSubscriptionStatus(): Promise<PushSubscriptionStatus> {
  const response = await fetch("/api/push/subscriptions", {
    cache: "no-store",
    credentials: "same-origin",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Could not read notification settings.");
  return {
    enabled: payload.enabled === true,
    vapidPublicKey: typeof payload.vapidPublicKey === "string" ? payload.vapidPublicKey : "",
    deviceCount: typeof payload.deviceCount === "number" ? payload.deviceCount : 0,
  };
}

function pushRuntimeStatus(payload: Record<string, unknown>): PushRuntimeStatus {
  if (payload.mode !== "shadow" && payload.mode !== "active" && payload.mode !== "paused") {
    throw new Error("Notification rollout status is unavailable.");
  }
  return {
    enabled: payload.enabled === true,
    configured: payload.configured === true,
    mode: payload.mode,
    activationWatermark: typeof payload.activationWatermark === "number" ? payload.activationWatermark : 0,
  };
}

export async function readPushRuntimeStatus(): Promise<PushRuntimeStatus> {
  const response = await fetch("/api/push/status", {
    cache: "no-store",
    credentials: "same-origin",
    headers: { accept: "application/json" },
  });
  return pushRuntimeStatus(await readJson(response, "Could not read notification rollout status."));
}

export async function setPushRuntimeAction(action: PushRuntimeAction): Promise<PushRuntimeStatus> {
  const response = await fetch("/api/push/status", {
    method: "POST",
    credentials: "same-origin",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify({ action }),
  });
  return pushRuntimeStatus(await readJson(response, "Could not change notification rollout status."));
}

function applicationServerKey(value: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - value.length % 4) % 4);
  const binary = window.atob((value + padding).replaceAll("-", "+").replaceAll("_", "/"));
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

async function serviceWorkerRegistration(): Promise<ServiceWorkerRegistration> {
  await navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" });
  return navigator.serviceWorker.ready;
}

export async function enablePushNotifications(vapidPublicKey: string): Promise<PushSubscriptionStatus> {
  if (!pushSupported()) throw new Error("This browser does not support Web Push.");
  if (!vapidPublicKey) throw new Error("Notifications are not configured on this dashboard yet.");

  const permission = Notification.permission === "default"
    ? await Notification.requestPermission()
    : Notification.permission;
  if (permission !== "granted") {
    throw new Error(permission === "denied"
      ? "Notifications are blocked in this browser's settings."
      : "Notification permission was not granted.");
  }

  const registration = await serviceWorkerRegistration();
  const existing = await registration.pushManager.getSubscription();
  const subscription = existing || await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: applicationServerKey(vapidPublicKey),
  });
  const response = await fetch("/api/push/subscriptions", {
    method: "POST",
    credentials: "same-origin",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify({ subscription: subscription.toJSON(), userAgent: navigator.userAgent }),
  });
  const payload = await readJson(response, "Could not enable notifications.");
  return {
    enabled: payload.enabled === true,
    vapidPublicKey,
    deviceCount: typeof payload.deviceCount === "number" ? payload.deviceCount : 1,
  };
}

export async function disablePushNotifications(scope: "device" | "all"): Promise<PushSubscriptionStatus> {
  if (!pushSupported()) throw new Error("This browser does not support Web Push.");
  const registration = await navigator.serviceWorker.getRegistration("/");
  const subscription = await registration?.pushManager.getSubscription() || null;
  if (scope === "device" && !subscription) throw new Error("This device is not subscribed.");
  const response = await fetch("/api/push/subscriptions", {
    method: "DELETE",
    credentials: "same-origin",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify({ scope, ...(subscription ? { endpoint: subscription.endpoint } : {}) }),
  });
  const payload = await readJson(response, "Could not disable notifications.");
  await subscription?.unsubscribe();
  return {
    enabled: payload.enabled === true,
    vapidPublicKey: "",
    deviceCount: typeof payload.deviceCount === "number" ? payload.deviceCount : 0,
  };
}

export async function sendPushCanary(endpoint: string): Promise<void> {
  const response = await fetch("/api/push/canary", {
    method: "POST",
    credentials: "same-origin",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify({ endpoint }),
  });
  await readJson(response, "Could not queue a test notification.");
}

export async function currentPushEndpoint(): Promise<string | null> {
  if (!pushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration("/");
  return (await registration?.pushManager.getSubscription())?.endpoint || null;
}

function cachedPayloadRequest(eventId: string): Request {
  return new Request(`${window.location.origin}/__push_payload__/${encodeURIComponent(eventId)}`);
}

export async function loadCachedPushPayload(eventId: string): Promise<NotificationArticlePayload | null> {
  if (!("caches" in window)) return null;
  const cache = await window.caches.open(PUSH_PAYLOAD_CACHE);
  const response = await cache.match(cachedPayloadRequest(eventId));
  if (!response) return null;
  const payload = await response.json().catch(() => null);
  return validPushPayload(payload) ? payload : null;
}

export async function storeCachedPushPayload(payload: NotificationArticlePayload): Promise<void> {
  if (!("caches" in window)) return;
  const cache = await window.caches.open(PUSH_PAYLOAD_CACHE);
  await cache.put(cachedPayloadRequest(payload.eventId), new Response(JSON.stringify(payload), {
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  }));
}

export function validPushPayload(value: unknown): value is NotificationArticlePayload {
  if (!value || typeof value !== "object") return false;
  const payload = value as Partial<NotificationArticlePayload>;
  const baseValid = payload.schemaVersion === PUSH_PAYLOAD_SCHEMA_VERSION
    && (payload.kind === "article_alert" || payload.kind === "research_result" || payload.kind === "canary")
    && [payload.eventId, payload.storyId, payload.title, payload.publisher, payload.category, payload.context, payload.provenance]
      .every((part) => typeof part === "string" && part.length > 0)
    && (typeof payload.detectedAt === "string" || typeof payload.detectedAt === "number");
  if (!baseValid) return false;
  if (payload.kind !== "article_alert") return true;
  return typeof payload.actions?.startResearch?.capability === "string"
    && payload.actions.startResearch.capability.length > 0
    && typeof payload.actions?.dismiss?.capability === "string"
    && payload.actions.dismiss.capability.length > 0;
}

export async function submitPushAction(payload: NotificationArticlePayload, action: PushAction): Promise<Record<string, unknown>> {
  const capability = action === "start_research"
    ? payload.actions?.startResearch.capability
    : payload.actions?.dismiss.capability;
  if (!capability) throw new Error("This notification action is unavailable.");
  const response = await fetch("/api/push/actions", {
    method: "POST",
    credentials: "same-origin",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify({ schemaVersion: PUSH_PAYLOAD_SCHEMA_VERSION, eventId: payload.eventId, action, capability }),
  });
  return readJson(response, action === "dismiss" ? "Could not dismiss this notification." : "Could not start working on this item.");
}
