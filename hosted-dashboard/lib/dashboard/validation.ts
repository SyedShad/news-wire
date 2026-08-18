import type { BridgeSyncEnvelope, DashboardSnapshot, JsonValue, ResourceProjection, StoryProjection } from "./types.ts";

export const MAX_SNAPSHOT_BYTES = 1_500_000;
export const MAX_COMMAND_PAYLOAD_BYTES = 32_000;
export const MAX_SYNC_CHUNK_BYTES = 1_500_000;

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isRecordArray(value: unknown): value is Array<Record<string, JsonValue>> {
  return Array.isArray(value) && value.every(isRecord);
}

export function parseSnapshot(value: unknown): DashboardSnapshot {
  if (!isRecord(value) || value.schema_version !== 1) {
    throw new TypeError("Unsupported dashboard snapshot schema");
  }
  if (
    typeof value.generated_at !== "string" ||
    typeof value.runtime_version !== "string" ||
    !isRecord(value.overview) ||
    !isRecord(value.overview.counts) ||
    !isRecord(value.overview.sources) ||
    !isRecordArray(value.overview.top_stories) ||
    !isRecordArray(value.overview.alerts) ||
    !isRecordArray(value.overview.scans) ||
    !isRecordArray(value.stories) ||
    !isRecordArray(value.drafts) ||
    !isRecordArray(value.sources) ||
    !isRecord(value.schedule) ||
    (value.notices !== undefined && !isRecordArray(value.notices)) ||
    (value.settings !== undefined && !isRecord(value.settings)) ||
    !isRecordArray(value.diagnostics)
  ) {
    throw new TypeError("Dashboard snapshot is incomplete");
  }
  return value as unknown as DashboardSnapshot;
}

function requiredSyncString(value: Record<string, unknown>, key: string, max = 200): string {
  const result = value[key];
  if (typeof result !== "string" || !result.trim() || result.length > max) {
    throw new TypeError(`${key} is required`);
  }
  return result.trim();
}

export function parseBridgeSync(value: unknown): BridgeSyncEnvelope {
  if (!isRecord(value) || value.schema_version !== 2) {
    throw new TypeError("Unsupported bridge sync schema");
  }
  const kind = value.kind;
  const syncId = requiredSyncString(value, "sync_id", 100);
  if (!/^[A-Za-z0-9_-]{16,100}$/u.test(syncId)) throw new TypeError("sync_id is invalid");
  if (kind === "state") {
    const storyDigest = requiredSyncString(value, "story_digest", 64);
    const resourceDigest = requiredSyncString(value, "resource_digest", 64);
    if (!/^[a-f0-9]{64}$/iu.test(storyDigest)) throw new TypeError("story_digest is invalid");
    if (!/^[a-f0-9]{64}$/iu.test(resourceDigest)) throw new TypeError("resource_digest is invalid");
    if (!Number.isInteger(value.story_total) || Number(value.story_total) < 0) {
      throw new TypeError("story_total is invalid");
    }
    if (!Number.isInteger(value.resource_total) || Number(value.resource_total) < 0) {
      throw new TypeError("resource_total is invalid");
    }
    return {
      schema_version: 2,
      kind,
      sync_id: syncId,
      story_digest: storyDigest,
      story_total: Number(value.story_total),
      resource_digest: resourceDigest,
      resource_total: Number(value.resource_total),
      snapshot: parseSnapshot(value.snapshot),
    };
  }
  if (kind === "stories") {
    const mode = value.mode === undefined ? "full" : value.mode;
    const deletedIds = value.deleted_ids === undefined ? [] : value.deleted_ids;
    if (mode !== "full" && mode !== "delta") throw new TypeError("stories mode is invalid");
    if (!Array.isArray(deletedIds) || deletedIds.length > 100 || deletedIds.some((id) => typeof id !== "string" || !id || id.length > 200)) {
      throw new TypeError("story deletions are invalid");
    }
    if (mode === "full" && deletedIds.length) throw new TypeError("full story sync cannot delete ids");
    if (!Array.isArray(value.stories) || value.stories.length > 100 || !value.stories.every(isRecord)) {
      throw new TypeError("stories chunk is invalid");
    }
    const stories = value.stories as StoryProjection[];
    if (stories.some((story) => typeof story.id !== "string" || !story.id || story.id.length > 200)) {
      throw new TypeError("story id is invalid");
    }
    return { schema_version: 2, kind, sync_id: syncId, mode, stories, deleted_ids: deletedIds };
  }
  if (kind === "resources") {
    const mode = value.mode === undefined ? "full" : value.mode;
    const deletedIds = value.deleted_ids === undefined ? [] : value.deleted_ids;
    if (mode !== "full" && mode !== "delta") throw new TypeError("resources mode is invalid");
    if (!Array.isArray(deletedIds) || deletedIds.length > 100 || deletedIds.some((id) => typeof id !== "string" || !id || id.length > 500)) {
      throw new TypeError("resource deletions are invalid");
    }
    if (mode === "full" && deletedIds.length) throw new TypeError("full resource sync cannot delete ids");
    if (!Array.isArray(value.resources) || value.resources.length > 100 || !value.resources.every(isRecord)) {
      throw new TypeError("resources chunk is invalid");
    }
    const supported = new Set(["story_detail", "draft_summary", "draft_detail", "source", "notice", "schedule", "settings", "diagnostic"]);
    const resources = value.resources as ResourceProjection[];
    if (resources.some((resource) => !supported.has(resource.resource_type) || typeof resource.resource_id !== "string" || !resource.resource_id || resource.resource_id.length > 240 || !Number.isInteger(resource.rank) || !isRecord(resource.payload))) {
      throw new TypeError("resource projection is invalid");
    }
    return { schema_version: 2, kind, sync_id: syncId, mode, resources, deleted_ids: deletedIds };
  }
  if (kind === "complete") {
    const mode = value.mode === undefined ? "full" : value.mode;
    if (mode !== "full" && mode !== "delta") throw new TypeError("completion mode is invalid");
    const digest = requiredSyncString(value, "digest", 64);
    if (!/^[a-f0-9]{64}$/iu.test(digest)) throw new TypeError("digest is invalid");
    if (value.projection !== "stories" && value.projection !== "resources") {
      throw new TypeError("projection is invalid");
    }
    if (!Number.isInteger(value.total) || Number(value.total) < 0) {
      throw new TypeError("total is invalid");
    }
    return {
      schema_version: 2,
      kind,
      sync_id: syncId,
      projection: value.projection,
      digest,
      total: Number(value.total),
      mode,
    };
  }
  throw new TypeError("Unsupported bridge sync kind");
}

