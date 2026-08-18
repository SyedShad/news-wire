import type {
  DashboardSnapshot,
  JsonValue,
  OwnerCommand,
  ReadRequest,
  ResourceProjection,
  StoredDashboardSnapshot,
  StoryPage,
  StoryProjection,
} from "./types.ts";

const schemaPromises = new WeakMap<object, Promise<void>>();
const COMMAND_TTL_MS = 5 * 60_000;
const READ_TTL_MS = 60_000;
const DETAIL_TTL_MS = 24 * 60 * 60_000;
const BRIDGE_STALE_MS = 45_000;

export async function ensureDashboardSchema(db: D1Database): Promise<void> {
  const key = db as unknown as object;
  const existing = schemaPromises.get(key);
  if (existing) return existing;
  const setup = db.batch([
    db.prepare(`CREATE TABLE IF NOT EXISTS dashboard_state (
      id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1), schema_version INTEGER NOT NULL,
      generated_at TEXT NOT NULL, received_at INTEGER NOT NULL, digest TEXT NOT NULL,
      bridge_version TEXT NOT NULL, snapshot_json TEXT NOT NULL)`),
    db.prepare(`CREATE TABLE IF NOT EXISTS bridge_nonces (
      nonce TEXT PRIMARY KEY NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)`),
    db.prepare("CREATE INDEX IF NOT EXISTS bridge_nonces_expires_at_idx ON bridge_nonces (expires_at)"),
    db.prepare(`CREATE TABLE IF NOT EXISTS bridge_status (
      id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1), last_seen_at INTEGER NOT NULL,
      bridge_version TEXT NOT NULL, runtime_version TEXT NOT NULL, last_error TEXT)`),
    db.prepare(`CREATE TABLE IF NOT EXISTS command_queue (
      id TEXT PRIMARY KEY NOT NULL, operation TEXT NOT NULL, payload_json TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('pending', 'claimed', 'completed', 'failed')),
      requested_by TEXT NOT NULL, requested_role TEXT NOT NULL CHECK (requested_role = 'master'),
      created_at INTEGER NOT NULL, claimed_at INTEGER, completed_at INTEGER,
      expires_at INTEGER NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
      result_json TEXT, error TEXT)`),
    db.prepare("CREATE INDEX IF NOT EXISTS command_queue_status_created_at_idx ON command_queue (status, created_at)"),
    db.prepare(`CREATE TABLE IF NOT EXISTS projection_state (
      id INTEGER PRIMARY KEY NOT NULL CHECK (id = 1), sync_id TEXT NOT NULL,
      story_digest TEXT NOT NULL, story_total INTEGER NOT NULL, generated_at TEXT NOT NULL,
      resource_digest TEXT NOT NULL, resource_total INTEGER NOT NULL,
      received_at INTEGER NOT NULL, runtime_version TEXT NOT NULL,
      bridge_version TEXT NOT NULL, snapshot_json TEXT NOT NULL)`),
    db.prepare(`CREATE TABLE IF NOT EXISTS resource_projection (
      resource_key TEXT PRIMARY KEY NOT NULL, resource_type TEXT NOT NULL,
      resource_id TEXT NOT NULL, sync_id TEXT NOT NULL, rank INTEGER NOT NULL,
      payload_json TEXT NOT NULL, updated_at INTEGER NOT NULL)`),
    db.prepare("CREATE INDEX IF NOT EXISTS resource_projection_type_rank_idx ON resource_projection (resource_type, rank)"),
    db.prepare("CREATE INDEX IF NOT EXISTS resource_projection_sync_idx ON resource_projection (sync_id)"),
    db.prepare("CREATE INDEX IF NOT EXISTS resource_projection_resource_idx ON resource_projection (resource_type, resource_id)"),
    db.prepare(`CREATE TABLE IF NOT EXISTS story_projection (
      id TEXT PRIMARY KEY NOT NULL, sync_id TEXT NOT NULL, status TEXT NOT NULL,
      lane TEXT NOT NULL, freshness TEXT NOT NULL, research_status TEXT NOT NULL,
      ingestion_context TEXT NOT NULL, is_review_current INTEGER NOT NULL,
      is_correction INTEGER NOT NULL, priority_rank INTEGER NOT NULL,
      newest_rank INTEGER NOT NULL, summary_json TEXT NOT NULL, updated_at INTEGER NOT NULL)`),
    db.prepare("CREATE INDEX IF NOT EXISTS story_projection_priority_idx ON story_projection (priority_rank)"),
    db.prepare("CREATE INDEX IF NOT EXISTS story_projection_newest_idx ON story_projection (newest_rank)"),
    db.prepare("CREATE INDEX IF NOT EXISTS story_projection_window_idx ON story_projection (is_review_current, freshness, status)"),
    db.prepare("CREATE INDEX IF NOT EXISTS story_projection_sync_idx ON story_projection (sync_id)"),
    db.prepare(`CREATE TABLE IF NOT EXISTS detail_cache (
      resource_key TEXT PRIMARY KEY NOT NULL, resource_type TEXT NOT NULL,
      resource_id TEXT NOT NULL, payload_json TEXT NOT NULL,
      updated_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)`),
    db.prepare("CREATE INDEX IF NOT EXISTS detail_cache_expires_idx ON detail_cache (expires_at)"),
    db.prepare(`CREATE TABLE IF NOT EXISTS read_request (
      id TEXT PRIMARY KEY NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('pending', 'claimed', 'completed', 'failed')),
      requested_by TEXT NOT NULL, created_at INTEGER NOT NULL, claimed_at INTEGER,
      completed_at INTEGER, expires_at INTEGER NOT NULL, error TEXT)`),
    db.prepare("CREATE INDEX IF NOT EXISTS read_request_status_created_idx ON read_request (status, created_at)"),
    db.prepare("CREATE INDEX IF NOT EXISTS read_request_resource_idx ON read_request (resource_type, resource_id)"),
  ]).then(async () => {
    try {
      await db.prepare("ALTER TABLE command_queue ADD COLUMN expires_at INTEGER NOT NULL DEFAULT 0").run();
    } catch { /* migration already applied */ }
    try {
      await db.prepare("ALTER TABLE projection_state ADD COLUMN resource_digest TEXT NOT NULL DEFAULT ''").run();
    } catch { /* migration already applied */ }
    try {
      await db.prepare("ALTER TABLE projection_state ADD COLUMN resource_total INTEGER NOT NULL DEFAULT 0").run();
    } catch { /* migration already applied */ }
    await db.prepare("UPDATE command_queue SET expires_at = created_at + ? WHERE expires_at = 0")
      .bind(COMMAND_TTL_MS).run();
  }).catch((error) => {
    schemaPromises.delete(key);
    throw error;
  });
  schemaPromises.set(key, setup);
  return setup;
}

