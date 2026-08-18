export type JsonScalar = string | number | boolean | null;
export type JsonValue = JsonScalar | JsonValue[] | { [key: string]: JsonValue };

export type DashboardSnapshot = {
  schema_version: number;
  generated_at: string;
  runtime_version: string;
  overview: {
    counts: Record<string, number>;
    sources: Record<string, number>;
    queue_count: number;
    queue_lag: string;
    top_stories: Array<Record<string, JsonValue>>;
    alerts: Array<Record<string, JsonValue>>;
    scans: Array<Record<string, JsonValue>>;
  };
  stories: Array<Record<string, JsonValue>>;
  drafts: Array<Record<string, JsonValue>>;
  sources: Array<Record<string, JsonValue>>;
  schedule: Record<string, JsonValue>;
  notices?: Array<Record<string, JsonValue>>;
  settings?: Record<string, JsonValue>;
  diagnostics: Array<Record<string, JsonValue>>;
};

export type StoryProjection = Record<string, JsonValue> & {
  id: string;
  status?: string;
  lane?: string;
  freshness?: string;
  research_status?: string;
  ingestion_context?: string;
  is_review_current?: boolean;
};

export type ResourceProjection = {
  resource_type: "story_detail" | "draft_summary" | "draft_detail" | "source" | "notice" | "schedule" | "settings" | "diagnostic";
  resource_id: string;
  rank: number;
  payload: Record<string, JsonValue>;
};

export type BridgeSyncEnvelope =
  | {
      schema_version: 2;
      kind: "state";
      sync_id: string;
      story_digest: string;
      story_total: number;
      resource_digest: string;
      resource_total: number;
      snapshot: DashboardSnapshot;
    }
  | {
      schema_version: 2;
      kind: "stories";
      sync_id: string;
      stories: StoryProjection[];
    }
  | {
      schema_version: 2;
      kind: "resources";
      sync_id: string;
      resources: ResourceProjection[];
    }
  | {
      schema_version: 2;
      kind: "complete";
      sync_id: string;
      projection: "stories" | "resources";
      digest: string;
      total: number;
    };

export type StoredDashboardSnapshot = {
  snapshot: DashboardSnapshot;
  digest: string;
  receivedAt: number;
  bridgeVersion: string;
  bridgeLastSeenAt: number | null;
  bridgeLastError: string | null;
  bridgeConnected: boolean;
};

export type OwnerCommand = {
  id: string;
  operation: string;
  payload: Record<string, JsonValue>;
  status: "pending" | "claimed" | "completed" | "failed";
  requestedBy: string;
  createdAt: number;
  claimedAt: number | null;
  completedAt: number | null;
  attemptCount: number;
  expiresAt: number;
  result: JsonValue | null;
  error: string | null;
};

export type ReadRequest = {
  id: string;
  resourceType: "story" | "draft";
  resourceId: string;
  status: "pending" | "claimed" | "completed" | "failed";
  requestedBy: string;
  createdAt: number;
  claimedAt: number | null;
  completedAt: number | null;
  expiresAt: number;
  error: string | null;
};

export type StoryPage = {
  stories: StoryProjection[];
  total: number;
  nextCursor: string;
};
