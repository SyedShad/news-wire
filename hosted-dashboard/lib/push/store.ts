import { masterPasswordVersion } from "../auth/config.ts";
import type { AuthRuntimeEnv } from "../auth/types.ts";
import { ensureArticleAlertCommand, ensureDashboardSchema } from "../dashboard/store.ts";
import type { JsonValue, NotificationArticleProjection } from "../dashboard/types.ts";
import {
  actionCapabilityHash,
  encryptSubscription,
  newActionCapability,
  pushConfiguration,
  subscriptionEndpointHash,
  type PushConfiguration,
} from "./crypto.ts";
import {
  articleProjectionToPayload,
  type PushAction,
  type PushPayload,
  type PushRuntimeMode,
  type PushSubscriptionInput,
  type StoredPushSubscription,
} from "./types.ts";

const ACTION_TTL_MS = 72 * 60 * 60_000;
const RECOVERY_LIMIT_MS = 72 * 60 * 60_000;
const SENDING_LEASE_MS = 5 * 60_000;
const PUSH_BATCH_LIMIT = 20;
const pushSchemaPromises = new WeakMap<object, Promise<void>>();

export async function ensurePushSchema(db: D1Database): Promise<void> {
  const key = db as unknown as object;
  const existing = pushSchemaPromises.get(key);
  if (existing) return existing;
  const setup = ensureDashboardSchema(db).then(async () => {
    const now = Date.now();
    await db.batch([
      db.prepare(`CREATE TABLE IF NOT EXISTS notification_projection_stage (
        stage_key TEXT PRIMARY KEY NOT NULL, sync_id TEXT NOT NULL,
        article_key TEXT NOT NULL, payload_json TEXT NOT NULL, staged_at INTEGER NOT NULL)`),
      db.prepare("CREATE INDEX IF NOT EXISTS notification_projection_stage_sync_idx ON notification_projection_stage (sync_id)"),
      db.prepare("CREATE INDEX IF NOT EXISTS notification_projection_stage_article_idx ON notification_projection_stage (article_key)"),
      db.prepare(`CREATE TABLE IF NOT EXISTS notification_event (
        event_id TEXT PRIMARY KEY NOT NULL, source_event_id TEXT NOT NULL,
        article_key TEXT NOT NULL UNIQUE,
        story_id TEXT NOT NULL, kind TEXT NOT NULL,
        payload_json TEXT NOT NULL, resolution TEXT NOT NULL,
        released_at INTEGER NOT NULL, resolved_at INTEGER)`),
      db.prepare("CREATE INDEX IF NOT EXISTS notification_event_released_idx ON notification_event (released_at)"),
      db.prepare(`CREATE TABLE IF NOT EXISTS push_subscription (
        id TEXT PRIMARY KEY NOT NULL, actor_id TEXT NOT NULL,
        endpoint_hash TEXT NOT NULL UNIQUE, subscription_ciphertext TEXT NOT NULL,
        credential_version TEXT NOT NULL, created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL, disabled_at INTEGER, last_success_at INTEGER)`),
      db.prepare("CREATE INDEX IF NOT EXISTS push_subscription_active_idx ON push_subscription (disabled_at, updated_at)"),
      db.prepare(`CREATE TABLE IF NOT EXISTS push_delivery (
        id TEXT PRIMARY KEY NOT NULL, event_id TEXT NOT NULL, subscription_id TEXT NOT NULL,
        status TEXT NOT NULL, attempt_count INTEGER NOT NULL,
        next_attempt_at INTEGER NOT NULL, last_attempt_at INTEGER, delivered_at INTEGER,
        provider_status INTEGER, last_error TEXT, created_at INTEGER NOT NULL,
        UNIQUE(event_id, subscription_id))`),
      db.prepare("CREATE INDEX IF NOT EXISTS push_delivery_outbox_idx ON push_delivery (status, next_attempt_at, created_at)"),
      db.prepare("CREATE INDEX IF NOT EXISTS push_delivery_event_idx ON push_delivery (event_id)"),
      db.prepare("CREATE INDEX IF NOT EXISTS push_delivery_subscription_idx ON push_delivery (subscription_id)"),
      db.prepare(`CREATE TABLE IF NOT EXISTS push_action_intent (
        id TEXT PRIMARY KEY NOT NULL, token_hash TEXT NOT NULL UNIQUE,
        event_id TEXT NOT NULL, subscription_id TEXT NOT NULL, story_id TEXT NOT NULL,
        action TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL, completed_at INTEGER, result_json TEXT)`),
      db.prepare("CREATE INDEX IF NOT EXISTS push_action_intent_expiry_idx ON push_action_intent (status, expires_at)"),
      db.prepare("CREATE INDEX IF NOT EXISTS push_action_intent_event_idx ON push_action_intent (event_id)"),
      db.prepare(`CREATE TABLE IF NOT EXISTS notification_global_action (
        event_id TEXT PRIMARY KEY NOT NULL, action TEXT NOT NULL,
        command_id TEXT, created_at INTEGER NOT NULL, result_json TEXT NOT NULL)`),
      db.prepare(`CREATE TABLE IF NOT EXISTS push_runtime_state (
        id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1), mode TEXT NOT NULL,
        activation_watermark INTEGER NOT NULL, updated_at INTEGER NOT NULL, updated_by TEXT)`),
      db.prepare(`INSERT OR IGNORE INTO push_runtime_state
        (id, mode, activation_watermark, updated_at, updated_by)
        VALUES (1, 'shadow', ?, ?, NULL)`).bind(now, now),
    ]);
  }).catch((error) => {
    pushSchemaPromises.delete(key);
    throw error;
  });
  pushSchemaPromises.set(key, setup);
  return setup;
}

