"""SQLite persistence for the local dashboard."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import RuntimePaths, ensure_runtime_layout


SCHEMA_VERSION = 9


def _exact_signature_records(value: object, *, source: bool) -> bool:
    if not isinstance(value, list) or not value:
        return False
    identifiers: list[str | int] = []
    for record in value:
        if not isinstance(record, dict) or set(record) != {"id", "signature"}:
            return False
        identifier = record["id"]
        if source:
            if not isinstance(identifier, str) or not identifier or len(identifier) > 160:
                return False
        elif isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 1:
            return False
        signature = record["signature"]
        if not isinstance(signature, str) or len(signature) != 64 or any(
            character not in "0123456789abcdef" for character in signature
        ):
            return False
        identifiers.append(identifier)
    return len(set(identifiers)) == len(identifiers) and identifiers == sorted(identifiers)


def _exact_v3_draft_snapshot(
    value: object, *, story_id: str, story_revision: int
) -> bool:
    if not isinstance(value, dict) or value.get("schema_version") != 3:
        return False
    revision = value.get("story_revision")
    action_id = value.get("review_action_id")
    return bool(
        value.get("story_id") == story_id
        and not isinstance(revision, bool)
        and isinstance(revision, int)
        and revision == story_revision
        and not isinstance(action_id, bool)
        and isinstance(action_id, int)
        and action_id > 0
        and _exact_signature_records(value.get("claim_signatures"), source=False)
        and _exact_signature_records(value.get("source_signatures"), source=True)
    )


class MigrationRequired(RuntimeError):
    """Raised when an existing store needs an explicit migration."""


class IncompatibleSchema(RuntimeError):
    """Raised when the database is newer than this application."""


SCHEMA_V1 = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_registry (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    family TEXT NOT NULL,
    monitoring_role TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    health TEXT NOT NULL DEFAULT 'healthy',
    failure_streak INTEGER NOT NULL DEFAULT 0,
    cursor TEXT,
    lag_minutes INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT,
    last_success_at TEXT,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS scan_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT NOT NULL,
    source_success_count INTEGER NOT NULL DEFAULT 0,
    source_failure_count INTEGER NOT NULL DEFAULT 0,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    details TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS story_cluster (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    headline TEXT NOT NULL,
    summary TEXT NOT NULL,
    lane TEXT NOT NULL,
    openness_class TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    priority_score INTEGER NOT NULL DEFAULT 0 CHECK(priority_score BETWEEN 0 AND 100),
    freshness TEXT NOT NULL,
    first_public_at TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    opportunity_strength TEXT,
    relevance_bridge TEXT NOT NULL DEFAULT '',
    counterargument TEXT NOT NULL DEFAULT '',
    watch_expires_at TEXT,
    watch_status TEXT,
    material_update INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT NOT NULL REFERENCES story_cluster(id) ON DELETE CASCADE,
    source_name TEXT NOT NULL,
    source_role TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    verification_status TEXT NOT NULL DEFAULT 'supports',
    passage TEXT NOT NULL DEFAULT '',
    UNIQUE(story_id, url)
);

CREATE TABLE IF NOT EXISTS claim (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT NOT NULL REFERENCES story_cluster(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    volatility TEXT NOT NULL DEFAULT 'stable'
);

CREATE TABLE IF NOT EXISTS evidence_link (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
    source_item_id INTEGER NOT NULL REFERENCES source_item(id) ON DELETE CASCADE,
    relationship TEXT NOT NULL,
    UNIQUE(claim_id, source_item_id, relationship)
);

CREATE TABLE IF NOT EXISTS alert (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT REFERENCES story_cluster(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT
);

CREATE TABLE IF NOT EXISTS review_action (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT NOT NULL REFERENCES story_cluster(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    draft_mode TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS draft (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT NOT NULL REFERENCES story_cluster(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    headline TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    lens TEXT NOT NULL DEFAULT '',
    sources_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    supersedes_id INTEGER REFERENCES draft(id),
    UNIQUE(story_id, version)
);

CREATE TABLE IF NOT EXISTS work_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    story_id TEXT REFERENCES story_cluster(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    operation TEXT NOT NULL,
    effort_units INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnostic_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_story_priority ON story_cluster(status, priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_source_story ON source_item(story_id);
CREATE INDEX IF NOT EXISTS idx_alert_unread ON alert(read_at, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_status ON work_item(status, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_time ON usage_ledger(created_at DESC);
"""


MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: (
        "ALTER TABLE source_registry ADD COLUMN adapter TEXT NOT NULL DEFAULT 'feed'",
        "ALTER TABLE source_registry ADD COLUMN base_hosts_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE source_registry ADD COLUMN parser_version INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE source_registry ADD COLUMN minimum_interval_minutes INTEGER NOT NULL DEFAULT 30",
        "ALTER TABLE source_registry ADD COLUMN checked_date TEXT",
        "ALTER TABLE source_registry ADD COLUMN definition_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE scan_run ADD COLUMN interval_start TEXT",
        "ALTER TABLE scan_run ADD COLUMN interval_end TEXT",
        "ALTER TABLE scan_run ADD COLUMN degraded INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE scan_run ADD COLUMN offline INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE scan_run ADD COLUMN queue_remaining INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE source_item ADD COLUMN canonical_url TEXT",
        "ALTER TABLE source_item ADD COLUMN source_registry_id TEXT",
        "ALTER TABLE source_item ADD COLUMN fingerprint TEXT",
        "ALTER TABLE source_item ADD COLUMN content_hash TEXT",
        "ALTER TABLE source_item ADD COLUMN first_seen_at TEXT",
        "ALTER TABLE source_item ADD COLUMN citation_parent_url TEXT",
        "ALTER TABLE review_action ADD COLUMN approval_snapshot_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE draft ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE draft ADD COLUMN approval_snapshot_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE work_item ADD COLUMN idempotency_key TEXT",
        "ALTER TABLE work_item ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE work_item ADD COLUMN available_at TEXT",
        "ALTER TABLE work_item ADD COLUMN last_error_class TEXT",
        "ALTER TABLE usage_ledger ADD COLUMN prompt_version TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE usage_ledger ADD COLUMN input_size INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE usage_ledger ADD COLUMN output_size INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE usage_ledger ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE diagnostic_event ADD COLUMN detail_json TEXT NOT NULL DEFAULT '{}'",
        """
        CREATE TABLE source_state (
            source_id TEXT PRIMARY KEY REFERENCES source_registry(id) ON DELETE CASCADE,
            enabled INTEGER NOT NULL DEFAULT 1,
            cursor TEXT,
            etag TEXT,
            last_modified TEXT,
            health TEXT NOT NULL DEFAULT 'pending',
            failure_streak INTEGER NOT NULL DEFAULT 0,
            lag_minutes INTEGER NOT NULL DEFAULT 0,
            last_checked_at TEXT,
            last_success_at TEXT,
            retry_after_at TEXT,
            last_error_class TEXT,
            last_error_detail TEXT NOT NULL DEFAULT ''
        )
        """,
        """
        INSERT INTO source_state(
            source_id, enabled, cursor, health, failure_streak, lag_minutes,
            last_checked_at, last_success_at
        )
        SELECT id, enabled, cursor, health, failure_streak, lag_minutes,
               last_checked_at, last_success_at
        FROM source_registry
        """,
        """
        CREATE TABLE source_transaction (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_run_id INTEGER REFERENCES scan_run(id) ON DELETE SET NULL,
            source_id TEXT NOT NULL REFERENCES source_registry(id) ON DELETE CASCADE,
            interval_start TEXT,
            interval_end TEXT,
            status TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            cursor_before TEXT,
            cursor_after TEXT,
            error_class TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT
        )
        """,
        """
        CREATE TABLE raw_observation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL REFERENCES source_registry(id) ON DELETE CASCADE,
            external_id TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            title TEXT NOT NULL,
            published_at TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            language TEXT NOT NULL DEFAULT 'und',
            fingerprint TEXT NOT NULL,
            content_hash TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(source_id, external_id),
            UNIQUE(source_id, fingerprint)
        )
        """,
        """
        CREATE TABLE story_membership (
            story_id TEXT NOT NULL REFERENCES story_cluster(id) ON DELETE CASCADE,
            source_item_id INTEGER NOT NULL REFERENCES source_item(id) ON DELETE CASCADE,
            relationship TEXT NOT NULL DEFAULT 'same-development',
            language TEXT NOT NULL DEFAULT 'und',
            PRIMARY KEY(story_id, source_item_id)
        )
        """,
        """
        CREATE TABLE candidate (
            story_id TEXT PRIMARY KEY REFERENCES story_cluster(id) ON DELETE CASCADE,
            evidence_gate INTEGER NOT NULL DEFAULT 0,
            importance_gate INTEGER NOT NULL DEFAULT 0,
            score INTEGER NOT NULL DEFAULT 0 CHECK(score BETWEEN 0 AND 100),
            score_json TEXT NOT NULL DEFAULT '{}',
            qualified_at TEXT
        )
        """,
        """
        CREATE TABLE watch_notice (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id TEXT NOT NULL REFERENCES story_cluster(id) ON DELETE CASCADE,
            reason TEXT NOT NULL,
            trace_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            next_check_at TEXT,
            expires_at TEXT NOT NULL,
            outcome TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE content_opportunity (
            story_id TEXT PRIMARY KEY REFERENCES story_cluster(id) ON DELETE CASCADE,
            strength TEXT NOT NULL,
            relevance_bridge TEXT NOT NULL,
            mechanism TEXT NOT NULL,
            counterargument TEXT NOT NULL,
            assessment_json TEXT NOT NULL DEFAULT '{}',
            assessed_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE retained_content (
            digest TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            byte_count INTEGER NOT NULL,
            media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
            source_url TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE assistance_result (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_item_id INTEGER REFERENCES work_item(id) ON DELETE SET NULL,
            operation TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            prompt_version TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE purge_plan (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            categories_json TEXT NOT NULL,
            effects_json TEXT NOT NULL,
            estimated_bytes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            confirmed_at TEXT,
            executed_at TEXT
        )
        """,
        """
        CREATE TABLE notification_delivery (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER REFERENCES alert(id) ON DELETE CASCADE,
            group_key TEXT,
            status TEXT NOT NULL,
            delivered_at TEXT,
            error_class TEXT,
            UNIQUE(alert_id)
        )
        """,
        "CREATE UNIQUE INDEX idx_work_idempotency ON work_item(idempotency_key) WHERE idempotency_key IS NOT NULL",
        "CREATE INDEX idx_observation_published ON raw_observation(published_at DESC)",
        "CREATE INDEX idx_source_transaction_source ON source_transaction(source_id, started_at DESC)",
        "CREATE INDEX idx_watch_due ON watch_notice(status, next_check_at)",
    ),
    3: (
        "ALTER TABLE candidate ADD COLUMN importance_override INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE candidate ADD COLUMN importance_override_reason TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE candidate ADD COLUMN importance_overridden_at TEXT",
        "ALTER TABLE candidate ADD COLUMN importance_override_action_id INTEGER REFERENCES review_action(id)",
        """
        CREATE TABLE evidence_source (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id TEXT NOT NULL REFERENCES story_cluster(id) ON DELETE CASCADE,
            requested_url TEXT NOT NULL,
            final_url TEXT,
            canonical_url TEXT NOT NULL,
            publisher_key TEXT NOT NULL,
            acquisition_method TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            passage TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            language TEXT NOT NULL DEFAULT 'und',
            proposed_role TEXT,
            confirmed_role TEXT,
            first_party_confirmed INTEGER NOT NULL DEFAULT 0,
            confirmation_reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            content_digest TEXT,
            content_type TEXT,
            fetched_at TEXT,
            confirmed_at TEXT,
            excluded_at TEXT,
            error_class TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(story_id, canonical_url)
        )
        """,
        """
        CREATE TABLE evidence_source_claim (
            evidence_source_id INTEGER NOT NULL REFERENCES evidence_source(id) ON DELETE CASCADE,
            claim_id INTEGER NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
            relationship TEXT NOT NULL,
            PRIMARY KEY(evidence_source_id, claim_id)
        )
        """,
        "CREATE INDEX idx_evidence_source_story ON evidence_source(story_id, status)",
        "CREATE INDEX idx_evidence_source_publisher ON evidence_source(publisher_key, confirmed_role, status)",
    ),
    4: (
        "ALTER TABLE evidence_source ADD COLUMN hosting_publisher_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE evidence_source ADD COLUMN proposed_origin_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE evidence_source ADD COLUMN proposed_origin_url TEXT",
        "ALTER TABLE evidence_source ADD COLUMN proposed_provenance_type TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE evidence_source ADD COLUMN reporting_origin_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE evidence_source ADD COLUMN reporting_origin_key TEXT",
        "ALTER TABLE evidence_source ADD COLUMN reporting_origin_url TEXT",
        "ALTER TABLE evidence_source ADD COLUMN provenance_type TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE evidence_source ADD COLUMN origin_status TEXT NOT NULL DEFAULT 'not_applicable'",
        "ALTER TABLE evidence_source ADD COLUMN origin_confirmed_at TEXT",
        "ALTER TABLE evidence_source ADD COLUMN origin_confirmation_action_id INTEGER REFERENCES review_action(id)",
        "CREATE INDEX idx_evidence_source_origin ON evidence_source(reporting_origin_key, origin_status, confirmed_role, status)",
    ),
    5: (
        "ALTER TABLE candidate ADD COLUMN manual_override INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE candidate ADD COLUMN manual_override_at TEXT",
        "ALTER TABLE candidate ADD COLUMN manual_override_action_id INTEGER REFERENCES review_action(id)",
        "ALTER TABLE candidate ADD COLUMN manual_override_snapshot_json TEXT NOT NULL DEFAULT '{}'",
    ),
    6: (
        "ALTER TABLE story_cluster ADD COLUMN importance_score INTEGER NOT NULL DEFAULT 0 CHECK(importance_score BETWEEN 0 AND 60)",
        "ALTER TABLE story_cluster ADD COLUMN importance_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE story_cluster ADD COLUMN material_updated_at TEXT",
        "ALTER TABLE story_cluster ADD COLUMN ingestion_context TEXT NOT NULL DEFAULT 'legacy'",
        """
        CREATE TABLE discovery_lead (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id TEXT NOT NULL REFERENCES story_cluster(id) ON DELETE CASCADE,
            via_source_id TEXT NOT NULL REFERENCES source_registry(id) ON DELETE CASCADE,
            external_id TEXT NOT NULL,
            identity_key TEXT NOT NULL,
            display_name TEXT NOT NULL,
            url TEXT NOT NULL,
            published_at TEXT NOT NULL,
            lead_type TEXT NOT NULL DEFAULT 'public_post',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(via_source_id, external_id)
        )
        """,
        """
        CREATE TABLE momentum_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id TEXT NOT NULL REFERENCES story_cluster(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL REFERENCES source_registry(id) ON DELETE CASCADE,
            captured_at TEXT NOT NULL,
            native_score REAL NOT NULL DEFAULT 0,
            post_count INTEGER,
            account_count INTEGER,
            daily_rank INTEGER,
            distinct_identity_count INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(story_id, source_id, captured_at)
        )
        """,
        "CREATE INDEX idx_story_current_review ON story_cluster(status, first_public_at DESC, material_updated_at DESC)",
        "CREATE INDEX idx_discovery_lead_story ON discovery_lead(story_id, identity_key)",
        "CREATE INDEX idx_momentum_story_time ON momentum_snapshot(story_id, captured_at DESC)",
    ),
    7: (
        "ALTER TABLE story_cluster ADD COLUMN story_revision INTEGER NOT NULL DEFAULT 1 CHECK(story_revision >= 1)",
        "ALTER TABLE story_cluster ADD COLUMN material_revision INTEGER NOT NULL DEFAULT 1 CHECK(material_revision >= 1)",
        "ALTER TABLE raw_observation ADD COLUMN observation_hash_version INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE raw_observation ADD COLUMN atomic_claim_signature TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE raw_observation ADD COLUMN source_reported_at TEXT",
        "ALTER TABLE raw_observation ADD COLUMN aggregator_published_at TEXT",
        "ALTER TABLE raw_observation ADD COLUMN effective_published_at TEXT",
        "ALTER TABLE raw_observation ADD COLUMN timestamp_status TEXT NOT NULL DEFAULT 'legacy_assumed'",
        "ALTER TABLE source_item ADD COLUMN source_revision INTEGER NOT NULL DEFAULT 1 CHECK(source_revision >= 1)",
        "ALTER TABLE source_item ADD COLUMN source_reported_at TEXT",
        "ALTER TABLE source_item ADD COLUMN aggregator_published_at TEXT",
        "ALTER TABLE source_item ADD COLUMN effective_published_at TEXT",
        "ALTER TABLE source_item ADD COLUMN timestamp_status TEXT NOT NULL DEFAULT 'legacy_assumed'",
        "ALTER TABLE review_action ADD COLUMN story_revision INTEGER",
        "ALTER TABLE review_action ADD COLUMN claim_signatures_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE review_action ADD COLUMN source_signatures_json TEXT NOT NULL DEFAULT '[]'",
        """
        CREATE TABLE source_revision (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_observation_id INTEGER REFERENCES raw_observation(id) ON DELETE SET NULL,
            source_id TEXT NOT NULL REFERENCES source_registry(id) ON DELETE CASCADE,
            external_id TEXT NOT NULL,
            revision_number INTEGER NOT NULL CHECK(revision_number >= 1),
            observation_hash_version INTEGER NOT NULL,
            observation_hash TEXT,
            atomic_claim_signature TEXT NOT NULL DEFAULT '',
            canonical_url TEXT NOT NULL,
            title TEXT NOT NULL,
            source_reported_at TEXT,
            aggregator_published_at TEXT,
            effective_published_at TEXT NOT NULL,
            timestamp_status TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            UNIQUE(source_id, external_id, revision_number)
        )
        """,
        "CREATE INDEX idx_source_revision_identity ON source_revision(source_id, external_id, revision_number DESC)",
        "CREATE INDEX idx_story_revisions ON story_cluster(story_revision, material_revision)",
    ),
    8: (
        "ALTER TABLE source_registry ADD COLUMN trust_class TEXT NOT NULL DEFAULT 'research_required'",
        "ALTER TABLE story_cluster ADD COLUMN organic_score INTEGER NOT NULL DEFAULT 0 CHECK(organic_score BETWEEN 0 AND 100)",
        "ALTER TABLE story_cluster ADD COLUMN review_score INTEGER NOT NULL DEFAULT 0 CHECK(review_score BETWEEN 0 AND 100)",
        "ALTER TABLE story_cluster ADD COLUMN priority_floor_applied INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE story_cluster ADD COLUMN source_trust_state TEXT NOT NULL DEFAULT 'research_required'",
        "ALTER TABLE story_cluster ADD COLUMN research_status TEXT NOT NULL DEFAULT 'queued'",
        "ALTER TABLE story_cluster ADD COLUMN verification_notice TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE draft ADD COLUMN suggested_flair TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE draft ADD COLUMN subreddit_reminder TEXT NOT NULL DEFAULT 'Verify rules before posting'",
        "ALTER TABLE draft ADD COLUMN search_attempt_id INTEGER REFERENCES research_attempt(id)",
        "ALTER TABLE evidence_source ADD COLUMN research_attempt_id INTEGER REFERENCES research_attempt(id)",
        "ALTER TABLE evidence_source ADD COLUMN source_provenance TEXT NOT NULL DEFAULT 'configured'",
        """
        CREATE TABLE research_attempt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id TEXT NOT NULL REFERENCES story_cluster(id) ON DELETE CASCADE,
            purpose TEXT NOT NULL CHECK(purpose IN ('background', 'draft_refresh')),
            status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'complete', 'partial', 'failed', 'unavailable')),
            query TEXT NOT NULL DEFAULT '',
            result_count INTEGER NOT NULL DEFAULT 0 CHECK(result_count BETWEEN 0 AND 3),
            error_class TEXT,
            detail TEXT NOT NULL DEFAULT '',
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_research_attempt_story ON research_attempt(story_id, purpose, created_at DESC)",
        "CREATE INDEX idx_research_attempt_status ON research_attempt(status, purpose, created_at)",
        "CREATE INDEX idx_evidence_source_attempt ON evidence_source(research_attempt_id)",
    ),
    9: (
        # SQLite cannot alter the purpose CHECK constraint in place. Schema 9
        # replaces only this parent table while foreign-key enforcement is
        # temporarily disabled by Database.migrate(), then verifies the full
        # graph before committing.
        """
        CREATE TABLE research_attempt_v9 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id TEXT NOT NULL REFERENCES story_cluster(id) ON DELETE CASCADE,
            purpose TEXT NOT NULL CHECK(purpose IN ('background', 'draft_refresh', 'operator_review')),
            status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'complete', 'partial', 'failed', 'unavailable')),
            query TEXT NOT NULL DEFAULT '',
            result_count INTEGER NOT NULL DEFAULT 0 CHECK(result_count BETWEEN 0 AND 3),
            error_class TEXT,
            detail TEXT NOT NULL DEFAULT '',
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        INSERT INTO research_attempt_v9(
            id, story_id, purpose, status, query, result_count, error_class,
            detail, started_at, completed_at, created_at, updated_at
        )
        SELECT id, story_id, purpose, status, query, result_count, error_class,
               detail, started_at, completed_at, created_at, updated_at
        FROM research_attempt
        """,
        "DROP TABLE research_attempt",
        "ALTER TABLE research_attempt_v9 RENAME TO research_attempt",
        "CREATE INDEX idx_research_attempt_story ON research_attempt(story_id, purpose, created_at DESC)",
        "CREATE INDEX idx_research_attempt_status ON research_attempt(status, purpose, created_at)",
        """
        CREATE TABLE relevance_notification_event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_key TEXT NOT NULL UNIQUE CHECK(length(article_key) = 64),
            source_item_id INTEGER REFERENCES source_item(id) ON DELETE SET NULL,
            story_id TEXT NOT NULL,
            canonical_url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            publisher TEXT NOT NULL,
            category TEXT NOT NULL,
            context TEXT NOT NULL,
            provenance TEXT NOT NULL CHECK(provenance IN (
                'publisher_excerpt', 'paper_abstract', 'repository_text',
                'discovery_metadata', 'limited_context'
            )),
            source_type TEXT NOT NULL CHECK(source_type IN (
                'article', 'announcement', 'paper', 'repository',
                'newsletter', 'discovery'
            )),
            published_at TEXT,
            detected_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE relevance_notification_outbox (
            event_id INTEGER PRIMARY KEY REFERENCES relevance_notification_event(id) ON DELETE CASCADE,
            delivery_state TEXT NOT NULL DEFAULT 'pending'
                CHECK(delivery_state IN ('pending', 'dismissed')),
            dismissed_at TEXT,
            research_attempt_id INTEGER REFERENCES research_attempt(id) ON DELETE SET NULL,
            research_requested_at TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE relevance_notification_followup (
            event_id INTEGER PRIMARY KEY REFERENCES relevance_notification_event(id) ON DELETE CASCADE,
            research_attempt_id INTEGER NOT NULL REFERENCES research_attempt(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK(status IN ('complete', 'partial', 'failed', 'unavailable')),
            result_count INTEGER NOT NULL DEFAULT 0 CHECK(result_count BETWEEN 0 AND 3),
            publishers_json TEXT NOT NULL DEFAULT '[]',
            detail TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE relevance_native_delivery (
            event_id INTEGER PRIMARY KEY REFERENCES relevance_notification_event(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'delivered', 'failed')),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            delivered_at TEXT,
            next_attempt_at TEXT,
            error_class TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_relevance_notification_detected ON relevance_notification_event(detected_at, id)",
        "CREATE INDEX idx_relevance_notification_outbox_state ON relevance_notification_outbox(delivery_state, updated_at)",
        """
        INSERT INTO app_state(key, value, updated_at)
        VALUES(
            'relevance_notification_watermark',
            strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
            strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
        )
        ON CONFLICT(key) DO NOTHING
        """,
        """
        INSERT INTO app_state(key, value, updated_at)
        VALUES('relevance_notification_capture_enabled', 'true', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ON CONFLICT(key) DO NOTHING
        """,
        """
        INSERT INTO app_state(key, value, updated_at)
        VALUES('relevance_notifications_enabled', 'false', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ON CONFLICT(key) DO NOTHING
        """,
        """
        INSERT INTO app_state(key, value, updated_at)
        VALUES('relevance_notifications_shadow_mode', 'true', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ON CONFLICT(key) DO NOTHING
        """,
        """
        INSERT INTO app_state(key, value, updated_at)
        VALUES('relevance_native_notifications_enabled', 'false', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ON CONFLICT(key) DO NOTHING
        """,
    ),
}


@dataclass(frozen=True, slots=True)
class StoragePressure:
    level: str
    free_bytes: int
    warning_bytes: int
    critical_bytes: int


class Database:
    def __init__(self, paths: RuntimePaths):
        self.paths = paths

    def initialize(self) -> None:
        ensure_runtime_layout(self.paths)
        database_exists = self.paths.database.exists()
        if not database_exists:
            with closing(self.connect()) as connection:
                connection.executescript(SCHEMA_V1)
                connection.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '1')"
                )
                connection.commit()
            self.migrate()
        else:
            version = self.schema_version()
            if version > SCHEMA_VERSION:
                raise IncompatibleSchema(
                    f"Database schema {version} is newer than supported schema {SCHEMA_VERSION}"
                )
            if version < SCHEMA_VERSION:
                raise MigrationRequired(
                    f"Database schema {version} requires explicit migration to {SCHEMA_VERSION}"
                )
        self._repair_037_orphaned_evidence()
        self.paths.database.chmod(0o600)

    def _repair_037_orphaned_evidence(self) -> None:
        """Repair the pre-0.3.7 evidence/work-item split without deleting history."""
        repair_key = "repair_0_3_7_orphaned_evidence"
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT value FROM app_state WHERE key = ?", (repair_key,)
            ).fetchone()
            if prior and prior["value"] == "complete":
                connection.rollback()
                return

            superseded: list[dict[str, int]] = []
            requeued: list[int] = []
            queued = connection.execute(
                "SELECT id, story_id, requested_url, canonical_url FROM evidence_source WHERE status = 'queued' ORDER BY id"
            ).fetchall()
            for evidence in queued:
                evidence_id = int(evidence["id"])
                active_work = connection.execute(
                    """
                    SELECT id FROM work_item
                    WHERE kind = 'evidence_enrichment'
                      AND status IN ('pending', 'queued', 'running')
                      AND json_valid(payload_json)
                      AND json_extract(payload_json, '$.evidence_source_id') = ?
                    LIMIT 1
                    """,
                    (evidence_id,),
                ).fetchone()
                if active_work:
                    continue
                duplicate = connection.execute(
                    """
                    SELECT id FROM evidence_source
                    WHERE story_id = ? AND id != ?
                      AND status IN ('fetched', 'confirmed')
                      AND (requested_url = ? OR canonical_url = ?)
                    ORDER BY CASE status WHEN 'confirmed' THEN 0 ELSE 1 END, id
                    LIMIT 1
                    """,
                    (
                        evidence["story_id"],
                        evidence_id,
                        evidence["requested_url"],
                        evidence["canonical_url"],
                    ),
                ).fetchone()
                if duplicate:
                    duplicate_id = int(duplicate["id"])
                    connection.execute(
                        """
                        UPDATE evidence_source
                        SET status = 'excluded', excluded_at = ?, updated_at = ?,
                            error_class = 'superseded_duplicate',
                            confirmation_reason = ?
                        WHERE id = ? AND status = 'queued'
                        """,
                        (
                            now,
                            now,
                            f"Superseded by preserved evidence proposal {duplicate_id}.",
                            evidence_id,
                        ),
                    )
                    superseded.append(
                        {"evidence_source_id": evidence_id, "superseded_by": duplicate_id}
                    )
                    continue

                connection.execute(
                    """
                    INSERT INTO work_item(
                        kind, story_id, status, priority, payload_json, created_at,
                        updated_at, idempotency_key, available_at
                    ) VALUES('evidence_enrichment', ?, 'queued', 70, ?, ?, ?, ?, ?)
                    ON CONFLICT(idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
                    """,
                    (
                        evidence["story_id"],
                        self.json({"schema_version": 1, "evidence_source_id": evidence_id}),
                        now,
                        now,
                        f"evidence-repair:{evidence_id}",
                        now,
                    ),
                )
                requeued.append(evidence_id)

            connection.execute(
                """
                INSERT INTO app_state(key, value, updated_at) VALUES(?, 'complete', ?)
                ON CONFLICT(key) DO UPDATE SET value='complete', updated_at=excluded.updated_at
                """,
                (repair_key, now),
            )
            if superseded or requeued:
                connection.execute(
                    """
                    INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                    VALUES('info', 'orphaned_evidence_repair', ?, ?, ?)
                    """,
                    (
                        "Reconciled evidence proposals that had no active enrichment work item.",
                        now,
                        self.json(
                            {
                                "superseded": superseded,
                                "requeued_evidence_source_ids": requeued,
                            }
                        ),
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def schema_version(self) -> int:
        if not self.paths.database.exists():
            return 0
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        return int(row["value"]) if row else 1

    def migrate(self) -> int:
        ensure_runtime_layout(self.paths)
        if not self.paths.database.exists():
            with closing(self.connect()) as connection:
                connection.executescript(SCHEMA_V1)
                connection.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '1')"
                )
                connection.commit()
        version = self.schema_version()
        if version > SCHEMA_VERSION:
            raise IncompatibleSchema(
                f"Database schema {version} is newer than supported schema {SCHEMA_VERSION}"
            )
        while version < SCHEMA_VERSION:
            target = version + 1
            statements = MIGRATIONS.get(target)
            if not statements:
                raise RuntimeError(f"Missing migration for schema {target}")
            connection = self.connect()
            try:
                if target == 9:
                    connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("BEGIN IMMEDIATE")
                for statement in statements:
                    connection.execute(statement)
                if version == 1:
                    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    cancelled = connection.execute(
                        """
                        UPDATE work_item
                        SET status = 'cancelled', updated_at = ?,
                            last_error_class = 'pre_worker_staging_request'
                        WHERE kind IN ('scout_scan', 'catch_up')
                          AND status IN ('pending', 'queued')
                        """,
                        (now,),
                    ).rowcount
                    cancelled_scans = connection.execute(
                        """
                        UPDATE scan_run
                        SET result = 'cancelled', finished_at = ?,
                            details = 'Cancelled during worker migration; no collection was performed.'
                        WHERE result = 'queued' AND finished_at IS NULL
                        """,
                        (now,),
                    ).rowcount
                    if cancelled or cancelled_scans:
                        connection.execute(
                            """
                            INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                            VALUES('info', 'migration', ?, ?, ?)
                            """,
                            (
                                "Cancelled pre-worker staged requests during schema migration.",
                                now,
                                self.json({
                                    "cancelled_work_items": cancelled,
                                    "cancelled_scan_records": cancelled_scans,
                                }),
                            ),
                        )
                if version == 2:
                    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    cancelled = connection.execute(
                        """
                        UPDATE work_item
                        SET status = 'cancelled', updated_at = ?,
                            last_error_class = 'superseded_legacy_research'
                        WHERE kind = 'research' AND status IN ('pending', 'queued')
                        """,
                        (now,),
                    ).rowcount
                    if cancelled:
                        connection.execute(
                            """
                            INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                            VALUES('info', 'migration', ?, ?, ?)
                            """,
                            (
                                "Cancelled legacy research requests superseded by evidence enrichment.",
                                now,
                                self.json({"cancelled_work_items": cancelled}),
                            ),
                        )
                if version == 3:
                    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    reporting_review_count = int(
                        connection.execute(
                            """
                            SELECT COUNT(*) AS count FROM evidence_source
                            WHERE confirmed_role = 'Reporting' AND status = 'confirmed'
                            """
                        ).fetchone()["count"]
                    )
                    connection.execute(
                        """
                        UPDATE evidence_source
                        SET hosting_publisher_name = CASE
                                WHEN hosting_publisher_name = '' THEN publisher_key
                                ELSE hosting_publisher_name
                            END,
                            origin_status = CASE
                                WHEN confirmed_role = 'Reporting' AND status = 'confirmed'
                                    THEN 'needs_review'
                                ELSE 'not_applicable'
                            END,
                            provenance_type = CASE
                                WHEN confirmed_role = 'Event' AND status = 'confirmed'
                                    THEN 'original'
                                ELSE provenance_type
                            END,
                            updated_at = ?
                        """,
                        (now,),
                    )
                    affected = [
                        str(row["story_id"])
                        for row in connection.execute(
                            """
                            SELECT c.story_id
                            FROM candidate c
                            WHERE c.evidence_gate = 1
                              AND NOT EXISTS (
                                  SELECT 1 FROM source_item si
                                  WHERE si.story_id = c.story_id AND si.source_role = 'Event'
                              )
                              AND NOT EXISTS (
                                  SELECT 1 FROM evidence_source es
                                  WHERE es.story_id = c.story_id
                                    AND es.status = 'confirmed'
                                    AND es.confirmed_role = 'Event'
                              )
                            """
                        ).fetchall()
                    ]
                    for story_id in affected:
                        connection.execute(
                            "UPDATE candidate SET evidence_gate = 0 WHERE story_id = ?",
                            (story_id,),
                        )
                        current = connection.execute(
                            "SELECT status, headline FROM story_cluster WHERE id = ?",
                            (story_id,),
                        ).fetchone()
                        if not current or current["status"] not in {"candidate", "approved", "draft_ready"}:
                            continue
                        active_watch = connection.execute(
                            "SELECT 1 FROM watch_notice WHERE story_id = ? AND status = 'active'",
                            (story_id,),
                        ).fetchone()
                        next_status = "watch" if active_watch else "signal"
                        connection.execute(
                            "UPDATE story_cluster SET status = ?, material_update = 1, updated_at = ? WHERE id = ?",
                            (next_status, now, story_id),
                        )
                        connection.execute(
                            """
                            UPDATE work_item
                            SET status = 'needs_reapproval', last_error_class = 'publisher_provenance_review',
                                updated_at = ?
                            WHERE story_id = ? AND kind = 'draft'
                              AND status IN ('pending', 'queued', 'running', 'generating', 'waiting')
                            """,
                            (now, story_id),
                        )
                        changed_drafts = connection.execute(
                            "UPDATE draft SET status = 'Needs Review', updated_at = ? WHERE story_id = ? AND status = 'Current'",
                            (now, story_id),
                        ).rowcount
                        if changed_drafts:
                            already_alerted = connection.execute(
                                "SELECT 1 FROM alert WHERE story_id = ? AND kind = 'correction' AND read_at IS NULL",
                                (story_id,),
                            ).fetchone()
                            if not already_alerted:
                                connection.execute(
                                    """
                                    INSERT INTO alert(story_id, kind, severity, title, body, created_at)
                                    VALUES(?, 'correction', 'high', ?,
                                           'Reporting provenance needs confirmation before this draft can be replaced.', ?)
                                    """,
                                    (story_id, current["headline"], now),
                                )
                    if reporting_review_count or affected:
                        connection.execute(
                            """
                            INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                            VALUES('info', 'migration', ?, ?, ?)
                            """,
                            (
                                "Reporting evidence now requires a human-confirmed original publisher.",
                                now,
                                self.json({
                                    "reporting_sources_needing_review": reporting_review_count,
                                    "affected_story_count": len(affected),
                                }),
                            ),
                        )
                if version == 5:
                    from .qualification import importance_components

                    rows = connection.execute(
                        "SELECT id, headline, summary FROM story_cluster"
                    ).fetchall()
                    for row in rows:
                        roles = [
                            str(item["source_role"])
                            for item in connection.execute(
                                "SELECT DISTINCT source_role FROM source_item WHERE story_id = ?",
                                (row["id"],),
                            ).fetchall()
                        ]
                        components = importance_components(
                            str(row["headline"]),
                            str(row["summary"]),
                            roles,
                            # Schema 6 deliberately does not pretend that a
                            # legacy record's novelty can be reconstructed.
                            novelty=0,
                            assume_relevant=True,
                        )
                        connection.execute(
                            """
                            UPDATE story_cluster
                            SET importance_score = ?, importance_json = ?,
                                material_updated_at = NULL, ingestion_context = 'legacy'
                            WHERE id = ?
                            """,
                            (components["total"], self.json(components), row["id"]),
                        )
                        connection.execute(
                            """
                            UPDATE candidate
                            SET importance_gate = MAX(importance_gate, ?)
                            WHERE story_id = ?
                            """,
                            (
                                int(
                                    components["material_importance"] >= 26
                                    and components["total"] >= 40
                                ),
                                row["id"],
                            ),
                        )
                    connection.execute(
                        """
                        UPDATE story_cluster
                        SET priority = 'Standard'
                        WHERE watch_status = 'Expired' AND status = 'signal'
                        """
                    )
                if version == 6:
                    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    # Existing rows retain their historical hash as a version-1
                    # baseline.  Recollection under hash version 2 records a
                    # new source revision without implying a material update.
                    connection.execute(
                        """
                        UPDATE raw_observation
                        SET observation_hash_version = 1,
                            effective_published_at = COALESCE(effective_published_at, published_at),
                            source_reported_at = COALESCE(source_reported_at, published_at),
                            timestamp_status = 'legacy_assumed'
                        """
                    )
                    connection.execute(
                        """
                        UPDATE source_item
                        SET effective_published_at = COALESCE(effective_published_at, published_at),
                            source_reported_at = COALESCE(source_reported_at, published_at),
                            timestamp_status = 'legacy_assumed'
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO source_revision(
                            raw_observation_id, source_id, external_id, revision_number,
                            observation_hash_version, observation_hash,
                            atomic_claim_signature, canonical_url, title,
                            source_reported_at, aggregator_published_at,
                            effective_published_at, timestamp_status, observed_at
                        )
                        SELECT id, source_id, external_id, 1, observation_hash_version,
                               content_hash, atomic_claim_signature, canonical_url, title,
                               source_reported_at, aggregator_published_at,
                               COALESCE(effective_published_at, published_at),
                               timestamp_status, observed_at
                        FROM raw_observation
                        """
                    )
                    legacy_revision_count = int(
                        connection.execute(
                            "SELECT COUNT(*) AS count FROM source_revision"
                        ).fetchone()["count"]
                    )
                    repair_timestamp = "2026-07-23T11:13:29Z"
                    repair_targets = (
                        "story-debeeae2b4fa78a37fda",
                        "story-670cb7d55f310b539bbc",
                    )
                    present_repair_ids = {
                        str(row["id"])
                        for row in connection.execute(
                            "SELECT id FROM story_cluster WHERE id IN (?, ?)",
                            repair_targets,
                        )
                    }
                    repaired_ids: list[str] = []
                    for story_id in repair_targets:
                        repaired = connection.execute(
                            """
                            UPDATE story_cluster
                            SET material_update = 0, material_updated_at = NULL
                            WHERE id = ? AND material_updated_at = ?
                            """,
                            (story_id, repair_timestamp),
                        ).rowcount
                        if repaired:
                            repaired_ids.append(story_id)
                    if legacy_revision_count:
                        connection.execute(
                            """
                            INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                            VALUES('info', 'schema7_hash_baseline', ?, ?, ?)
                            """,
                            (
                                "Versioned legacy observation hashes without treating the rebase as editorial change.",
                                now,
                                self.json({
                                    "legacy_hash_version": 1,
                                    "next_hash_version": 2,
                                    "source_revision_rows": legacy_revision_count,
                                }),
                            ),
                        )
                    if present_repair_ids:
                        connection.execute(
                            """
                            INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                            VALUES('info', 'false_material_update_repair', ?, ?, ?)
                            """,
                            (
                                "Conditionally evaluated confirmed false material-update ranking anchors.",
                                now,
                                self.json({
                                    "expected_timestamp": repair_timestamp,
                                    "repaired_story_ids": repaired_ids,
                                    "skipped_story_ids": sorted(
                                        present_repair_ids - set(repaired_ids)
                                    ),
                                }),
                            ),
                        )
                    invalid_draft_ids: list[int] = []
                    active_drafts = connection.execute(
                        """
                        SELECT w.id, w.story_id, w.payload_json, s.story_revision
                        FROM work_item w
                        LEFT JOIN story_cluster s ON s.id = w.story_id
                        WHERE w.kind = 'draft'
                          AND w.status NOT IN ('completed', 'cancelled', 'failed', 'needs_reapproval')
                        ORDER BY w.id
                        """
                    ).fetchall()
                    for work in active_drafts:
                        try:
                            payload = json.loads(str(work["payload_json"] or "{}"))
                        except (TypeError, json.JSONDecodeError):
                            payload = None
                        story_id = str(work["story_id"] or "")
                        story_revision = int(work["story_revision"] or 0)
                        exact = _exact_v3_draft_snapshot(
                            payload,
                            story_id=story_id,
                            story_revision=story_revision,
                        )
                        action = None
                        if exact and isinstance(payload, dict):
                            action = connection.execute(
                                "SELECT id, story_id FROM review_action WHERE id = ?",
                                (int(payload["review_action_id"]),),
                            ).fetchone()
                            exact = bool(action and str(action["story_id"]) == story_id)
                        if not exact:
                            invalid_draft_ids.append(int(work["id"]))
                            continue
                        # Schema 7 makes the snapshot signatures independently
                        # auditable on the approval action. Backfill only from an
                        # already exact v3 payload; no editorial data is inferred.
                        connection.execute(
                            """
                            UPDATE review_action
                            SET story_revision = ?, claim_signatures_json = ?,
                                source_signatures_json = ?
                            WHERE id = ?
                            """,
                            (
                                story_revision,
                                self.json(payload["claim_signatures"]),
                                self.json(payload["source_signatures"]),
                                int(payload["review_action_id"]),
                            ),
                        )
                    if invalid_draft_ids:
                        placeholders = ",".join("?" for _ in invalid_draft_ids)
                        quarantine_query = f"""
                            UPDATE work_item
                            SET status = 'needs_reapproval',
                                last_error_class = 'legacy_approval_snapshot_incomplete',
                                updated_at = ?
                            WHERE id IN ({placeholders})
                            """  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- placeholders are generated only from integer IDs
                        connection.execute(
                            quarantine_query,
                            (now, *invalid_draft_ids),
                        )
                        connection.execute(
                            """
                            INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                            VALUES('warning', 'legacy_draft_snapshot_quarantine', ?, ?, ?)
                            """,
                            (
                                "Quarantined active draft requests without exact schema-7 approval provenance.",
                                now,
                                self.json({
                                    "count": len(invalid_draft_ids),
                                    "work_item_ids": invalid_draft_ids[:50],
                                    "truncated_count": max(0, len(invalid_draft_ids) - 50),
                                    "error_class": "legacy_approval_snapshot_incomplete",
                                }),
                            ),
                        )
                    for key, value in (
                        ("assistance_enabled", "false"),
                        ("assistance_isolation_gate", "requires_0.3.6_revalidation"),
                        ("assistance_unavailable_reason", "security_revalidation"),
                    ):
                        connection.execute(
                            """
                            INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?)
                            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                            """,
                            (key, value, now),
                        )
                if version == 7:
                    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    connection.execute(
                        """
                        UPDATE source_registry
                        SET trust_class = CASE
                            WHEN monitoring_role IN ('Event', 'Reporting') OR family = 'Research'
                                THEN 'trusted'
                            ELSE 'research_required'
                        END
                        """
                    )
                    connection.execute(
                        """
                        UPDATE story_cluster
                        SET organic_score = priority_score,
                            source_trust_state = CASE WHEN EXISTS (
                                SELECT 1 FROM source_item si
                                JOIN source_registry sr ON sr.id = si.source_registry_id
                                WHERE si.story_id = story_cluster.id
                                  AND sr.trust_class = 'trusted'
                            ) THEN 'trusted' ELSE 'research_required' END
                        """
                    )
                    connection.execute(
                        """
                        UPDATE story_cluster
                        SET status = 'ready'
                        WHERE status IN ('signal', 'watch', 'candidate')
                        """
                    )
                    connection.execute(
                        """
                        UPDATE story_cluster
                        SET research_status = CASE
                                WHEN source_trust_state = 'trusted' THEN 'not_needed'
                                ELSE 'unavailable'
                            END,
                            verification_notice = CASE
                                WHEN source_trust_state = 'trusted' THEN ''
                                ELSE 'Verify this yourself'
                            END,
                            priority_floor_applied = CASE
                                WHEN status NOT IN ('archived', 'withdrawn') THEN 1
                                ELSE 0
                            END,
                            review_score = CASE
                                WHEN status NOT IN ('archived', 'withdrawn')
                                    THEN MAX(organic_score, 80)
                                ELSE organic_score
                            END,
                            priority_score = CASE
                                WHEN status NOT IN ('archived', 'withdrawn')
                                    THEN MAX(organic_score, 80)
                                ELSE organic_score
                            END,
                            priority = CASE
                                WHEN status NOT IN ('archived', 'withdrawn') THEN 'Urgent'
                                ELSE priority
                            END,
                            watch_status = CASE
                                WHEN status = 'ready' THEN NULL ELSE watch_status
                            END,
                            watch_expires_at = CASE
                                WHEN status = 'ready' THEN NULL ELSE watch_expires_at
                            END,
                            updated_at = ?
                        """,
                        (now,),
                    )
                    connection.execute(
                        """
                        INSERT INTO research_attempt(
                            story_id, purpose, status, result_count, error_class,
                            detail, started_at, completed_at, created_at, updated_at
                        )
                        SELECT id, 'background', 'unavailable', 0,
                               'migration_research_unavailable',
                               'Story predates automatic research; verify it yourself.',
                               ?, ?, ?, ?
                        FROM story_cluster
                        WHERE status NOT IN ('archived', 'withdrawn')
                          AND source_trust_state = 'research_required'
                        """,
                        (now, now, now, now),
                    )
                    connection.execute(
                        """
                        UPDATE work_item
                        SET status = 'cancelled', last_error_class = 'obsolete_editorial_gate',
                            updated_at = ?
                        WHERE kind = 'draft'
                          AND status NOT IN ('completed', 'cancelled', 'failed')
                        """,
                        (now,),
                    )
                    if connection.execute(
                        "SELECT 1 FROM story_cluster LIMIT 1"
                    ).fetchone():
                        connection.execute(
                            """
                            INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                            VALUES('info', 'schema8_ungated_workflow', ?, ?, ?)
                            """,
                            (
                                "Migrated active stories to the trust-and-status workflow.",
                                now,
                                self.json({
                                    "active_status": "ready",
                                    "priority_floor": 80,
                                    "legacy_draft_work": "cancelled",
                                }),
                            ),
                        )
                    for key, value in (
                        ("assistance_enabled", "false"),
                        ("assistance_isolation_gate", "requires_0.4.0_revalidation"),
                        ("assistance_unavailable_reason", "security_revalidation"),
                    ):
                        connection.execute(
                            """
                            INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?)
                            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                            """,
                            (key, value, now),
                        )
                connection.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                    (str(target),),
                )
                if target == 9:
                    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise RuntimeError("Schema 9 migration produced invalid foreign keys")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            version = target
        self.paths.database.chmod(0o600)
        return version

    def integrity_check(self) -> str:
        with closing(self.connect()) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"

    def storage_pressure(
        self,
        *,
        warning_bytes: int = 10 * 1024**3,
        critical_bytes: int = 2 * 1024**3,
    ) -> StoragePressure:
        free = shutil.disk_usage(self.paths.root).free
        level = "critical" if free < critical_bytes else "warning" if free < warning_bytes else "normal"
        return StoragePressure(level, free, warning_bytes, critical_bytes)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.paths.database, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def query(self, sql: str, parameters: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]

    def one(self, sql: str, parameters: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, parameters)
        return rows[0] if rows else None

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        with closing(self.connect()) as connection:
            cursor = connection.execute(sql, parameters)
            connection.commit()
            return int(cursor.lastrowid)

    def get_state(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM app_state WHERE key = ?", (key,))
        return str(row["value"]) if row else default

    def set_state(self, key: str, value: str, updated_at: str) -> None:
        self.execute(
            """
            INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, updated_at),
        )

    @staticmethod
    def json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def database_size(path: Path) -> int:
    total = 0
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            total += candidate.stat().st_size
    return total