export async function consumeBridgeNonce(db: D1Database, nonce: string, timestamp: number): Promise<boolean> {
  await db.prepare("DELETE FROM bridge_nonces WHERE expires_at < ?").bind(Date.now()).run();
  try {
    const result = await db.prepare(
      "INSERT INTO bridge_nonces (nonce, created_at, expires_at) VALUES (?, ?, ?)",
    ).bind(nonce, Date.now(), timestamp + 10 * 60_000).run();
    return Boolean(result.success);
  } catch {
    return false;
  }
}

export async function saveSnapshot(
  db: D1Database,
  snapshot: DashboardSnapshot,
  input: { digest: string; bridgeVersion: string },
): Promise<void> {
  await ensureDashboardSchema(db);
  const now = Date.now();
  await db.batch([
    db.prepare(`INSERT INTO dashboard_state
      (id, schema_version, generated_at, received_at, digest, bridge_version, snapshot_json)
      VALUES (1, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET schema_version = excluded.schema_version,
        generated_at = excluded.generated_at, received_at = excluded.received_at,
        digest = excluded.digest, bridge_version = excluded.bridge_version,
        snapshot_json = excluded.snapshot_json`).bind(
          snapshot.schema_version, snapshot.generated_at, now, input.digest,
          input.bridgeVersion, JSON.stringify(snapshot),
        ),
    db.prepare(`INSERT INTO bridge_status
      (id, last_seen_at, bridge_version, runtime_version, last_error)
      VALUES (1, ?, ?, ?, NULL)
      ON CONFLICT(id) DO UPDATE SET last_seen_at = excluded.last_seen_at,
        bridge_version = excluded.bridge_version, runtime_version = excluded.runtime_version,
        last_error = NULL`).bind(now, input.bridgeVersion, snapshot.runtime_version),
  ]);
}