type RuntimeRow = { mode: PushRuntimeMode; activation_watermark: number; updated_at: number };

async function runtimeRow(db: D1Database): Promise<RuntimeRow> {
  await ensurePushSchema(db);
  const row = await db.prepare(`SELECT mode, activation_watermark, updated_at
    FROM push_runtime_state WHERE id = 1`).first<RuntimeRow>();
  if (!row) throw new Error("push_runtime_state_unavailable");
  return row;
}

async function deactivateRotatedSubscriptions(source: AuthRuntimeEnv): Promise<void> {
  await source.DB.prepare(`UPDATE push_subscription SET disabled_at = ?, updated_at = ?
    WHERE disabled_at IS NULL AND credential_version != ?`)
    .bind(Date.now(), Date.now(), masterPasswordVersion(source)).run();
}

async function activeDeviceCount(source: AuthRuntimeEnv): Promise<number> {
  await deactivateRotatedSubscriptions(source);
  const row = await source.DB.prepare(`SELECT COUNT(*) AS count FROM push_subscription
    WHERE disabled_at IS NULL AND credential_version = ?`)
    .bind(masterPasswordVersion(source)).first<{ count: number }>();
  return Number(row?.count || 0);
}

export async function readPushStatus(source: AuthRuntimeEnv): Promise<{
  enabled: boolean;
  configured: boolean;
  vapidPublicKey: string;
  deviceCount: number;
  mode: PushRuntimeMode;
  activationWatermark: number;
}> {
  await ensurePushSchema(source.DB);
  const config = pushConfiguration(source);
  const runtime = await runtimeRow(source.DB);
  return {
    enabled: Boolean(config?.enabled),
    configured: Boolean(config),
    vapidPublicKey: config?.vapidPublicKey || "",
    deviceCount: await activeDeviceCount(source),
    mode: runtime.mode,
    activationWatermark: runtime.activation_watermark,
  };
}

