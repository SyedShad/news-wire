"""Tracked source definitions synchronized with private local source state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .settings import load_source_definitions
from .storage import Database


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def synchronize_sources(database: Database) -> int:
    """Upsert tracked definitions without overwriting existing local enablement or cursors."""
    definitions = load_source_definitions(database.paths)
    now = _now()
    with database.transaction() as connection:
        for definition in definitions:
            source_id = str(definition["id"])
            existing = connection.execute(
                "SELECT enabled FROM source_registry WHERE id = ?", (source_id,)
            ).fetchone()
            enabled = int(existing["enabled"]) if existing else int(
                bool(definition.get("enabled_by_default", False))
            )
            detail = str(definition.get("detail", ""))
            connection.execute(
                """
                INSERT INTO source_registry(
                    id, name, family, monitoring_role, url, enabled, health,
                    failure_streak, lag_minutes, detail, adapter, base_hosts_json,
                    parser_version, minimum_interval_minutes, checked_date,
                    definition_json, trust_class
                ) VALUES(?, ?, ?, ?, ?, ?, 'pending', 0, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    family = excluded.family,
                    monitoring_role = excluded.monitoring_role,
                    url = excluded.url,
                    detail = excluded.detail,
                    adapter = excluded.adapter,
                    base_hosts_json = excluded.base_hosts_json,
                    parser_version = excluded.parser_version,
                    minimum_interval_minutes = excluded.minimum_interval_minutes,
                    checked_date = excluded.checked_date,
                    trust_class = excluded.trust_class,
                    definition_json = excluded.definition_json
                """,
                (
                    source_id,
                    definition["name"],
                    definition["family"],
                    definition["monitoring_role"],
                    definition["url"],
                    enabled,
                    detail,
                    definition["adapter"],
                    Database.json(definition["base_hosts"]),
                    int(definition["parser_version"]),
                    int(definition["minimum_interval_minutes"]),
                    definition.get("checked_date"),
                    Database.json(definition),
                    definition["trust_class"],
                ),
            )
            connection.execute(
                """
                INSERT INTO source_state(source_id, enabled, health)
                VALUES(?, ?, 'pending')
                ON CONFLICT(source_id) DO NOTHING
                """,
                (source_id, enabled),
            )
        known = {str(definition["id"]) for definition in definitions}
        rows = connection.execute("SELECT id FROM source_registry").fetchall()
        for row in rows:
            if row["id"] not in known and not str(row["id"]).startswith("demo-"):
                connection.execute(
                    "UPDATE source_registry SET enabled = 0, health = 'retired' WHERE id = ?",
                    (row["id"],),
                )
                connection.execute(
                    "UPDATE source_state SET enabled = 0, health = 'retired' WHERE source_id = ?",
                    (row["id"],),
                )
        connection.execute(
            """
            INSERT INTO app_state(key, value, updated_at) VALUES('source_registry_synced_at', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (now, now),
        )
    return len(definitions)


def registered_source(database: Database, source_id: str) -> dict[str, Any] | None:
    return database.one(
        """
        SELECT r.*, s.etag, s.last_modified, s.retry_after_at,
               s.last_error_class, s.last_error_detail,
               s.enabled AS state_enabled, s.health AS state_health,
               s.cursor AS state_cursor, s.failure_streak AS state_failure_streak,
               s.last_checked_at AS state_last_checked_at,
               s.last_success_at AS state_last_success_at
        FROM source_registry r
        JOIN source_state s ON s.source_id = r.id
        WHERE r.id = ?
        """,
        (source_id,),
    )


def set_source_enabled(database: Database, source_id: str, enabled: bool) -> None:
    now = _now()
    with database.transaction() as connection:
        if not connection.execute(
            "SELECT 1 FROM source_registry WHERE id = ?", (source_id,)
        ).fetchone():
            raise LookupError("Source not found")
        state = 1 if enabled else 0
        health = "pending" if enabled else "paused"
        connection.execute(
            "UPDATE source_registry SET enabled = ?, health = ? WHERE id = ?",
            (state, health, source_id),
        )
        connection.execute(
            "UPDATE source_state SET enabled = ?, health = ? WHERE source_id = ?",
            (state, health, source_id),
        )
        connection.execute(
            """
            INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
            VALUES('info', 'source_enablement', ?, ?, ?)
            """,
            (
                "Source locally enabled." if enabled else "Source locally disabled.",
                now,
                Database.json({"source_id": source_id, "enabled": enabled}),
            ),
        )