type ProjectionStateRow = {
  snapshot_json: string;
  story_digest: string;
  received_at: number;
  bridge_version: string;
  last_seen_at: number | null;
  last_error: string | null;
};

export async function saveProjectionState(
  db: D1Database,
  input: {
    syncId: string;
    storyDigest: string;
    storyTotal: number;
    resourceDigest: string;
    resourceTotal: number;
    snapshot: DashboardSnapshot;
    bridgeVersion: string;
  },
): Promise<{
  storiesRequired: boolean;
  resourcesRequired: boolean;
  storyDigest: string;
  resourceDigest: string;
}> {
  await ensureDashboardSchema(db);
  const previous = await db.prepare("SELECT story_digest, resource_digest FROM projection_state WHERE id = 1")
    .first<{ story_digest: string; resource_digest: string }>();
  const storiesRequired = previous?.story_digest !== input.storyDigest;
  const resourcesRequired = previous?.resource_digest !== input.resourceDigest;
  // Do not publish the new digest until the matching story set is complete. If
  // a bridge process dies between chunks, the next poll will resume by sending
  // a fresh complete set instead of mistaking the partial upload for success.
  const storedDigest = storiesRequired ? (previous?.story_digest || "") : input.storyDigest;
  const storedResourceDigest = resourcesRequired ? (previous?.resource_digest || "") : input.resourceDigest;
  const now = Date.now();
  await db.batch([
    db.prepare(`INSERT INTO projection_state
      (id, sync_id, story_digest, story_total, resource_digest, resource_total, generated_at, received_at,
       runtime_version, bridge_version, snapshot_json)
      VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET sync_id = excluded.sync_id,
        story_digest = excluded.story_digest, story_total = excluded.story_total,
        resource_digest = excluded.resource_digest, resource_total = excluded.resource_total,
        generated_at = excluded.generated_at, received_at = excluded.received_at,
        runtime_version = excluded.runtime_version, bridge_version = excluded.bridge_version,
        snapshot_json = excluded.snapshot_json`).bind(
          input.syncId, storedDigest, input.storyTotal, storedResourceDigest, input.resourceTotal,
          input.snapshot.generated_at, now, input.snapshot.runtime_version, input.bridgeVersion,
          JSON.stringify(input.snapshot),
        ),
    db.prepare(`INSERT INTO bridge_status
      (id, last_seen_at, bridge_version, runtime_version, last_error)
      VALUES (1, ?, ?, ?, NULL)
      ON CONFLICT(id) DO UPDATE SET last_seen_at = excluded.last_seen_at,
        bridge_version = excluded.bridge_version, runtime_version = excluded.runtime_version,
        last_error = NULL`).bind(now, input.bridgeVersion, input.snapshot.runtime_version),
  ]);
  return {
    storiesRequired,
    resourcesRequired,
    storyDigest: previous?.story_digest || "",
    resourceDigest: previous?.resource_digest || "",
  };
}

function scalarText(value: JsonValue | undefined): string {
  return typeof value === "string" ? value : "";
}

function scalarBoolean(value: JsonValue | undefined): number {
  return value === true || value === 1 ? 1 : 0;
}

function scalarNumber(value: JsonValue | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : fallback;
}