export async function setPushRuntimeMode(
  source: AuthRuntimeEnv,
  mode: PushRuntimeMode,
  actorId: string,
): Promise<Awaited<ReturnType<typeof readPushStatus>>> {
  await ensurePushSchema(source.DB);
  const now = Date.now();
  // Every transition into active mode establishes a fresh activation
  // watermark. Historical/shadow events are never released retroactively.
  await source.DB.prepare(`UPDATE push_runtime_state SET mode = ?,
      activation_watermark = ?, updated_at = ?, updated_by = ? WHERE id = 1`)
    .bind(mode, now, now, actorId).run();
  return readPushStatus(source);
}

export async function registerPushSubscription(
  source: AuthRuntimeEnv,
  input: PushSubscriptionInput,
  actorId: string,
): Promise<number> {
  await ensurePushSchema(source.DB);
  const config = pushConfiguration(source);
  if (!config) throw new Error("push_not_configured");
  const endpointHash = await subscriptionEndpointHash(input.endpoint);
  const existing = await source.DB.prepare("SELECT id, created_at FROM push_subscription WHERE endpoint_hash = ?")
    .bind(endpointHash).first<{ id: string; created_at: number }>();
  const id = existing?.id || crypto.randomUUID();
  const stored: StoredPushSubscription = { id, ...input };
  const ciphertext = await encryptSubscription(config, stored);
  const now = Date.now();
  await source.DB.prepare(`INSERT INTO push_subscription
      (id, actor_id, endpoint_hash, subscription_ciphertext, credential_version,
       created_at, updated_at, disabled_at, last_success_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
    ON CONFLICT(endpoint_hash) DO UPDATE SET actor_id = excluded.actor_id,
      subscription_ciphertext = excluded.subscription_ciphertext,
      credential_version = excluded.credential_version, updated_at = excluded.updated_at,
      disabled_at = NULL`)
    .bind(id, actorId, endpointHash, ciphertext, masterPasswordVersion(source),
      existing?.created_at || now, now).run();
  return activeDeviceCount(source);
}

export async function revokePushSubscriptions(
  source: AuthRuntimeEnv,
  input: { scope: "device" | "all"; endpoint?: string },
): Promise<number> {
  await ensurePushSchema(source.DB);
  const now = Date.now();
  if (input.scope === "all") {
    await source.DB.prepare("UPDATE push_subscription SET disabled_at = ?, updated_at = ? WHERE disabled_at IS NULL")
      .bind(now, now).run();
  } else {
    const endpointHash = await subscriptionEndpointHash(input.endpoint || "");
    await source.DB.prepare(`UPDATE push_subscription SET disabled_at = ?, updated_at = ?
      WHERE endpoint_hash = ? AND disabled_at IS NULL`).bind(now, now, endpointHash).run();
  }
  return activeDeviceCount(source);
}

export async function stageNotificationArticles(
  db: D1Database,
  syncId: string,
  articles: NotificationArticleProjection[],
): Promise<void> {
  await ensurePushSchema(db);
  const now = Date.now();
  const statements = articles.map((article) => db.prepare(`INSERT INTO notification_projection_stage
      (stage_key, sync_id, article_key, payload_json, staged_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(stage_key) DO UPDATE SET payload_json = excluded.payload_json,
      staged_at = excluded.staged_at`)
    .bind(`${syncId}:${article.article_key}`, syncId, article.article_key, JSON.stringify(article), now));
  for (let offset = 0; offset < statements.length; offset += 50) {
    await db.batch(statements.slice(offset, offset + 50));
  }
}

async function activeSubscriptionIds(source: AuthRuntimeEnv): Promise<string[]> {
  await deactivateRotatedSubscriptions(source);
  const response = await source.DB.prepare(`SELECT id FROM push_subscription
    WHERE disabled_at IS NULL AND credential_version = ? ORDER BY created_at, id`)
    .bind(masterPasswordVersion(source)).all<{ id: string }>();
  return (response.results || []).map((row) => row.id);
}