const OPERATIONS = new Set([
  "story.review",
  "story.evidence.inspect",
  "story.evidence.confirm",
  "story.evidence.exclude",
  "story.content.create",
  "story.draft.retry",
  "draft.save",
  "source.toggle",
  "schedule.action",
  "schedule.catch_up",
  "diagnostics.purge",
  "alerts.mark_read",
  "bridge.healthcheck",
]);

function requiredString(input: Record<string, unknown>, key: string, max = 2_000): string {
  const value = input[key];
  if (typeof value !== "string" || !value.trim() || value.length > max) {
    throw new TypeError(`${key} is required`);
  }
  return value.trim();
}

export function validateOwnerCommand(value: unknown): {
  operation: string;
  payload: Record<string, JsonValue>;
} {
  if (!isRecord(value)) throw new TypeError("Command body must be an object");
  const operation = requiredString(value, "operation", 80);
  if (!OPERATIONS.has(operation)) throw new TypeError("Unsupported owner operation");
  if (!isRecord(value.payload)) throw new TypeError("payload must be an object");
  const payload = value.payload as Record<string, JsonValue>;
  if (JSON.stringify(payload).length > MAX_COMMAND_PAYLOAD_BYTES) {
    throw new TypeError("Command payload is too large");
  }

  if (operation.startsWith("story.")) requiredString(value.payload, "story_id", 200);
  if (operation === "draft.save") {
    if (!Number.isInteger(value.payload.draft_id)) throw new TypeError("draft_id is required");
    requiredString(value.payload, "headline", 500);
    requiredString(value.payload, "body", 20_000);
  }
  if (operation === "source.toggle") requiredString(value.payload, "source_id", 200);
  if (operation === "schedule.action") {
    const action = requiredString(value.payload, "action", 40);
    if (!new Set(["install", "pause", "resume", "run_now", "uninstall"]).has(action)) {
      throw new TypeError("Unsupported schedule action");
    }
  }
  if (operation === "schedule.catch_up") {
    requiredString(value.payload, "start", 10);
    requiredString(value.payload, "end", 10);
  }
  if (operation === "diagnostics.purge" && value.payload.confirmation !== "PURGE OPERATIONS") {
    throw new TypeError("The purge confirmation phrase did not match");
  }
  return { operation, payload };
}
