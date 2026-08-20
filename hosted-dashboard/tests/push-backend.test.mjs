import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { base64UrlEncode } from "../lib/auth/crypto.ts";
import { parseBridgeSync } from "../lib/dashboard/validation.ts";
import {
  actionCapabilityHash,
  pushConfiguration,
} from "../lib/push/crypto.ts";
import { dispatchPendingNotifications } from "../lib/push/dispatch.ts";
import { isTrustedPushActionRequest } from "../lib/push/http.ts";
import {
  completeNotificationArticleSync,
  ensurePushSchema,
  performPushAction,
  queueCanary,
  queueResearchResultFollowup,
  readPushStatus,
  registerPushSubscription,
  revokePushSubscriptions,
  setPushRuntimeMode,
  stageNotificationArticles,
} from "../lib/push/store.ts";
import {
  parseCanaryRequest,
  parsePushAction,
  parsePushRuntimeAction,
  parsePushSubscription,
  parseSubscriptionRevocation,
  validatePushEndpoint,
} from "../lib/push/validation.ts";

class Statement {
  constructor(database, sql) { this.database = database; this.sql = sql; this.values = []; }
  bind(...values) { this.values = values; return this; }
  async run() {
    const result = this.database.sqlite.prepare(this.sql).run(...this.values);
    return { success: true, meta: { changes: Number(result.changes) } };
  }
  async first() { return this.database.sqlite.prepare(this.sql).get(...this.values) || null; }
  async all() { return { success: true, results: this.database.sqlite.prepare(this.sql).all(...this.values) }; }
}

class SqliteD1 {
  constructor() { this.sqlite = new DatabaseSync(":memory:"); }
  prepare(sql) { return new Statement(this, sql); }
  async batch(statements) {
    const results = [];
    for (const statement of statements) results.push(await statement.run());
    return results;
  }
}

async function keyMaterial() {
  const pair = await crypto.subtle.generateKey(
    { name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"],
  );
  const publicKey = new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey));
  const privateKey = await crypto.subtle.exportKey("jwk", pair.privateKey);
  return { publicKey: base64UrlEncode(publicKey), privateKey: privateKey.d };
}

async function environment(database) {
  const vapid = await keyMaterial();
  return {
    DB: database,
    MASTER_PASSWORD_VERSION: "test-v1",
    PUSH_VAPID_PUBLIC_KEY: vapid.publicKey,
    PUSH_VAPID_PRIVATE_KEY: vapid.privateKey,
    PUSH_VAPID_SUBJECT: "mailto:owner@example.invalid",
    PUSH_SUBSCRIPTION_ENCRYPTION_KEY: base64UrlEncode(crypto.getRandomValues(new Uint8Array(32))),
    PUSH_ACTION_SECRET: "test-action-secret-with-more-than-thirty-two-characters",
    PUSH_DELIVERY_ENABLED: "true",
  };
}

function article(index, overrides = {}) {
  return {
    event_id: String(index),
    article_key: index.toString(16).padStart(64, "0"),
    story_id: `story-${index}`,
    canonical_url: `https://example.com/news/${index}`,
    title: "Same title is allowed at distinct URLs",
    publisher: "Example Publisher",
    category: "Open ecosystem",
    context: "Example Publisher reports a new release. The publisher describes the available artifact.",
    provenance: "publisher_excerpt",
    source_type: "article",
    published_at: null,
    detected_at: new Date(Date.now() + 1_000).toISOString(),
    ...overrides,
  };
}

test("notification projection is strict, relevance-only, and accepts integer local event ids", () => {
  const parsed = parseBridgeSync({
    schema_version: 2,
    kind: "notification_articles",
    sync_id: "0123456789abcdef",
    mode: "delta",
    notification_articles: [{ ...article(1), event_id: 1 }],
    deleted_ids: [],
  });
  assert.equal(parsed.notification_articles[0].event_id, "1");
  assert.throws(() => parseBridgeSync({
    schema_version: 2,
    kind: "notification_articles",
    sync_id: "0123456789abcdef",
    mode: "delta",
    notification_articles: [{ ...article(1), quality_score: 99 }],
    deleted_ids: [],
  }), /fields are invalid/);
});