async function ensureDeliveries(
  db: D1Database,
  eventId: string,
  subscriptionIds: string[],
  createdAt: number,
): Promise<void> {
  const statements = subscriptionIds.map((subscriptionId) => db.prepare(`INSERT OR IGNORE INTO push_delivery
      (id, event_id, subscription_id, status, attempt_count, next_attempt_at, created_at)
    VALUES (?, ?, ?, 'pending', 0, ?, ?)`)
    .bind(crypto.randomUUID(), eventId, subscriptionId, createdAt, createdAt));
  for (let offset = 0; offset < statements.length; offset += 50) {
    await db.batch(statements.slice(offset, offset + 50));
  }
}

export async function completeNotificationArticleSync(
  source: AuthRuntimeEnv,
  input: { syncId: string; digest: string; total: number; mode: "full" | "delta" },
): Promise<void> {
  await ensurePushSchema(source.DB);
  const count = await source.DB.prepare(`SELECT COUNT(*) AS count
    FROM notification_projection_stage WHERE sync_id = ?`).bind(input.syncId).first<{ count: number }>();
  if (input.mode === "full" && Number(count?.count || 0) !== input.total) {
    throw new Error("notification_projection_incomplete");
  }
  if (input.mode === "delta") {
    const projected = await source.DB.prepare(`SELECT COUNT(*) AS count FROM (
      SELECT article_key FROM notification_event WHERE kind = 'article_alert'
      UNION SELECT article_key FROM notification_projection_stage WHERE sync_id = ?
    )`).bind(input.syncId).first<{ count: number }>();
    if (Number(projected?.count || 0) !== input.total) throw new Error("notification_projection_incomplete");
  }
  const staged = await source.DB.prepare(`SELECT payload_json FROM notification_projection_stage
    WHERE sync_id = ? ORDER BY staged_at, article_key`).bind(input.syncId).all<{ payload_json: string }>();
  const runtime = await runtimeRow(source.DB);
  const subscriptions = runtime.mode === "active" ? await activeSubscriptionIds(source) : [];
  const now = Date.now();
  for (const row of staged.results || []) {
    const article = JSON.parse(row.payload_json) as NotificationArticleProjection;
    const eventId = `article:${article.article_key}`;
    const payload = articleProjectionToPayload(article);
    await source.DB.prepare(`INSERT OR IGNORE INTO notification_event
        (event_id, source_event_id, article_key, story_id, kind, payload_json, resolution, released_at)
      VALUES (?, ?, ?, ?, 'article_alert', ?, 'open', ?)`)
      .bind(eventId, article.event_id, article.article_key, article.story_id,
        JSON.stringify({ ...payload, eventId }), now).run();
    const detectedAt = Date.parse(article.detected_at);
    if (
      runtime.mode === "active" &&
      detectedAt >= runtime.activation_watermark &&
      detectedAt >= now - RECOVERY_LIMIT_MS
    ) {
      await ensureDeliveries(source.DB, eventId, subscriptions, now);
    }
  }
  await source.DB.batch([
    source.DB.prepare(`UPDATE projection_state SET notification_digest = ?, notification_total = ?
      WHERE id = 1 AND sync_id = ?`).bind(input.digest, input.total, input.syncId),
    source.DB.prepare("DELETE FROM notification_projection_stage WHERE staged_at < ?")
      .bind(now - RECOVERY_LIMIT_MS),
  ]);
}

export type ClaimedPushDelivery = {
  deliveryId: string;
  eventId: string;
  subscriptionId: string;
  subscriptionCiphertext: string;
  kind: PushPayload["kind"];
  storyId: string;
  payload: PushPayload;
  attemptCount: number;
  createdAt: number;
};

type DeliveryRow = {
  id: string; event_id: string; subscription_id: string; subscription_ciphertext: string;
  kind: PushPayload["kind"]; story_id: string; payload_json: string;
  attempt_count: number; created_at: number;
};