export async function saveStoryChunk(
  db: D1Database,
  syncId: string,
  mode: "full" | "delta",
  stories: StoryProjection[],
  deletedIds: string[],
): Promise<void> {
  await ensureDashboardSchema(db);
  const now = Date.now();
  const statements = [
    ...(mode === "delta"
      ? deletedIds.map((id) => db.prepare("DELETE FROM story_projection WHERE id = ?").bind(id))
      : []),
    ...stories.map((story, index) => db.prepare(`INSERT INTO story_projection
    (id, sync_id, status, lane, freshness, research_status, ingestion_context,
     is_review_current, is_correction, priority_rank, newest_rank, summary_json, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET sync_id = excluded.sync_id, status = excluded.status,
      lane = excluded.lane, freshness = excluded.freshness,
      research_status = excluded.research_status, ingestion_context = excluded.ingestion_context,
      is_review_current = excluded.is_review_current, is_correction = excluded.is_correction,
      priority_rank = excluded.priority_rank, newest_rank = excluded.newest_rank,
      summary_json = excluded.summary_json, updated_at = excluded.updated_at`).bind(
        story.id, syncId, scalarText(story.status), scalarText(story.lane),
        scalarText(story.freshness), scalarText(story.research_status),
        scalarText(story.ingestion_context), scalarBoolean(story.is_review_current),
        scalarBoolean(story.is_correction), scalarNumber(story.priority_rank, index),
        scalarNumber(story.newest_rank, index), JSON.stringify(story), now,
      )),
  ];
  for (let offset = 0; offset < statements.length; offset += 50) {
    await db.batch(statements.slice(offset, offset + 50));
  }
}

export async function completeProjectionSync(
  db: D1Database,
  syncId: string,
  storyDigest: string,
  storyTotal: number,
  mode: "full" | "delta",
): Promise<void> {
  await ensureDashboardSchema(db);
  const count = mode === "delta"
    ? await db.prepare("SELECT COUNT(*) AS count FROM story_projection").first<{ count: number }>()
    : await db.prepare("SELECT COUNT(*) AS count FROM story_projection WHERE sync_id = ?")
      .bind(syncId).first<{ count: number }>();
  if (Number(count?.count || 0) !== storyTotal) throw new Error("story_projection_incomplete");
  await db.batch([
    ...(mode === "full"
      ? [db.prepare("DELETE FROM story_projection WHERE sync_id != ?").bind(syncId)]
      : []),
    db.prepare("UPDATE projection_state SET story_digest = ?, story_total = ? WHERE id = 1 AND sync_id = ?")
      .bind(storyDigest, storyTotal, syncId),
  ]);
}

export async function saveResourceChunk(
  db: D1Database,
  syncId: string,
  mode: "full" | "delta",
  resources: ResourceProjection[],
  deletedIds: string[],
): Promise<void> {
  await ensureDashboardSchema(db);
  const now = Date.now();
  const statements = [
    ...(mode === "delta"
      ? deletedIds.map((key) => db.prepare("DELETE FROM resource_projection WHERE resource_key = ?").bind(key))
      : []),
    ...resources.map((resource) => db.prepare(`INSERT INTO resource_projection
    (resource_key, resource_type, resource_id, sync_id, rank, payload_json, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(resource_key) DO UPDATE SET resource_type = excluded.resource_type,
      resource_id = excluded.resource_id, sync_id = excluded.sync_id, rank = excluded.rank,
      payload_json = excluded.payload_json, updated_at = excluded.updated_at`).bind(
        `${resource.resource_type}:${resource.resource_id}`, resource.resource_type,
        resource.resource_id, syncId, resource.rank, JSON.stringify(resource.payload), now,
      )),
  ];
  for (let offset = 0; offset < statements.length; offset += 50) {
    await db.batch(statements.slice(offset, offset + 50));
  }
}

export async function completeResourceSync(
  db: D1Database,
  syncId: string,
  digest: string,
  total: number,
  mode: "full" | "delta",
): Promise<void> {
  await ensureDashboardSchema(db);
  const count = mode === "delta"
    ? await db.prepare("SELECT COUNT(*) AS count FROM resource_projection").first<{ count: number }>()
    : await db.prepare("SELECT COUNT(*) AS count FROM resource_projection WHERE sync_id = ?")
      .bind(syncId).first<{ count: number }>();
  if (Number(count?.count || 0) !== total) throw new Error("resource_projection_incomplete");
  await db.batch([
    ...(mode === "full"
      ? [db.prepare("DELETE FROM resource_projection WHERE sync_id != ?").bind(syncId)]
      : []),
    db.prepare("UPDATE projection_state SET resource_digest = ?, resource_total = ? WHERE id = 1 AND sync_id = ?")
      .bind(digest, total, syncId),
  ]);
}

