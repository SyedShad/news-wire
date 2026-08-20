import { sql } from "drizzle-orm";
import { check, index, integer, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

export const authSessions = sqliteTable(
  "auth_sessions",
  {
    sessionHash: text("session_hash").primaryKey(),
    actorId: text("actor_id").notNull(),
    role: text("role", { enum: ["editor", "master"] }).notNull(),
    email: text("email"),
    credentialVersion: text("credential_version").notNull(),
    createdAt: integer("created_at").notNull(),
    expiresAt: integer("expires_at").notNull(),
  },
  (table) => [
    index("auth_sessions_expires_at_idx").on(table.expiresAt),
    check("auth_sessions_role_check", sql`${table.role} IN ('editor', 'master')`),
  ],
);

export const oauthTransactions = sqliteTable(
  "oauth_transactions",
  {
    stateHash: text("state_hash").primaryKey(),
    verifier: text("verifier").notNull(),
    nonce: text("nonce").notNull(),
    createdAt: integer("created_at").notNull(),
    expiresAt: integer("expires_at").notNull(),
  },
  (table) => [index("oauth_transactions_expires_at_idx").on(table.expiresAt)],
);

export const masterLoginState = sqliteTable("master_login_state", {
  fingerprint: text("fingerprint").primaryKey(),
  failures: integer("failures").notNull(),
  nextAllowedAt: integer("next_allowed_at").notNull(),
  lockedUntil: integer("locked_until").notNull(),
  updatedAt: integer("updated_at").notNull(),
});

export const auditEvents = sqliteTable(
  "audit_events",
  {
    id: text("id").primaryKey(),
    createdAt: integer("created_at").notNull(),
    actorId: text("actor_id"),
    role: text("role"),
    action: text("action").notNull(),
    outcome: text("outcome").notNull(),
    ipHash: text("ip_hash"),
    detail: text("detail"),
  },
  (table) => [
    index("audit_events_created_at_idx").on(table.createdAt),
    check(
      "audit_events_role_check",
      sql`${table.role} IS NULL OR ${table.role} IN ('editor', 'master')`,
    ),
  ],
);

export const dashboardState = sqliteTable(
  "dashboard_state",
  {
    id: integer("id").primaryKey(),
    schemaVersion: integer("schema_version").notNull(),
    generatedAt: text("generated_at").notNull(),
    receivedAt: integer("received_at").notNull(),
    digest: text("digest").notNull(),
    bridgeVersion: text("bridge_version").notNull(),
    snapshotJson: text("snapshot_json").notNull(),
  },
  (table) => [check("dashboard_state_singleton_check", sql`${table.id} = 1`)],
);

export const bridgeNonces = sqliteTable(
  "bridge_nonces",
  {
    nonce: text("nonce").primaryKey(),
    createdAt: integer("created_at").notNull(),
    expiresAt: integer("expires_at").notNull(),
  },
  (table) => [index("bridge_nonces_expires_at_idx").on(table.expiresAt)],
);

export const bridgeStatus = sqliteTable(
  "bridge_status",
  {
    id: integer("id").primaryKey(),
    lastSeenAt: integer("last_seen_at").notNull(),
    bridgeVersion: text("bridge_version").notNull(),
    runtimeVersion: text("runtime_version").notNull(),
    lastError: text("last_error"),
  },
  (table) => [check("bridge_status_singleton_check", sql`${table.id} = 1`)],
);

export const commandQueue = sqliteTable(
  "command_queue",
  {
    id: text("id").primaryKey(),
    operation: text("operation").notNull(),
    payloadJson: text("payload_json").notNull(),
    status: text("status", {
      enum: ["pending", "claimed", "completed", "failed"],
    }).notNull(),
    requestedBy: text("requested_by").notNull(),
    requestedRole: text("requested_role", { enum: ["master"] }).notNull(),
    createdAt: integer("created_at").notNull(),
    claimedAt: integer("claimed_at"),
    completedAt: integer("completed_at"),
    expiresAt: integer("expires_at").notNull(),
    attemptCount: integer("attempt_count").notNull(),
    resultJson: text("result_json"),
    error: text("error"),
  },
  (table) => [
    index("command_queue_status_created_at_idx").on(table.status, table.createdAt),
    check(
      "command_queue_status_check",
      sql`${table.status} IN ('pending', 'claimed', 'completed', 'failed')`,
    ),
    check("command_queue_requested_role_check", sql`${table.requestedRole} = 'master'`),
  ],
);

export const projectionState = sqliteTable(
  "projection_state",
  {
    id: integer("id").primaryKey(),
    syncId: text("sync_id").notNull(),
    storyDigest: text("story_digest").notNull(),
    storyTotal: integer("story_total").notNull(),
    resourceDigest: text("resource_digest").notNull(),
    resourceTotal: integer("resource_total").notNull(),
    notificationDigest: text("notification_digest").notNull().default(""),
    notificationTotal: integer("notification_total").notNull().default(0),
    generatedAt: text("generated_at").notNull(),
    receivedAt: integer("received_at").notNull(),
    runtimeVersion: text("runtime_version").notNull(),
    bridgeVersion: text("bridge_version").notNull(),
    snapshotJson: text("snapshot_json").notNull(),
  },
  (table) => [check("projection_state_singleton_check", sql`${table.id} = 1`)],
);

export const resourceProjection = sqliteTable(
  "resource_projection",
  {
    resourceKey: text("resource_key").primaryKey(),
    resourceType: text("resource_type").notNull(),
    resourceId: text("resource_id").notNull(),
    syncId: text("sync_id").notNull(),
    rank: integer("rank").notNull(),
    payloadJson: text("payload_json").notNull(),
    updatedAt: integer("updated_at").notNull(),
  },
  (table) => [
    index("resource_projection_type_rank_idx").on(table.resourceType, table.rank),
    index("resource_projection_sync_idx").on(table.syncId),
    index("resource_projection_resource_idx").on(table.resourceType, table.resourceId),
  ],
);

export const storyProjection = sqliteTable(
  "story_projection",
  {
    id: text("id").primaryKey(),
    syncId: text("sync_id").notNull(),
    status: text("status").notNull(),
    lane: text("lane").notNull(),
    freshness: text("freshness").notNull(),
    researchStatus: text("research_status").notNull(),
    ingestionContext: text("ingestion_context").notNull(),
    isReviewCurrent: integer("is_review_current").notNull(),
    isCorrection: integer("is_correction").notNull(),
    priorityRank: integer("priority_rank").notNull(),
    newestRank: integer("newest_rank").notNull(),
    summaryJson: text("summary_json").notNull(),
    updatedAt: integer("updated_at").notNull(),
  },
  (table) => [
    index("story_projection_priority_idx").on(table.priorityRank),
    index("story_projection_newest_idx").on(table.newestRank),
    index("story_projection_window_idx").on(table.isReviewCurrent, table.freshness, table.status),
    index("story_projection_sync_idx").on(table.syncId),
  ],
);

export const detailCache = sqliteTable(
  "detail_cache",
  {
    resourceKey: text("resource_key").primaryKey(),
    resourceType: text("resource_type", { enum: ["story", "draft"] }).notNull(),
    resourceId: text("resource_id").notNull(),
    payloadJson: text("payload_json").notNull(),
    updatedAt: integer("updated_at").notNull(),
    expiresAt: integer("expires_at").notNull(),
  },
  (table) => [index("detail_cache_expires_idx").on(table.expiresAt)],
);

export const readRequests = sqliteTable(
  "read_request",
  {
    id: text("id").primaryKey(),
    resourceType: text("resource_type", { enum: ["story", "draft"] }).notNull(),
    resourceId: text("resource_id").notNull(),
    status: text("status", { enum: ["pending", "claimed", "completed", "failed"] }).notNull(),
    requestedBy: text("requested_by").notNull(),
    createdAt: integer("created_at").notNull(),
    claimedAt: integer("claimed_at"),
    completedAt: integer("completed_at"),
    expiresAt: integer("expires_at").notNull(),
    error: text("error"),
  },
  (table) => [
    index("read_request_status_created_idx").on(table.status, table.createdAt),
    index("read_request_resource_idx").on(table.resourceType, table.resourceId),
    check(
      "read_request_status_check",
      sql`${table.status} IN ('pending', 'claimed', 'completed', 'failed')`,
    ),
    check(
      "read_request_resource_type_check",
      sql`${table.resourceType} IN ('story', 'draft')`,
    ),
  ],
);

export const notificationProjectionStage = sqliteTable(
  "notification_projection_stage",
  {
    stageKey: text("stage_key").primaryKey(),
    syncId: text("sync_id").notNull(),
    articleKey: text("article_key").notNull(),
    payloadJson: text("payload_json").notNull(),
    stagedAt: integer("staged_at").notNull(),
  },
  (table) => [
    index("notification_projection_stage_sync_idx").on(table.syncId),
    index("notification_projection_stage_article_idx").on(table.articleKey),
  ],
);

export const notificationEvents = sqliteTable(
  "notification_event",
  {
    eventId: text("event_id").primaryKey(),
    sourceEventId: text("source_event_id").notNull(),
    articleKey: text("article_key").notNull().unique(),
    storyId: text("story_id").notNull(),
    kind: text("kind", { enum: ["article_alert", "research_result", "canary"] }).notNull(),
    payloadJson: text("payload_json").notNull(),
    resolution: text("resolution", {
      enum: ["open", "research_requested", "dismissed", "terminal"],
    }).notNull(),
    releasedAt: integer("released_at").notNull(),
    resolvedAt: integer("resolved_at"),
  },
  (table) => [
    index("notification_event_released_idx").on(table.releasedAt),
    check(
      "notification_event_kind_check",
      sql`${table.kind} IN ('article_alert', 'research_result', 'canary')`,
    ),
    check(
      "notification_event_resolution_check",
      sql`${table.resolution} IN ('open', 'research_requested', 'dismissed', 'terminal')`,
    ),
  ],
);

export const pushSubscriptions = sqliteTable(
  "push_subscription",
  {
    id: text("id").primaryKey(),
    actorId: text("actor_id").notNull(),
    endpointHash: text("endpoint_hash").notNull().unique(),
    subscriptionCiphertext: text("subscription_ciphertext").notNull(),
    credentialVersion: text("credential_version").notNull(),
    createdAt: integer("created_at").notNull(),
    updatedAt: integer("updated_at").notNull(),
    disabledAt: integer("disabled_at"),
    lastSuccessAt: integer("last_success_at"),
  },
  (table) => [
    index("push_subscription_active_idx").on(table.disabledAt, table.updatedAt),
  ],
);

export const pushDeliveries = sqliteTable(
  "push_delivery",
  {
    id: text("id").primaryKey(),
    eventId: text("event_id").notNull(),
    subscriptionId: text("subscription_id").notNull(),
    status: text("status", {
      enum: ["pending", "sending", "retry", "delivered", "failed", "expired"],
    }).notNull(),
    attemptCount: integer("attempt_count").notNull(),
    nextAttemptAt: integer("next_attempt_at").notNull(),
    lastAttemptAt: integer("last_attempt_at"),
    deliveredAt: integer("delivered_at"),
    providerStatus: integer("provider_status"),
    lastError: text("last_error"),
    createdAt: integer("created_at").notNull(),
  },
  (table) => [
    index("push_delivery_outbox_idx").on(table.status, table.nextAttemptAt, table.createdAt),
    index("push_delivery_event_idx").on(table.eventId),
    index("push_delivery_subscription_idx").on(table.subscriptionId),
    uniqueIndex("push_delivery_event_subscription_unique").on(table.eventId, table.subscriptionId),
  ],
);

export const pushActionIntents = sqliteTable(
  "push_action_intent",
  {
    id: text("id").primaryKey(),
    tokenHash: text("token_hash").notNull().unique(),
    eventId: text("event_id").notNull(),
    subscriptionId: text("subscription_id").notNull(),
    storyId: text("story_id").notNull(),
    action: text("action", { enum: ["start_research", "dismiss"] }).notNull(),
    status: text("status", { enum: ["pending", "completed", "expired"] }).notNull(),
    createdAt: integer("created_at").notNull(),
    expiresAt: integer("expires_at").notNull(),
    completedAt: integer("completed_at"),
    resultJson: text("result_json"),
  },
  (table) => [
    index("push_action_intent_expiry_idx").on(table.status, table.expiresAt),
    index("push_action_intent_event_idx").on(table.eventId),
    check("push_action_intent_action_check", sql`${table.action} IN ('start_research', 'dismiss')`),
  ],
);

export const notificationGlobalActions = sqliteTable("notification_global_action", {
  eventId: text("event_id").primaryKey(),
  action: text("action", { enum: ["start_research", "dismiss"] }).notNull(),
  commandId: text("command_id"),
  createdAt: integer("created_at").notNull(),
  resultJson: text("result_json").notNull(),
});

export const pushRuntimeState = sqliteTable(
  "push_runtime_state",
  {
    id: integer("id").primaryKey(),
    mode: text("mode", { enum: ["shadow", "active", "paused"] }).notNull(),
    activationWatermark: integer("activation_watermark").notNull(),
    updatedAt: integer("updated_at").notNull(),
    updatedBy: text("updated_by"),
  },
  (table) => [
    check("push_runtime_state_singleton_check", sql`${table.id} = 1`),
    check("push_runtime_state_mode_check", sql`${table.mode} IN ('shadow', 'active', 'paused')`),
  ],
);