export async function claimPushDeliveries(source: AuthRuntimeEnv): Promise<ClaimedPushDelivery[]> {
  await ensurePushSchema(source.DB);
  const config = pushConfiguration(source);
  if (!config?.enabled) return [];
  await deactivateRotatedSubscriptions(source);
  const now = Date.now();
  await source.DB.batch([
    source.DB.prepare(`UPDATE push_delivery SET status = 'retry', next_attempt_at = ?
      WHERE status = 'sending' AND last_attempt_at < ?`).bind(now, now - SENDING_LEASE_MS),
    source.DB.prepare(`UPDATE push_delivery SET status = 'expired', last_error = 'delivery_expired'
      WHERE status IN ('pending', 'retry', 'sending') AND created_at <= ?`).bind(now - RECOVERY_LIMIT_MS),
    source.DB.prepare(`UPDATE push_action_intent SET status = 'expired'
      WHERE status = 'pending' AND expires_at <= ?`).bind(now),
  ]);
  const response = await source.DB.prepare(`SELECT d.id, d.event_id, d.subscription_id,
      d.attempt_count, d.created_at, s.subscription_ciphertext,
      e.kind, e.story_id, e.payload_json
    FROM push_delivery d
    JOIN push_subscription s ON s.id = d.subscription_id
    JOIN notification_event e ON e.event_id = d.event_id
    WHERE d.status IN ('pending', 'retry') AND d.next_attempt_at <= ?
      AND s.disabled_at IS NULL AND s.credential_version = ?
    ORDER BY d.created_at, d.id LIMIT ?`)
    .bind(now, masterPasswordVersion(source), PUSH_BATCH_LIMIT).all<DeliveryRow>();
  const claimed: ClaimedPushDelivery[] = [];
  for (const row of response.results || []) {
    const result = await source.DB.prepare(`UPDATE push_delivery SET status = 'sending',
        last_attempt_at = ?, attempt_count = attempt_count + 1, last_error = NULL
      WHERE id = ? AND status IN ('pending', 'retry') AND next_attempt_at <= ?`)
      .bind(now, row.id, now).run();
    if (result.meta.changes !== 1) continue;
    try {
      claimed.push({
        deliveryId: row.id,
        eventId: row.event_id,
        subscriptionId: row.subscription_id,
        subscriptionCiphertext: row.subscription_ciphertext,
        kind: row.kind,
        storyId: row.story_id,
        payload: JSON.parse(row.payload_json) as PushPayload,
        attemptCount: row.attempt_count + 1,
        createdAt: row.created_at,
      });
    } catch {
      await markPushDeliveryFailed(source.DB, row.id, null, "event_payload_invalid");
    }
  }
  return claimed;
}

export async function createDeliveryActionCapabilities(
  db: D1Database,
  config: PushConfiguration,
  delivery: ClaimedPushDelivery,
): Promise<{ startResearch: string; dismiss: string }> {
  const startResearch = newActionCapability();
  const dismiss = newActionCapability();
  const now = Date.now();
  await db.batch([
    db.prepare(`INSERT INTO push_action_intent
      (id, token_hash, event_id, subscription_id, story_id, action, status, created_at, expires_at)
      VALUES (?, ?, ?, ?, ?, 'start_research', 'pending', ?, ?)`)
      .bind(crypto.randomUUID(), await actionCapabilityHash(config, startResearch), delivery.eventId,
        delivery.subscriptionId, delivery.storyId, now, now + ACTION_TTL_MS),
    db.prepare(`INSERT INTO push_action_intent
      (id, token_hash, event_id, subscription_id, story_id, action, status, created_at, expires_at)
      VALUES (?, ?, ?, ?, ?, 'dismiss', 'pending', ?, ?)`)
      .bind(crypto.randomUUID(), await actionCapabilityHash(config, dismiss), delivery.eventId,
        delivery.subscriptionId, delivery.storyId, now, now + ACTION_TTL_MS),
  ]);
  return { startResearch, dismiss };
}