export async function readSnapshot(db: D1Database): Promise<StoredDashboardSnapshot | null> {
  await ensureDashboardSchema(db);
  const projected = await db.prepare(`SELECT p.snapshot_json, p.story_digest,
      p.received_at, p.bridge_version, b.last_seen_at, b.last_error
    FROM projection_state p LEFT JOIN bridge_status b ON b.id = 1 WHERE p.id = 1`)
    .first<ProjectionStateRow>();
  const legacy = projected ? null : await db.prepare(`SELECT d.snapshot_json,
      d.digest AS story_digest, d.received_at, d.bridge_version,
      b.last_seen_at, b.last_error
    FROM dashboard_state d LEFT JOIN bridge_status b ON b.id = 1 WHERE d.id = 1`)
    .first<ProjectionStateRow>();
  const row = projected || legacy;
  if (!row) return null;
  try {
    const snapshot = JSON.parse(row.snapshot_json) as DashboardSnapshot;
    if (projected) {
      const projectedResources = await db.prepare(`SELECT resource_type, payload_json
        FROM resource_projection ORDER BY resource_type, rank, resource_key`)
        .all<{ resource_type: ResourceProjection["resource_type"]; payload_json: string }>();
      const grouped = new Map<string, Array<Record<string, JsonValue>>>();
      for (const resource of projectedResources.results || []) {
        try {
          const payload = JSON.parse(resource.payload_json) as Record<string, JsonValue>;
          const current = grouped.get(resource.resource_type) || [];
          current.push(payload);
          grouped.set(resource.resource_type, current);
        } catch { /* skip malformed projection row */ }
      }
      if (grouped.has("story_detail")) snapshot.stories = grouped.get("story_detail")!;
      if (grouped.has("draft_summary")) snapshot.drafts = grouped.get("draft_summary")!;
      if (grouped.has("source")) snapshot.sources = grouped.get("source")!;
      if (grouped.has("notice")) snapshot.notices = grouped.get("notice")!;
      if (grouped.has("diagnostic")) snapshot.diagnostics = grouped.get("diagnostic")!;
      if (grouped.has("schedule")) snapshot.schedule = grouped.get("schedule")![0] || {};
      if (grouped.has("settings")) snapshot.settings = grouped.get("settings")![0] || {};
    }
    return {
      snapshot,
      digest: row.story_digest,
      receivedAt: row.received_at,
      bridgeVersion: row.bridge_version,
      bridgeLastSeenAt: row.last_seen_at,
      bridgeLastError: row.last_error,
      bridgeConnected: Date.now() - (row.last_seen_at || row.received_at) < BRIDGE_STALE_MS,
    };
  } catch {
    return null;
  }
}

export async function recordBridgeHeartbeat(
  db: D1Database,
  input: { bridgeVersion: string; runtimeVersion: string; lastError?: string | null },
): Promise<void> {
  await ensureDashboardSchema(db);
  await db.prepare(`INSERT INTO bridge_status
    (id, last_seen_at, bridge_version, runtime_version, last_error)
    VALUES (1, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET last_seen_at = excluded.last_seen_at,
      bridge_version = excluded.bridge_version, runtime_version = excluded.runtime_version,
      last_error = excluded.last_error`)
    .bind(Date.now(), input.bridgeVersion, input.runtimeVersion, input.lastError ?? null).run();
}

