"""Dashboard queries and guarded state transitions."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import __version__
from .storage import Database, database_size


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class DashboardService:
    def __init__(self, database: Database):
        self.database = database

    def overview(self) -> dict[str, Any]:
        counts = self.database.one(
            """
            SELECT
              SUM(CASE WHEN status = 'candidate' THEN 1 ELSE 0 END) AS candidates,
              SUM(CASE WHEN status = 'watch' THEN 1 ELSE 0 END) AS watches,
              SUM(CASE WHEN status = 'draft_ready' THEN 1 ELSE 0 END) AS draft_ready,
              SUM(CASE WHEN priority = 'Urgent' AND status != 'archived' THEN 1 ELSE 0 END) AS urgent
            FROM story_cluster
            """
        ) or {}
        sources = self.database.one(
            """
            SELECT COUNT(*) AS total,
              SUM(CASE WHEN health = 'healthy' THEN 1 ELSE 0 END) AS healthy,
              SUM(CASE WHEN health = 'degraded' THEN 1 ELSE 0 END) AS degraded,
              SUM(CASE WHEN health = 'paused' THEN 1 ELSE 0 END) AS paused
            FROM source_registry WHERE enabled = 1
            """
        ) or {}
        top_stories = self.database.query(
            """
            SELECT * FROM story_cluster
            WHERE status IN ('candidate', 'watch', 'draft_ready')
            ORDER BY CASE priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 WHEN 'High potential' THEN 2 ELSE 3 END,
                     priority_score DESC, first_public_at DESC
            LIMIT 5
            """
        )
        alerts = self.database.query(
            "SELECT * FROM alert ORDER BY created_at DESC LIMIT 5"
        )
        scans = self.database.query(
            "SELECT * FROM scan_run ORDER BY started_at DESC LIMIT 7"
        )
        queue = self.database.one(
            """
            SELECT COUNT(*) AS count, MIN(created_at) AS oldest_at
            FROM work_item WHERE status IN ('pending', 'queued')
            """
        ) or {"count": 0, "oldest_at": None}
        queue_count = int(queue["count"] or 0)
        queue_lag = "Clear"
        if queue_count and queue.get("oldest_at"):
            oldest = datetime.fromisoformat(str(queue["oldest_at"]).replace("Z", "+00:00"))
            lag_minutes = max(0, int((datetime.now(UTC) - oldest).total_seconds() // 60))
            queue_lag = "<1 min" if lag_minutes == 0 else f"{lag_minutes} min"
        return {
            "counts": {key: int(value or 0) for key, value in counts.items()},
            "sources": {key: int(value or 0) for key, value in sources.items()},
            "top_stories": top_stories,
            "alerts": alerts,
            "scans": scans,
            "queue_count": queue_count,
            "queue_lag": queue_lag,
            "schedule": self.schedule_status(),
        }

    def list_stories(
        self,
        status: str | None = None,
        lane: str | None = None,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        parameters: list[str] = []
        if status and status != "all":
            conditions.append("status = ?")
            parameters.append(status)
        if lane and lane != "all":
            conditions.append("lane = ?")
            parameters.append(lane)
        if kind == "candidate":
            conditions.append("status = 'candidate'")
        elif kind == "watch":
            conditions.append("status = 'watch'")
        elif kind == "catch_up":
            conditions.append("freshness = 'Catch-Up'")
        elif kind == "correction":
            conditions.append("EXISTS (SELECT 1 FROM alert a WHERE a.story_id = s.id AND a.kind = 'correction')")
        elif kind == "health":
            conditions.append("0 = 1")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return self.database.query(
            f"""
            SELECT s.*,
              (SELECT COUNT(*) FROM source_item i WHERE i.story_id = s.id) AS source_count,
              (SELECT COUNT(*) FROM claim c WHERE c.story_id = s.id) AS claim_count
            FROM story_cluster s
            {where}
            ORDER BY CASE priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 WHEN 'High potential' THEN 2 ELSE 3 END,
                     priority_score DESC, first_public_at DESC
            """,
            parameters,
        )

    def list_inbox_notices(self, kind: str | None = None) -> list[dict[str, Any]]:
        operational_kinds = ("health", "correction", "catch_up", "recovery")
        if kind in {"candidate", "watch"}:
            return []
        if kind and kind != "all":
            if kind not in operational_kinds:
                return []
            return self.database.query(
                "SELECT * FROM alert WHERE kind = ? ORDER BY created_at DESC",
                (kind,),
            )
        placeholders = ",".join("?" for _ in operational_kinds)
        return self.database.query(
            f"SELECT * FROM alert WHERE kind IN ({placeholders}) ORDER BY created_at DESC",
            operational_kinds,
        )

    def get_story(self, story_id: str) -> dict[str, Any] | None:
        story = self.database.one("SELECT * FROM story_cluster WHERE id = ?", (story_id,))
        if not story:
            return None
        story["sources"] = self.database.query(
            "SELECT * FROM source_item WHERE story_id = ? ORDER BY published_at, id",
            (story_id,),
        )
        story["claims"] = self.database.query(
            """
            SELECT c.*,
              GROUP_CONCAT(si.source_name, ' · ') AS evidence_sources,
              GROUP_CONCAT(el.relationship, ' · ') AS evidence_relationships
            FROM claim c
            LEFT JOIN evidence_link el ON el.claim_id = c.id
            LEFT JOIN source_item si ON si.id = el.source_item_id
            WHERE c.story_id = ?
            GROUP BY c.id
            ORDER BY c.id
            """,
            (story_id,),
        )
        story["actions"] = self.database.query(
            "SELECT * FROM review_action WHERE story_id = ? ORDER BY created_at DESC",
            (story_id,),
        )
        story["drafts"] = self.database.query(
            "SELECT * FROM draft WHERE story_id = ? ORDER BY version DESC",
            (story_id,),
        )
        return story

    def list_drafts(self) -> list[dict[str, Any]]:
        return self.database.query(
            """
            SELECT d.*, s.headline AS story_headline, s.lane, s.priority
            FROM draft d
            JOIN story_cluster s ON s.id = d.story_id
            ORDER BY d.updated_at DESC, d.version DESC
            """
        )

    def get_draft(self, draft_id: int) -> dict[str, Any] | None:
        draft = self.database.one(
            """
            SELECT d.*, s.headline AS story_headline, s.lane, s.priority,
                   s.freshness, s.status AS story_status
            FROM draft d JOIN story_cluster s ON s.id = d.story_id
            WHERE d.id = ?
            """,
            (draft_id,),
        )
        if draft:
            draft["sources"] = json.loads(draft.get("sources_json") or "[]")
            draft["history"] = self.database.query(
                "SELECT id, version, status, updated_at FROM draft WHERE story_id = ? ORDER BY version DESC",
                (draft["story_id"],),
            )
            draft["corrections"] = self.database.query(
                """
                SELECT severity, title, body, created_at
                FROM alert WHERE story_id = ? AND kind = 'correction'
                ORDER BY created_at DESC
                """,
                (draft["story_id"],),
            )
            comparison: dict[str, Any] | None
            if draft.get("supersedes_id"):
                comparison = self.database.one(
                    """
                    SELECT id, version, status, headline, metadata, body, lens, updated_at
                    FROM draft WHERE id = ?
                    """,
                    (draft["supersedes_id"],),
                )
                direction = "Previous"
            else:
                comparison = self.database.one(
                    """
                    SELECT id, version, status, headline, metadata, body, lens, updated_at
                    FROM draft WHERE supersedes_id = ? ORDER BY version LIMIT 1
                    """,
                    (draft["id"],),
                )
                direction = "Next"
            if comparison:
                comparison["direction"] = direction
                comparison["changes"] = {
                    field: comparison[field] != draft[field]
                    for field in ("headline", "metadata", "body", "lens")
                }
            draft["comparison"] = comparison
        return draft

    def review(self, story_id: str, action: str, reason: str = "") -> str:
        allowed = {"accept", "research", "archive", "approve_neutral", "approve_lens", "withdraw"}
        if action not in allowed:
            raise ValueError("Unsupported review action")
        story = self.database.one("SELECT * FROM story_cluster WHERE id = ?", (story_id,))
        if not story:
            raise LookupError("Story not found")
        now = utc_now()
        mode: str | None = None
        new_status = story["status"]
        work_kind: str | None = None
        work_payload: dict[str, Any] = {"reason": reason.strip(), "requested_at": now}
        if action == "archive":
            new_status = "archived"
        elif action == "research":
            work_kind = "research"
        elif action == "accept":
            new_status = "candidate"
        elif action == "withdraw":
            new_status = "withdrawn"
        elif action in {"approve_neutral", "approve_lens"}:
            if story["status"] not in {"candidate", "draft_ready", "approved"}:
                raise ValueError("Only verified candidates can be approved for drafting")
            if story["status"] == "approved":
                pending_draft = self.database.one(
                    """
                    SELECT id FROM work_item
                    WHERE story_id = ? AND kind = 'draft'
                      AND status IN ('pending', 'queued', 'generating')
                    LIMIT 1
                    """,
                    (story_id,),
                )
                if pending_draft:
                    raise ValueError("A draft request is already pending for this story")
            if action == "approve_lens":
                if story["opportunity_strength"] not in {"Strong", "Moderate"}:
                    raise ValueError("An open-source lens requires a Strong or Moderate opportunity")
                mode = "Open-Source Lens Brief"
            else:
                mode = "Neutral News Brief"
            new_status = "approved"
            work_kind = "draft"
            work_payload = {
                "schema_version": 1,
                "mode": mode,
                "reason": reason.strip(),
                "approval_snapshot_at": now,
                "story": {
                    "id": story["id"],
                    "updated_at": story["updated_at"],
                    "first_public_at": story["first_public_at"],
                    "detected_at": story["detected_at"],
                    "freshness": story["freshness"],
                    "material_update": bool(story["material_update"]),
                },
                "claims": self.database.query(
                    "SELECT id, status, volatility FROM claim WHERE story_id = ? ORDER BY id",
                    (story_id,),
                ),
                "sources": self.database.query(
                    """
                    SELECT id, source_role, url, published_at, verification_status
                    FROM source_item WHERE story_id = ? ORDER BY id
                    """,
                    (story_id,),
                ),
            }
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO review_action(story_id, action, reason, draft_mode, created_at) VALUES(?, ?, ?, ?, ?)",
                (story_id, action, reason.strip(), mode, now),
            )
            connection.execute(
                "UPDATE story_cluster SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, story_id),
            )
            if work_kind:
                connection.execute(
                    """
                    INSERT INTO work_item(kind, story_id, status, priority, payload_json, created_at, updated_at)
                    VALUES(?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (
                        work_kind,
                        story_id,
                        int(story["priority_score"]),
                        Database.json(work_payload),
                        now,
                        now,
                    ),
                )
        return new_status

    def save_draft(self, draft_id: int, headline: str, metadata: str, body: str, lens: str) -> int:
        original = self.get_draft(draft_id)
        if not original:
            raise LookupError("Draft not found")
        if original["status"] != "Current":
            raise ValueError("Only the current draft version can be revised")
        headline_value = headline.strip()
        metadata_value = metadata.strip()
        body_value = body.replace("\r\n", "\n").replace("\r", "\n").strip()
        lens_value = lens.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not headline_value or not body_value:
            raise ValueError("Headline and factual brief are required")
        if lens_value and original["mode"] != "Open-Source Lens Brief":
            raise ValueError("Open-source lens text requires an approved lens brief")
        if lens_value:
            story = self.database.one(
                "SELECT opportunity_strength FROM story_cluster WHERE id = ?",
                (original["story_id"],),
            )
            if not story or story["opportunity_strength"] not in {"Strong", "Moderate"}:
                raise ValueError("Open-source lens text requires a Strong or Moderate opportunity")
        now = utc_now()
        with self.database.transaction() as connection:
            current_version = connection.execute(
                "SELECT MAX(version) AS version FROM draft WHERE story_id = ?",
                (original["story_id"],),
            ).fetchone()["version"]
            connection.execute("UPDATE draft SET status = 'Superseded' WHERE id = ?", (draft_id,))
            cursor = connection.execute(
                """
                INSERT INTO draft(
                    story_id, mode, status, version, headline, metadata, body, lens,
                    sources_json, created_at, updated_at, supersedes_id
                ) VALUES(?, ?, 'Current', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    original["story_id"], original["mode"], int(current_version) + 1,
                    headline_value, metadata_value, body_value, lens_value,
                    original["sources_json"], now, now, draft_id,
                ),
            )
            connection.execute(
                "INSERT INTO review_action(story_id, action, reason, draft_mode, created_at) VALUES(?, 'manual_revision', ?, ?, ?)",
                (original["story_id"], f"Saved version {int(current_version) + 1}", original["mode"], now),
            )
            return int(cursor.lastrowid)

    def sources(self) -> dict[str, Any]:
        rows = self.database.query(
            "SELECT * FROM source_registry ORDER BY family, name"
        )
        enabled_rows = [row for row in rows if bool(row["enabled"])]
        health_counts = {
            health: sum(1 for row in enabled_rows if row["health"] == health)
            for health in ("healthy", "degraded", "paused")
        }
        families: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            families.setdefault(str(row["family"]), []).append(row)
        return {
            "rows": rows,
            "families": families,
            "enabled_count": len(enabled_rows),
            "disabled_count": len(rows) - len(enabled_rows),
            "health_counts": health_counts,
        }

    def toggle_source(self, source_id: str) -> bool:
        source = self.database.one("SELECT enabled FROM source_registry WHERE id = ?", (source_id,))
        if not source:
            raise LookupError("Source not found")
        enabled = not bool(source["enabled"])
        self.database.execute(
            "UPDATE source_registry SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, source_id),
        )
        return enabled

    def schedule_status(self) -> dict[str, Any]:
        recent = self.database.query("SELECT * FROM scan_run ORDER BY started_at DESC LIMIT 8")
        queued = self.database.query(
            "SELECT * FROM work_item WHERE status IN ('pending', 'queued') ORDER BY priority DESC, created_at"
        )
        usage = self.database.one(
            """
            SELECT
              COALESCE(SUM(CASE WHEN category = 'background' THEN effort_units ELSE 0 END), 0) AS background,
              COALESCE(SUM(CASE WHEN category = 'draft' THEN effort_units ELSE 0 END), 0) AS draft
            FROM usage_ledger
            WHERE created_at >= datetime('now', '-24 hours')
            """
        ) or {"background": 0, "draft": 0}
        today = datetime.now(UTC).date()
        return {
            "installed": self.database.get_state("schedule_installed", "false") == "true",
            "status": self.database.get_state("schedule_status", "not_installed"),
            "last_scan_at": self.database.get_state("last_scan_at", "Never"),
            "next_scan_at": self.database.get_state("next_scan_at", "Not scheduled"),
            "assistance_enabled": self.database.get_state("assistance_enabled", "false") == "true",
            "shadow_mode": self.database.get_state("shadow_mode", "true") == "true",
            "background_units": int(usage["background"] or 0),
            "draft_units": int(usage["draft"] or 0),
            "background_limit": int(self.database.get_state("background_unit_limit", "8")),
            "reserve_limit": int(self.database.get_state("urgent_reserve_limit", "2")),
            "today": today.isoformat(),
            "extended_start_suggestion": (today - timedelta(days=7)).isoformat(),
            "recent_scans": recent,
            "queue": queued,
        }

    def queue_extended_catchup(self, start_value: str, end_value: str) -> str:
        try:
            start = date.fromisoformat(start_value)
            end = date.fromisoformat(end_value)
        except (TypeError, ValueError) as error:
            raise ValueError("Choose valid start and end dates for extended catch-up") from error
        today = datetime.now(UTC).date()
        if start > end:
            raise ValueError("Extended catch-up start date must not be after the end date")
        if end > today:
            raise ValueError("Extended catch-up cannot include future dates")
        now = utc_now()
        payload = Database.json(
            {"trigger": "human", "start_date": start.isoformat(), "end_date": end.isoformat()}
        )
        existing = self.database.one(
            """
            SELECT id FROM work_item
            WHERE kind = 'catch_up' AND status IN ('pending', 'queued', 'running')
              AND payload_json = ?
            LIMIT 1
            """,
            (payload,),
        )
        if existing:
            return "already_queued"
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO work_item(kind, status, priority, payload_json, created_at, updated_at)
                VALUES('catch_up', 'queued', 90, ?, ?, ?)
                """,
                (payload, now, now),
            )
            connection.execute(
                """
                INSERT INTO scan_run(trigger_type, started_at, result, details)
                VALUES('extended-catch-up', ?, 'queued', ?)
                """,
                (now, f"Queued human-selected interval {start.isoformat()} through {end.isoformat()}."),
            )
        return "queued"

    def schedule_action(self, action: str) -> str:
        if action not in {"pause", "resume", "run_now"}:
            raise ValueError("Unsupported schedule action")
        now = utc_now()
        if action == "pause":
            self.database.set_state("schedule_status", "paused", now)
            return "paused"
        if action == "resume":
            if self.database.get_state("schedule_installed", "false") != "true":
                raise ValueError("The local schedule is not installed yet")
            self.database.set_state("schedule_status", "active", now)
            return "active"
        existing = self.database.one(
            """
            SELECT id FROM work_item
            WHERE kind = 'scout_scan' AND status IN ('pending', 'queued', 'running')
            LIMIT 1
            """
        )
        if existing:
            return "already_queued"
        self.database.execute(
            """
            INSERT INTO work_item(kind, status, priority, payload_json, created_at, updated_at)
            VALUES('scout_scan', 'queued', 100, ?, ?, ?)
            """,
            (Database.json({"trigger": "manual"}), now, now),
        )
        self.database.execute(
            """
            INSERT INTO scan_run(trigger_type, started_at, result, details)
            VALUES('manual', ?, 'queued', 'Queued from the local dashboard.')
            """,
            (now,),
        )
        return "queued"

    def settings(self) -> dict[str, Any]:
        tables = {
            "stories": "story_cluster",
            "registered sources": "source_registry",
            "source items": "source_item",
            "drafts": "draft",
            "actions": "review_action",
            "operations": "diagnostic_event",
        }
        counts: dict[str, int] = {}
        for label, table in tables.items():
            row = self.database.one(f"SELECT COUNT(*) AS count FROM {table}") or {"count": 0}
            counts[label] = int(row["count"])
        purge_row = self.database.one(
            """
            SELECT COUNT(*) AS count,
              COALESCE(SUM(LENGTH(level) + LENGTH(event_type) + LENGTH(message) + LENGTH(created_at)), 0) AS payload_bytes
            FROM diagnostic_event
            """
        ) or {"count": 0, "payload_bytes": 0}
        return {
            "app_version": __version__,
            "data_root": str(self.database.paths.root),
            "database_path": str(self.database.paths.database),
            "database_size": human_bytes(database_size(self.database.paths.database)),
            "cloud_warning": self.database.paths.cloud_sync_warning,
            "demo_mode": self.database.get_state("demo_mode", "false") == "true",
            "counts": counts,
            "purge_preview": {
                "operations_count": int(purge_row["count"]),
                "operations_payload": human_bytes(int(purge_row["payload_bytes"])),
            },
            "diagnostics": self.database.query(
                "SELECT * FROM diagnostic_event ORDER BY created_at DESC LIMIT 12"
            ),
        }

    def purge_operations(self, confirmation: str, category_selected: bool = False) -> int:
        if not category_selected:
            raise ValueError("Select the operational diagnostics category before purging")
        if confirmation != "PURGE OPERATIONS":
            raise ValueError("Confirmation phrase did not match")
        with self.database.transaction() as connection:
            count = int(connection.execute("SELECT COUNT(*) AS count FROM diagnostic_event").fetchone()["count"])
            connection.execute("DELETE FROM diagnostic_event")
            return count

    def mark_alerts_read(self) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE alert SET read_at = ? WHERE read_at IS NULL",
                (utc_now(),),
            )
            return int(cursor.rowcount)

    def evidence_bundle(self, story_id: str) -> dict[str, Any]:
        story = self.get_story(story_id)
        if not story:
            raise LookupError("Story not found")
        return {
            "schema_version": 1,
            "exported_at": utc_now(),
            "story": {key: value for key, value in story.items() if key not in {"sources", "claims", "actions", "drafts"}},
            "claims": story["claims"],
            "sources": story["sources"],
            "review_actions": story["actions"],
        }

    def draft_markdown(self, draft_id: int) -> str:
        draft = self.get_draft(draft_id)
        if not draft:
            raise LookupError("Draft not found")
        sections = [f"# {draft['headline']}", "", draft["metadata"], "", draft["body"]]
        if draft["lens"]:
            sections.extend(["", "## Open-Source Lens", "", draft["lens"]])
        sections.extend(["", "## Sources", ""])
        sections.extend(f"- {source}" for source in draft["sources"])
        return "\n".join(sections).strip() + "\n"