export async function markPushDeliveryDelivered(
  db: D1Database,
  deliveryId: string,
  subscriptionId: string,
  providerStatus: number,
): Promise<void> {
  const now = Date.now();
  await db.batch([
    db.prepare(`UPDATE push_delivery SET status = 'delivered', delivered_at = ?,
      provider_status = ?, last_error = NULL WHERE id = ? AND status = 'sending'`)
      .bind(now, providerStatus, deliveryId),
    db.prepare("UPDATE push_subscription SET last_success_at = ?, updated_at = ? WHERE id = ?")
      .bind(now, now, subscriptionId),
  ]);
}

export async function markPushDeliveryRetry(
  db: D1Database,
  deliveryId: string,
  attemptCount: number,
  providerStatus: number | null,
  errorCode: string,
): Promise<void> {
  const now = Date.now();
  if (attemptCount >= 6) {
    await markPushDeliveryFailed(db, deliveryId, providerStatus, "retry_limit_reached");
    return;
  }
  const delay = Math.min(30 * 60_000, 30_000 * 2 ** Math.max(0, attemptCount - 1));
  await db.prepare(`UPDATE push_delivery SET status = 'retry', next_attempt_at = ?,
      provider_status = ?, last_error = ? WHERE id = ? AND status = 'sending'`)
    .bind(now + delay, providerStatus, errorCode.slice(0, 120), deliveryId).run();
}

export async function markPushDeliveryFailed(
  db: D1Database,
  deliveryId: string,
  providerStatus: number | null,
  errorCode: string,
): Promise<void> {
  await db.prepare(`UPDATE push_delivery SET status = 'failed', provider_status = ?,
      last_error = ? WHERE id = ? AND status = 'sending'`)
    .bind(providerStatus, errorCode.slice(0, 120), deliveryId).run();
}

export async function disablePushSubscription(
  db: D1Database,
  subscriptionId: string,
  providerStatus: number,
): Promise<void> {
  const now = Date.now();
  await db.batch([
    db.prepare("UPDATE push_subscription SET disabled_at = ?, updated_at = ? WHERE id = ?")
      .bind(now, now, subscriptionId),
    db.prepare(`UPDATE push_delivery SET status = 'failed', provider_status = ?,
      last_error = 'subscription_gone' WHERE subscription_id = ? AND status IN ('pending', 'retry', 'sending')`)
      .bind(providerStatus, subscriptionId),
  ]);
}

type IntentRow = {
  id: string; event_id: string; subscription_id: string; story_id: string;
  action: PushAction; status: "pending" | "completed" | "expired";
  expires_at: number; result_json: string | null;
};

type GlobalActionRow = { action: PushAction; command_id: string | null; result_json: string };

