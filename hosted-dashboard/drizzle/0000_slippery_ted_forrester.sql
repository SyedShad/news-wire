CREATE TABLE `audit_events` (
	`id` text PRIMARY KEY NOT NULL,
	`created_at` integer NOT NULL,
	`actor_id` text,
	`role` text,
	`action` text NOT NULL,
	`outcome` text NOT NULL,
	`ip_hash` text,
	`detail` text
);
--> statement-breakpoint
CREATE TABLE `auth_sessions` (
	`session_hash` text PRIMARY KEY NOT NULL,
	`actor_id` text NOT NULL,
	`role` text NOT NULL,
	`email` text,
	`credential_version` text NOT NULL,
	`created_at` integer NOT NULL,
	`expires_at` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `master_login_state` (
	`fingerprint` text PRIMARY KEY NOT NULL,
	`failures` integer NOT NULL,
	`next_allowed_at` integer NOT NULL,
	`locked_until` integer NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `oauth_transactions` (
	`state_hash` text PRIMARY KEY NOT NULL,
	`verifier` text NOT NULL,
	`nonce` text NOT NULL,
	`created_at` integer NOT NULL,
	`expires_at` integer NOT NULL
);