function encodeCursor(offset: number, sort: string): string {
  return btoa(JSON.stringify({ v: 1, offset, sort })).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

function decodeCursor(value: string, sort: string): number {
  if (!value) return 0;
  try {
    const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
    const parsed = JSON.parse(atob(padded)) as { v?: number; offset?: number; sort?: string };
    return parsed.v === 1 && parsed.sort === sort && Number.isInteger(parsed.offset) && Number(parsed.offset) >= 0
      ? Number(parsed.offset)
      : 0;
  } catch {
    return 0;
  }
}

export async function listStoryPage(
  db: D1Database,
  input: {
    status?: string; lane?: string; kind?: string; window?: string;
    sort?: string; cursor?: string; pageSize?: number;
  },
): Promise<StoryPage> {
  await ensureDashboardSchema(db);
  const window = new Set(["review_now", "older", "all"]).has(input.window || "") ? input.window! : "review_now";
  const sort = input.sort === "newest" ? "newest" : "priority";
  const clauses: string[] = [];
  const values: unknown[] = [];
  if (input.status && input.status !== "all") { clauses.push("status = ?"); values.push(input.status); }
  if (input.lane && input.lane !== "all") { clauses.push("lane = ?"); values.push(input.lane); }
  if (window === "review_now") clauses.push("is_review_current = 1 AND status NOT IN ('archived', 'withdrawn')");
  if (window === "older") clauses.push("freshness = 'Older' AND status NOT IN ('archived', 'withdrawn')");
  const kind = input.kind || "all";
  if (kind === "ready") clauses.push("status NOT IN ('archived', 'withdrawn', 'content_ready', 'draft_ready')");
  if (kind === "researching") clauses.push("research_status = 'researching'");
  if (kind === "content_ready") clauses.push("status IN ('content_ready', 'draft_ready')");
  if (kind === "catch_up") clauses.push("ingestion_context IN ('recovery', 'extended')");
  if (kind === "correction") clauses.push("is_correction = 1");
  if (kind === "health") return { stories: [], total: 0, nextCursor: "" };
  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const count = await db.prepare(`SELECT COUNT(*) AS count FROM story_projection ${where}`)
    .bind(...values).first<{ count: number }>();
  const total = Number(count?.count || 0);
  const offset = decodeCursor(input.cursor || "", sort);
  const pageSize = Math.max(1, Math.min(100, input.pageSize || 25));
  const rank = sort === "newest" ? "newest_rank" : "priority_rank";
  const response = await db.prepare(`SELECT summary_json FROM story_projection ${where}
    ORDER BY ${rank}, id LIMIT ? OFFSET ?`).bind(...values, pageSize, offset).all<{ summary_json: string }>();
  const stories = (response.results || []).flatMap((row) => {
    try { return [JSON.parse(row.summary_json) as StoryProjection]; } catch { return []; }
  });
  return {
    stories, total,
    nextCursor: offset + stories.length < total ? encodeCursor(offset + stories.length, sort) : "",
  };
}

type StoredCommand = {
  id: string; operation: string; payload_json: string; status: OwnerCommand["status"];
  requested_by: string; created_at: number; claimed_at: number | null;
  completed_at: number | null; expires_at: number; attempt_count: number;
  result_json: string | null; error: string | null;
};

function commandFromRow(row: StoredCommand): OwnerCommand {
  let payload: Record<string, JsonValue> = {};
  let result: JsonValue | null = null;
  try { payload = JSON.parse(row.payload_json); } catch { /* invalid historical payload */ }
  try { result = row.result_json ? JSON.parse(row.result_json) : null; } catch { /* invalid historical result */ }
  return {
    id: row.id, operation: row.operation, payload, status: row.status,
    requestedBy: row.requested_by, createdAt: row.created_at, claimedAt: row.claimed_at,
    completedAt: row.completed_at, expiresAt: row.expires_at,
    attemptCount: row.attempt_count, result, error: row.error,
  };
}

async function expireCommands(db: D1Database): Promise<void> {
  await db.prepare(`UPDATE command_queue SET status = 'failed', completed_at = ?,
      error = 'Command expired before the laptop accepted it'
    WHERE status = 'pending' AND expires_at <= ?`).bind(Date.now(), Date.now()).run();
}

export async function enqueueCommand(
  db: D1Database,
  input: { operation: string; payload: Record<string, JsonValue>; requestedBy: string },
): Promise<OwnerCommand> {
  await ensureDashboardSchema(db);
  const id = crypto.randomUUID();
  const createdAt = Date.now();
  const expiresAt = createdAt + COMMAND_TTL_MS;
  await db.prepare(`INSERT INTO command_queue
    (id, operation, payload_json, status, requested_by, requested_role,
     created_at, expires_at, attempt_count)
    VALUES (?, ?, ?, 'pending', ?, 'master', ?, ?, 0)`)
    .bind(id, input.operation, JSON.stringify(input.payload), input.requestedBy, createdAt, expiresAt).run();
  return {
    id, operation: input.operation, payload: input.payload, status: "pending",
    requestedBy: input.requestedBy, createdAt, claimedAt: null, completedAt: null,
    expiresAt, attemptCount: 0, result: null, error: null,
  };
}

export async function listCommands(db: D1Database, limit = 20): Promise<OwnerCommand[]> {
  await ensureDashboardSchema(db);
  await expireCommands(db);
  const response = await db.prepare(`SELECT id, operation, payload_json, status, requested_by,
      created_at, claimed_at, completed_at, expires_at, attempt_count, result_json, error
    FROM command_queue ORDER BY created_at DESC LIMIT ?`).bind(Math.min(100, Math.max(1, limit))).all<StoredCommand>();
  return (response.results || []).map(commandFromRow);
}

export async function claimCommands(db: D1Database, limit = 10): Promise<OwnerCommand[]> {
  await ensureDashboardSchema(db);
  await expireCommands(db);
  const now = Date.now();
  const staleBefore = now - 2 * 60_000;
  const response = await db.prepare(`SELECT id, operation, payload_json, status, requested_by,
      created_at, claimed_at, completed_at, expires_at, attempt_count, result_json, error
    FROM command_queue WHERE expires_at > ? AND
      (status = 'pending' OR (status = 'claimed' AND claimed_at < ?))
    ORDER BY created_at LIMIT ?`).bind(now, staleBefore, Math.min(25, Math.max(1, limit))).all<StoredCommand>();
  const claimed: OwnerCommand[] = [];
  for (const row of response.results || []) {
    const result = await db.prepare(`UPDATE command_queue SET status = 'claimed', claimed_at = ?,
        attempt_count = attempt_count + 1, error = NULL
      WHERE id = ? AND expires_at > ? AND
        (status = 'pending' OR (status = 'claimed' AND claimed_at < ?))`)
      .bind(now, row.id, now, staleBefore).run();
    if (result.meta.changes === 1) {
      claimed.push(commandFromRow({ ...row, status: "claimed", claimed_at: now, attempt_count: row.attempt_count + 1 }));
    }
  }
  return claimed;
}

export async function completeCommand(
  db: D1Database,
  id: string,
  input: { ok: boolean; result?: JsonValue | null; error?: string | null },
): Promise<boolean> {
  await ensureDashboardSchema(db);
  const response = await db.prepare(`UPDATE command_queue SET status = ?, completed_at = ?,
      result_json = ?, error = ? WHERE id = ? AND status = 'claimed'`)
    .bind(input.ok ? "completed" : "failed", Date.now(),
      input.result === undefined ? null : JSON.stringify(input.result),
      input.ok ? null : (input.error || "Command failed").slice(0, 2_000), id).run();
  return response.meta.changes === 1;
}

type ReadRequestRow = {
  id: string; resource_type: ReadRequest["resourceType"]; resource_id: string;
  status: ReadRequest["status"]; requested_by: string; created_at: number;
  claimed_at: number | null; completed_at: number | null; expires_at: number; error: string | null;
};

function readRequestFromRow(row: ReadRequestRow): ReadRequest {
  return {
    id: row.id, resourceType: row.resource_type, resourceId: row.resource_id,
    status: row.status, requestedBy: row.requested_by, createdAt: row.created_at,
    claimedAt: row.claimed_at, completedAt: row.completed_at,
    expiresAt: row.expires_at, error: row.error,
  };
}

export async function readDetail(
  db: D1Database,
  resourceType: ReadRequest["resourceType"],
  resourceId: string,
): Promise<Record<string, JsonValue> | null> {
  await ensureDashboardSchema(db);
  const projectionType = resourceType === "story" ? "story_detail" : "draft_detail";
  const projected = await db.prepare(`SELECT payload_json FROM resource_projection
    WHERE resource_type = ? AND resource_id = ? LIMIT 1`).bind(projectionType, resourceId)
    .first<{ payload_json: string }>();
  if (projected) {
    try { return JSON.parse(projected.payload_json) as Record<string, JsonValue>; } catch { /* use cache */ }
  }
  await db.prepare("DELETE FROM detail_cache WHERE expires_at <= ?").bind(Date.now()).run();
  const key = `${resourceType}:${resourceId}`;
  const row = await db.prepare("SELECT payload_json FROM detail_cache WHERE resource_key = ? AND expires_at > ?")
    .bind(key, Date.now()).first<{ payload_json: string }>();
  if (!row) return null;
  try { return JSON.parse(row.payload_json) as Record<string, JsonValue>; } catch { return null; }
}

export async function requestDetail(
  db: D1Database,
  input: { resourceType: ReadRequest["resourceType"]; resourceId: string; requestedBy: string },
): Promise<ReadRequest> {
  await ensureDashboardSchema(db);
  const now = Date.now();
  await db.prepare(`UPDATE read_request SET status = 'failed', completed_at = ?, error = 'Read request expired'
    WHERE status IN ('pending', 'claimed') AND expires_at <= ?`).bind(now, now).run();
  const existing = await db.prepare(`SELECT id, resource_type, resource_id, status, requested_by,
      created_at, claimed_at, completed_at, expires_at, error FROM read_request
    WHERE resource_type = ? AND resource_id = ? AND status IN ('pending', 'claimed') AND expires_at > ?
    ORDER BY created_at DESC LIMIT 1`).bind(input.resourceType, input.resourceId, now).first<ReadRequestRow>();
  if (existing) return readRequestFromRow(existing);
  const row: ReadRequestRow = {
    id: crypto.randomUUID(), resource_type: input.resourceType, resource_id: input.resourceId,
    status: "pending", requested_by: input.requestedBy, created_at: now,
    claimed_at: null, completed_at: null, expires_at: now + READ_TTL_MS, error: null,
  };
  await db.prepare(`INSERT INTO read_request
    (id, resource_type, resource_id, status, requested_by, created_at, expires_at)
    VALUES (?, ?, ?, 'pending', ?, ?, ?)`)
    .bind(row.id, row.resource_type, row.resource_id, row.requested_by, row.created_at, row.expires_at).run();
  return readRequestFromRow(row);
}

export async function claimReadRequests(db: D1Database, limit = 10): Promise<ReadRequest[]> {
  await ensureDashboardSchema(db);
  const now = Date.now();
  await db.prepare(`UPDATE read_request SET status = 'failed', completed_at = ?, error = 'Read request expired'
    WHERE status IN ('pending', 'claimed') AND expires_at <= ?`).bind(now, now).run();
  const response = await db.prepare(`SELECT id, resource_type, resource_id, status, requested_by,
      created_at, claimed_at, completed_at, expires_at, error FROM read_request
    WHERE status = 'pending' AND expires_at > ? ORDER BY created_at LIMIT ?`)
    .bind(now, Math.min(25, Math.max(1, limit))).all<ReadRequestRow>();
  const claimed: ReadRequest[] = [];
  for (const row of response.results || []) {
    const result = await db.prepare(`UPDATE read_request SET status = 'claimed', claimed_at = ?
      WHERE id = ? AND status = 'pending' AND expires_at > ?`).bind(now, row.id, now).run();
    if (result.meta.changes === 1) claimed.push(readRequestFromRow({ ...row, status: "claimed", claimed_at: now }));
  }
  return claimed;
}

export async function completeReadRequest(
  db: D1Database,
  id: string,
  input: { ok: boolean; payload?: Record<string, JsonValue> | null; error?: string | null },
): Promise<boolean> {
  await ensureDashboardSchema(db);
  const row = await db.prepare("SELECT resource_type, resource_id FROM read_request WHERE id = ? AND status = 'claimed'")
    .bind(id).first<{ resource_type: ReadRequest["resourceType"]; resource_id: string }>();
  if (!row) return false;
  const now = Date.now();
  const statements = [db.prepare(`UPDATE read_request SET status = ?, completed_at = ?, error = ?
    WHERE id = ? AND status = 'claimed'`).bind(input.ok ? "completed" : "failed", now,
      input.ok ? null : (input.error || "Detail fetch failed").slice(0, 2_000), id)];
  if (input.ok && input.payload) {
    statements.push(db.prepare(`INSERT INTO detail_cache
      (resource_key, resource_type, resource_id, payload_json, updated_at, expires_at)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(resource_key) DO UPDATE SET payload_json = excluded.payload_json,
        updated_at = excluded.updated_at, expires_at = excluded.expires_at`).bind(
          `${row.resource_type}:${row.resource_id}`, row.resource_type, row.resource_id,
          JSON.stringify(input.payload), now, now + DETAIL_TTL_MS,
        ));
  }
  const results = await db.batch(statements);
  return results[0]?.meta.changes === 1;
}
