CREATE TABLE `resource_projection` (
	`resource_key` text PRIMARY KEY NOT NULL,
	`resource_type` text NOT NULL,
	`resource_id` text NOT NULL,
	`sync_id` text NOT NULL,
	`rank` integer NOT NULL,
	`payload_json` text NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `resource_projection_type_rank_idx` ON `resource_projection` (`resource_type`,`rank`);--> statement-breakpoint
CREATE INDEX `resource_projection_sync_idx` ON `resource_projection` (`sync_id`);--> statement-breakpoint
CREATE INDEX `resource_projection_resource_idx` ON `resource_projection` (`resource_type`,`resource_id`);--> statement-breakpoint
ALTER TABLE `projection_state` ADD `resource_digest` text DEFAULT '' NOT NULL;--> statement-breakpoint
ALTER TABLE `projection_state` ADD `resource_total` integer DEFAULT 0 NOT NULL;
