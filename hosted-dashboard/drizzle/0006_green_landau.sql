CREATE TABLE `notification_event` (
	`event_id` text PRIMARY KEY NOT NULL,
	`source_event_id` text NOT NULL,
	`article_key` text NOT NULL,
	`story_id` text NOT NULL,
	`kind` text NOT NULL,
	`payload_json` text NOT NULL,
	`resolution` text NOT NULL,
	`released_at` integer NOT NULL,
	`resolved_at` integer,
	CONSTRAINT "notification_event_kind_check" CHECK("notification_event"."kind" IN ('article_alert', 'research_result', 'canary')),
	CONSTRAINT "notification_event_resolution_check" CHECK("notification_event"."resolution" IN ('open', 'research_requested', 'dismissed', 'terminal'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `notification_event_article_key_unique` ON `notification_event` (`article_key`);--> statement-breakpoint
CREATE INDEX `notification_event_released_idx` ON `notification_event` (`released_at`);--> statement-breakpoint
CREATE TABLE `notification_global_action` (
	`event_id` text PRIMARY KEY NOT NULL,
	`action` text NOT NULL,
	`command_id` text,
	`created_at` integer NOT NULL,
	`result_json` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `notification_projection_stage` (
	`stage_key` text PRIMARY KEY NOT NULL,
	`sync_id` text NOT NULL,
	`article_key` text NOT NULL,
	`payload_json` text NOT NULL,
	`staged_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `notification_projection_stage_sync_idx` ON `notification_projection_stage` (`sync_id`);--> statement-breakpoint
CREATE INDEX `notification_projection_stage_article_idx` ON `notification_projection_stage` (`article_key`);--> statement-breakpoint
CREATE TABLE `push_action_intent` (
	`id` text PRIMARY KEY NOT NULL,
	`token_hash` text NOT NULL,
	`event_id` text NOT NULL,
	`subscription_id` text NOT NULL,
	`story_id` text NOT NULL,
	`action` text NOT NULL,
	`status` text NOT NULL,
	`created_at` integer NOT NULL,
	`expires_at` integer NOT NULL,
	`completed_at` integer,
	`result_json` text,
	CONSTRAINT "push_action_intent_action_check" CHECK("push_action_intent"."action" IN ('start_research', 'dismiss'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `push_action_intent_token_hash_unique` ON `push_action_intent` (`token_hash`);--> statement-breakpoint
CREATE INDEX `push_action_intent_expiry_idx` ON `push_action_intent` (`status`,`expires_at`);--> statement-breakpoint
CREATE INDEX `push_action_intent_event_idx` ON `push_action_intent` (`event_id`);--> statement-breakpoint
CREATE TABLE `push_delivery` (
	`id` text PRIMARY KEY NOT NULL,
	`event_id` text NOT NULL,
	`subscription_id` text NOT NULL,
	`status` text NOT NULL,
	`attempt_count` integer NOT NULL,
	`next_attempt_at` integer NOT NULL,
	`last_attempt_at` integer,
	`delivered_at` integer,
	`provider_status` integer,
	`last_error` text,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `push_delivery_outbox_idx` ON `push_delivery` (`status`,`next_attempt_at`,`created_at`);--> statement-breakpoint
CREATE INDEX `push_delivery_event_idx` ON `push_delivery` (`event_id`);--> statement-breakpoint
CREATE INDEX `push_delivery_subscription_idx` ON `push_delivery` (`subscription_id`);--> statement-breakpoint
CREATE UNIQUE INDEX `push_delivery_event_subscription_unique` ON `push_delivery` (`event_id`,`subscription_id`);--> statement-breakpoint
CREATE TABLE `push_runtime_state` (
	`id` integer PRIMARY KEY NOT NULL,
	`mode` text NOT NULL,
	`activation_watermark` integer NOT NULL,
	`updated_at` integer NOT NULL,
	`updated_by` text,
	CONSTRAINT "push_runtime_state_singleton_check" CHECK("push_runtime_state"."id" = 1),
	CONSTRAINT "push_runtime_state_mode_check" CHECK("push_runtime_state"."mode" IN ('shadow', 'active', 'paused'))
);
--> statement-breakpoint
CREATE TABLE `push_subscription` (
	`id` text PRIMARY KEY NOT NULL,
	`actor_id` text NOT NULL,
	`endpoint_hash` text NOT NULL,
	`subscription_ciphertext` text NOT NULL,
	`credential_version` text NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	`disabled_at` integer,
	`last_success_at` integer
);
--> statement-breakpoint
CREATE UNIQUE INDEX `push_subscription_endpoint_hash_unique` ON `push_subscription` (`endpoint_hash`);--> statement-breakpoint
CREATE INDEX `push_subscription_active_idx` ON `push_subscription` (`disabled_at`,`updated_at`);--> statement-breakpoint
ALTER TABLE `projection_state` ADD `notification_digest` text DEFAULT '' NOT NULL;--> statement-breakpoint
ALTER TABLE `projection_state` ADD `notification_total` integer DEFAULT 0 NOT NULL;