test("push endpoints reject SSRF targets and accept only known Web Push providers", () => {
  for (const endpoint of [
    "http://fcm.googleapis.com/send/x",
    "https://127.0.0.1/push",
    "https://localhost/push",
    "https://metadata.google.internal/push",
    "https://example.com/push",
    "https://fcm.googleapis.com:8443/send/x",
    "https://user@fcm.googleapis.com/send/x",
  ]) assert.throws(() => validatePushEndpoint(endpoint), /supported Web Push provider/);
  for (const endpoint of [
    "https://fcm.googleapis.com/fcm/send/test",
    "https://updates.push.services.mozilla.com/wpush/v2/test",
    "https://web.push.apple.com/Q/test",
    "https://wns2-notify.notify.windows.com/w/?token=test",
  ]) assert.equal(validatePushEndpoint(endpoint).protocol, "https:");
});

test("push API bodies use closed schemas and bounded subscription material", async () => {
  const client = await keyMaterial();
  const endpoint = "https://fcm.googleapis.com/fcm/send/device";
  const subscription = { endpoint, expirationTime: null, keys: {
    p256dh: client.publicKey,
    auth: base64UrlEncode(crypto.getRandomValues(new Uint8Array(16))),
  } };
  assert.deepEqual(parsePushSubscription({ subscription }), subscription);
  assert.deepEqual(parseSubscriptionRevocation({ scope: "device", endpoint }), { scope: "device", endpoint });
  assert.deepEqual(parseSubscriptionRevocation({ scope: "all", endpoint }), { scope: "all" });
  assert.deepEqual(parseCanaryRequest({ endpoint }), { endpoint });
  assert.deepEqual(parsePushRuntimeAction({ action: "pause" }), { action: "pause" });
  const capability = base64UrlEncode(crypto.getRandomValues(new Uint8Array(32)));
  assert.deepEqual(parsePushAction({ schemaVersion: 1, eventId: "event", action: "dismiss", capability }), {
    eventId: "event", action: "dismiss", capability,
  });
  assert.throws(() => parsePushSubscription({ subscription: { ...subscription, endpoint: "https://example.com/push" } }), /supported/);
  assert.throws(() => parseSubscriptionRevocation({ scope: "device" }), /endpoint/);
  assert.throws(() => parsePushAction({ schemaVersion: 1, eventId: "event", action: "rank", capability }), /action/);
  assert.throws(() => parsePushRuntimeAction({ action: "auto_activate" }), /runtime action/);
});

test("capability action origin rejects cross-site requests without requiring a live session", () => {
  const source = { AUTH_BASE_URL: "https://dashboard.example.chatgpt.site" };
  assert.equal(isTrustedPushActionRequest(new Request("https://dashboard.example.chatgpt.site/api/push/actions", {
    method: "POST", headers: { origin: "https://dashboard.example.chatgpt.site", "sec-fetch-site": "same-origin" },
  }), source), true);
  assert.equal(isTrustedPushActionRequest(new Request("https://dashboard.example.chatgpt.site/api/push/actions", {
    method: "POST", headers: { origin: "https://evil.example", "sec-fetch-site": "cross-site" },
  }), source), false);
});

