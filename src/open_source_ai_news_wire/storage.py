"""SQLite persistence for the local dashboard."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import RuntimePaths, ensure_runtime_layout


SCHEMA_VERSION = 1


SCHEMA = """
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


class Database:
    def __init__(self, paths: RuntimePaths):
        self.paths = paths

    def initialize(self) -> None:
        ensure_runtime_layout(self.paths)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            connection.commit()
        self.paths.database.chmod(0o600)

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
