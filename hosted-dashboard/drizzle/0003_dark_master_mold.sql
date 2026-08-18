PRAGMA foreign_keys=OFF;--> statement-breakpoint
CREATE TABLE `__new_audit_events` (
	`id` text PRIMARY KEY NOT NULL,
	`created_at` integer NOT NULL,
	`actor_id` text,
	`role` text,
	`action` text NOT NULL,
	`outcome` text NOT NULL,
	`ip_hash` text,
	`detail` text,
	CONSTRAINT "audit_events_role_check" CHECK("__new_audit_events"."role" IS NULL OR "__new_audit_events"."role" IN ('editor', 'master'))
);
--> statement-breakpoint
INSERT INTO `__new_audit_events`("id", "created_at", "actor_id", "role", "action", "outcome", "ip_hash", "detail") SELECT "id", "created_at", "actor_id", "role", "action", "outcome", "ip_hash", "detail" FROM `audit_events`;--> statement-breakpoint
DROP TABLE `audit_events`;--> statement-breakpoint
ALTER TABLE `__new_audit_events` RENAME TO `audit_events`;--> statement-breakpoint
PRAGMA foreign_keys=ON;--> statement-breakpoint
CREATE INDEX `audit_events_created_at_idx` ON `audit_events` (`created_at`);--> statement-breakpoint
CREATE TABLE `__new_auth_sessions` (
	`session_hash` text PRIMARY KEY NOT NULL,
	`actor_id` text NOT NULL,
	`role` text NOT NULL,
	`email` text,
	`credential_version` text NOT NULL,
	`created_at` integer NOT NULL,
	`expires_at` integer NOT NULL,
	CONSTRAINT "auth_sessions_role_check" CHECK("__new_auth_sessions"."role" IN ('editor', 'master'))
);
--> statement-breakpoint
INSERT INTO `__new_auth_sessions`("session_hash", "actor_id", "role", "email", "credential_version", "created_at", "expires_at") SELECT "session_hash", "actor_id", "role", "email", "credential_version", "created_at", "expires_at" FROM `auth_sessions`;--> statement-breakpoint
DROP TABLE `auth_sessions`;--> statement-breakpoint
ALTER TABLE `__new_auth_sessions` RENAME TO `auth_sessions`;--> statement-breakpoint
CREATE INDEX `auth_sessions_expires_at_idx` ON `auth_sessions` (`expires_at`);--> statement-breakpoint
CREATE TABLE `__new_bridge_status` (
	`id` integer PRIMARY KEY NOT NULL,
	`last_seen_at` integer NOT NULL,
	`bridge_version` text NOT NULL,
	`runtime_version` text NOT NULL,
	`last_error` text,
	CONSTRAINT "bridge_status_singleton_check" CHECK("__new_bridge_status"."id" = 1)
);
--> statement-breakpoint
INSERT INTO `__new_bridge_status`("id", "last_seen_at", "bridge_version", "runtime_version", "last_error") SELECT "id", "last_seen_at", "bridge_version", "runtime_version", "last_error" FROM `bridge_status`;--> statement-breakpoint
DROP TABLE `bridge_status`;--> statement-breakpoint
ALTER TABLE `__new_bridge_status` RENAME TO `bridge_status`;--> statement-breakpoint
CREATE TABLE `__new_command_queue` (
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
	`error` text,
	CONSTRAINT "command_queue_status_check" CHECK("__new_command_queue"."status" IN ('pending', 'claimed', 'completed', 'failed')),
	CONSTRAINT "command_queue_requested_role_check" CHECK("__new_command_queue"."requested_role" = 'master')
);
--> statement-breakpoint
INSERT INTO `__new_command_queue`("id", "operation", "payload_json", "status", "requested_by", "requested_role", "created_at", "claimed_at", "completed_at", "attempt_count", "result_json", "error") SELECT "id", "operation", "payload_json", "status", "requested_by", "requested_role", "created_at", "claimed_at", "completed_at", "attempt_count", "result_json", "error" FROM `command_queue`;--> statement-breakpoint
DROP TABLE `command_queue`;--> statement-breakpoint
ALTER TABLE `__new_command_queue` RENAME TO `command_queue`;--> statement-breakpoint
CREATE INDEX `command_queue_status_created_at_idx` ON `command_queue` (`status`,`created_at`);--> statement-breakpoint
CREATE TABLE `__new_dashboard_state` (
	`id` integer PRIMARY KEY NOT NULL,
	`schema_version` integer NOT NULL,
	`generated_at` text NOT NULL,
	`received_at` integer NOT NULL,
	`digest` text NOT NULL,
	`bridge_version` text NOT NULL,
	`snapshot_json` text NOT NULL,
	CONSTRAINT "dashboard_state_singleton_check" CHECK("__new_dashboard_state"."id" = 1)
);
--> statement-breakpoint
INSERT INTO `__new_dashboard_state`("id", "schema_version", "generated_at", "received_at", "digest", "bridge_version", "snapshot_json") SELECT "id", "schema_version", "generated_at", "received_at", "digest", "bridge_version", "snapshot_json" FROM `dashboard_state`;--> statement-breakpoint
DROP TABLE `dashboard_state`;--> statement-breakpoint
ALTER TABLE `__new_dashboard_state` RENAME TO `dashboard_state`;