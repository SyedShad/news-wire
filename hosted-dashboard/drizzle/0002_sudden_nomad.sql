CREATE TABLE `bridge_nonces` (
	`nonce` text PRIMARY KEY NOT NULL,
	`created_at` integer NOT NULL,
	`expires_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `bridge_nonces_expires_at_idx` ON `bridge_nonces` (`expires_at`);--> statement-breakpoint
CREATE TABLE `bridge_status` (
	`id` integer PRIMARY KEY NOT NULL,
	`last_seen_at` integer NOT NULL,
	`bridge_version` text NOT NULL,
	`runtime_version` text NOT NULL,
	`last_error` text
);
--> statement-breakpoint
CREATE TABLE `command_queue` (
	`id` text PRIMARY KEY NOT NULL,
	`operation` text NOT NULL,
	`payload_json` text NOT NULL,
	`status` text NOT NULL,
	`requested_by` text NOT NULL,
	`requested_role` text NOT NULL,
	`created_at` integer NOT NULL,
	`claimed_at` integer,
	`completed_at` integer,
	`attempt_count` integer NOT NULL,
	`result_json` text,
	`error` text
);
--> statement-breakpoint
CREATE INDEX `command_queue_status_created_at_idx` ON `command_queue` (`status`,`created_at`);--> statement-breakpoint
CREATE TABLE `dashboard_state` (
	`id` integer PRIMARY KEY NOT NULL,
	`schema_version` integer NOT NULL,
	`generated_at` text NOT NULL,
	`received_at` integer NOT NULL,
	`digest` text NOT NULL,
	`bridge_version` text NOT NULL,
	`snapshot_json` text NOT NULL
);
