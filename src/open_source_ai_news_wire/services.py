"""Dashboard queries and guarded state transitions."""

from __future__ import annotations

import json
import hashlib
import html
import ipaddress
import re
import base64
import bisect
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlsplit

from . import __version__
from .storage import Database, database_size
from .source_registry import set_source_enabled
from .scheduler import next_scheduled_run
from .ranking import percentile_points, rank_story, ranking_sort_key
from .revisions import approval_signature_sets
from .evidence import (
    ALLOWED_PROVENANCE_TYPES,
    ALLOWED_RELATIONSHIPS,
    ALLOWED_ROLES,
    normalize_optional_public_https_url,
    normalize_publisher_name,
    normalize_public_https_url,
    publisher_display_name,
    publisher_identity_key,
    publisher_key,
    qualification_state,
    recalculate_claim_statuses,
    recalculate_story_qualification,
)


INLINE_SOURCE_LINK_RE = re.compile(r"\[([^\]\n]{1,200})\]\(<(https://[^>\n]{1,2048})>\)")
ANY_MARKDOWN_LINK_RE = re.compile(r"\[[^\]\n]+\]\([^\)\n]+\)")
RAW_HTML_RE = re.compile(r"</?[A-Za-z][^>]*>")
MANUAL_CONFIRMATION_VERSION = "manual_override_v1"


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
    def __init__(
        self,
        database: Database,
        *,
        scheduler: Any | None = None,
        run_callback: Callable[[], None] | None = None,
        draft_callback: Callable[[int], None] | None = None,
        research_callback: Callable[[int], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.database = database
        self.scheduler = scheduler
        self.run_callback = run_callback
        self.draft_callback = draft_callback
        self.research_callback = research_callback
        self.clock = clock or (lambda: datetime.now(UTC))

    def _current_time(self) -> datetime:
        value = self.clock()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def _momentum_context(self) -> dict[str, dict[str, int]]:
        cutoff = (self._current_time() - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
        rows = self.database.query(
            """
            SELECT * FROM momentum_snapshot
            WHERE captured_at >= ?
            ORDER BY source_id, story_id, captured_at, id
            """,
            (cutoff,),
        )
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault((str(row["source_id"]), str(row["story_id"])), []).append(row)
        values: dict[str, list[float]] = {}
        velocities: dict[tuple[str, str], float] = {}
        for key, snapshots in grouped.items():
            first, latest = snapshots[0], snapshots[-1]
            velocity = max(
                0.0,
                float(latest.get("native_score") or 0) - float(first.get("native_score") or 0),
            )
            first_rank = int(first.get("daily_rank") or 0)
            latest_rank = int(latest.get("daily_rank") or 0)
            if first_rank and latest_rank and latest_rank < first_rank:
                velocity += float(first_rank - latest_rank) * 2
            velocities[key] = velocity
            values.setdefault(key[0], []).append(velocity)
        for items in values.values():
            items.sort()
        context: dict[str, dict[str, int]] = {}
        for key, snapshots in grouped.items():
            source_id, story_id = key
            row = snapshots[-1]
            velocity = velocities[key]
            population = values[source_id]
            percentile = (
                bisect.bisect_right(population, velocity) / max(1, len(population))
                if velocity > 0
                else 0.0
            )
            story = context.setdefault(
                story_id,
                {
                    "momentum_velocity_points": 0,
                    "momentum_account_count": 0,
                    "momentum_post_count": 0,
                    "momentum_daily_rank": 0,
                },
            )
            story["momentum_velocity_points"] = max(
                story["momentum_velocity_points"], percentile_points(percentile)
            )
            story["momentum_account_count"] = max(
                story["momentum_account_count"], int(row.get("account_count") or 0)
            )
            story["momentum_post_count"] = max(
                story["momentum_post_count"], int(row.get("post_count") or 0)
            )
            rank = int(row.get("daily_rank") or 0)
            if rank and (not story["momentum_daily_rank"] or rank < story["momentum_daily_rank"]):
                story["momentum_daily_rank"] = rank
        return context

    def _story_rows(self) -> list[dict[str, Any]]:
        rows = self.database.query(
            """
            SELECT s.*,
              (SELECT COUNT(*) FROM source_item i WHERE i.story_id = s.id) AS source_count,
              (
                SELECT COUNT(*) FROM source_item i
                WHERE i.story_id = s.id AND i.source_role != 'Discovery'
              ) AS direct_source_count,
              (SELECT COUNT(*) FROM claim c WHERE c.story_id = s.id) AS claim_count,
              (
                SELECT COUNT(DISTINCT identity) FROM (
                  SELECT LOWER(source_name) AS identity FROM source_item
                    WHERE story_id = s.id AND source_role = 'Discovery'
                  UNION
                  SELECT identity_key AS identity FROM discovery_lead WHERE story_id = s.id
                )
              ) AS discovery_identity_count,
              (
                SELECT COUNT(*) FROM source_item i
                WHERE i.story_id = s.id AND i.source_role = 'Event'
              ) + (
                SELECT COUNT(*) FROM evidence_source e
                WHERE e.story_id = s.id AND e.status = 'confirmed' AND e.confirmed_role = 'Event'
              ) AS confirmed_event_count,
              (
                SELECT COUNT(DISTINCT reporting_origin_key) FROM evidence_source e
                WHERE e.story_id = s.id AND e.status = 'confirmed'
                  AND e.confirmed_role = 'Reporting' AND e.origin_status = 'confirmed'
                  AND e.reporting_origin_key IS NOT NULL
              ) AS reporting_origin_count,
              CASE WHEN NOT EXISTS (
                SELECT 1 FROM source_item i WHERE i.story_id = s.id
              ) OR EXISTS (
                SELECT 1 FROM source_item i
                LEFT JOIN source_registry r
                  ON r.id = i.source_registry_id
                  OR (i.source_registry_id IS NULL AND r.name = i.source_name)
                WHERE i.story_id = s.id
                  AND i.timestamp_status NOT LIKE 'unknown_original%'
                  AND NOT (
                    i.source_role = 'Discovery'
                    AND COALESCE(r.family, '') = 'Aggregation'
                  )
              ) OR EXISTS (
                SELECT 1 FROM evidence_source e
                WHERE e.story_id = s.id
                  AND e.status IN ('fetched', 'confirmed')
                  AND e.acquisition_method = 'discovery_enrichment'
                  AND e.published_at IS NOT NULL
              ) THEN 1 ELSE 0 END AS original_publication_known
            FROM story_cluster s
            """
        )
        momentum = self._momentum_context()
        identities: dict[str, set[str]] = {}
        for item in self.database.query(
            "SELECT story_id, COALESCE(canonical_url, url) AS url FROM source_item"
        ):
            key = publisher_key(str(item.get("url") or ""))
            if key:
                identities.setdefault(str(item["story_id"]), set()).add(
                    f"publisher:{key}"
                )
        for lead in self.database.query(
            "SELECT story_id, identity_key FROM discovery_lead WHERE TRIM(identity_key) != ''"
        ):
            key = re.sub(r"\s+", "", str(lead["identity_key"]).casefold())
            if key:
                identities.setdefault(str(lead["story_id"]), set()).add(
                    f"account:{key}"
                )
        for evidence in self.database.query(
            """
            SELECT story_id, confirmed_role, publisher_key, reporting_origin_key
            FROM evidence_source WHERE status = 'confirmed'
              AND confirmed_role IN ('Event', 'Reporting')
            """
        ):
            key = (
                str(evidence.get("reporting_origin_key") or "")
                if evidence["confirmed_role"] == "Reporting"
                else str(evidence.get("publisher_key") or "")
            ).casefold().strip()
            if key:
                identities.setdefault(str(evidence["story_id"]), set()).add(
                    f"publisher:{key}"
                )
        current = self._current_time()
        return [
            rank_story(
                {
                    **row,
                    **momentum.get(str(row["id"]), {}),
                    "source_identity_count": len(identities.get(str(row["id"]), set())),
                },
                now=current,
            )
            for row in rows
        ]

    @staticmethod
    def _encode_cursor(story: dict[str, Any], sort: str) -> str:
        payload = json.dumps({"v": 1, "sort": sort, "id": story["id"]}, separators=(",", ":"))
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(value: str, sort: str) -> str | None:
        if not value:
            return None
        try:
            padded = value + "=" * (-len(value) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if payload.get("v") != 1 or payload.get("sort") != sort:
            return None
        return str(payload.get("id") or "") or None

    def overview(
        self,
        *,
        story_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        counts = self.database.one(
            """
            SELECT
              SUM(CASE WHEN status NOT IN ('archived', 'withdrawn', 'content_ready', 'draft_ready')
                        AND research_status != 'researching' THEN 1 ELSE 0 END) AS ready,
              SUM(CASE WHEN status NOT IN ('archived', 'withdrawn')
                        AND research_status = 'researching' THEN 1 ELSE 0 END) AS researching,
              SUM(CASE WHEN status IN ('content_ready', 'draft_ready') THEN 1 ELSE 0 END) AS content_ready,
              0 AS urgent
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
        current_rows = [
            story for story in (story_rows if story_rows is not None else self._story_rows())
            if story["is_review_current"]
            and story["status"] not in {"archived", "withdrawn"}
        ]
        current_rows.sort(key=ranking_sort_key)
        top_stories = current_rows[:5]
        counts["urgent"] = sum(story["priority"] == "Urgent" for story in current_rows)
        counts["review_now"] = len(current_rows)
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
        return self.list_story_page(
            status=status,
            lane=lane,
            kind=kind,
            window="all",
            page_size=None,
        )["stories"]

    def list_story_page(
        self,
        *,
        status: str | None = None,
        lane: str | None = None,
        kind: str | None = None,
        window: str = "review_now",
        sort: str = "priority",
        cursor: str = "",
        page_size: int | None = 25,
    ) -> dict[str, Any]:
        if window not in {"review_now", "older", "all"}:
            window = "review_now"
        if sort not in {"priority", "newest"}:
            sort = "priority"
        rows = self._story_rows()
        correction_ids = {
            str(row["story_id"])
            for row in self.database.query(
                "SELECT DISTINCT story_id FROM alert WHERE kind = 'correction' AND story_id IS NOT NULL"
            )
        }

        def included(story: dict[str, Any]) -> bool:
            if status and status != "all" and story["status"] != status:
                return False
            if lane and lane != "all" and story["lane"] != lane:
                return False
            if kind == "ready" and story["status"] in {"archived", "withdrawn", "content_ready", "draft_ready"}:
                return False
            if kind == "researching" and story.get("research_status") != "researching":
                return False
            if kind == "content_ready" and story["status"] not in {"content_ready", "draft_ready"}:
                return False
            # Kept only for historical deep links; the v0.4.0 dashboard no
            # longer presents Candidate or Watch filters.
            if kind == "candidate" and story["status"] != "candidate":
                return False
            if kind == "watch" and story["status"] != "watch":
                return False
            if kind == "catch_up" and story.get("ingestion_context") not in {"recovery", "extended"}:
                return False
            if kind == "correction" and story["id"] not in correction_ids:
                return False
            if kind == "health":
                return False
            if window == "review_now":
                return bool(
                    story["is_review_current"]
                    and story["status"] not in {"archived", "withdrawn"}
                )
            if window == "older":
                return bool(
                    story["freshness"] == "Older"
                    and story["status"] not in {"archived", "withdrawn"}
                )
            return True

        selected = [story for story in rows if included(story)]
        selected.sort(key=lambda story: ranking_sort_key(story, newest=sort == "newest"))
        total = len(selected)
        cursor_id = self._decode_cursor(cursor, sort)
        start = 0
        if cursor_id:
            for index, story in enumerate(selected):
                if story["id"] == cursor_id:
                    start = index + 1
                    break
        limit = len(selected) if page_size is None else max(1, min(100, page_size))
        page = selected[start:start + limit]
        next_cursor = ""
        if start + len(page) < total and page:
            next_cursor = self._encode_cursor(page[-1], sort)
        return {"stories": page, "next_cursor": next_cursor, "total": total}

    def list_inbox_notices(self, kind: str | None = None) -> list[dict[str, Any]]:
        operational_kinds = ("health", "correction", "catch_up", "recovery")
        if kind in {"ready", "researching", "content_ready"}:
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

    def get_story(
        self,
        story_id: str,
        *,
        ranked_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
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
        for action in story["actions"]:
            try:
                snapshot = json.loads(str(action.get("approval_snapshot_json") or "{}"))
            except json.JSONDecodeError:
                snapshot = {}
            action["approval_basis"] = str(snapshot.get("approval_basis") or "")
            action["qualification_snapshot"] = snapshot.get("qualification_snapshot") or {}
        story["drafts"] = self.database.query(
            "SELECT * FROM draft WHERE story_id = ? ORDER BY version DESC",
            (story_id,),
        )
        story["research_attempts"] = self.database.query(
            """
            SELECT * FROM research_attempt
            WHERE story_id = ? ORDER BY created_at DESC, id DESC
            """,
            (story_id,),
        )
        story["evidence_sources"] = self.database.query(
            "SELECT * FROM evidence_source WHERE story_id = ? ORDER BY created_at, id",
            (story_id,),
        )
        for evidence in story["evidence_sources"]:
            evidence["claim_links"] = self.database.query(
                """
                SELECT esc.claim_id, esc.relationship, c.text
                FROM evidence_source_claim esc JOIN claim c ON c.id = esc.claim_id
                WHERE esc.evidence_source_id = ? ORDER BY esc.claim_id
                """,
                (evidence["id"],),
            )
            evidence["claim_relationships"] = {
                int(link["claim_id"]): str(link["relationship"])
                for link in evidence["claim_links"]
            }
            for link in evidence["claim_links"]:
                link["relationship_label"] = {
                    "attributes": "reports or attributes",
                    "supports": "directly supports",
                    "contradicts": "contradicts",
                    "context": "context",
                }.get(str(link["relationship"]), str(link["relationship"]))
            stored_hosting_name = str(evidence.get("hosting_publisher_name") or "")
            evidence["hosting_publisher_name"] = (
                publisher_display_name(str(evidence.get("final_url") or evidence["canonical_url"]))
                if not stored_hosting_name or stored_hosting_name == str(evidence["publisher_key"])
                else stored_hosting_name
            )
            evidence["origin_review_required"] = bool(
                evidence.get("confirmed_role") == "Reporting"
                and evidence.get("origin_status") != "confirmed"
            )
        manual_links = self.database.query(
            """
            SELECT esc.claim_id, esc.relationship, es.title, es.final_url,
                   es.confirmed_role, es.reporting_origin_name, es.origin_status
            FROM evidence_source_claim esc JOIN evidence_source es ON es.id = esc.evidence_source_id
            WHERE es.story_id = ? AND es.status = 'confirmed'
            ORDER BY es.id
            """,
            (story_id,),
        )
        claims_by_id = {int(claim["id"]): claim for claim in story["claims"]}
        for link in manual_links:
            claim = claims_by_id.get(int(link["claim_id"]))
            if not claim:
                continue
            source_name = str(
                link["reporting_origin_name"]
                if link["confirmed_role"] == "Reporting"
                and link["origin_status"] == "confirmed"
                and link["reporting_origin_name"]
                else link["title"] or link["final_url"] or "Confirmed evidence"
            )
            claim["evidence_sources"] = " · ".join(
                part for part in (claim.get("evidence_sources"), source_name) if part
            )
            claim["evidence_relationships"] = " · ".join(
                part
                for part in (
                    claim.get("evidence_relationships"),
                    {
                        "attributes": "reports or attributes",
                        "supports": "directly supports",
                        "contradicts": "contradicts",
                        "context": "context",
                    }.get(str(link["relationship"]), str(link["relationship"])),
                )
                if part
            )
        story["candidate"] = self.database.one(
            "SELECT * FROM candidate WHERE story_id = ?", (story_id,)
        ) or {}
        story["qualification"] = qualification_state(self.database, story_id)
        story["qualification"]["lock_reasons"] = self._qualification_lock_reasons(story)
        story["candidate_basis"] = story["qualification"]["candidate_basis"]
        story["manual_override_active"] = story["qualification"]["manual_override_active"]
        story["draft_eligible"] = story["qualification"]["draft_eligible"]
        latest_draft = story["drafts"][0] if story["drafts"] else None
        story["draft_request_allowed"] = bool(
            not latest_draft
            or (
                latest_draft["status"] == "Needs Review"
                and story["status"] == "candidate"
            )
        )
        manual_sources = self._draft_sources(story_id, allow_discovery=True)
        story["manual_override_allowed"] = bool(
            story["status"] not in {"archived", "withdrawn"}
            and story["claims"]
            and any(source.get("citation_allowed") for source in manual_sources)
            and story["draft_request_allowed"]
        )
        if story["manual_override_allowed"]:
            story["manual_override_lock_reason"] = ""
        elif not story["draft_request_allowed"]:
            story["manual_override_lock_reason"] = (
                "A current draft already exists. Revise it from Drafts & history."
            )
        else:
            story["manual_override_lock_reason"] = (
                "Manual approval requires an active story with at least one claim and one safe public source."
            )
        story["draft_request"] = self.draft_status(story_id)
        story["approval_basis"] = (
            story["draft_request"]["approval_basis"]
            if story["draft_request"]
            else "manual_override"
            if story["manual_override_active"]
            else "verified"
            if story["qualification"]["verified_qualified"]
            else ""
        )
        ranked = (
            ranked_summary
            if ranked_summary is not None and ranked_summary.get("id") == story_id
            else next(
                (item for item in self._story_rows() if item["id"] == story_id),
                None,
            )
        )
        if ranked:
            for key in (
                "historical_priority", "historical_priority_score", "freshness",
                "age_hours", "ranking_anchor_at", "ranking_age_hours",
                "review_score", "priority", "importance_score", "impact_level",
                "evidence_points", "evidence_state", "momentum_score",
                "attention_level", "source_identity_count", "source_count",
                "direct_source_count", "confirmed_origin_count", "age_label",
                "ranking_age_label",
                "discovery_identity_count", "reporting_origin_count",
                "confirmed_event_count", "is_review_current",
                "is_material_update_current", "momentum_account_count",
                "momentum_post_count", "momentum_daily_rank",
                "momentum_velocity_points", "original_publication_known",
                "is_newly_surfaced",
                "organic_score", "priority_floor_applied",
                "score_floor_explanation",
            ):
                story[key] = ranked.get(key)
        story["discovery_leads"] = self.database.query(
            """
            SELECT * FROM discovery_lead WHERE story_id = ?
            ORDER BY published_at, id
            """,
            (story_id,),
        )
        story["content_allowed"] = story["status"] not in {"archived", "withdrawn"}
        story["trust_label"] = (
            "Trusted source"
            if story.get("source_trust_state") == "trusted"
            else "Research required"
        )
        return story

    @staticmethod
    def _qualification_lock_reasons(story: dict[str, Any]) -> list[str]:
        state = story["qualification"]
        reasons: list[str] = []
        if not state["evidence_gate"]:
            if state["reporting_count"]:
                reasons.append(
                    f"Confirm {state['reporting_needed']} more independent original reporting publisher."
                    if state["reporting_needed"] == 1
                    else f"Confirm {state['reporting_needed']} more independent original reporting publishers."
                )
            else:
                reasons.append("Confirm one Event source or two independent original reporting publishers.")
        if not state["effective_importance"]:
            reasons.append("Automated importance did not pass; a recorded human reason is required.")
        if story["status"] not in {"candidate", "draft_ready", "approved"}:
            reasons.append("Qualify this story as a candidate before approving a draft.")
        if story.get("opportunity_strength") not in {"Strong", "Moderate"}:
            reasons.append("The open-source lens requires a Strong or Moderate opportunity.")
        return reasons

    def _draft_sources(
        self, story_id: str, *, allow_discovery: bool = False
    ) -> list[dict[str, Any]]:
        from .assistance import _story_sources

        return _story_sources(
            self.database, story_id, allow_discovery=allow_discovery
        )

    def _editorial_signature(self, story_id: str) -> str:
        claims = self.database.query(
            "SELECT id, text, status, volatility FROM claim WHERE story_id = ? ORDER BY id",
            (story_id,),
        )
        sources = self._draft_sources(story_id, allow_discovery=True)
        claim_signatures, source_signatures = approval_signature_sets(claims, sources)
        return hashlib.sha256(
            Database.json(
                {"claims": claim_signatures, "sources": source_signatures}
            ).encode("utf-8")
        ).hexdigest()

    def _record_nonmaterial_editorial_change(
        self, story_id: str, before_signature: str, *, now: str
    ) -> None:
        if self._editorial_signature(story_id) == before_signature:
            return
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE story_cluster SET story_revision = story_revision + 1, updated_at = ? WHERE id = ?",
                (now, story_id),
            )
            connection.execute(
                """
                UPDATE work_item
                SET status = 'needs_reapproval', last_error_class = 'story_revision_changed',
                    updated_at = ?
                WHERE story_id = ? AND kind = 'draft'
                  AND status IN ('pending', 'queued', 'running', 'generating', 'waiting')
                """,
                (now, story_id),
            )
            connection.execute(
                "UPDATE draft SET status = 'Needs Review', updated_at = ? WHERE story_id = ? AND status = 'Current'",
                (now, story_id),
            )

    def queue_evidence_inspection(
        self, story_id: str, url: str, acquisition_method: str
    ) -> tuple[int, str]:
        story = self.database.one("SELECT id FROM story_cluster WHERE id = ?", (story_id,))
        if not story:
            raise LookupError("Story not found")
        if acquisition_method not in {"linked", "manual"}:
            raise ValueError("Unsupported evidence acquisition method")
        normalized = normalize_public_https_url(url)
        if acquisition_method == "linked":
            linked = self.database.one(
                """
                SELECT id, source_role FROM source_item
                WHERE story_id = ? AND source_role IN ('Discovery', 'Reporting')
                  AND COALESCE(canonical_url, url) = ?
                """,
                (story_id, normalized),
            )
            if not linked:
                lead = self.database.one(
                    "SELECT id FROM discovery_lead WHERE story_id = ? AND url = ?",
                    (story_id, normalized),
                )
                if lead:
                    linked = {"id": lead["id"], "source_role": "Discovery"}
            if not linked:
                raise ValueError("Linked inspection must use a stored Discovery or Reporting URL")
            proposed_role = "Reporting" if linked["source_role"] == "Reporting" else "Event"
        else:
            proposed_role = "Reporting"
        now = utc_now()
        key = "evidence:" + story_id + ":" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM evidence_source WHERE story_id = ? AND canonical_url = ?",
                (story_id, normalized),
            ).fetchone()
            if existing and existing["status"] == "confirmed":
                return int(existing["id"]), "confirmed"
            if existing:
                evidence_id = int(existing["id"])
                connection.execute(
                    """
                    UPDATE evidence_source SET requested_url = ?, acquisition_method = ?, proposed_role = ?,
                        status = 'queued', error_class = NULL, excluded_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (normalized, acquisition_method, proposed_role, now, evidence_id),
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO evidence_source(
                        story_id, requested_url, canonical_url, publisher_key,
                        acquisition_method, proposed_role, status, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        story_id, normalized, normalized, publisher_key(normalized),
                        acquisition_method, proposed_role, now, now,
                    ),
                )
                evidence_id = int(cursor.lastrowid)
            payload = Database.json({"schema_version": 1, "evidence_source_id": evidence_id})
            connection.execute(
                """
                INSERT INTO work_item(
                    kind, story_id, status, priority, payload_json, created_at,
                    updated_at, idempotency_key, available_at
                ) VALUES('evidence_enrichment', ?, 'queued', 95, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) WHERE idempotency_key IS NOT NULL DO UPDATE SET
                    status = 'queued', payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at, available_at = excluded.available_at,
                    last_error_class = NULL
                """,
                (story_id, payload, now, now, key, now),
            )
            connection.execute(
                """
                INSERT INTO review_action(story_id, action, reason, created_at, approval_snapshot_json)
                VALUES(?, 'inspect_evidence', ?, ?, ?)
                """,
                (story_id, normalized, now, Database.json({"evidence_source_id": evidence_id, "method": acquisition_method})),
            )
        if self.run_callback:
            self.run_callback()
        return evidence_id, "queued"

    def confirm_evidence(
        self,
        story_id: str,
        evidence_id: int,
        role: str,
        relationships: dict[int, str],
        *,
        first_party: bool = False,
        reason: str = "",
        provenance_type: str = "unknown",
        origin_name: str = "",
        origin_url: str = "",
    ) -> dict[str, Any]:
        if role not in ALLOWED_ROLES:
            raise ValueError("Choose Event, Reporting, or Discovery")
        evidence = self.database.one(
            "SELECT * FROM evidence_source WHERE id = ? AND story_id = ?",
            (evidence_id, story_id),
        )
        if not evidence:
            raise LookupError("Evidence source not found")
        if evidence["status"] not in {"fetched", "confirmed"}:
            raise ValueError("Evidence must be fetched successfully before confirmation")
        if role == "Event" and not first_party:
            raise ValueError("Event evidence requires first-party confirmation")
        if role in {"Event", "Reporting"} and not relationships:
            raise ValueError("Map at least one claim before confirming qualifying evidence")
        if any(value not in ALLOWED_RELATIONSHIPS for value in relationships.values()):
            raise ValueError("Unsupported claim relationship")
        reporting_origin_name = ""
        reporting_origin_key: str | None = None
        reporting_origin_url: str | None = None
        origin_status = "not_applicable"
        effective_provenance = "original" if role == "Event" else "unknown"
        stored_hosting_name = str(evidence.get("hosting_publisher_name") or "")
        hosting_publisher_name = (
            publisher_display_name(str(evidence.get("final_url") or evidence["canonical_url"]))
            if not stored_hosting_name or stored_hosting_name == str(evidence["publisher_key"])
            else stored_hosting_name
        )
        if role == "Reporting":
            if provenance_type not in ALLOWED_PROVENANCE_TYPES - {"unknown"}:
                raise ValueError("Choose whether this reporting is original, syndicated, or citing another publisher")
            reporting_origin_name = normalize_publisher_name(origin_name)
            reporting_origin_key = publisher_identity_key(reporting_origin_name)
            reporting_origin_url = normalize_optional_public_https_url(origin_url)
            if provenance_type == "original":
                if reporting_origin_url and publisher_key(reporting_origin_url) != str(evidence["publisher_key"]):
                    raise ValueError("An original-reporting URL must use the hosting publisher's domain")
                reporting_origin_url = reporting_origin_url or str(
                    evidence.get("final_url") or evidence["canonical_url"]
                )
            origin_status = "confirmed"
            effective_provenance = provenance_type
        claim_ids = set(relationships)
        if claim_ids:
            placeholders = ",".join("?" for _ in claim_ids)
            rows = self.database.query(
                f"SELECT id FROM claim WHERE story_id = ? AND id IN ({placeholders})",
                (story_id, *sorted(claim_ids)),
            )
            if {int(row["id"]) for row in rows} != claim_ids:
                raise ValueError("Claim mapping crossed the story boundary")
        before_editorial_signature = self._editorial_signature(story_id)
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM evidence_source_claim WHERE evidence_source_id = ?",
                (evidence_id,),
            )
            connection.executemany(
                """
                INSERT INTO evidence_source_claim(evidence_source_id, claim_id, relationship)
                VALUES(?, ?, ?)
                """,
                [(evidence_id, claim_id, relationship) for claim_id, relationship in relationships.items()],
            )
            action = connection.execute(
                """
                INSERT INTO review_action(story_id, action, reason, created_at, approval_snapshot_json)
                VALUES(?, 'confirm_evidence', ?, ?, ?)
                """,
                (
                    story_id, reason.strip(), now,
                    Database.json({
                        "evidence_source_id": evidence_id, "role": role,
                        "first_party": bool(first_party), "relationships": relationships,
                        "provenance_type": effective_provenance,
                        "reporting_origin_name": reporting_origin_name,
                        "reporting_origin_url": reporting_origin_url,
                    }),
                ),
            )
            connection.execute(
                """
                UPDATE evidence_source SET confirmed_role = ?, first_party_confirmed = ?,
                    confirmation_reason = ?, status = 'confirmed', confirmed_at = ?,
                    hosting_publisher_name = ?,
                    reporting_origin_name = ?, reporting_origin_key = ?, reporting_origin_url = ?,
                    provenance_type = ?, origin_status = ?, origin_confirmed_at = ?,
                    origin_confirmation_action_id = ?, excluded_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    role,
                    int(first_party),
                    reason.strip(),
                    now,
                    hosting_publisher_name,
                    reporting_origin_name,
                    reporting_origin_key,
                    reporting_origin_url,
                    effective_provenance,
                    origin_status,
                    now if role == "Reporting" else None,
                    int(action.lastrowid) if role == "Reporting" else None,
                    now,
                    evidence_id,
                ),
            )
        recalculate_claim_statuses(self.database, story_id)
        result = recalculate_story_qualification(self.database, story_id)
        self._record_nonmaterial_editorial_change(
            story_id, before_editorial_signature, now=now
        )
        return result

    def exclude_evidence(self, story_id: str, evidence_id: int, reason: str = "") -> dict[str, Any]:
        evidence = self.database.one(
            "SELECT id FROM evidence_source WHERE id = ? AND story_id = ?",
            (evidence_id, story_id),
        )
        if not evidence:
            raise LookupError("Evidence source not found")
        before_editorial_signature = self._editorial_signature(story_id)
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE evidence_source SET status = 'excluded', excluded_at = ?, updated_at = ? WHERE id = ?",
                (now, now, evidence_id),
            )
            connection.execute(
                """
                INSERT INTO review_action(story_id, action, reason, created_at, approval_snapshot_json)
                VALUES(?, 'exclude_evidence', ?, ?, ?)
                """,
                (story_id, reason.strip(), now, Database.json({"evidence_source_id": evidence_id})),
            )
        recalculate_claim_statuses(self.database, story_id)
        result = recalculate_story_qualification(self.database, story_id)
        self._record_nonmaterial_editorial_change(
            story_id, before_editorial_signature, now=now
        )
        return result

    def qualify_story(self, story_id: str, reason: str = "") -> str:
        state = recalculate_story_qualification(self.database, story_id)
        if not state["evidence_gate"]:
            raise ValueError("Evidence must pass before manual qualification")
        now = utc_now()
        reason_value = reason.strip()
        if not state["automated_importance"] and not state["importance_override"] and not reason_value:
            raise ValueError("A recorded reason is required to override automated importance")
        with self.database.transaction() as connection:
            action = "qualify" if state["automated_importance"] or state["importance_override"] else "override_importance"
            cursor = connection.execute(
                """
                INSERT INTO review_action(story_id, action, reason, created_at, approval_snapshot_json)
                VALUES(?, ?, ?, ?, ?)
                """,
                (story_id, action, reason_value, now, Database.json(state)),
            )
            if not state["automated_importance"] and not state["importance_override"]:
                connection.execute(
                    """
                    UPDATE candidate SET importance_override = 1, importance_override_reason = ?,
                        importance_overridden_at = ?, importance_override_action_id = ?,
                        qualified_at = COALESCE(qualified_at, ?) WHERE story_id = ?
                    """,
                    (reason_value, now, int(cursor.lastrowid), now, story_id),
                )
            else:
                connection.execute(
                    "UPDATE candidate SET qualified_at = COALESCE(qualified_at, ?) WHERE story_id = ?",
                    (now, story_id),
                )
            connection.execute(
                "UPDATE story_cluster SET status = 'candidate', updated_at = ? WHERE id = ?",
                (now, story_id),
            )
        return "candidate"

    def list_drafts(self) -> list[dict[str, Any]]:
        drafts = self.database.query(
            """
            SELECT d.*, s.headline AS story_headline, s.lane, s.priority
            FROM draft d
            JOIN story_cluster s ON s.id = d.story_id
            ORDER BY d.updated_at DESC, d.version DESC
            """
        )
        for draft in drafts:
            draft["entry_kind"] = "draft"
            draft["display_status"] = str(draft["status"])
            try:
                snapshot = json.loads(str(draft.get("approval_snapshot_json") or "{}"))
            except json.JSONDecodeError:
                snapshot = {}
            draft["approval_basis"] = str(snapshot.get("approval_basis") or "verified")

        requests = self.database.query(
            """
            SELECT w.*, s.headline AS story_headline, s.lane, s.priority
            FROM work_item w
            JOIN story_cluster s ON s.id = w.story_id
            WHERE w.kind IN ('content', 'draft') AND w.status != 'completed'
            ORDER BY w.updated_at DESC, w.id DESC
            """
        )
        for work in requests:
            try:
                payload = json.loads(str(work.get("payload_json") or "{}"))
            except json.JSONDecodeError:
                payload = {}
            status = self._draft_status_view(work)
            work.update(
                {
                    "entry_kind": "request",
                    "mode": str(payload.get("mode") or "Draft request"),
                    "display_status": status["label"],
                    "status_code": status["status"],
                    "status_message": status["message"],
                    "approval_basis": (
                        "ungated"
                        if work.get("kind") == "content"
                        else str(payload.get("approval_basis") or "verified")
                    ),
                }
            )
        return sorted(
            [*drafts, *requests],
            key=lambda row: (str(row.get("updated_at") or ""), int(row.get("id") or 0)),
            reverse=True,
        )

    @staticmethod
    def _draft_status_view(work: dict[str, Any]) -> dict[str, Any]:
        raw_status = str(work.get("status") or "queued")
        attempts = int(work.get("attempt_count") or 0)
        error_code = str(work.get("last_error_class") or "")
        status = raw_status
        if raw_status in {"pending", "queued"}:
            status = "retrying" if attempts else "starting"
        elif raw_status in {"running", "generating"}:
            status = "generating"
        elif raw_status == "deferred":
            status = "failed"
        elif raw_status == "completed":
            status = "draft_ready"
        elif raw_status == "cancelled" and error_code == "manual_draft_completed":
            status = "draft_ready"

        if error_code in {
            "codex_authentication_unavailable",
            "codex_authentication_invalid",
            "waiting_for_login",
        }:
            status = "waiting_for_login"
        elif error_code in {
            "assistance_disabled",
            "AssistanceDisabled",
            "isolation_not_passed",
            "IsolationUnavailable",
            "isolation_attestation_stale",
        }:
            status = "waiting_for_isolation"
        elif error_code in {
            "usage_limit_reached",
            "background_budget_exhausted",
            "waiting_for_usage_reset",
        }:
            status = "waiting_for_usage_reset"
        elif error_code in {
            "codex_identity_invalid",
            "codex_identity_drift",
            "codex_identity_mismatch",
            "codex_path_unreviewed",
            "codex_signature_invalid",
            "codex_signature_unavailable",
            "broker_policy_failed",
            "broker_policy_invalid",
            "broker_dns_unsafe",
            "broker_peer_mismatch",
            "sandbox_policy_failed",
            "codex_sandbox_profile_invalid",
            "credential_isolation_failed",
            "child_credential_isolation_failed",
            "private_network_isolation_failed",
            "private_network_isolation_unproven",
            "unix_socket_isolation_failed",
            "codex_tool_invocation_blocked",
        }:
            status = "failed_security"
        elif raw_status == "waiting" and (
            error_code.startswith("attestation_")
            or error_code.startswith("isolation_")
        ):
            status = "waiting_for_isolation"

        labels = {
            "starting": "Starting",
            "generating": "Generating",
            "waiting": "Waiting",
            "retrying": "Retrying",
            "failed": "Failed",
            "needs_reapproval": "Needs reapproval",
            "waiting_for_login": "Waiting for ChatGPT login",
            "waiting_for_isolation": "Waiting for security check",
            "waiting_for_usage_reset": "Waiting for usage reset",
            "failed_security": "Security check failed",
            "draft_ready": "Draft ready",
        }
        messages = {
            "starting": "Fresh source search and content generation are starting.",
            "generating": "ChatGPT is creating the Reddit content now.",
            "waiting": "Drafting is waiting for a required local prerequisite.",
            "retrying": "A temporary failure is being retried once.",
            "failed": "Content generation could not complete safely. The editable shell remains available.",
            "needs_reapproval": "A legacy draft request needs attention; create content again from the story.",
            "waiting_for_login": "Sign in to ChatGPT locally, then retry the preserved approval.",
            "waiting_for_isolation": "Run the local assistance security check, then retry the preserved approval.",
            "waiting_for_usage_reset": "ChatGPT usage is temporarily unavailable. The preserved approval can resume after reset.",
            "failed_security": "Drafting stopped because the current security policy did not pass. Review diagnostics before retrying.",
            "draft_ready": "The content is ready for human review.",
        }
        if error_code in {"codex_unavailable", "CodexUnavailable"}:
            messages[status] = "The local ChatGPT drafting command is unavailable. Check the installation, then retry."
        elif error_code in {
            "codex_authentication_unavailable",
            "codex_authentication_invalid",
            "waiting_for_login",
        }:
            messages[status] = "ChatGPT authentication is unavailable. Sign in to ChatGPT, then retry this approval."
        elif error_code in {"codex_timeout", "codex_process_failed", "codex_result_invalid"}:
            messages[status] = "The temporary ChatGPT generation attempt did not complete safely. Retry the preserved approval."
        elif error_code in {"assistance_disabled", "AssistanceDisabled"}:
            messages[status] = "ChatGPT assistance is disabled. The editable draft shell remains available."
        elif error_code in {"isolation_not_passed", "IsolationUnavailable"}:
            messages[status] = "The local isolation check must pass before ChatGPT can generate this draft."
        elif error_code in {"approval_invalidated", "evidence_invalidated", "ApprovalInvalidated"}:
            status = "needs_reapproval"
        elif error_code in {"story_revision_changed", "approval_revision_mismatch"}:
            status = "needs_reapproval"
            messages[status] = (
                "The approved claims or sources changed. Review the latest revision and approve again."
            )
        elif error_code == "volatile_source_unavailable":
            messages[status] = (
                "An approved source is temporarily unavailable, so its volatile passage could not be rechecked. "
                "The editable shell is preserved; retry after the source recovers."
            )
        elif error_code == "volatile_source_unsafe":
            status = "failed_security"
            messages[status] = (
                "An approved source failed the safe-network policy during revalidation. "
                "Review the source before retrying."
            )
        elif error_code == "volatile_source_drift":
            status = "needs_reapproval"
            messages[status] = (
                "An approved volatile source changed. Review the latest passage and approve again."
            )
        return {
            "status": status,
            "raw_status": raw_status,
            "label": labels.get(status, status.replace("_", " ").title()),
            "message": messages.get(status, "Draft generation is awaiting a safe next step."),
            "attempt_count": attempts,
            "error_code": error_code,
            "active": status in {"starting", "generating", "retrying"},
            "retryable": status
            in {
                "failed",
                "waiting",
                "waiting_for_login",
                "waiting_for_isolation",
                "waiting_for_usage_reset",
                "failed_security",
            },
        }

    def draft_status(self, story_id: str) -> dict[str, Any] | None:
        work = self.database.one(
            """
            SELECT * FROM work_item
            WHERE story_id = ? AND kind IN ('content', 'draft')
            ORDER BY id DESC LIMIT 1
            """,
            (story_id,),
        )
        if not work:
            return None
        status = self._draft_status_view(work)
        draft = self.database.one(
            "SELECT id, status FROM draft WHERE story_id = ? ORDER BY version DESC LIMIT 1",
            (story_id,),
        )
        if not draft and status["status"] not in {
            "needs_reapproval",
            "generating",
            "draft_ready",
        }:
            try:
                approval_snapshot = json.loads(str(work.get("payload_json") or "{}"))
            except json.JSONDecodeError:
                approval_snapshot = {}
            self._create_editable_draft_shell(int(work["id"]), approval_snapshot)
            draft = self.database.one(
                "SELECT id, status FROM draft WHERE story_id = ? ORDER BY version DESC LIMIT 1",
                (story_id,),
            )
        draft_id = int(draft["id"]) if draft else None
        if (
            status["status"] == "draft_ready"
            and draft
            and draft["status"] == "Needs Review"
        ):
            status.update(
                {
                    "status": "needs_reapproval",
                    "label": "Needs reapproval",
                    "message": "The evidence changed after this draft was created. Requalify and approve a new draft while preserving this version.",
                    "retryable": False,
                }
            )
        status.update(
            {
                "work_item_id": int(work["id"]),
                "story_id": story_id,
                "draft_id": draft_id,
                "updated_at": work["updated_at"],
                "approval_basis": "ungated" if work.get("kind") == "content" else "verified",
                "manual_editor_available": bool(
                    draft and draft["status"] == "Editable Shell"
                ),
            }
        )
        try:
            payload = json.loads(str(work.get("payload_json") or "{}"))
        except json.JSONDecodeError:
            payload = {}
        status["approval_basis"] = (
            "ungated"
            if work.get("kind") == "content"
            else str(payload.get("approval_basis") or "verified")
        )
        status["manual_override_active"] = status["approval_basis"] == "manual_override"
        if status["status"] == "draft_ready" and not draft:
            status.update(
                {
                    "status": "failed",
                    "label": "Failed",
                    "message": "Draft generation finished without a reviewable draft. Retry this approval.",
                    "retryable": True,
                }
            )
        return status

    def _dispatch_draft(self, work_item_id: int) -> None:
        if not self.draft_callback:
            return
        try:
            self.draft_callback(work_item_id)
        except Exception as error:
            now = utc_now()
            self.database.execute(
                """
                INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                VALUES('warning', 'draft_dispatch', ?, ?, ?)
                """,
                (
                    "Immediate draft dispatch could not start; the durable queue was preserved.",
                    now,
                    Database.json({"work_item_id": work_item_id, "error_class": type(error).__name__}),
                ),
            )

    def _create_editable_draft_shell(
        self, work_item_id: int, approval_snapshot: dict[str, Any]
    ) -> int | None:
        """Create a source-bound manual fallback without claiming AI completion."""
        work_state = self.database.one(
            "SELECT status, kind FROM work_item WHERE id = ? AND kind IN ('draft', 'content')",
            (work_item_id,),
        )
        if not work_state or work_state["status"] not in {
            "pending",
            "queued",
            "waiting",
            "failed",
        }:
            return None
        story = approval_snapshot.get("story")
        claims = approval_snapshot.get("claims")
        sources = approval_snapshot.get("sources")
        if not isinstance(story, dict) or not isinstance(claims, list) or not isinstance(sources, list):
            return None
        headline = " ".join(str(story.get("headline") or "").split())[:240]
        summary = " ".join(str(story.get("summary") or "").split())
        if not summary:
            first_claim = next(
                (
                    claim.get("text")
                    for claim in claims
                    if isinstance(claim, dict) and claim.get("text")
                ),
                "",
            )
            summary = " ".join(str(first_claim).split())
        if not headline or not summary:
            return None
        body = (
            summary + "\n\nWhat do you think this development means in practice?"
            if work_state["kind"] == "content"
            else summary
        )

        source_rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for source in sources:
            if not isinstance(source, dict):
                continue
            raw_url = str(source.get("citation_url") or source.get("url") or "")
            try:
                safe_url = normalize_optional_public_https_url(raw_url) or ""
            except ValueError:
                safe_url = ""
            label = " ".join(
                str(
                    source.get("citation_label")
                    or source.get("title")
                    or source.get("source_name")
                    or "Source"
                ).split()
            )[:160]
            key = f"{label.casefold()}|{safe_url}"
            if key in seen:
                continue
            seen.add(key)
            source_rows.append(
                {
                    "display_label": label,
                    "title": " ".join(str(source.get("title") or label).split())[:500],
                    "url": safe_url,
                    "role": str(source.get("source_role") or "")[:80],
                    "hosting_publisher_name": str(
                        source.get("hosting_publisher_name") or ""
                    )[:120],
                    "reporting_origin_name": str(
                        source.get("reporting_origin_name") or ""
                    )[:120],
                    "provenance_type": str(source.get("provenance_type") or "unknown")[:20],
                    "source_provenance": str(source.get("source_provenance") or "configured")[:24],
                }
            )

        now = utc_now()
        with self.database.transaction() as connection:
            work = connection.execute(
                "SELECT status, kind FROM work_item WHERE id = ? AND kind IN ('draft', 'content')",
                (work_item_id,),
            ).fetchone()
            if not work or work["status"] not in {"pending", "queued", "waiting", "failed"}:
                return None
            if work["kind"] == "draft":
                existing = connection.execute(
                    """
                    SELECT id FROM draft
                    WHERE story_id = ? AND status IN ('Current', 'Editable Shell')
                    ORDER BY version DESC LIMIT 1
                    """,
                    (story.get("id"),),
                ).fetchone()
                if existing:
                    return int(existing["id"])
            version = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version FROM draft WHERE story_id = ?",
                    (story.get("id"),),
                ).fetchone()["version"]
            ) + 1
            connection.execute(
                """
                UPDATE draft SET status = 'Superseded', updated_at = ?
                WHERE story_id = ? AND status IN ('Current', 'Editable Shell')
                """,
                (now, story.get("id")),
            )
            cursor = connection.execute(
                """
                INSERT INTO draft(
                    story_id, mode, status, version, headline, metadata, body, lens,
                    sources_json, created_at, updated_at, provenance_json,
                    approval_snapshot_json, suggested_flair, subreddit_reminder,
                    search_attempt_id
                ) VALUES(?, ?, 'Editable Shell', ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    story.get("id"),
                    approval_snapshot.get("mode") or "Reddit Post",
                    version,
                    headline,
                    "",
                    body,
                    Database.json(source_rows),
                    now,
                    now,
                    Database.json(
                        {
                            "kind": "deterministic_editable_shell",
                            "work_item_id": work_item_id,
                        }
                    ),
                    Database.json(approval_snapshot),
                    "News",
                    "Verify rules before posting",
                    approval_snapshot.get("search_attempt_id"),
                ),
            )
            return int(cursor.lastrowid)

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
            try:
                approval_snapshot = json.loads(
                    str(draft.get("approval_snapshot_json") or "{}")
                )
            except json.JSONDecodeError:
                approval_snapshot = {}
            draft["approval_basis"] = str(
                approval_snapshot.get("approval_basis") or "verified"
            )
            draft["manual_override_active"] = (
                draft["approval_basis"] == "manual_override"
            )
            draft["source_rows"] = [self._source_parts(source) for source in draft["sources"]]
            draft["search_attempt"] = (
                self.database.one(
                    "SELECT * FROM research_attempt WHERE id = ?",
                    (draft["search_attempt_id"],),
                )
                if draft.get("search_attempt_id")
                else None
            )
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

    def _retired_review_action(
        self,
        story_id: str,
        action: str,
        reason: str = "",
        confirmation_version: str = "",
    ) -> str:
        del confirmation_version
        if action not in {"archive", "withdraw"}:
            raise ValueError(
                "This editorial action is obsolete. Use Create content; no qualification or approval gate remains."
            )
        story = self.database.one("SELECT * FROM story_cluster WHERE id = ?", (story_id,))
        if not story:
            raise LookupError("Story not found")
        now = utc_now()
        new_status = "archived" if action == "archive" else "withdrawn"
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO review_action(story_id, action, reason, created_at, story_revision)
                VALUES(?, ?, ?, ?, ?)
                """,
                (story_id, action, reason.strip(), now, int(story.get("story_revision") or 1)),
            )
            connection.execute(
                "UPDATE story_cluster SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, story_id),
            )
        return new_status

    def review(
        self,
        story_id: str,
        action: str,
        reason: str = "",
        confirmation_version: str = "",
    ) -> str:
        verified_actions = {"approve_neutral", "approve_lens"}
        manual_actions = {"manual_approve_neutral", "manual_approve_lens"}
        allowed = {"archive", "withdraw", *verified_actions, *manual_actions}
        if action not in allowed:
            raise ValueError("Unsupported review action")
        story = self.database.one("SELECT * FROM story_cluster WHERE id = ?", (story_id,))
        if not story:
            raise LookupError("Story not found")
        ranked_story = next(
            (item for item in self._story_rows() if item["id"] == story_id),
            None,
        )
        if ranked_story:
            story.update(ranked_story)
        now = utc_now()
        mode: str | None = None
        new_status = story["status"]
        work_kind: str | None = None
        work_item_id: int | None = None
        work_payload: dict[str, Any] = {"reason": reason.strip(), "requested_at": now}
        if action == "archive":
            new_status = "archived"
        elif action == "withdraw":
            new_status = "withdrawn"
        elif action in verified_actions | manual_actions:
            manual_override = action in manual_actions
            qualification = (
                qualification_state(self.database, story_id)
                if manual_override
                else recalculate_story_qualification(self.database, story_id)
            )
            story = self.database.one("SELECT * FROM story_cluster WHERE id = ?", (story_id,)) or story
            if ranked_story:
                story.update(ranked_story)
            if story["status"] in {"archived", "withdrawn"}:
                raise ValueError("Archived or withdrawn stories cannot be approved for drafting")
            if manual_override and confirmation_version != MANUAL_CONFIRMATION_VERSION:
                raise ValueError("Manual approval requires the current confirmation prompt")
            if not manual_override and not qualification["qualified"]:
                raise ValueError("Evidence and importance qualification must pass before drafting")
            if not manual_override and story["status"] not in {"candidate", "draft_ready", "approved"}:
                raise ValueError("Only verified candidates can be approved for drafting")
            claims = self.database.query(
                "SELECT id, text, status, volatility FROM claim WHERE story_id = ? ORDER BY id",
                (story_id,),
            )
            sources = self._draft_sources(
                story_id, allow_discovery=manual_override
            )
            claim_signatures, source_signatures = approval_signature_sets(claims, sources)
            if manual_override and not claims:
                raise ValueError("Manual approval requires at least one stored claim")
            if manual_override and not any(
                source.get("citation_allowed") for source in sources
            ):
                raise ValueError("Manual approval requires at least one safe public source")
            lens_action = action in {"approve_lens", "manual_approve_lens"}
            if lens_action:
                if (
                    not manual_override
                    and story["opportunity_strength"] not in {"Strong", "Moderate"}
                ):
                    raise ValueError("An open-source lens requires a Strong or Moderate opportunity")
                mode = "Open-Source Lens Brief"
            else:
                mode = "Neutral News Brief"
            new_status = "approved"
            work_kind = "draft"
            approval_basis = "manual_override" if manual_override else "verified"
            gate_snapshot = {
                "evidence_gate": bool(qualification["evidence_gate"]),
                "importance_gate": bool(qualification["effective_importance"]),
                "automated_importance": bool(qualification["automated_importance"]),
                "effective_importance": bool(qualification["effective_importance"]),
                "lens_gate": story.get("opportunity_strength") in {"Strong", "Moderate"},
                "lens_eligible": story.get("opportunity_strength") in {"Strong", "Moderate"},
                "candidate_basis": str(qualification["candidate_basis"]),
            }
            work_payload = {
                "schema_version": 3,
                "story_id": story_id,
                "mode": mode,
                "reason": reason.strip(),
                "approval_basis": approval_basis,
                "manual_override": manual_override,
                "confirmation_version": confirmation_version if manual_override else "",
                "qualification_snapshot": gate_snapshot,
                "approval_snapshot_at": now,
                "story_revision": int(story.get("story_revision") or 1),
                "claim_signatures": claim_signatures,
                "source_signatures": source_signatures,
                "story": {
                    "id": story["id"],
                    "story_revision": int(story.get("story_revision") or 1),
                    "material_revision": int(story.get("material_revision") or 1),
                    "headline": str(story.get("headline") or ""),
                    "summary": str(story.get("summary") or ""),
                    "lane": str(story.get("lane") or ""),
                    "openness_class": str(story.get("openness_class") or ""),
                    "opportunity_strength": story.get("opportunity_strength"),
                    "relevance_bridge": str(story.get("relevance_bridge") or ""),
                    "counterargument": str(story.get("counterargument") or ""),
                    "updated_at": story["updated_at"],
                    "first_public_at": story["first_public_at"],
                    "detected_at": story["detected_at"],
                    "freshness": story["freshness"],
                    "material_update": bool(story["material_update"]),
                },
                "claims": claims,
                "sources": sources,
            }
        with self.database.transaction() as connection:
            if work_kind:
                current_revision = connection.execute(
                    "SELECT story_revision FROM story_cluster WHERE id = ?",
                    (story_id,),
                ).fetchone()
                if (
                    not current_revision
                    or int(current_revision["story_revision"])
                    != int(work_payload["story_revision"])
                ):
                    raise ValueError(
                        "The story changed during approval; review the latest claims and sources again"
                    )
                latest_existing_draft = connection.execute(
                    """
                    SELECT status FROM draft
                    WHERE story_id = ?
                    ORDER BY version DESC LIMIT 1
                    """,
                    (story_id,),
                ).fetchone()
                if latest_existing_draft and latest_existing_draft["status"] not in {
                    "Needs Review",
                    "Editable Shell",
                }:
                    raise ValueError(
                        "A completed draft already exists; revise it from Drafts & history"
                    )
                if (
                    latest_existing_draft
                    and latest_existing_draft["status"] == "Needs Review"
                    and story["status"] != "candidate"
                ):
                    raise ValueError(
                        "A replacement draft requires a candidate whose current draft needs review"
                    )
                existing = connection.execute(
                    """
                    SELECT id, status FROM work_item
                    WHERE story_id = ? AND kind = 'draft'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (story_id,),
                ).fetchone()
                if existing and existing["status"] not in {"cancelled", "needs_reapproval", "completed"}:
                    if existing["status"] in {"failed", "deferred", "waiting"}:
                        raise ValueError("An existing draft request can be retried from this story")
                    raise ValueError("A draft request is already pending for this story")
                if existing and existing["status"] == "needs_reapproval":
                    current_story = connection.execute(
                        "SELECT status FROM story_cluster WHERE id = ?", (story_id,)
                    ).fetchone()
                    if not current_story or current_story["status"] != "candidate":
                        raise ValueError("Requalify this story before approving a replacement draft")
                if existing and existing["status"] == "completed":
                    current_story = connection.execute(
                        "SELECT status FROM story_cluster WHERE id = ?", (story_id,)
                    ).fetchone()
                    latest_draft = connection.execute(
                        """
                        SELECT status FROM draft
                        WHERE story_id = ?
                        ORDER BY version DESC LIMIT 1
                        """,
                        (story_id,),
                    ).fetchone()
                    if (
                        not current_story
                        or current_story["status"] != "candidate"
                        or not latest_draft
                        or latest_draft["status"] != "Needs Review"
                    ):
                        raise ValueError(
                            "A completed draft already exists; revise it from Drafts & history"
                        )
            action_cursor = connection.execute(
                """
                INSERT INTO review_action(
                    story_id, action, reason, draft_mode, created_at, approval_snapshot_json,
                    story_revision, claim_signatures_json, source_signatures_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    story_id,
                    action,
                    reason.strip(),
                    mode,
                    now,
                    Database.json(work_payload) if work_kind else "{}",
                    int(story.get("story_revision") or 1),
                    Database.json(claim_signatures) if work_kind else "[]",
                    Database.json(source_signatures) if work_kind else "[]",
                ),
            )
            connection.execute(
                "UPDATE story_cluster SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, story_id),
            )
            if work_kind:
                work_payload["review_action_id"] = int(action_cursor.lastrowid)
                connection.execute(
                    "UPDATE review_action SET approval_snapshot_json = ? WHERE id = ?",
                    (
                        Database.json(work_payload),
                        int(action_cursor.lastrowid),
                    ),
                )
                if action in manual_actions:
                    connection.execute(
                        """
                        INSERT INTO candidate(
                            story_id, evidence_gate, importance_gate, score, score_json,
                            manual_override, manual_override_at, manual_override_action_id,
                            manual_override_snapshot_json
                        ) VALUES(?, ?, ?, ?, '{}', 1, ?, ?, ?)
                        ON CONFLICT(story_id) DO UPDATE SET
                            manual_override = 1,
                            manual_override_at = excluded.manual_override_at,
                            manual_override_action_id = excluded.manual_override_action_id,
                            manual_override_snapshot_json = excluded.manual_override_snapshot_json
                        """,
                        (
                            story_id,
                            int(qualification["evidence_gate"]),
                            int(qualification["automated_importance"]),
                            int(story.get("importance_score") or 0),
                            now,
                            int(action_cursor.lastrowid),
                            Database.json(gate_snapshot),
                        ),
                    )
                work_cursor = connection.execute(
                    """
                    INSERT INTO work_item(
                        kind, story_id, status, priority, payload_json, created_at,
                        updated_at, idempotency_key, available_at
                    ) VALUES(?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        work_kind,
                        story_id,
                        int(story.get("review_score") or 0),
                        Database.json(work_payload),
                        now,
                        now,
                        f"draft-approval:{int(action_cursor.lastrowid)}",
                        now,
                    ),
                )
                work_item_id = int(work_cursor.lastrowid)
        if work_item_id is not None:
            self._create_editable_draft_shell(work_item_id, work_payload)
            self._dispatch_draft(work_item_id)
        return new_status

    def relevance_notification_state(self) -> dict[str, Any]:
        return {
            "capture_enabled": self.database.get_state(
                "relevance_notification_capture_enabled", "false"
            ) == "true",
            "delivery_enabled": self.database.get_state(
                "relevance_notifications_enabled", "false"
            ) == "true",
            "shadow_mode": self.database.get_state(
                "relevance_notifications_shadow_mode", "true"
            ) == "true",
            "native_enabled": self.database.get_state(
                "relevance_native_notifications_enabled", "false"
            ) == "true",
            "watermark": self.database.get_state(
                "relevance_notification_watermark", ""
            ),
        }

    def begin_relevance_notification_shadow(self) -> dict[str, Any]:
        """Start a new non-delivering validation window without backfill."""
        now = utc_now()
        with self.database.transaction() as connection:
            for key, value in (
                ("relevance_notification_capture_enabled", "true"),
                ("relevance_notifications_enabled", "false"),
                ("relevance_notifications_shadow_mode", "true"),
                ("relevance_notification_watermark", now),
            ):
                connection.execute(
                    """
                    INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (key, value, now),
                )
        return self.relevance_notification_state()

    def set_relevance_notification_delivery(self, enabled: bool) -> dict[str, Any]:
        """Enable or pause delivery; every intentional resume gets a fresh watermark."""
        now = utc_now()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT value FROM app_state WHERE key = 'relevance_notifications_enabled'"
            ).fetchone()
            was_enabled = bool(current and str(current["value"]) == "true")
            values = [
                ("relevance_notification_capture_enabled", "true"),
                ("relevance_notifications_enabled", "true" if enabled else "false"),
                ("relevance_notifications_shadow_mode", "false"),
            ]
            if enabled and not was_enabled:
                values.append(("relevance_notification_watermark", now))
            for key, value in values:
                connection.execute(
                    """
                    INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (key, value, now),
                )
        return self.relevance_notification_state()

    def set_relevance_notification_capture(self, enabled: bool) -> dict[str, Any]:
        """Independent local kill switch; resuming capture never backfills."""
        now = utc_now()
        with self.database.transaction() as connection:
            values = [("relevance_notification_capture_enabled", "true" if enabled else "false")]
            if enabled:
                values.append(("relevance_notification_watermark", now))
            for key, value in values:
                connection.execute(
                    """
                    INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (key, value, now),
                )
        return self.relevance_notification_state()

    def list_relevance_notification_events(self) -> list[dict[str, Any]]:
        """Return immutable relevance events in FIFO order for bridge/native consumers."""
        return self.database.query(
            """
            SELECT id AS event_id, article_key, story_id, canonical_url, title,
                   publisher, category, context, provenance, source_type,
                   published_at, detected_at
            FROM relevance_notification_event
            ORDER BY detected_at, id
            """
        )

    def list_pending_native_relevance_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return a separate FIFO native queue without consuming hosted state."""
        state = self.relevance_notification_state()
        if not state["delivery_enabled"] or not state["native_enabled"]:
            return []
        now = utc_now()
        return self.database.query(
            """
            SELECT e.id AS event_id, e.article_key, e.story_id, e.canonical_url,
                   e.title, e.publisher, e.category, e.context, e.provenance,
                   e.source_type, e.published_at, e.detected_at,
                   COALESCE(d.attempt_count, 0) AS attempt_count
            FROM relevance_notification_event e
            JOIN relevance_notification_outbox o ON o.event_id = e.id
            LEFT JOIN relevance_native_delivery d ON d.event_id = e.id
            WHERE e.detected_at > ?
              AND o.delivery_state = 'pending'
              AND (d.event_id IS NULL OR (
                    d.status IN ('pending', 'failed')
                    AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?)
              ))
            ORDER BY e.detected_at, e.id
            LIMIT ?
            """,
            (str(state["watermark"]), now, max(1, min(100, int(limit)))),
        )

    def record_native_relevance_delivery(
        self,
        event_id: int,
        *,
        delivered: bool,
        error_class: str = "",
        retry_at: str | None = None,
    ) -> dict[str, Any]:
        if not self.database.one(
            "SELECT 1 FROM relevance_notification_event WHERE id = ?", (event_id,)
        ):
            raise LookupError("Notification article not found")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO relevance_native_delivery(
                    event_id, status, attempt_count, delivered_at,
                    next_attempt_at, error_class, updated_at
                ) VALUES(?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    status=CASE WHEN relevance_native_delivery.status = 'delivered'
                                THEN 'delivered' ELSE excluded.status END,
                    attempt_count=relevance_native_delivery.attempt_count +
                        CASE WHEN relevance_native_delivery.status = 'delivered' THEN 0 ELSE 1 END,
                    delivered_at=COALESCE(relevance_native_delivery.delivered_at, excluded.delivered_at),
                    next_attempt_at=CASE WHEN relevance_native_delivery.status = 'delivered'
                                         THEN NULL ELSE excluded.next_attempt_at END,
                    error_class=CASE WHEN relevance_native_delivery.status = 'delivered'
                                     THEN NULL ELSE excluded.error_class END,
                    updated_at=excluded.updated_at
                """,
                (
                    event_id,
                    "delivered" if delivered else "failed",
                    now if delivered else None,
                    None if delivered else retry_at,
                    None if delivered else error_class[:120],
                    now,
                ),
            )
        return self.database.one(
            "SELECT * FROM relevance_native_delivery WHERE event_id = ?", (event_id,)
        ) or {}

    def _notification_event(
        self,
        event_id: int,
        article_key: str,
        story_id: str,
    ) -> dict[str, Any]:
        event = self.database.one(
            """
            SELECT * FROM relevance_notification_event
            WHERE id = ? AND article_key = ? AND story_id = ?
            """,
            (event_id, article_key, story_id),
        )
        if not event:
            raise LookupError("Notification article not found")
        return event

    def _notification_research_result(self, event_id: int) -> dict[str, Any]:
        row = self.database.one(
            """
            SELECT ra.status, ra.result_count, ra.detail, f.publishers_json
            FROM relevance_notification_outbox o
            LEFT JOIN research_attempt ra ON ra.id = o.research_attempt_id
            LEFT JOIN relevance_notification_followup f ON f.event_id = o.event_id
            WHERE o.event_id = ?
            """,
            (event_id,),
        ) or {}
        try:
            publishers = json.loads(str(row.get("publishers_json") or "[]"))
        except json.JSONDecodeError:
            publishers = []
        return {
            "status": str(row.get("status") or "queued"),
            "result_count": int(row.get("result_count") or 0),
            "publishers": [str(value) for value in publishers if str(value).strip()],
            "detail": str(row.get("detail") or ""),
        }

    def start_notification_research(
        self,
        event_id: int,
        article_key: str,
        story_id: str,
    ) -> dict[str, Any]:
        """Idempotently run one fresh operator-requested search, never a draft."""
        self._notification_event(event_id, article_key, story_id)
        outbox = self.database.one(
            "SELECT research_attempt_id FROM relevance_notification_outbox WHERE event_id = ?",
            (event_id,),
        )
        attempt_id = int((outbox or {}).get("research_attempt_id") or 0)
        work_id = 0
        if attempt_id:
            work = self.database.one(
                """
                SELECT id FROM work_item
                WHERE kind = 'source_research'
                  AND json_extract(payload_json, '$.research_attempt_id') = ?
                ORDER BY id DESC LIMIT 1
                """,
                (attempt_id,),
            )
            work_id = int((work or {}).get("id") or 0)
        else:
            from .research import queue_research_attempt

            attempt_id, work_id = queue_research_attempt(
                self.database, story_id, purpose="operator_review", priority=100
            )
            now = utc_now()
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE relevance_notification_outbox
                    SET research_attempt_id = COALESCE(research_attempt_id, ?),
                        research_requested_at = COALESCE(research_requested_at, ?),
                        updated_at = ?
                    WHERE event_id = ?
                    """,
                    (attempt_id, now, now, event_id),
                )
                linked = connection.execute(
                    "SELECT research_attempt_id FROM relevance_notification_outbox WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                attempt_id = int(linked["research_attempt_id"])
        result = self._notification_research_result(event_id)
        if result["status"] in {"complete", "partial", "failed", "unavailable"}:
            return result
        if not work_id:
            raise RuntimeError("Operator review research work is unavailable")
        if self.research_callback is not None:
            self.research_callback(work_id)
            result = self._notification_research_result(event_id)
            if result["status"] not in {"complete", "partial", "failed", "unavailable"}:
                raise RuntimeError("Operator review research did not reach a terminal state")
        return result

    def dismiss_notification_article(
        self,
        event_id: int,
        article_key: str,
        story_id: str,
    ) -> dict[str, Any]:
        """Dismiss only this global notification; never archive its story."""
        self._notification_event(event_id, article_key, story_id)
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE relevance_notification_outbox
                SET delivery_state = 'dismissed',
                    dismissed_at = COALESCE(dismissed_at, ?), updated_at = ?
                WHERE event_id = ?
                """,
                (now, now, event_id),
            )
        row = self.database.one(
            "SELECT delivery_state, dismissed_at FROM relevance_notification_outbox WHERE event_id = ?",
            (event_id,),
        ) or {}
        return {
            "status": str(row.get("delivery_state") or "dismissed"),
            "dismissed_at": row.get("dismissed_at"),
        }


    def create_content(self, story_id: str, guidance: str = "") -> tuple[int, int]:
        story = self.database.one("SELECT * FROM story_cluster WHERE id = ?", (story_id,))
        if not story:
            raise LookupError("Story not found")
        if story["status"] in {"archived", "withdrawn"}:
            raise ValueError("Archived or withdrawn stories cannot create content")
        active = self.database.one(
            """
            SELECT id FROM work_item
            WHERE story_id = ? AND kind = 'content'
              AND status IN ('pending', 'queued', 'waiting', 'running', 'generating')
            ORDER BY id DESC LIMIT 1
            """,
            (story_id,),
        )
        if active:
            raise ValueError("Content creation is already active for this story")
        claims = self.database.query(
            "SELECT id, text, status, volatility FROM claim WHERE story_id = ? ORDER BY id",
            (story_id,),
        )
        sources = self._draft_sources(story_id, allow_discovery=True)
        now = utc_now()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM story_cluster WHERE id = ?", (story_id,)
            ).fetchone()
            if not current or current["status"] in {"archived", "withdrawn"}:
                raise ValueError("Story is no longer available for content creation")
            duplicate = connection.execute(
                """
                SELECT 1 FROM work_item WHERE story_id = ? AND kind = 'content'
                  AND status IN ('pending', 'queued', 'waiting', 'running', 'generating')
                """,
                (story_id,),
            ).fetchone()
            if duplicate:
                raise ValueError("Content creation is already active for this story")
            attempt_cursor = connection.execute(
                """
                INSERT INTO research_attempt(story_id, purpose, status, created_at, updated_at)
                VALUES(?, 'draft_refresh', 'queued', ?, ?)
                """,
                (story_id, now, now),
            )
            attempt_id = int(attempt_cursor.lastrowid)
            research_cursor = connection.execute(
                """
                INSERT INTO work_item(
                    kind, story_id, status, priority, payload_json, created_at,
                    updated_at, idempotency_key, available_at
                ) VALUES('source_research', ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    story_id,
                    int(current["review_score"] or current["priority_score"] or 0),
                    Database.json({
                        "schema_version": 1,
                        "purpose": "draft_refresh",
                        "research_attempt_id": attempt_id,
                        "story_revision": int(current["story_revision"] or 1),
                    }),
                    now,
                    now,
                    f"source-research:draft_refresh:{story_id}:{attempt_id}",
                    now,
                ),
            )
            research_work_id = int(research_cursor.lastrowid)
            payload = {
                "schema_version": 4,
                "operation": "draft_reddit",
                "story_id": story_id,
                "story_revision": int(current["story_revision"] or 1),
                "mode": "Reddit Post",
                "human_guidance": guidance.strip()[:2000],
                "requested_at": now,
                "search_attempt_id": attempt_id,
                "search_work_item_id": research_work_id,
                "story": {
                    "id": story_id,
                    "headline": str(current["headline"]),
                    "summary": str(current["summary"]),
                    "lane": str(current["lane"]),
                    "freshness": str(current["freshness"]),
                    "first_public_at": str(current["first_public_at"]),
                    "source_trust_state": str(current["source_trust_state"]),
                },
                "claims": claims,
                "sources": sources,
                "policy": {
                    "style": "reddit-posts-v1.0.1",
                    "subreddit_assumed": False,
                    "subreddit_reminder": "Verify rules before posting",
                },
            }
            work_cursor = connection.execute(
                """
                INSERT INTO work_item(
                    kind, story_id, status, priority, payload_json, created_at,
                    updated_at, idempotency_key, available_at
                ) VALUES('content', ?, 'waiting', ?, ?, ?, ?, ?, NULL)
                """,
                (
                    story_id,
                    int(current["review_score"] or current["priority_score"] or 0),
                    Database.json(payload),
                    now,
                    now,
                    f"content:{story_id}:{attempt_id}",
                ),
            )
            work_item_id = int(work_cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO review_action(
                    story_id, action, reason, draft_mode, created_at,
                    approval_snapshot_json, story_revision
                ) VALUES(?, 'create_content', ?, 'Reddit Post', ?, ?, ?)
                """,
                (
                    story_id,
                    guidance.strip()[:2000],
                    now,
                    Database.json({
                        "content_work_item_id": work_item_id,
                        "research_attempt_id": attempt_id,
                    }),
                    int(current["story_revision"] or 1),
                ),
            )
        draft_id = self._create_editable_draft_shell(work_item_id, payload)
        if draft_id is None:
            raise RuntimeError("The editable content shell could not be created")
        self._dispatch_draft(work_item_id)
        return work_item_id, draft_id

    def retry_draft(self, story_id: str) -> int:
        story = self.database.one("SELECT * FROM story_cluster WHERE id = ?", (story_id,))
        if not story:
            raise LookupError("Story not found")
        work = self.database.one(
            """
            SELECT * FROM work_item
            WHERE story_id = ? AND kind = 'draft'
            ORDER BY id DESC LIMIT 1
            """,
            (story_id,),
        )
        if not work:
            raise LookupError("Draft request not found")
        status = self._draft_status_view(work)
        if status["status"] == "needs_reapproval":
            raise ValueError("Evidence changed after approval; review and approve this story again")
        if not status["retryable"]:
            raise ValueError("This draft request is not available for retry")
        try:
            from .assistance import ApprovalInvalidated, AssistanceError, build_packet

            build_packet(self.database, int(work["id"]))
        except ApprovalInvalidated as error:
            raise ValueError(
                "Evidence changed after approval; review and approve this story again"
            ) from error
        except AssistanceError as error:
            raise ValueError(str(error)) from error

        now = utc_now()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT status FROM work_item WHERE id = ? AND story_id = ? AND kind = 'draft'",
                (work["id"], story_id),
            ).fetchone()
            if not current:
                raise LookupError("Draft request not found")
            current_view = self._draft_status_view(dict(current))
            if not current_view["retryable"]:
                raise ValueError("This draft request is not available for retry")
            connection.execute(
                """
                UPDATE work_item SET status = 'queued', last_error_class = NULL,
                    attempt_count = 0, updated_at = ?, available_at = ? WHERE id = ?
                """,
                (now, now, work["id"]),
            )
            connection.execute(
                """
                INSERT INTO review_action(
                    story_id, action, reason, draft_mode, created_at, approval_snapshot_json
                ) VALUES(?, 'retry_draft', 'Retried the existing approved draft request.', ?, ?, ?)
                """,
                (
                    story_id,
                    json.loads(str(work.get("payload_json") or "{}")).get("mode"),
                    now,
                    Database.json({"work_item_id": int(work["id"])}),
                ),
            )
        self._dispatch_draft(int(work["id"]))
        return int(work["id"])

    def save_draft(
        self,
        draft_id: int,
        headline: str,
        metadata: str,
        body: str,
        lens: str = "",
        suggested_flair: str = "",
        subreddit_reminder: str = "Verify rules before posting",
    ) -> int:
        original = self.get_draft(draft_id)
        if not original:
            raise LookupError("Draft not found")
        if original["status"] not in {"Current", "Editable Shell"}:
            raise ValueError("Only the current draft version or editable shell can be revised")
        headline_value = headline.strip()
        metadata_value = metadata.strip()
        body_value = body.replace("\r\n", "\n").replace("\r", "\n").strip()
        lens_value = lens.replace("\r\n", "\n").replace("\r", "\n").strip()
        flair_value = " ".join(suggested_flair.split())[:80]
        reminder_value = subreddit_reminder.strip()
        if not headline_value or not body_value:
            raise ValueError("Headline and factual brief are required")
        self._validate_draft_links(body_value, original["sources"])
        self._validate_draft_links(lens_value, original["sources"])
        if original["mode"] == "Reddit Post" and lens_value:
            raise ValueError("Reddit content has one mode and cannot include a lens section")
        if original["mode"] == "Reddit Post" and reminder_value != "Verify rules before posting":
            raise ValueError("The posting reminder must remain: Verify rules before posting")
        if lens_value and original["mode"] != "Open-Source Lens Brief":
            raise ValueError("Open-source lens text requires an approved lens brief")
        if lens_value:
            story = self.database.one(
                """
                SELECT s.opportunity_strength, COALESCE(c.manual_override, 0) AS manual_override
                FROM story_cluster s LEFT JOIN candidate c ON c.story_id = s.id
                WHERE s.id = ?
                """,
                (original["story_id"],),
            )
            if (
                not story
                or (
                    story["opportunity_strength"] not in {"Strong", "Moderate"}
                    and not original["manual_override_active"]
                )
            ):
                raise ValueError("Open-source lens text requires a Strong or Moderate opportunity")
        now = utc_now()
        with self.database.transaction() as connection:
            locked_original = connection.execute(
                "SELECT status, provenance_json FROM draft WHERE id = ?",
                (draft_id,),
            ).fetchone()
            if (
                not locked_original
                or locked_original["status"] not in {"Current", "Editable Shell"}
            ):
                raise ValueError(
                    "This draft changed while it was being edited. Reload the latest version."
                )
            locked_shell_work_item_id: int | None = None
            if locked_original["status"] == "Editable Shell":
                try:
                    locked_provenance = json.loads(
                        str(locked_original["provenance_json"] or "{}")
                    )
                except json.JSONDecodeError:
                    locked_provenance = {}
                if isinstance(locked_provenance.get("work_item_id"), int):
                    locked_shell_work_item_id = int(
                        locked_provenance["work_item_id"]
                    )
                if locked_shell_work_item_id is None:
                    raise ValueError(
                        "This editable shell is not linked to its approved draft request."
                    )
                cancelled = connection.execute(
                    """
                    UPDATE work_item
                    SET status = 'cancelled', last_error_class = 'manual_draft_completed',
                        updated_at = ?, available_at = NULL
                    WHERE id = ? AND story_id = ? AND kind IN ('content', 'draft')
                      AND status IN ('pending', 'queued', 'waiting', 'running', 'generating', 'failed')
                    """,
                    (now, locked_shell_work_item_id, original["story_id"]),
                ).rowcount
                if cancelled != 1:
                    raise ValueError(
                        "Draft generation completed while this shell was being edited. Reload the latest version."
                    )
            current_version = connection.execute(
                "SELECT MAX(version) AS version FROM draft WHERE story_id = ?",
                (original["story_id"],),
            ).fetchone()["version"]
            connection.execute(
                "UPDATE draft SET status = 'Superseded', updated_at = ? WHERE story_id = ? AND status IN ('Current', 'Editable Shell')",
                (now, original["story_id"]),
            )
            cursor = connection.execute(
                """
                INSERT INTO draft(
                    story_id, mode, status, version, headline, metadata, body, lens,
                    sources_json, created_at, updated_at, supersedes_id,
                    provenance_json, approval_snapshot_json, suggested_flair,
                    subreddit_reminder, search_attempt_id
                ) VALUES(?, ?, 'Current', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    original["story_id"], original["mode"], int(current_version) + 1,
                    headline_value, metadata_value, body_value, lens_value,
                    original["sources_json"], now, now, draft_id,
                    original.get("provenance_json") or "{}",
                    original.get("approval_snapshot_json") or "{}",
                    flair_value,
                    reminder_value if original["mode"] == "Reddit Post" else "",
                    original.get("search_attempt_id"),
                ),
            )
            connection.execute(
                "INSERT INTO review_action(story_id, action, reason, draft_mode, created_at) VALUES(?, 'manual_revision', ?, ?, ?)",
                (original["story_id"], f"Saved version {int(current_version) + 1}", original["mode"], now),
            )
            if locked_shell_work_item_id is not None:
                connection.execute(
                    "UPDATE story_cluster SET status = ?, updated_at = ? WHERE id = ?",
                    (
                        "content_ready" if original["mode"] == "Reddit Post" else "draft_ready",
                        now,
                        original["story_id"],
                    ),
                )
            return int(cursor.lastrowid)

    def sources(self) -> dict[str, Any]:
        rows = self.database.query(
            "SELECT * FROM source_registry ORDER BY family, name"
        )
        for row in rows:
            try:
                definition = json.loads(str(row.get("definition_json") or "{}"))
            except json.JSONDecodeError:
                definition = {}
            row["validation_status"] = definition.get("validation_status", "unknown")
            row["operational_status"] = row["health"] if row["enabled"] else row["validation_status"]
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
        if self.database.one("SELECT 1 AS present FROM source_state WHERE source_id = ?", (source_id,)):
            set_source_enabled(self.database, source_id, enabled)
        else:
            self.database.execute(
                "UPDATE source_registry SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, source_id),
            )
        return enabled

    def schedule_status(self) -> dict[str, Any]:
        from .assistance import assistance_status

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
        if self.scheduler is not None:
            try:
                live_schedule = self.scheduler.status()
                installed = bool(live_schedule.installed)
                schedule_state = str(live_schedule.state)
                schedule_error = live_schedule.error
            except Exception:
                installed = False
                schedule_state = "unavailable"
                schedule_error = "launchctl_status_unavailable"
        else:
            installed = self.database.get_state("schedule_installed", "false") == "true"
            schedule_state = self.database.get_state("schedule_status", "not_installed")
            schedule_error = None
        schedule_active = schedule_state == "active"
        next_scan = (
            next_scheduled_run().isoformat().replace("+00:00", "Z")
            if schedule_active
            else "Not scheduled"
        )
        assistance = assistance_status(self.database)
        return {
            "installed": installed,
            "status": schedule_state,
            "error": schedule_error,
            "last_scan_at": self.database.get_state("last_scan_at", "Never"),
            "next_scan_at": next_scan,
            "assistance_enabled": self.database.get_state("assistance_enabled", "false") == "true",
            "assistance": assistance,
            "shadow_mode": self.database.get_state("shadow_mode", "true") == "true",
            "background_units": int(usage["background"] or 0),
            "draft_units": int(usage["draft"] or 0),
            "background_limit": int(self.database.get_state("background_unit_limit", "8")),
            "reserve_limit": int(self.database.get_state("urgent_reserve_limit", "2")),
            "today": today.isoformat(),
            "extended_start_suggestion": (today - timedelta(days=7)).isoformat(),
            "recent_scans": recent,
            "queue": queued,
            "controls_available": self.scheduler is not None,
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
        if action not in {"install", "pause", "resume", "run_now", "uninstall"}:
            raise ValueError("Unsupported schedule action")
        now = utc_now()
        if action in {"install", "uninstall"}:
            if self.scheduler is None:
                raise ValueError("Operational scheduler controls are unavailable in this process")
            try:
                status = self.scheduler.install() if action == "install" else self.scheduler.uninstall()
            except Exception as error:
                raise ValueError(str(error)) from error
            return str(status.state)
        if action == "pause":
            if self.scheduler is not None:
                try:
                    return str(self.scheduler.pause().state)
                except Exception as error:
                    raise ValueError(str(error)) from error
            self.database.set_state("schedule_status", "paused", now)
            return "paused"
        if action == "resume":
            if self.scheduler is not None:
                try:
                    return str(self.scheduler.resume().state)
                except Exception as error:
                    raise ValueError(str(error)) from error
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
        if self.run_callback:
            self.run_callback()
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
            "schema_version": 2,
            "exported_at": utc_now(),
            "story": {key: value for key, value in story.items() if key not in {"sources", "evidence_sources", "claims", "actions", "drafts"}},
            "claims": story["claims"],
            "sources": story["sources"],
            "enriched_evidence_sources": story["evidence_sources"],
            "review_actions": story["actions"],
        }

    def draft_markdown(self, draft_id: int) -> str:
        draft = self.get_draft(draft_id)
        if not draft:
            raise LookupError("Draft not found")
        if draft["mode"] == "Reddit Post":
            sections = [
                f"**Title:** {draft['headline']}",
                "",
                str(draft["body"]),
                "",
                f"**Suggested flair:** {draft['suggested_flair'] or 'Not specified'}",
                "",
                "Verify rules before posting",
            ]
        else:
            sections = [f"# {draft['headline']}", "", draft["metadata"], "", draft["body"]]
        if draft["lens"]:
            sections.extend(["", "## Open-Source Lens", "", draft["lens"]])
        sections.extend(["", "## Sources", ""])
        sections.extend(f"- {self._source_markdown(source)}" for source in draft["sources"])
        return "\n".join(sections).strip() + "\n"

    @staticmethod
    def _source_parts(source: Any) -> dict[str, str]:
        if isinstance(source, dict):
            title = " ".join(
                str(
                    source.get("display_label")
                    or source.get("title")
                    or source.get("name")
                    or "Source"
                ).split()
            )[:500]
            role = " ".join(str(source.get("role") or source.get("source_role") or "").split())[:80]
            raw_url = str(source.get("url") or "").strip()
        else:
            title = " ".join(str(source).split())[:500] or "Source"
            role = ""
            raw_url = ""
        try:
            parsed = urlsplit(raw_url)
            hostname = parsed.hostname or ""
            try:
                literal_address = ipaddress.ip_address(hostname)
            except ValueError:
                literal_address = None
            blocked_address = bool(
                literal_address
                and (
                    literal_address.is_private
                    or literal_address.is_loopback
                    or literal_address.is_link_local
                    or literal_address.is_multicast
                    or literal_address.is_reserved
                    or literal_address.is_unspecified
                )
            )
            safe_url = (
                raw_url
                if parsed.scheme == "https"
                and bool(hostname)
                and not parsed.username
                and not parsed.password
                and parsed.port in {None, 443}
                and hostname.casefold() != "localhost"
                and not hostname.casefold().endswith(".localhost")
                and not blocked_address
                else ""
            )
        except ValueError:
            safe_url = ""
        return {"title": title, "role": role, "url": safe_url}

    @classmethod
    def _validate_draft_links(cls, value: str, sources: list[Any]) -> None:
        without_links = INLINE_SOURCE_LINK_RE.sub("", value)
        if RAW_HTML_RE.search(without_links):
            raise ValueError("Draft text cannot contain raw HTML")
        allowed_urls = {
            cls._source_parts(source)["url"]
            for source in sources
            if cls._source_parts(source)["url"]
        }
        for match in INLINE_SOURCE_LINK_RE.finditer(value):
            if match.group(2) not in allowed_urls:
                raise ValueError("Draft links must use URLs from the confirmed evidence snapshot")
        if ANY_MARKDOWN_LINK_RE.search(without_links):
            raise ValueError("Draft links must use safe source Markdown")
        if re.search(r"https?://", without_links, re.I):
            raise ValueError("Draft URLs must be linked to a confirmed source name")

    @classmethod
    def _inline_markdown_html(cls, value: str, sources: list[Any]) -> str:
        allowed_urls = {
            cls._source_parts(source)["url"]
            for source in sources
            if cls._source_parts(source)["url"]
        }
        output: list[str] = []
        position = 0
        for match in INLINE_SOURCE_LINK_RE.finditer(value):
            output.append(html.escape(value[position:match.start()]))
            label, url = match.groups()
            if url in allowed_urls:
                output.append(
                    f'<a href="{html.escape(url, quote=True)}" rel="noreferrer noopener">'
                    f"{html.escape(label)}</a>"
                )
            else:
                output.append(html.escape(match.group(0)))
            position = match.end()
        output.append(html.escape(value[position:]))
        return "".join(output).replace("\n", "<br>")

    @classmethod
    def _source_markdown(cls, source: Any) -> str:
        parts = cls._source_parts(source)
        title = parts["title"].replace("[", "\\[").replace("]", "\\]")
        url = parts["url"].replace("<", "%3C").replace(">", "%3E")
        rendered = f"[{title}](<{url}>)" if url else title
        return f"{rendered} — {parts['role']}" if parts["role"] else rendered

    @classmethod
    def _source_html(cls, source: Any) -> str:
        parts = cls._source_parts(source)
        title = html.escape(parts["title"])
        if parts["url"]:
            rendered = (
                f'<a href="{html.escape(parts["url"], quote=True)}" '
                f'rel="noreferrer noopener">{title}</a>'
            )
        else:
            rendered = title
        if parts["role"]:
            rendered += f" — {html.escape(parts['role'])}"
        return rendered

    def draft_html(self, draft_id: int) -> str:
        draft = self.get_draft(draft_id)
        if not draft:
            raise LookupError("Draft not found")
        body = "".join(
            f"<p>{self._inline_markdown_html(paragraph, draft['sources'])}</p>"
            for paragraph in str(draft["body"]).split("\n\n")
        )
        lens = ""
        if draft["lens"]:
            lens = (
                "<h2>Open-Source Lens</h2><p>"
                f"{self._inline_markdown_html(str(draft['lens']), draft['sources'])}</p>"
            )
        sources = "".join(f"<li>{self._source_html(source)}</li>" for source in draft["sources"])
        headline = html.escape(str(draft["headline"]))
        metadata = html.escape(str(draft["metadata"]))
        reddit_meta = ""
        if draft["mode"] == "Reddit Post":
            reddit_meta = (
                f"<p><strong>Suggested flair:</strong> {html.escape(str(draft['suggested_flair'] or 'Not specified'))}</p>"
                "<p><strong>Verify rules before posting</strong></p>"
            )
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{headline}</title>
<style>body{{font:17px/1.65 system-ui;max-width:760px;margin:7vh auto;padding:0 24px;color:#17201d}}h1{{font:700 42px/1.08 Georgia,serif}}.meta{{color:#61706a}}h2{{margin-top:2.4rem}}</style>
</head><body><article><h1>{headline}</h1><p class="meta">{metadata}</p>{body}{lens}{reddit_meta}<h2>Sources</h2><ul>{sources}</ul></article></body></html>"""
