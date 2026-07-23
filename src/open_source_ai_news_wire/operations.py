"""Redacted diagnostics and explicit no-undo purge plans."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .scheduler import SchedulerStatus, live_scheduler_status
from .storage import Database, database_size


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


PURGE_CATEGORIES = {
    "raw_observations": {"protected": False, "table": "raw_observation"},
    "retained_content": {"protected": False, "table": "retained_content"},
    "read_alerts": {"protected": False, "table": "alert"},
    "diagnostics": {"protected": False, "table": "diagnostic_event"},
    "drafts": {"protected": True, "table": "draft"},
    "decisions": {"protected": True, "table": "review_action"},
    "evidence_maps": {"protected": True, "table": "claim"},
    "usage_ledger": {"protected": True, "table": "usage_ledger"},
}


@dataclass(frozen=True, slots=True)
class PurgePreview:
    plan_id: str
    categories: tuple[str, ...]
    estimated_bytes: int
    effects: dict[str, Any]


def diagnostic_payload(
    database: Database,
    *,
    scheduler_status: Callable[[], SchedulerStatus] | None = None,
) -> dict[str, Any]:
    live_schedule = (scheduler_status or (lambda: live_scheduler_status(database)))()
    source_health = database.query(
        """
        SELECT r.id, r.family, s.health, s.failure_streak, s.last_checked_at,
               s.last_success_at, s.last_error_class
        FROM source_registry r JOIN source_state s ON s.source_id = r.id
        ORDER BY r.family, r.id
        """
    )
    counts: dict[str, int] = {}
    for table in (
        "source_registry", "raw_observation", "story_cluster", "source_item", "evidence_source", "claim",
        "alert", "draft", "work_item", "usage_ledger", "diagnostic_event",
    ):
        counts[table] = int(database.one(f"SELECT COUNT(*) AS count FROM {table}")["count"])
    pressure = database.storage_pressure()
    queue = database.one(
        "SELECT COUNT(*) AS count, MIN(created_at) AS oldest_at FROM work_item "
        "WHERE status IN ('pending', 'queued', 'running', 'generating', 'waiting')"
    ) or {"count": 0, "oldest_at": None}
    return {
        "generated_at": _now(),
        "application": "open-source-ai-news-wire",
        "schema_version": database.schema_version(),
        "integrity": database.integrity_check(),
        "database_bytes": database_size(database.paths.database),
        "storage": {"level": pressure.level, "free_bytes": pressure.free_bytes},
        "counts": counts,
        "queue": queue,
        "source_health": source_health,
        "schedule": {
            "installed": live_schedule.installed,
            "loaded": live_schedule.loaded,
            "status": live_schedule.state,
            "error": live_schedule.error,
            "last_scan_at": database.get_state("last_scan_at", "Never"),
        },
        "assistance": {
            "enabled": database.get_state("assistance_enabled", "false") == "true",
            "isolation_gate": database.get_state("assistance_isolation_gate", "not_run"),
        },
        "redaction": "No source URLs, query strings, passages, packets, drafts, credentials, or local usernames are included.",
    }


def export_diagnostics(database: Database, destination: Path) -> Path:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.write_text(
        json.dumps(diagnostic_payload(database), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    destination.chmod(0o600)
    return destination


def create_purge_plan(
    database: Database,
    categories: list[str],
    *,
    allow_protected: bool = False,
) -> PurgePreview:
    selected = tuple(dict.fromkeys(categories))
    unknown = set(selected) - set(PURGE_CATEGORIES)
    if unknown:
        raise ValueError(f"Unknown purge categories: {', '.join(sorted(unknown))}")
    protected = [name for name in selected if PURGE_CATEGORIES[name]["protected"]]
    if protected and not allow_protected:
        raise ValueError("Protected editorial categories require separate deliberate selection")
    effects: dict[str, Any] = {}
    estimated = 0
    for category in selected:
        if category == "read_alerts":
            count = int(database.one("SELECT COUNT(*) AS count FROM alert WHERE read_at IS NOT NULL")["count"])
        elif category == "evidence_maps":
            count = int(database.one("SELECT COUNT(*) AS count FROM claim")["count"])
        else:
            table = str(PURGE_CATEGORIES[category]["table"])
            count = int(database.one(f"SELECT COUNT(*) AS count FROM {table}")["count"])
        effects[category] = {
            "records": count,
            "protected": bool(PURGE_CATEGORIES[category]["protected"]),
            "consequence": _purge_consequence(category),
        }
    if "retained_content" in selected:
        estimated += int(database.one("SELECT COALESCE(SUM(byte_count), 0) AS total FROM retained_content")["total"])
    plan_id = "purge-" + secrets.token_hex(8)
    now = _now()
    database.execute(
        """
        INSERT INTO purge_plan(id, status, categories_json, effects_json, estimated_bytes, created_at)
        VALUES(?, 'preview', ?, ?, ?, ?)
        """,
        (plan_id, Database.json(selected), Database.json(effects), estimated, now),
    )
    return PurgePreview(plan_id, selected, estimated, effects)


def execute_purge_plan(database: Database, plan_id: str) -> dict[str, int]:
    plan = database.one("SELECT * FROM purge_plan WHERE id = ?", (plan_id,))
    if not plan or plan["status"] != "preview":
        raise ValueError("Purge plan is missing, expired, or already executed")
    categories = json.loads(plan["categories_json"])
    deleted: dict[str, int] = {}
    retained_digests: list[tuple[str, str]] = []
    now = _now()
    with database.transaction() as connection:
        if "raw_observations" in categories:
            deleted["raw_observations"] = connection.execute("DELETE FROM raw_observation").rowcount
        if "retained_content" in categories:
            retained_digests = [
                (row["category"], row["digest"])
                for row in connection.execute("SELECT category, digest FROM retained_content").fetchall()
            ]
            deleted["retained_content"] = connection.execute("DELETE FROM retained_content").rowcount
        if "read_alerts" in categories:
            deleted["read_alerts"] = connection.execute("DELETE FROM alert WHERE read_at IS NOT NULL").rowcount
        if "diagnostics" in categories:
            deleted["diagnostics"] = connection.execute("DELETE FROM diagnostic_event").rowcount
        if "drafts" in categories:
            deleted["drafts"] = connection.execute("DELETE FROM draft").rowcount
        if "decisions" in categories:
            deleted["decisions"] = connection.execute("DELETE FROM review_action").rowcount
        if "evidence_maps" in categories:
            deleted["evidence_links"] = connection.execute("DELETE FROM evidence_link").rowcount
            deleted["claims"] = connection.execute("DELETE FROM claim").rowcount
        if "usage_ledger" in categories:
            deleted["usage_ledger"] = connection.execute("DELETE FROM usage_ledger").rowcount
        connection.execute(
            "UPDATE purge_plan SET status = 'executed', confirmed_at = ?, executed_at = ? WHERE id = ?",
            (now, now, plan_id),
        )
    for category, digest in retained_digests:
        path = database.paths.content / category / digest[:2] / digest
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return deleted


def _purge_consequence(category: str) -> str:
    return {
        "raw_observations": "Removes replayable transport metadata while preserving qualified stories and evidence passages.",
        "retained_content": "Removes retained snapshots and translations; affected historical artifacts may no longer be reproducible.",
        "read_alerts": "Removes only notices already marked read.",
        "diagnostics": "Removes local operational history used for troubleshooting.",
        "drafts": "Permanently removes all editorial draft versions.",
        "decisions": "Permanently removes the human review audit trail.",
        "evidence_maps": "Permanently removes claims and their evidence relationships.",
        "usage_ledger": "Permanently removes local ChatGPT effort accounting.",
    }[category]
