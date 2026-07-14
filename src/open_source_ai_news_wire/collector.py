"""Deterministic incremental source collection and qualification."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .adapters import AdapterError, Observation, parse_source
from .network import FetchResult, ResponseTooLarge, SafeHttpClient, UnsafeRequest
from .qualification import Qualification, qualify
from .evidence import publisher_key
from .settings import load_settings
from .source_registry import synchronize_sources
from .storage import Database


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ScanSummary:
    scan_id: int
    result: str
    source_success_count: int
    source_failure_count: int
    discovered_count: int
    offline: bool


ClientFactory = Callable[[dict[str, Any]], SafeHttpClient]


def _default_client_factory(source: dict[str, Any]) -> SafeHttpClient:
    hosts = json.loads(source.get("base_hosts_json") or "[]")
    return SafeHttpClient(allowed_hosts=hosts)


def _title_tokens(value: str) -> set[str]:
    ignored = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with", "ai"}
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2 and token not in ignored}


def _similarity(left: str, right: str) -> float:
    left_tokens = _title_tokens(left)
    right_tokens = _title_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


class Collector:
    def __init__(
        self,
        database: Database,
        *,
        client_factory: ClientFactory = _default_client_factory,
        now: Callable[[], str] = utc_now,
    ):
        self.database = database
        self.client_factory = client_factory
        self.now = now

    def scan(
        self,
        *,
        trigger: str = "manual",
        interval_start: str | None = None,
        interval_end: str | None = None,
        deadline: datetime | None = None,
    ) -> ScanSummary:
        if trigger not in {"manual", "scheduled", "recovery", "extended"}:
            raise ValueError("Unsupported scan trigger")
        settings = load_settings(self.database.paths)
        storage = settings.section("storage")
        pressure = self.database.storage_pressure(
            warning_bytes=int(storage["warning_free_bytes"]),
            critical_bytes=int(storage["critical_free_bytes"]),
        )
        started = self.now()
        if pressure.level == "critical":
            scan_id = self.database.execute(
                """
                INSERT INTO scan_run(trigger_type, started_at, finished_at, result, details, interval_start, interval_end)
                VALUES(?, ?, ?, 'blocked_low_disk', 'Critical disk state prevented collection.', ?, ?)
                """,
                (trigger, started, started, interval_start, interval_end),
            )
            return ScanSummary(scan_id, "blocked_low_disk", 0, 0, 0, False)

        synchronize_sources(self.database)
        scan_id = self.database.execute(
            """
            INSERT INTO scan_run(trigger_type, started_at, result, interval_start, interval_end)
            VALUES(?, ?, 'running', ?, ?)
            """,
            (trigger, started, interval_start, interval_end),
        )
        sources = self.database.query(
            """
            SELECT r.*, s.etag, s.last_modified,
                   s.last_checked_at AS state_last_checked_at,
                   s.failure_streak AS state_failure_streak,
                   s.health AS state_health
            FROM source_registry r
            JOIN source_state s ON s.source_id = r.id
            WHERE r.enabled = 1 AND s.enabled = 1
            ORDER BY r.family, r.id
            """
        )
        successes = 0
        failures = 0
        discovered = 0
        network_failures: list[tuple[dict[str, Any], int, Exception]] = []
        backlog = 0
        for index, source in enumerate(sources):
            if deadline and datetime.now(UTC) >= deadline:
                backlog = len(sources) - index
                break
            if trigger == "scheduled" and not self._due(source, started):
                continue
            transaction_id = self.database.execute(
                """
                INSERT INTO source_transaction(
                    scan_run_id, source_id, interval_start, interval_end, status, cursor_before, started_at
                ) VALUES(?, ?, ?, ?, 'running', ?, ?)
                """,
                (scan_id, source["id"], interval_start, interval_end, source.get("cursor"), self.now()),
            )
            try:
                with self.client_factory(source) as client:
                    fetched = client.fetch(
                        str(source["url"]),
                        etag=source.get("etag"),
                        last_modified=source.get("last_modified"),
                    )
                observations = [] if fetched.not_modified else parse_source(
                    str(source["adapter"]),
                    fetched.body,
                    source_url=fetched.url,
                    observed_at=self.now(),
                )
                observations = self._definition_filtered_observations(source, observations)
                observations = self._incremental_observations(
                    source, observations, interval_start, interval_end
                )
                new_count = self._record_success(
                    source, transaction_id, fetched, observations
                )
                successes += 1
                discovered += new_count
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as error:
                network_failures.append((source, transaction_id, error))
            except Exception as error:
                self._record_failure(source, transaction_id, error)
                failures += 1

        offline = bool(network_failures and successes == 0 and failures == 0)
        if offline:
            for source, transaction_id, error in network_failures:
                self.database.execute(
                    """
                    UPDATE source_transaction
                    SET status = 'offline', error_class = ?, finished_at = ? WHERE id = ?
                    """,
                    (type(error).__name__, self.now(), transaction_id),
                )
        else:
            for source, transaction_id, error in network_failures:
                self._record_failure(source, transaction_id, error)
                failures += 1

        result = "offline" if offline else "degraded" if failures or backlog else "success"
        finished = self.now()
        self.database.execute(
            """
            UPDATE scan_run
            SET finished_at = ?, result = ?, source_success_count = ?, source_failure_count = ?,
                discovered_count = ?, degraded = ?, offline = ?, queue_remaining = ?
            WHERE id = ?
            """,
            (finished, result, successes, failures, discovered, int(bool(failures or backlog)), int(offline), backlog, scan_id),
        )
        self.database.set_state("last_scan_at", finished, finished)
        if offline:
            self.database.execute(
                """
                INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                VALUES('warning', 'offline', 'The Mac appeared offline; source failure streaks were not advanced.', ?, ?)
                """,
                (finished, Database.json({"source_count": len(network_failures)})),
            )
        return ScanSummary(scan_id, result, successes, failures, discovered, offline)

    @staticmethod
    def _definition_filtered_observations(
        source: dict[str, Any], observations: list[Observation]
    ) -> list[Observation]:
        try:
            definition = json.loads(str(source.get("definition_json") or "{}"))
        except json.JSONDecodeError:
            definition = {}
        include_terms = [str(term) for term in definition.get("include_terms", [])]
        exclude_terms = [str(term) for term in definition.get("exclude_terms", [])]

        def contains(text: str, term: str) -> bool:
            return bool(re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text.lower()))

        filtered: list[Observation] = []
        for observation in observations:
            text = f"{observation.title} {observation.summary}"
            if include_terms and not any(contains(text, term) for term in include_terms):
                continue
            if exclude_terms and any(contains(text, term) for term in exclude_terms):
                continue
            filtered.append(observation)
        return filtered

    @staticmethod
    def _within_interval(
        observations: list[Observation],
        start: str | None,
        end: str | None,
    ) -> list[Observation]:
        if not start and not end:
            return observations
        start_time = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
        end_time = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None
        return [
            item for item in observations
            if (not start_time or datetime.fromisoformat(item.published_at.replace("Z", "+00:00")) >= start_time)
            and (not end_time or datetime.fromisoformat(item.published_at.replace("Z", "+00:00")) <= end_time)
        ]

    def _incremental_observations(
        self,
        source: dict[str, Any],
        observations: list[Observation],
        start: str | None,
        end: str | None,
    ) -> list[Observation]:
        cursor_time: datetime | None = None
        cursor_external = ""
        if not start and source.get("cursor"):
            try:
                cursor = json.loads(str(source["cursor"]))
                cursor_time = datetime.fromisoformat(str(cursor["published_at"]).replace("Z", "+00:00"))
                cursor_external = str(cursor.get("external_id") or "")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                cursor_time = None
        if not start and cursor_time is None:
            start = (
                datetime.fromisoformat(self.now().replace("Z", "+00:00")) - timedelta(hours=72)
            ).isoformat().replace("+00:00", "Z")
        bounded = self._within_interval(observations, start, end)
        result: list[Observation] = []
        for item in bounded:
            published = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
            newer = cursor_time is None or (published, item.external_id) > (cursor_time, cursor_external)
            prior = self.database.one(
                "SELECT content_hash FROM raw_observation WHERE source_id = ? AND external_id = ?",
                (source["id"], item.external_id),
            )
            changed = bool(prior and prior["content_hash"] != item.content_hash)
            if newer or changed:
                result.append(item)
        return result

    @staticmethod
    def _due(source: dict[str, Any], now_value: str) -> bool:
        last_checked = source.get("state_last_checked_at")
        if not last_checked:
            return True
        checked = datetime.fromisoformat(str(last_checked).replace("Z", "+00:00"))
        now = datetime.fromisoformat(now_value.replace("Z", "+00:00"))
        return now - checked >= timedelta(minutes=int(source["minimum_interval_minutes"]))

    def _record_success(
        self,
        source: dict[str, Any],
        transaction_id: int,
        fetched: FetchResult,
        observations: list[Observation],
    ) -> int:
        observed_at = self.now()
        cursor = source.get("cursor")
        if observations:
            newest = max(observations, key=lambda item: (item.published_at, item.external_id))
            cursor = Database.json({"published_at": newest.published_at, "external_id": newest.external_id})
        changed = 0
        with self.database.transaction() as connection:
            recovered = str(source.get("state_health") or "") == "degraded" or int(source.get("state_failure_streak") or 0) >= 3
            for observation in observations:
                prior = connection.execute(
                    "SELECT content_hash FROM raw_observation WHERE source_id = ? AND external_id = ?",
                    (source["id"], observation.external_id),
                ).fetchone()
                material_update = bool(prior and prior["content_hash"] != observation.content_hash)
                connection.execute(
                    """
                    INSERT INTO raw_observation(
                        source_id, external_id, canonical_url, title, published_at,
                        observed_at, language, fingerprint, content_hash, metadata_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, external_id) DO UPDATE SET
                        canonical_url=excluded.canonical_url,
                        title=excluded.title,
                        published_at=excluded.published_at,
                        observed_at=excluded.observed_at,
                        language=excluded.language,
                        fingerprint=excluded.fingerprint,
                        content_hash=excluded.content_hash,
                        metadata_json=excluded.metadata_json
                    """,
                    (
                        source["id"], observation.external_id, observation.url,
                        observation.title, observation.published_at, observed_at,
                        observation.language, observation.fingerprint,
                        observation.content_hash, Database.json({"summary": observation.summary}),
                    ),
                )
                if not prior or material_update:
                    qualification = qualify(observation, source, observed_at=observed_at)
                    if qualification.relevant:
                        self._persist_story(
                            connection, source, observation, qualification, observed_at, material_update
                        )
                    changed += 1
            connection.execute(
                """
                UPDATE source_state
                SET cursor = ?, etag = ?, last_modified = ?, health = 'healthy',
                    failure_streak = 0, last_checked_at = ?, last_success_at = ?,
                    retry_after_at = NULL, last_error_class = NULL, last_error_detail = ''
                WHERE source_id = ?
                """,
                (
                    cursor, fetched.headers.get("etag"), fetched.headers.get("last-modified"),
                    observed_at, observed_at, source["id"],
                ),
            )
            connection.execute(
                """
                UPDATE source_registry
                SET cursor = ?, health = 'healthy', failure_streak = 0,
                    last_checked_at = ?, last_success_at = ?, lag_minutes = 0
                WHERE id = ?
                """,
                (cursor, observed_at, observed_at, source["id"]),
            )
            connection.execute(
                """
                UPDATE source_transaction
                SET status = ?, item_count = ?, cursor_after = ?, finished_at = ?
                WHERE id = ?
                """,
                ("not_modified" if fetched.not_modified else "success", len(observations), cursor, observed_at, transaction_id),
            )
            if recovered:
                connection.execute(
                    """
                    INSERT INTO alert(kind, severity, title, body, created_at)
                    VALUES('recovery', 'standard', ?, ?, ?)
                    """,
                    (
                        f"Source recovered: {source['name']}",
                        "The source is healthy again; cursor-based catch-up remains visible in scan history.",
                        observed_at,
                    ),
                )
        return changed

    def _record_failure(self, source: dict[str, Any], transaction_id: int, error: Exception) -> None:
        now = self.now()
        immediate = isinstance(error, (UnsafeRequest, AdapterError, ResponseTooLarge))
        error_class = type(error).__name__
        prior_streak = int(source.get("state_failure_streak") or 0)
        streak = prior_streak + 1
        health = "degraded" if immediate or streak >= 3 else "pending"
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE source_transaction
                SET status = 'failed', error_class = ?, finished_at = ? WHERE id = ?
                """,
                (error_class, now, transaction_id),
            )
            connection.execute(
                """
                UPDATE source_state
                SET health = ?, failure_streak = ?, last_checked_at = ?,
                    last_error_class = ?, last_error_detail = ?
                WHERE source_id = ?
                """,
                (health, streak, now, error_class, str(error)[:500], source["id"]),
            )
            connection.execute(
                """
                UPDATE source_registry
                SET health = ?, failure_streak = ?, last_checked_at = ?, detail = ?
                WHERE id = ?
                """,
                (health, streak, now, f"{error_class}: {str(error)[:300]}", source["id"]),
            )
            if health == "degraded":
                existing = connection.execute(
                    "SELECT 1 FROM alert WHERE kind = 'health' AND title = ? AND read_at IS NULL",
                    (f"Source degraded: {source['name']}",),
                ).fetchone()
                if not existing:
                    connection.execute(
                        """
                        INSERT INTO alert(kind, severity, title, body, created_at)
                        VALUES('health', ?, ?, ?, ?)
                        """,
                        (
                            "high" if immediate else "standard",
                            f"Source degraded: {source['name']}",
                            f"{error_class}; coverage from this source may be incomplete.",
                            now,
                        ),
                    )

    def _find_story(self, connection: Any, observation: Observation) -> dict[str, Any] | None:
        exact = connection.execute(
            """
            SELECT s.* FROM story_cluster s
            JOIN source_item i ON i.story_id = s.id
            WHERE i.canonical_url = ? OR i.fingerprint = ?
            ORDER BY s.first_public_at LIMIT 1
            """,
            (observation.url, observation.fingerprint),
        ).fetchone()
        if exact:
            return dict(exact)
        for row in connection.execute(
            "SELECT * FROM story_cluster ORDER BY first_public_at DESC LIMIT 200"
        ).fetchall():
            if _similarity(observation.title, row["headline"]) >= 0.72:
                return dict(row)
        return None

    def _persist_story(
        self,
        connection: Any,
        source: dict[str, Any],
        observation: Observation,
        qualification: Qualification,
        observed_at: str,
        material_update: bool,
    ) -> str:
        story = self._find_story(connection, observation)
        role = str(source["monitoring_role"])
        if story:
            story_id = str(story["id"])
        else:
            story_id = "story-" + hashlib.sha256(
                f"{observation.fingerprint}:{observation.published_at}".encode("utf-8")
            ).hexdigest()[:20]
            evidence_gate = role == "Event"
            status = "candidate" if evidence_gate and qualification.importance_gate else "watch" if role == "Discovery" and qualification.score >= 70 and qualification.impact_score >= 26 else "signal"
            priority = "High potential" if status == "watch" else qualification.priority
            slug = re.sub(r"[^a-z0-9]+", "-", observation.title.lower()).strip("-")[:100]
            connection.execute(
                """
                INSERT INTO story_cluster(
                    id, slug, headline, summary, lane, openness_class, status,
                    priority, priority_score, freshness, first_public_at, detected_at,
                    opportunity_strength, relevance_bridge, counterargument,
                    watch_expires_at, watch_status, material_update, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    story_id, slug or story_id, observation.title,
                    observation.summary or observation.title, qualification.lane,
                    qualification.openness_class, status, priority, qualification.score,
                    qualification.freshness, observation.published_at, observed_at,
                    qualification.opportunity_strength, qualification.relevance_bridge,
                    qualification.counterargument,
                    (datetime.fromisoformat(observed_at.replace("Z", "+00:00")) + timedelta(hours=24)).isoformat().replace("+00:00", "Z") if status == "watch" else None,
                    "Active" if status == "watch" else None,
                    int(material_update), observed_at, observed_at,
                ),
            )

        existing_item = connection.execute(
            "SELECT id, content_hash FROM source_item WHERE story_id = ? AND url = ?",
            (story_id, observation.url),
        ).fetchone()
        if existing_item:
            if material_update:
                connection.execute(
                    """
                    UPDATE source_item SET title = ?, published_at = ?, passage = ?,
                        content_hash = ?, first_seen_at = COALESCE(first_seen_at, ?)
                    WHERE id = ?
                    """,
                    (
                        observation.title, observation.published_at, observation.summary,
                        observation.content_hash, observed_at, existing_item["id"],
                    ),
                )
                connection.execute(
                    "UPDATE story_cluster SET material_update = 1, updated_at = ? WHERE id = ?",
                    (observed_at, story_id),
                )
                affected = connection.execute(
                    "UPDATE draft SET status = 'Needs Review', updated_at = ? WHERE story_id = ? AND status = 'Current'",
                    (observed_at, story_id),
                ).rowcount
                if affected:
                    existing_correction = connection.execute(
                        "SELECT 1 FROM alert WHERE story_id = ? AND kind = 'correction' AND read_at IS NULL",
                        (story_id,),
                    ).fetchone()
                    if not existing_correction:
                        connection.execute(
                            """
                            INSERT INTO alert(story_id, kind, severity, title, body, created_at)
                            VALUES(?, 'correction', 'high', ?, 'A material source update invalidated the current draft snapshot.', ?)
                            """,
                            (story_id, observation.title, observed_at),
                        )
            return story_id

        cursor = connection.execute(
            """
            INSERT INTO source_item(
                story_id, source_name, source_role, title, url, published_at,
                language, verification_status, passage, canonical_url,
                source_registry_id, fingerprint, content_hash, first_seen_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                story_id, source["name"], role, observation.title, observation.url,
                observation.published_at, observation.language,
                "supports" if role in {"Event", "Reporting"} else "trace",
                observation.summary, observation.url, source["id"],
                observation.fingerprint, observation.content_hash, observed_at,
            ),
        )
        source_item_id = int(cursor.lastrowid)
        claim_status = "verified" if role == "Event" else "attributed" if role == "Reporting" else "unverified"
        claim_cursor = connection.execute(
            "INSERT INTO claim(story_id, text, status, volatility) VALUES(?, ?, ?, 'volatile')",
            (story_id, observation.title, claim_status),
        )
        claim_id = int(claim_cursor.lastrowid)
        connection.execute(
            "INSERT INTO evidence_link(claim_id, source_item_id, relationship) VALUES(?, ?, ?)",
            (claim_id, source_item_id, "supports" if role in {"Event", "Reporting"} else "traces"),
        )
        connection.execute(
            "INSERT INTO story_membership(story_id, source_item_id, relationship, language) VALUES(?, ?, 'same-development', ?)",
            (story_id, source_item_id, observation.language),
        )
        first_public = connection.execute(
            "SELECT MIN(published_at) AS value FROM source_item WHERE story_id = ?",
            (story_id,),
        ).fetchone()["value"]
        legacy_evidence = connection.execute(
            "SELECT source_role, canonical_url, url FROM source_item WHERE story_id = ?",
            (story_id,),
        ).fetchall()
        manual_evidence = connection.execute(
            """
            SELECT confirmed_role, publisher_key FROM evidence_source
            WHERE story_id = ? AND status = 'confirmed'
            """,
            (story_id,),
        ).fetchall()
        event_count = sum(row["source_role"] == "Event" for row in legacy_evidence) + sum(
            row["confirmed_role"] == "Event" for row in manual_evidence
        )
        reporting_publishers = {
            publisher_key(str(row["canonical_url"] or row["url"]))
            for row in legacy_evidence
            if row["source_role"] == "Reporting"
        }
        reporting_publishers.update(
            str(row["publisher_key"])
            for row in manual_evidence
            if row["confirmed_role"] == "Reporting"
        )
        evidence_gate = event_count >= 1 or len(reporting_publishers) >= 2
        current = connection.execute("SELECT status, priority_score FROM story_cluster WHERE id = ?", (story_id,)).fetchone()
        new_status = current["status"]
        if evidence_gate and qualification.importance_gate and new_status in {"signal", "watch"}:
            new_status = "candidate"
        priority = qualification.priority if new_status != "watch" else "High potential"
        connection.execute(
            """
            UPDATE story_cluster SET first_public_at = ?, status = ?, priority = ?,
                priority_score = MAX(priority_score, ?), material_update = MAX(material_update, ?), updated_at = ?
            WHERE id = ?
            """,
            (first_public, new_status, priority, qualification.score, int(material_update), observed_at, story_id),
        )
        connection.execute(
            """
            INSERT INTO candidate(story_id, evidence_gate, importance_gate, score, score_json, qualified_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(story_id) DO UPDATE SET
                evidence_gate=excluded.evidence_gate,
                importance_gate=excluded.importance_gate,
                score=MAX(candidate.score, excluded.score),
                score_json=excluded.score_json,
                qualified_at=CASE WHEN excluded.evidence_gate = 1 AND excluded.importance_gate = 1 THEN excluded.qualified_at ELSE candidate.qualified_at END
            """,
            (
                story_id, int(evidence_gate), int(qualification.importance_gate),
                qualification.score, Database.json({"reasons": qualification.reasons, "impact": qualification.impact_score}),
                observed_at if evidence_gate and qualification.importance_gate else None,
            ),
        )
        if new_status == "watch":
            expires = (datetime.fromisoformat(observed_at.replace("Z", "+00:00")) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
            exists = connection.execute(
                "SELECT 1 FROM watch_notice WHERE story_id = ? AND status = 'active'", (story_id,)
            ).fetchone()
            if not exists:
                connection.execute(
                    """
                    INSERT INTO watch_notice(story_id, reason, trace_json, status, next_check_at, expires_at, created_at, updated_at)
                    VALUES(?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (
                        story_id, "High-potential public trace awaiting verification.",
                        Database.json({"source_id": source["id"], "url": observation.url}),
                        (datetime.fromisoformat(observed_at.replace("Z", "+00:00")) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
                        expires, observed_at, observed_at,
                    ),
                )
        if qualification.opportunity_strength in {"Strong", "Moderate"}:
            connection.execute(
                """
                INSERT INTO content_opportunity(
                    story_id, strength, relevance_bridge, mechanism, counterargument, assessment_json, assessed_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(story_id) DO UPDATE SET
                    strength=excluded.strength, relevance_bridge=excluded.relevance_bridge,
                    mechanism=excluded.mechanism, counterargument=excluded.counterargument,
                    assessment_json=excluded.assessment_json, assessed_at=excluded.assessed_at
                """,
                (
                    story_id, qualification.opportunity_strength, qualification.relevance_bridge,
                    qualification.mechanism, qualification.counterargument,
                    Database.json({"deterministic": True}), observed_at,
                ),
            )
        if new_status in {"candidate", "watch"} and (priority in {"Urgent", "High", "High potential"}):
            kind = "watch" if new_status == "watch" else "candidate"
            existing_alert = connection.execute(
                "SELECT 1 FROM alert WHERE story_id = ? AND kind = ? AND read_at IS NULL",
                (story_id, kind),
            ).fetchone()
            if not existing_alert:
                connection.execute(
                    """
                    INSERT INTO alert(story_id, kind, severity, title, body, created_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        story_id, kind, "high" if priority in {"Urgent", "High"} else "watch",
                        observation.title,
                        "Verified candidate ready for review." if kind == "candidate" else "Unverified high-potential signal under watch.",
                        observed_at,
                    ),
                )
        return story_id