export async function performPushAction(
  source: AuthRuntimeEnv,
  input: { eventId: string; action: PushAction; capability: string },
): Promise<Record<string, JsonValue>> {
  await ensurePushSchema(source.DB);
  const config = pushConfiguration(source);
  if (!config) throw new Error("push_not_configured");
  const tokenHash = await actionCapabilityHash(config, input.capability);
  const intent = await source.DB.prepare(`SELECT id, event_id, subscription_id, story_id,
      action, status, expires_at, result_json FROM push_action_intent WHERE token_hash = ?`)
    .bind(tokenHash).first<IntentRow>();
  if (!intent || intent.event_id !== input.eventId || intent.action !== input.action) {
    throw new Error("action_capability_invalid");
  }
  if (intent.status === "completed" && intent.result_json) {
    return JSON.parse(intent.result_json) as Record<string, JsonValue>;
  }
  if (intent.status !== "pending" || intent.expires_at <= Date.now()) {
    if (intent.status === "pending") {
      await source.DB.prepare("UPDATE push_action_intent SET status = 'expired' WHERE id = ?")
        .bind(intent.id).run();
    }
    throw new Error("action_capability_expired");
  }
  const event = await source.DB.prepare(`SELECT source_event_id, article_key, story_id, payload_json
    FROM notification_event WHERE event_id = ? AND kind = 'article_alert'`)
    .bind(intent.event_id).first<{
      source_event_id: string; article_key: string; story_id: string; payload_json: string;
    }>();
  if (!event) throw new Error("notification_event_unavailable");
  const commandId = crypto.randomUUID();
  const proposedResult: Record<string, JsonValue> = input.action === "start_research"
    ? { ok: true, status: "research_queued", commandId: commandId! }
    : { ok: true, status: "dismissed", commandId };
  await source.DB.prepare(`INSERT OR IGNORE INTO notification_global_action
      (event_id, action, command_id, created_at, result_json) VALUES (?, ?, ?, ?, ?)`)
    .bind(intent.event_id, input.action, commandId, Date.now(), JSON.stringify(proposedResult)).run();
  const global = await source.DB.prepare(`SELECT action, command_id, result_json
    FROM notification_global_action WHERE event_id = ?`).bind(intent.event_id).first<GlobalActionRow>();
  if (!global) throw new Error("notification_action_unavailable");
  let result = JSON.parse(global.result_json) as Record<string, JsonValue>;
  if (global.command_id) {
    await ensureArticleAlertCommand(source.DB, {
      commandId: global.command_id,
      operation: global.action === "start_research"
        ? "article_alert.start_research"
        : "article_alert.dismiss",
      requestedBy: `push:${intent.subscription_id}`,
      payload: {
        event_id: event.source_event_id,
        article_key: event.article_key,
        story_id: event.story_id,
        ...(global.action === "start_research" ? { purpose: "operator_review" } : {}),
      },
    });
  }
  if (global.action === "dismiss") {
    await source.DB.batch([
      source.DB.prepare(`UPDATE notification_event SET resolution = 'dismissed', resolved_at = ?
        WHERE event_id = ? AND resolution = 'open'`).bind(Date.now(), intent.event_id),
      source.DB.prepare(`UPDATE push_delivery SET status = 'failed', last_error = 'globally_dismissed'
        WHERE event_id = ? AND status IN ('pending', 'retry')`).bind(intent.event_id),
    ]);
  }
  if (global.action !== input.action) {
    result = global.action === "dismiss"
      ? { ok: true, status: "already_dismissed" }
      : { ok: true, status: "research_already_requested", commandId: global.command_id };
  }
  await source.DB.prepare(`UPDATE push_action_intent SET status = 'completed', completed_at = ?,
      result_json = ? WHERE id = ? AND status = 'pending'`)
    .bind(Date.now(), JSON.stringify(result), intent.id).run();
  if (global.action === "start_research") {
    await source.DB.prepare(`UPDATE notification_event SET resolution = 'research_requested', resolved_at = ?
      WHERE event_id = ? AND resolution = 'open'`).bind(Date.now(), intent.event_id).run();
  }
  return result;
}

async function queueInformationalEvent(
  source: AuthRuntimeEnv,
  input: { eventId: string; articleKey: string; storyId: string; payload: PushPayload; subscriptionIds: string[] },
): Promise<void> {
  await ensurePushSchema(source.DB);
  const now = Date.now();
  await source.DB.prepare(`INSERT OR IGNORE INTO notification_event
      (event_id, source_event_id, article_key, story_id, kind, payload_json, resolution, released_at, resolved_at)
    VALUES (?, ?, ?, ?, ?, ?, 'terminal', ?, ?)`)
    .bind(input.eventId, input.eventId, input.articleKey, input.storyId, input.payload.kind,
      JSON.stringify(input.payload), now, now).run();
  await ensureDeliveries(source.DB, input.eventId, input.subscriptionIds, now);
}