test("shadow watermark, immutable delta sync, and distinct URL identity prevent backfill and false dedupe", async () => {
  const database = new SqliteD1();
  const source = await environment(database);
  await ensurePushSchema(database);
  const client = await keyMaterial();
  await registerPushSubscription(source, {
    endpoint: "https://fcm.googleapis.com/fcm/send/test-device",
    expirationTime: null,
    keys: { p256dh: client.publicKey, auth: base64UrlEncode(crypto.getRandomValues(new Uint8Array(16))) },
  }, "master");
  await stageNotificationArticles(database, "sync-shadow-0001", [article(1)]);
  await completeNotificationArticleSync(source, {
    syncId: "sync-shadow-0001", digest: "a".repeat(64), total: 1, mode: "full",
  });
  assert.equal((await database.prepare("SELECT COUNT(*) count FROM push_delivery").first()).count, 0);
  await setPushRuntimeMode(source, "active", "master");
  await stageNotificationArticles(database, "sync-delta-000002", [article(2)]);
  await completeNotificationArticleSync(source, {
    syncId: "sync-delta-000002", digest: "b".repeat(64), total: 2, mode: "delta",
  });
  assert.equal((await database.prepare("SELECT COUNT(*) count FROM notification_event WHERE kind = 'article_alert'").first()).count, 2);
  assert.equal((await database.prepare("SELECT COUNT(*) count FROM push_delivery").first()).count, 1);
  await completeNotificationArticleSync(source, {
    syncId: "sync-delta-000002", digest: "b".repeat(64), total: 2, mode: "delta",
  });
  assert.equal((await database.prepare("SELECT COUNT(*) count FROM push_delivery").first()).count, 1);
});

test("capability actions are replay-safe and queue only durable alert commands", async () => {
  const database = new SqliteD1();
  const source = await environment(database);
  await ensurePushSchema(database);
  const config = pushConfiguration(source);
  const now = Date.now();
  await database.prepare(`INSERT INTO notification_event
      (event_id, source_event_id, article_key, story_id, kind, payload_json, resolution, released_at)
    VALUES ('article:test', '17', ?, 'story-17', 'article_alert', ?, 'open', ?)`)
    .bind("f".repeat(64), JSON.stringify({ schemaVersion: 1, eventId: "article:test" }), now).run();
  await database.prepare(`INSERT INTO push_subscription
      (id, actor_id, endpoint_hash, subscription_ciphertext, credential_version, created_at, updated_at)
    VALUES ('device-1', 'master', 'hash', 'ciphertext', 'test-v1', ?, ?)`).bind(now, now).run();
  const capability = base64UrlEncode(crypto.getRandomValues(new Uint8Array(32)));
  const tokenHash = await actionCapabilityHash(config, capability);
  await database.prepare(`INSERT INTO push_action_intent
      (id, token_hash, event_id, subscription_id, story_id, action, status, created_at, expires_at)
    VALUES ('intent-1', ?, 'article:test', 'device-1', 'story-17', 'dismiss', 'pending', ?, ?)`)
    .bind(tokenHash, now, now + 72 * 60 * 60_000).run();
  const first = await performPushAction(source, {
    eventId: "article:test", action: "dismiss", capability,
  });
  const replay = await performPushAction(source, {
    eventId: "article:test", action: "dismiss", capability,
  });
  assert.deepEqual(replay, first);
  const command = await database.prepare("SELECT operation, expires_at, payload_json FROM command_queue").first();
  assert.equal(command.operation, "article_alert.dismiss");
  assert.ok(command.expires_at - now >= 71 * 60 * 60_000);
  assert.equal(JSON.parse(command.payload_json).event_id, "17");
  assert.equal((await database.prepare("SELECT COUNT(*) count FROM command_queue").first()).count, 1);
});

test("dispatch stays below 3 KB, follows no redirects, and deactivates gone subscriptions", async () => {
  const database = new SqliteD1();
  const source = await environment(database);
  const client = await keyMaterial();
  const endpoint = "https://fcm.googleapis.com/fcm/send/gone-device";
  await registerPushSubscription(source, {
    endpoint,
    expirationTime: null,
    keys: { p256dh: client.publicKey, auth: base64UrlEncode(crypto.getRandomValues(new Uint8Array(16))) },
  }, "master");
  await queueCanary(source, endpoint);
  const previousFetch = globalThis.fetch;
  let captured;
  globalThis.fetch = async (_url, init) => {
    captured = init;
    return new Response(null, { status: 410 });
  };
  try {
    await dispatchPendingNotifications(source);
  } finally {
    globalThis.fetch = previousFetch;
  }
  assert.equal(captured.redirect, "manual");
  assert.ok(captured.body.byteLength < 3_072);
  assert.equal((await database.prepare("SELECT COUNT(*) count FROM push_subscription WHERE disabled_at IS NOT NULL").first()).count, 1);
  assert.equal((await database.prepare("SELECT status FROM push_delivery").first()).status, "failed");
});

