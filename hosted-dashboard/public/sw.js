"use strict";

const PUSH_SCHEMA_VERSION = 1;
const PAYLOAD_CACHE = "news-wire-push-payload-v1";
const PAYLOAD_PATH = "/__push_payload__/";
const ARTICLE_ACTIONS = [
  { action: "start_research", title: "Start working" },
  { action: "dismiss", title: "Dismiss" },
];
const ALLOWED_KINDS = new Set(["article_alert", "research_result", "canary"]);

function cleanText(value) {
  return typeof value === "string"
    ? value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/gu, "").trim()
    : "";
}

function cleanPayload(value) {
  if (!value || typeof value !== "object" || value.schemaVersion !== PUSH_SCHEMA_VERSION || !ALLOWED_KINDS.has(value.kind)) return null;
  const payload = {
    schemaVersion: PUSH_SCHEMA_VERSION,
    kind: value.kind,
    eventId: cleanText(value.eventId),
    storyId: cleanText(value.storyId),
    title: cleanText(value.title),
    publisher: cleanText(value.publisher),
    category: cleanText(value.category),
    context: cleanText(value.context),
    provenance: cleanText(value.provenance),
    detectedAt: typeof value.detectedAt === "number" ? value.detectedAt : cleanText(value.detectedAt),
    publishedAt: typeof value.publishedAt === "number" || value.publishedAt === null ? value.publishedAt : cleanText(value.publishedAt),
    sourceType: cleanText(value.sourceType),
    canonicalUrl: cleanText(value.canonicalUrl),
  };
  if ([payload.eventId, payload.storyId, payload.title, payload.publisher, payload.category, payload.context, payload.provenance].some((part) => !part)) return null;
  if (payload.detectedAt === "") return null;

  if (payload.kind === "article_alert") {
    const startCapability = cleanText(value.actions?.startResearch?.capability);
    const dismissCapability = cleanText(value.actions?.dismiss?.capability);
    if (!startCapability || !dismissCapability) return null;
    payload.actions = {
      startResearch: { capability: startCapability },
      dismiss: { capability: dismissCapability },
    };
  }
  return payload;
}

function payloadRequest(eventId) {
  return new Request(new URL(`${PAYLOAD_PATH}${encodeURIComponent(eventId)}`, self.location.origin));
}

async function storePayload(payload) {
  const cache = await caches.open(PAYLOAD_CACHE);
  await cache.put(payloadRequest(payload.eventId), new Response(JSON.stringify(payload), {
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  }));
}

async function updateActionState(payload, action, status, message) {
  const nextPayload = { ...payload, actionState: { action, status, ...(message ? { message } : {}) } };
  await storePayload(nextPayload);
  return nextPayload;
}

function notificationActions(payload) {
  const maximum = typeof Notification === "undefined" ? 0 : Number(Notification.maxActions || 0);
  return payload.kind === "article_alert" && maximum >= 2 ? ARTICLE_ACTIONS : [];
}

function notificationBody(payload) {
  const labels = [payload.publisher, payload.category, payload.sourceType].filter(Boolean).join(" · ");
  const published = payload.publishedAt === null || payload.publishedAt === ""
    ? "not provided"
    : String(payload.publishedAt);
  const timestamps = `Published: ${published} · Detected: ${String(payload.detectedAt)}`;
  return `${labels}\n${timestamps}\nContext source: ${payload.provenance}\n${payload.context}`;
}

async function displayNotification(payload) {
  if (payload.kind === "article_alert") await storePayload(payload);
  await self.registration.showNotification(payload.title, {
    body: notificationBody(payload),
    icon: "/icons/news-wire-192.png",
    badge: "/icons/news-wire-192.png",
    tag: `news-wire:${payload.eventId}`,
    renotify: false,
    timestamp: new Date(payload.detectedAt).valueOf() || Date.now(),
    data: payload,
    actions: notificationActions(payload),
  });
}

function capabilityFor(payload, action) {
  return action === "start_research"
    ? payload.actions?.startResearch?.capability
    : payload.actions?.dismiss?.capability;
}

async function submitAction(payload, action) {
  const capability = capabilityFor(payload, action);
  if (!capability) throw new Error("notification_action_unavailable");
  await updateActionState(payload, action, "submitting");
  const response = await fetch(new URL("/api/push/actions", self.location.origin), {
    method: "POST",
    credentials: "same-origin",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify({ schemaVersion: PUSH_SCHEMA_VERSION, eventId: payload.eventId, action, capability }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(typeof body?.error === "string" ? body.error : "notification_action_failed");
  }
  const resultStatus = typeof body?.status === "string" ? body.status : "";
  const completedAction = resultStatus.includes("dismiss") ? "dismiss" : "start_research";
  await updateActionState(payload, completedAction, "completed");
}

async function openOrFocus(path) {
  const target = new URL(path, self.location.origin).toString();
  const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const client of windows) {
    if (client.url === target && "focus" in client) return client.focus();
  }
  return self.clients.openWindow ? self.clients.openWindow(target) : undefined;
}

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  if (!event.data) return;
  let payload = null;
  try {
    payload = cleanPayload(event.data.json());
  } catch {
    return;
  }
  if (!payload) return;
  event.waitUntil(displayNotification(payload));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const payload = cleanPayload(event.notification.data);
  if (!payload) return;
  const cardPath = `/notification/${encodeURIComponent(payload.eventId)}`;
  if (payload.kind !== "article_alert") {
    event.waitUntil(openOrFocus("/dashboard"));
    return;
  }
  if (event.action !== "start_research" && event.action !== "dismiss") {
    event.waitUntil(openOrFocus(cardPath));
    return;
  }
  event.waitUntil(submitAction(payload, event.action).catch(async (reason) => {
    await updateActionState(payload, event.action, "failed", reason instanceof Error ? reason.message : "notification_action_failed");
    await openOrFocus(cardPath);
  }));
});
