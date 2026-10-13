ALTER TABLE story_projection ADD COLUMN first_fetched_at TEXT NOT NULL DEFAULT '';
--> statement-breakpoint
CREATE TABLE read_request_expanded (
  id TEXT PRIMARY KEY NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'claimed', 'completed', 'failed')),
  requested_by TEXT NOT NULL, created_at INTEGER NOT NULL, claimed_at INTEGER,
  completed_at INTEGER, expires_at INTEGER NOT NULL, error TEXT,
  CONSTRAINT read_request_resource_type_check CHECK (resource_type IN ('story', 'draft', 'article', 'generation_catalog', 'generation_job'))
);
--> statement-breakpoint
INSERT INTO read_request_expanded SELECT * FROM read_request;
--> statement-breakpoint
DROP TABLE read_request;
--> statement-breakpoint
ALTER TABLE read_request_expanded RENAME TO read_request;
--> statement-breakpoint
CREATE INDEX read_request_status_created_idx ON read_request (status, created_at);
--> statement-breakpoint
CREATE INDEX read_request_resource_idx ON read_request (resource_type, resource_id);
--> statement-breakpoint
CREATE INDEX story_projection_fetched_idx ON story_projection (first_fetched_at, id);
