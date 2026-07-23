"""SQLite persistence for the local dashboard."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import RuntimePaths, ensure_runtime_layout


SCHEMA_VERSION = 5


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
            with self.connect() as connection:
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
        self.paths.database.chmod(0o600)

    def schema_version(self) -> int:
        if not self.paths.database.exists():
            return 0
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        return int(row["value"]) if row else 1

    def migrate(self) -> int:
        ensure_runtime_layout(self.paths)
        if not self.paths.database.exists():
            with self.connect() as connection:
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
                connection.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                    (str(target),),
                )
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
        with self.connect() as connection:
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
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]

    def one(self, sql: str, parameters: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, parameters)
        return rows[0] if rows else None

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        with self.connect() as connection:
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