test("dispatch handles accepted, rejected, transient, and network provider outcomes", async () => {
  for (const scenario of [
    { name: "accepted", response: 202, expected: "delivered" },
    { name: "rejected", response: 400, expected: "failed" },
    { name: "transient", response: 503, expected: "retry" },
    { name: "network", response: null, expected: "retry" },
  ]) {
    const database = new SqliteD1();
    const source = await environment(database);
    const client = await keyMaterial();
    const endpoint = `https://fcm.googleapis.com/fcm/send/${scenario.name}`;
    await registerPushSubscription(source, {
      endpoint, expirationTime: null,
      keys: { p256dh: client.publicKey, auth: base64UrlEncode(crypto.getRandomValues(new Uint8Array(16))) },
    }, "master");
    await queueCanary(source, endpoint);
    const previousFetch = globalThis.fetch;
    globalThis.fetch = async () => {
      if (scenario.response === null) throw new Error("test network failure");
      return new Response(null, { status: scenario.response });
    };
    try { await dispatchPendingNotifications(source); } finally { globalThis.fetch = previousFetch; }
    assert.equal((await database.prepare("SELECT status FROM push_delivery").first()).status, scenario.expected);
  }
});

test("article delivery creates only HMAC action intents and terminal research queues a follow-up", async () => {
  const database = new SqliteD1();
  const source = await environment(database);
  const client = await keyMaterial();
  const endpoint = "https://fcm.googleapis.com/fcm/send/article-device";
  await registerPushSubscription(source, {
    endpoint, expirationTime: null,
    keys: { p256dh: client.publicKey, auth: base64UrlEncode(crypto.getRandomValues(new Uint8Array(16))) },
  }, "master");
  await setPushRuntimeMode(source, "active", "master");
  await stageNotificationArticles(database, "sync-article-0001", [article(30)]);
  await completeNotificationArticleSync(source, {
    syncId: "sync-article-0001", digest: "c".repeat(64), total: 1, mode: "full",
  });
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(null, { status: 201 });
  try { await dispatchPendingNotifications(source); } finally { globalThis.fetch = previousFetch; }
  assert.equal((await database.prepare("SELECT COUNT(*) count FROM push_action_intent").first()).count, 2);
  const hashes = await database.prepare("SELECT token_hash FROM push_action_intent").all();
  assert.ok(hashes.results.every((row) => /^[A-Za-z0-9_-]{43}$/u.test(row.token_hash)));
  await queueResearchResultFollowup(source, {
    id: "00000000-0000-4000-8000-000000000030",
    payload: { article_key: article(30).article_key },
  }, {
    ok: true,
    result: { status: "completed", result_count: 2, publishers: ["Publisher A", "Publisher B"], detail: "Fresh search finished." },
    error: null,
  });
  const followup = await database.prepare("SELECT payload_json FROM notification_event WHERE kind = 'research_result'").first();
  assert.match(JSON.parse(followup.payload_json).context, /2 results.*Publisher A, Publisher B/u);
});

test("runtime status exposes the safe default and device revocation is scoped", async () => {
  const database = new SqliteD1();
  const source = await environment(database);
  const client = await keyMaterial();
  for (const suffix of ["one", "two"]) {
    await registerPushSubscription(source, {
      endpoint: `https://fcm.googleapis.com/fcm/send/${suffix}`,
      expirationTime: null,
      keys: { p256dh: client.publicKey, auth: base64UrlEncode(crypto.getRandomValues(new Uint8Array(16))) },
    }, "master");
  }
  const status = await readPushStatus(source);
  assert.equal(status.mode, "shadow");
  assert.equal(status.deviceCount, 2);
  assert.equal(await revokePushSubscriptions(source, {
    scope: "device", endpoint: "https://fcm.googleapis.com/fcm/send/one",
  }), 1);
  assert.equal(await revokePushSubscriptions(source, { scope: "all" }), 0);
});
