import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const root = new URL("../", import.meta.url);
const serviceWorkerSource = await readFile(new URL("public/sw.js", root), "utf8");

function articlePayload(overrides = {}) {
  return {
    schemaVersion: 1,
    kind: "article_alert",
    eventId: "evt-article-1",
    storyId: "story-1",
    title: "Publisher releases model weights",
    publisher: "Example Lab",
    category: "Open ecosystem",
    context: "Example Lab says the model weights and release notes are now available. This context comes from the publisher excerpt.",
    publishedAt: "2026-08-20T08:00:00Z",
    detectedAt: "2026-08-20T08:12:00Z",
    provenance: "publisher excerpt",
    sourceType: "announcement",
    actions: {
      startResearch: { capability: "test-start-capability" },
      dismiss: { capability: "test-dismiss-capability" },
    },
    ...overrides,
  };
}

function workerHarness({ maxActions = 2, fetchResponse } = {}) {
  const listeners = new Map();
  const notifications = [];
  const opened = [];
  const requests = [];
  const cached = new Map();
  const cache = {
    async put(request, response) { cached.set(request.url, response); },
    async match(request) { return cached.get(request.url); },
  };
  const self = {
    location: { origin: "https://wire.example" },
    registration: {
      async showNotification(title, options) { notifications.push({ title, options }); },
    },
    clients: {
      async claim() {},
      async matchAll() { return []; },
      async openWindow(url) { opened.push(url); },
    },
    async skipWaiting() {},
    addEventListener(name, handler) { listeners.set(name, handler); },
  };
  const context = {
    self,
    caches: { async open() { return cache; } },
    Notification: { maxActions },
    Request,
    Response,
    URL,
    Set,
    Date,
    JSON,
    Number,
    Error,
    encodeURIComponent,
    fetch: async (request, init) => {
      requests.push({ request: String(request), init });
      return fetchResponse || new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  };
  vm.runInNewContext(serviceWorkerSource, context, { filename: "sw.js" });
  return { listeners, notifications, opened, requests, cached };
}

async function dispatchWaitable(handler, event) {
  let pending;
  handler({ ...event, waitUntil(value) { pending = value; } });
  if (pending) await pending;
}

test("PWA manifest has installable iOS-compatible raster assets", async () => {
  const [manifestSource, layout, nextConfig, worker] = await Promise.all([
    readFile(new URL("public/manifest.webmanifest", root), "utf8"),
    readFile(new URL("app/layout.tsx", root), "utf8"),
    readFile(new URL("next.config.ts", root), "utf8"),
    readFile(new URL("worker/index.ts", root), "utf8"),
  ]);
  const manifest = JSON.parse(manifestSource);
  assert.equal(manifest.scope, "/");
  assert.equal(manifest.display, "standalone");
  assert.ok(manifest.icons.some((icon) => icon.sizes === "192x192" && icon.type === "image/png"));
  assert.ok(manifest.icons.some((icon) => icon.sizes === "512x512" && icon.purpose.includes("maskable")));
  assert.match(layout, /manifest:\s*["']\/manifest\.webmanifest["']/u);
  assert.match(layout, /appleWebApp/u);
  assert.match(layout, /news-wire-192\.png/u);
  assert.match(nextConfig, /source:\s*["']\/sw\.js["']/u);
  assert.match(nextConfig, /Service-Worker-Allowed/u);
  assert.match(nextConfig, /no-cache, no-store, must-revalidate/u);
  assert.match(nextConfig, /connect-src 'self'/u);
  assert.match(nextConfig, /worker-src 'self'/u);
  assert.match(worker, /application\/javascript; charset=utf-8/u);
  assert.match(worker, /service-worker-allowed/u);
  assert.match(worker, /connect-src 'self'/u);
  assert.match(worker, /worker-src 'self'/u);
});

test("service worker renders full source-derived context and exactly two supported actions", async () => {
  const harness = workerHarness({ maxActions: 2 });
  await dispatchWaitable(harness.listeners.get("push"), { data: { json: () => articlePayload() } });
  assert.equal(harness.notifications.length, 1);
  const notification = harness.notifications[0];
  assert.equal(notification.title, "Publisher releases model weights");
  assert.match(notification.options.body, /Example Lab · Open ecosystem · announcement/u);
  assert.match(notification.options.body, /Published: 2026-08-20T08:00:00Z · Detected: 2026-08-20T08:12:00Z/u);
  assert.match(notification.options.body, /Context source: publisher excerpt/u);
  assert.match(notification.options.body, /This context comes from the publisher excerpt/u);
  assert.deepEqual(Array.from(notification.options.actions, (item) => item.action), ["start_research", "dismiss"]);
  assert.deepEqual(Array.from(notification.options.actions, (item) => item.title), ["Start working", "Dismiss"]);
  assert.equal(harness.cached.size, 1);
  assert.doesNotMatch(serviceWorkerSource, /importScripts|https?:\/\//u);
});

test("service worker omits inline actions when the platform does not support both", async () => {
  const harness = workerHarness({ maxActions: 1 });
  await dispatchWaitable(harness.listeners.get("push"), { data: { json: () => articlePayload() } });
  assert.equal(harness.notifications[0].options.actions.length, 0);
});

test("service worker strips control and bidi characters while keeping markup inert text", async () => {
  const harness = workerHarness();
  await dispatchWaitable(harness.listeners.get("push"), {
    data: { json: () => articlePayload({
      title: "Publisher\u202e release <b>notice</b>",
      context: "Neutral\u0000 source context <img src=x onerror=alert(1)>",
    }) },
  });
  const notification = harness.notifications[0];
  assert.equal(notification.title, "Publisher release <b>notice</b>");
  assert.doesNotMatch(notification.options.body, /[\u0000\u202e]/u);
  assert.match(notification.options.body, /<img src=x onerror=alert\(1\)>/u);
});

test("notification click opens one device-local alert card and inline action posts its capability", async () => {
  const harness = workerHarness();
  const payload = articlePayload();
  await dispatchWaitable(harness.listeners.get("notificationclick"), {
    action: "",
    notification: { data: payload, close() {} },
  });
  assert.deepEqual(harness.opened, ["https://wire.example/notification/evt-article-1"]);

  await dispatchWaitable(harness.listeners.get("notificationclick"), {
    action: "start_research",
    notification: { data: payload, close() {} },
  });
  assert.equal(harness.requests.length, 1);
  assert.equal(harness.requests[0].request, "https://wire.example/api/push/actions");
  assert.deepEqual(JSON.parse(harness.requests[0].init.body), {
    schemaVersion: 1,
    eventId: "evt-article-1",
    action: "start_research",
    capability: "test-start-capability",
  });
});

test("follow-up and canary notifications have no article actions or alert card state", async () => {
  for (const kind of ["research_result", "canary"]) {
    const harness = workerHarness();
    const payload = articlePayload({ kind, actions: undefined, eventId: `evt-${kind}` });
    await dispatchWaitable(harness.listeners.get("push"), { data: { json: () => payload } });
    assert.equal(harness.notifications[0].options.actions.length, 0);
    assert.equal(harness.cached.size, 0);
  }
});

test("notification UI contains the privacy warning, explicit opt-in, and only two alert-card actions", async () => {
  const [settings, card, client] = await Promise.all([
    readFile(new URL("app/dashboard/push-settings.tsx", root), "utf8"),
    readFile(new URL("app/notification/[id]/notification-card.tsx", root), "utf8"),
    readFile(new URL("lib/push/client.ts", root), "utf8"),
  ]);
  assert.match(settings, /Enable notifications/u);
  assert.match(settings, /articles, announcements, papers, repositories, newsletters, and discovery signals/u);
  assert.match(settings, /Full titles and news context may appear on your lock screen/u);
  assert.match(settings, /Disable this device/u);
  assert.match(settings, /Disable all devices/u);
  assert.match(settings, /Add to Home Screen/u);
  assert.match(client, /Notification\.requestPermission\(\)/u);
  assert.match(card, /Start working/u);
  assert.match(card, /Dismiss/u);
  assert.equal((card.match(/<button\b/gu) || []).length, 2);
  assert.doesNotMatch(`${settings}\n${card}\n${serviceWorkerSource}`, /\b(?:importance|high-quality|priority|review score)\b/iu);
});

test("live-alert rollout stays manual and separate from subscription and canary controls", async () => {
  const [settings, client] = await Promise.all([
    readFile(new URL("app/dashboard/push-settings.tsx", root), "utf8"),
    readFile(new URL("lib/push/client.ts", root), "utf8"),
  ]);
  assert.match(settings, /Shadow review/u);
  assert.match(settings, /Live alerts/u);
  assert.match(settings, /Live alerts paused/u);
  assert.match(settings, /Activate relevant-item alerts \(manual\)/u);
  assert.match(settings, /Pause live alerts/u);
  assert.match(settings, /Return to shadow/u);
  assert.match(settings, /successful test notification and a completed 72-hour shadow review are required/u);
  assert.match(settings, /never activates automatically/u);
  assert.match(settings, /window\.confirm/u);
  assert.match(client, /fetch\(["']\/api\/push\/status["']/u);
  assert.match(client, /body:\s*JSON\.stringify\(\{ action \}\)/u);
  assert.match(settings, /sendPushCanary\(endpoint\)/u);
  assert.match(settings, /setPushRuntimeAction\(action\)/u);
});

test("revoking a subscription does not disable re-enabling the hosted Push service", async () => {
  const [client, settings] = await Promise.all([
    readFile(new URL("lib/push/client.ts", root), "utf8"),
    readFile(new URL("app/dashboard/push-settings.tsx", root), "utf8"),
  ]);
  const disableFunction = client.slice(
    client.indexOf("export async function disablePushNotifications"),
    client.indexOf("export async function sendPushCanary"),
  );
  assert.match(disableFunction, /await readPushSubscriptionStatus\(\)\.catch/u);
  assert.match(disableFunction, /enabled:\s*refreshed\?\.enabled \?\? true/u);
  assert.doesNotMatch(disableFunction, /enabled:\s*payload\.enabled/u);
  assert.match(settings, /setThisDeviceEnabled\(Boolean\(endpoint\) && nextStatus\.deviceCount > 0\)/u);
  assert.doesNotMatch(settings, /setThisDeviceEnabled\(Boolean\(endpoint\)\);/u);
});
