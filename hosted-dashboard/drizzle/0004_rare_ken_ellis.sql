CREATE TABLE `detail_cache` (
	`resource_key` text PRIMARY KEY NOT NULL,
	`resource_type` text NOT NULL,
	`resource_id` text NOT NULL,
	`payload_json` text NOT NULL,
	`updated_at` integer NOT NULL,
	`expires_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `detail_cache_expires_idx` ON `detail_cache` (`expires_at`);--> statement-breakpoint
CREATE TABLE `projection_state` (
	`id` integer PRIMARY KEY NOT NULL,
	`sync_id` text NOT NULL,
	`story_digest` text NOT NULL,
	`story_total` integer NOT NULL,
	`generated_at` text NOT NULL,
	`received_at` integer NOT NULL,
	`runtime_version` text NOT NULL,
	`bridge_version` text NOT NULL,
	`snapshot_json` text NOT NULL,
	CONSTRAINT "projection_state_singleton_check" CHECK("projection_state"."id" = 1)
);
--> statement-breakpoint
CREATE TABLE `read_request` (
	`id` text PRIMARY KEY NOT NULL,
	`resource_type` text NOT NULL,
	`resource_id` text NOT NULL,
	`status` text NOT NULL,
	`requested_by` text NOT NULL,
	`created_at` integer NOT NULL,
	`claimed_at` integer,
	`completed_at` integer,
	`expires_at` integer NOT NULL,
	`error` text,
	CONSTRAINT "read_request_status_check" CHECK("read_request"."status" IN ('pending', 'claimed', 'completed', 'failed')),
	CONSTRAINT "read_request_resource_type_check" CHECK("read_request"."resource_type" IN ('story', 'draft'))
);
--> statement-breakpoint
CREATE INDEX `read_request_status_created_idx` ON `read_request` (`status`,`created_at`);--> statement-breakpoint
CREATE INDEX `read_request_resource_idx` ON `read_request` (`resource_type`,`resource_id`);--> statement-breakpoint
CREATE TABLE `story_projection` (
	`id` text PRIMARY KEY NOT NULL,
	`sync_id` text NOT NULL,
	`status` text NOT NULL,
	`lane` text NOT NULL,
	`freshness` text NOT NULL,
	`research_status` text NOT NULL,
	`ingestion_context` text NOT NULL,
	`is_review_current` integer NOT NULL,
	`is_correction` integer NOT NULL,
	`priority_rank` integer NOT NULL,
	`newest_rank` integer NOT NULL,
	`summary_json` text NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `story_projection_priority_idx` ON `story_projection` (`priority_rank`);--> statement-breakpoint
CREATE INDEX `story_projection_newest_idx` ON `story_projection` (`newest_rank`);--> statement-breakpoint
CREATE INDEX `story_projection_window_idx` ON `story_projection` (`is_review_current`,`freshness`,`status`);--> statement-breakpoint
CREATE INDEX `story_projection_sync_idx` ON `story_projection` (`sync_id`);--> statement-breakpoint
ALTER TABLE `command_queue` ADD `expires_at` integer DEFAULT 0 NOT NULL;
--> statement-breakpoint
UPDATE `command_queue` SET `expires_at` = `created_at` + 300000 WHERE `expires_at` = 0;