function safeResearchResult(value: JsonValue | null, ok: boolean, error: string | null): {
  status: string; resultCount: number; publishers: string[]; detail: string;
} {
  const record = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, JsonValue> : {};
  const status = ok && typeof record.status === "string" ? record.status.slice(0, 80) : "failed";
  const resultCount = ok && typeof record.result_count === "number" && Number.isSafeInteger(record.result_count)
    ? Math.max(0, record.result_count)
    : 0;
  const publishers = ok && Array.isArray(record.publishers)
    ? record.publishers.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
      .slice(0, 8).map((item) => item.trim().slice(0, 100))
    : [];
  const detail = ok && typeof record.detail === "string" ? record.detail.trim().slice(0, 400) : (error || "Research failed").slice(0, 400);
  return { status, resultCount, publishers, detail };
}

export async function queueResearchResultFollowup(
  source: AuthRuntimeEnv,
  command: { id: string; payload: Record<string, JsonValue> },
  completion: { ok: boolean; result: JsonValue | null; error: string | null },
): Promise<void> {
  await ensurePushSchema(source.DB);
  const articleKey = typeof command.payload.article_key === "string" ? command.payload.article_key : "";
  const original = await source.DB.prepare(`SELECT story_id, payload_json FROM notification_event
    WHERE article_key = ? AND kind = 'article_alert'`).bind(articleKey)
    .first<{ story_id: string; payload_json: string }>();
  if (!original) return;
  const runtime = await runtimeRow(source.DB);
  if (runtime.mode !== "active") return;
  const subscriptions = await activeSubscriptionIds(source);
  const sourcePayload = JSON.parse(original.payload_json) as PushPayload;
  const result = safeResearchResult(completion.result, completion.ok, completion.error);
  const publisherText = result.publishers.length ? ` Publishers: ${result.publishers.join(", ")}.` : "";
  const countText = `${result.resultCount} result${result.resultCount === 1 ? "" : "s"}`;
  const context = `Research ${result.status} with ${countText}.${publisherText} ${result.detail}`.trim().slice(0, 700);
  await queueInformationalEvent(source, {
    eventId: `research:${command.id}`,
    articleKey: `research:${command.id}`,
    storyId: original.story_id,
    subscriptionIds: subscriptions,
    payload: {
      schemaVersion: 1,
      kind: "research_result",
      eventId: `research:${command.id}`,
      storyId: original.story_id,
      title: `Research update: ${sourcePayload.title}`.slice(0, 500),
      publisher: "News Wire",
      category: "Research update",
      context,
      publishedAt: null,
      detectedAt: new Date().toISOString(),
      provenance: "Research result",
    },
  });
}

export async function queueCanary(source: AuthRuntimeEnv, endpoint: string): Promise<void> {
  await ensurePushSchema(source.DB);
  const config = pushConfiguration(source);
  if (!config?.enabled) throw new Error("push_delivery_disabled");
  const endpointHash = await subscriptionEndpointHash(endpoint);
  const subscription = await source.DB.prepare(`SELECT id FROM push_subscription
    WHERE endpoint_hash = ? AND disabled_at IS NULL AND credential_version = ?`)
    .bind(endpointHash, masterPasswordVersion(source)).first<{ id: string }>();
  if (!subscription) throw new Error("push_subscription_unavailable");
  const eventId = `canary:${crypto.randomUUID()}`;
  await queueInformationalEvent(source, {
    eventId,
    articleKey: eventId,
    storyId: "canary",
    subscriptionIds: [subscription.id],
    payload: {
      schemaVersion: 1,
      kind: "canary",
      eventId,
      storyId: "canary",
      title: "News Wire notification test",
      publisher: "News Wire",
      category: "Notification test",
      context: "Your browser can receive private News Wire notifications while the dashboard is closed.",
      publishedAt: null,
      detectedAt: new Date().toISOString(),
      provenance: "System test",
    },
  });
}
