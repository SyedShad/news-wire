"""Deterministic incremental source collection and qualification."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from .adapters import (
    AdapterError,
    Observation,
    enrich_huggingnews_detail,
    parse_huggingnews_momentum,
    parse_source,
)
from .network import (
    FetchResult,
    NetworkDeadlineExceeded,
    NetworkUnavailable,
    ResponseTooLarge,
    SafeHttpClient,
    UnsafeRequest,
    resolve_known_short_url,
)
from .qualification import Qualification, qualify
from .evidence import publisher_key
from .settings import load_settings
from .source_registry import synchronize_sources
from .storage import Database
from .revisions import atomic_claim_signature


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


def _editorial_passage(observation: Observation) -> str:
    return str(observation.metadata.get("material_summary", observation.summary))


def _signature(value: object) -> str:
    return hashlib.sha256(Database.json(value).encode("utf-8")).hexdigest()


class Collector:
    def __init__(
        self,
        database: Database,
        *,
        client_factory: ClientFactory = _default_client_factory,
        now: Callable[[], str] = utc_now,
        short_link_resolver: Callable[[str], str] = resolve_known_short_url,
    ):
        self.database = database
        self.client_factory = client_factory
        self.now = now
        self.short_link_resolver = short_link_resolver

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
                        deadline=deadline,
                    )
                    observations = [] if fetched.not_modified else parse_source(
                        str(source["adapter"]),
                        fetched.body,
                        source_url=fetched.url,
                        observed_at=self.now(),
                    )
                    observations = self._contextualize_release_titles(source, observations)
                    observations = self._contextualize_native_metrics(source, observations)
                    observations = self._definition_filtered_observations(source, observations)
                    if source["adapter"] == "huggingnews_json" and observations:
                        observations = self._huggingnews_momentum(
                            client, observations, deadline=deadline
                        )
                    observations = self._incremental_observations(
                        source, observations, interval_start, interval_end
                    )
                    if source["adapter"] == "huggingnews_json" and observations:
                        observations = self._huggingnews_details(
                            client, source, observations, deadline=deadline
                        )
                new_count = self._record_success(
                    source, transaction_id, fetched, observations, trigger=trigger
                )
                successes += 1
                discovered += new_count
            except NetworkDeadlineExceeded as error:
                self.database.execute(
                    """
                    UPDATE source_transaction
                    SET status = 'deadline', error_class = ?, finished_at = ? WHERE id = ?
                    """,
                    (type(error).__name__, self.now(), transaction_id),
                )
                backlog = len(sources) - index
                break
            except (NetworkUnavailable, httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as error:
                network_failures.append((source, transaction_id, error))
            except Exception as error:
                self._record_failure(source, transaction_id, error)
                failures += 1

        offline = bool(
            network_failures and successes == 0 and failures == 0 and backlog == 0
        )
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
                INSERT INTO app_state(key, value, updated_at) VALUES('network_offline_active', 'true', ?)
                ON CONFLICT(key) DO UPDATE SET value='true', updated_at=excluded.updated_at
                """,
                (finished,),
            )
            if self.database.get_state("network_offline_alerted", "false") != "true":
                with self.database.transaction() as connection:
                    connection.execute(
                        """
                        INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                        VALUES('warning', 'offline', 'The Mac appeared offline; source failure streaks were not advanced.', ?, ?)
                        """,
                        (finished, Database.json({"source_count": len(network_failures)})),
                    )
                    connection.execute(
                        """
                        INSERT INTO alert(kind, severity, title, body, created_at)
                        VALUES('health', 'high', 'News Wire is offline', 'Collection paused without advancing source failure streaks.', ?)
                        """,
                        (finished,),
                    )
                self.database.set_state("network_offline_alerted", "true", finished)
        elif successes and self.database.get_state("network_offline_active", "false") == "true":
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                    VALUES('info', 'network_recovery', 'Network collection recovered after an offline interval.', ?, '{}')
                    """,
                    (finished,),
                )
                connection.execute(
                    """
                    INSERT INTO alert(kind, severity, title, body, created_at)
                    VALUES('recovery', 'high', 'News Wire is back online', 'Collection resumed and source health will recover through normal scans.', ?)
                    """,
                    (finished,),
                )
            self.database.set_state("network_offline_active", "false", finished)
            self.database.set_state("network_offline_alerted", "false", finished)
        return ScanSummary(scan_id, result, successes, failures, discovered, offline)

    @staticmethod
    def _contextualize_release_titles(
        source: dict[str, Any], observations: list[Observation]
    ) -> list[Observation]:
        path = urlsplit(str(source.get("url") or "")).path.rstrip("/")
        if "github.com" not in str(source.get("url") or "") or not path.endswith("releases.atom"):
            return observations
        segments = path.split("/")
        project = segments[-2].replace("-", " ").strip().title() if len(segments) >= 2 else str(source.get("name") or "Project")
        contextualized: list[Observation] = []
        for item in observations:
            title = item.title
            version = re.search(r"\bv?\d+(?:\.\d+){1,3}(?:[-.][a-z0-9]+)?\b", title, re.IGNORECASE)
            generic = bool(re.match(r"^(?:patch\s+)?release\s*:|^v?\d+(?:\.\d+)+\b", title, re.IGNORECASE))
            if generic and version:
                title = f"{project} {version.group(0)} released"
            contextualized.append(
                Observation(
                    item.external_id,
                    title,
                    item.url,
                    item.published_at,
                    item.summary,
                    item.language,
                    item.metadata,
                )
            )
        return contextualized

    @staticmethod
    def _contextualize_native_metrics(
        source: dict[str, Any], observations: list[Observation]
    ) -> list[Observation]:
        if source.get("id") != "hacker-news-ai":
            return observations
        enriched: list[Observation] = []
        for item in observations:
            points_match = re.search(r"\bPoints:\s*(\d+)", item.summary, re.IGNORECASE)
            comments_match = re.search(r"#\s*Comments:\s*(\d+)", item.summary, re.IGNORECASE)
            points = int(points_match.group(1)) if points_match else 0
            comments = int(comments_match.group(1)) if comments_match else 0
            metadata = dict(item.metadata)
            metadata.update(
                {
                    "points": points,
                    "comments": comments,
                    "native_score": float(points + comments * 2),
                    "material_summary": re.sub(
                        r"\s*Points:\s*\d+\s*#\s*Comments:\s*\d+\s*$",
                        "",
                        item.summary,
                        flags=re.IGNORECASE,
                    ).strip(),
                }
            )
            enriched.append(
                Observation(
                    item.external_id,
                    item.title,
                    item.url,
                    item.published_at,
                    item.summary,
                    item.language,
                    metadata,
                )
            )
        return enriched

    def _huggingnews_momentum(
        self,
        client: SafeHttpClient,
        observations: list[Observation],
        *,
        deadline: datetime | None = None,
    ) -> list[Observation]:
        try:
            visible = client.fetch("https://huggingnews.com/", deadline=deadline)
            metrics = parse_huggingnews_momentum(visible.body)
        except Exception:
            metrics = {}
        enriched: list[Observation] = []
        for item in observations:
            metadata = dict(item.metadata)
            metadata.update(metrics.get(item.external_id, {}))
            posts = int(metadata.get("post_count") or 0)
            accounts = int(metadata.get("account_count") or 0)
            if posts or accounts:
                metadata["native_score"] = float(accounts * 2 + posts)
            enriched.append(
                Observation(
                    item.external_id,
                    item.title,
                    item.url,
                    item.published_at,
                    item.summary,
                    item.language,
                    metadata,
                )
            )
        return enriched

    def _huggingnews_details(
        self,
        client: SafeHttpClient,
        source: dict[str, Any],
        observations: list[Observation],
        *,
        deadline: datetime | None = None,
    ) -> list[Observation]:
        enriched: list[Observation] = []
        short_link_attempts = 0
        for index, item in enumerate(observations):
            if deadline and datetime.now(UTC) >= deadline:
                enriched.extend(observations[index:])
                break
            prior = self.database.one(
                "SELECT content_hash, metadata_json FROM raw_observation WHERE source_id = ? AND external_id = ?",
                (source["id"], item.external_id),
            )
            prior_metadata: dict[str, Any] = {}
            if prior:
                try:
                    prior_metadata = json.loads(str(prior.get("metadata_json") or "{}"))
                except json.JSONDecodeError:
                    prior_metadata = {}
            needs_detail = (
                not prior
                or prior.get("content_hash") != item.content_hash
                or not prior_metadata.get("detail_fetched")
            )
            if not needs_detail or index >= 25:
                enriched.append(item)
                continue
            try:
                detail = client.fetch(
                    f"https://api.huggingnews.com/api/stories/{item.external_id}",
                    deadline=deadline,
                )
                detailed = enrich_huggingnews_detail(item, detail.body)
                metadata = dict(detailed.metadata)
                public_links: list[dict[str, str]] = []
                for short_url in metadata.get("short_links", []):
                    if deadline and datetime.now(UTC) >= deadline:
                        break
                    if short_link_attempts >= 12:
                        break
                    short_link_attempts += 1
                    try:
                        resolver_parameters = inspect.signature(
                            self.short_link_resolver
                        ).parameters.values()
                        accepts_deadline = any(
                            parameter.name == "deadline"
                            or parameter.kind is inspect.Parameter.VAR_KEYWORD
                            for parameter in resolver_parameters
                        )
                        final_url = (
                            self.short_link_resolver(
                                str(short_url), deadline=deadline
                            )
                            if accepts_deadline
                            else self.short_link_resolver(str(short_url))
                        )
                    except Exception:
                        continue
                    if urlsplit(final_url).hostname in {
                        "x.com", "www.x.com", "twitter.com", "www.twitter.com",
                    }:
                        continue
                    public_links.append(
                        {"short_url": str(short_url), "url": final_url}
                    )
                metadata["public_links"] = public_links
                enriched.append(
                    Observation(
                        detailed.external_id,
                        detailed.title,
                        detailed.url,
                        detailed.published_at,
                        detailed.summary,
                        detailed.language,
                        metadata,
                    )
                )
            except Exception:
                enriched.append(item)
        return enriched

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
                """
                SELECT content_hash, observation_hash_version, atomic_claim_signature,
                       metadata_json FROM raw_observation
                WHERE source_id = ? AND external_id = ?
                """,
                (source["id"], item.external_id),
            )
            changed = bool(
                prior
                and (
                    prior["content_hash"] != item.content_hash
                    or int(prior.get("observation_hash_version") or 1)
                    != item.observation_hash_version
                    or str(prior.get("atomic_claim_signature") or "")
                    != item.atomic_claim_signature
                )
            )
            metadata_changed = bool(
                prior
                and str(prior.get("metadata_json") or "{}")
                != Database.json({"summary": item.summary, **item.metadata})
            )
            if newer or changed or metadata_changed:
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
        *,
        trigger: str,
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
                prior_row = connection.execute(
                    """
                    SELECT * FROM raw_observation
                    WHERE source_id = ? AND (external_id = ? OR fingerprint = ?)
                    ORDER BY CASE WHEN external_id = ? THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (
                        source["id"], observation.external_id,
                        observation.fingerprint, observation.external_id,
                    ),
                ).fetchone()
                # Some discovery feeds republish the same headline under a new
                # item ID. Preserve the first stored observation, advance the
                # source cursor below, and do not replay the story or evidence.
                if prior_row and prior_row["external_id"] != observation.external_id:
                    continue
                prior = dict(prior_row) if prior_row else None
                metadata_payload = {"summary": observation.summary, **observation.metadata}
                previous_metadata: dict[str, Any] = {}
                if prior:
                    try:
                        previous_metadata = json.loads(str(prior.get("metadata_json") or "{}"))
                    except json.JSONDecodeError:
                        previous_metadata = {}
                observation_changed = bool(
                    not prior
                    or prior.get("content_hash") != observation.content_hash
                    or int(prior.get("observation_hash_version") or 1)
                    != observation.observation_hash_version
                    or str(prior.get("atomic_claim_signature") or "")
                    != observation.atomic_claim_signature
                    or str(prior.get("canonical_url") or "") != observation.url
                    or str(prior.get("published_at") or "") != observation.published_at
                    or str(prior.get("metadata_json") or "") != Database.json(metadata_payload)
                )
                legacy_hash_rebase = bool(
                    prior
                    and int(prior.get("observation_hash_version") or 1)
                    != observation.observation_hash_version
                    and not str(prior.get("atomic_claim_signature") or "")
                )
                previous_material_summary = str(
                    previous_metadata.get("material_summary", previous_metadata.get("summary", ""))
                )
                editorial_changed = bool(
                    not prior
                    or (
                        not legacy_hash_rebase
                        and (
                            str(prior.get("title") or "") != observation.title
                            or previous_material_summary != _editorial_passage(observation)
                            or str(prior.get("canonical_url") or "") != observation.url
                        )
                    )
                )
                atomic_claim_changed = bool(
                    prior
                    and str(prior.get("atomic_claim_signature") or "")
                    and str(prior.get("atomic_claim_signature"))
                    != observation.atomic_claim_signature
                )
                connection.execute(
                    """
                    INSERT INTO raw_observation(
                        source_id, external_id, canonical_url, title, published_at,
                        observed_at, language, fingerprint, content_hash, metadata_json,
                        observation_hash_version, atomic_claim_signature,
                        source_reported_at, aggregator_published_at,
                        effective_published_at, timestamp_status
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, external_id) DO UPDATE SET
                        canonical_url=excluded.canonical_url,
                        title=excluded.title,
                        published_at=excluded.published_at,
                        observed_at=excluded.observed_at,
                        language=excluded.language,
                        fingerprint=excluded.fingerprint,
                        content_hash=excluded.content_hash,
                        metadata_json=excluded.metadata_json,
                        observation_hash_version=excluded.observation_hash_version,
                        atomic_claim_signature=excluded.atomic_claim_signature,
                        source_reported_at=excluded.source_reported_at,
                        aggregator_published_at=excluded.aggregator_published_at,
                        effective_published_at=excluded.effective_published_at,
                        timestamp_status=excluded.timestamp_status
                    """,
                    (
                        source["id"], observation.external_id, observation.url,
                        observation.title, observation.published_at, observed_at,
                        observation.language, observation.fingerprint,
                        observation.content_hash, Database.json(metadata_payload),
                        observation.observation_hash_version,
                        observation.atomic_claim_signature,
                        observation.source_reported_at,
                        observation.aggregator_published_at,
                        observation.effective_published_at,
                        observation.timestamp_status,
                    ),
                )
                raw_id = int(
                    connection.execute(
                        "SELECT id FROM raw_observation WHERE source_id = ? AND external_id = ?",
                        (source["id"], observation.external_id),
                    ).fetchone()["id"]
                )
                source_revision_number = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(revision_number), 0) AS value FROM source_revision
                        WHERE source_id = ? AND external_id = ?
                        """,
                        (source["id"], observation.external_id),
                    ).fetchone()["value"]
                )
                if observation_changed:
                    source_revision_number += 1
                    connection.execute(
                        """
                        INSERT INTO source_revision(
                            raw_observation_id, source_id, external_id, revision_number,
                            observation_hash_version, observation_hash,
                            atomic_claim_signature, canonical_url, title,
                            source_reported_at, aggregator_published_at,
                            effective_published_at, timestamp_status, observed_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            raw_id, source["id"], observation.external_id,
                            source_revision_number, observation.observation_hash_version,
                            observation.content_hash, observation.atomic_claim_signature,
                            observation.url, observation.title,
                            observation.source_reported_at,
                            observation.aggregator_published_at,
                            observation.effective_published_at,
                            observation.timestamp_status, observed_at,
                        ),
                    )
                if legacy_hash_rebase:
                    message = f"Rebased observation hash without editorial update: {source['id']}:{observation.external_id}"
                    if not connection.execute(
                        "SELECT 1 FROM diagnostic_event WHERE event_type = 'hash_rebase' AND message = ?",
                        (message,),
                    ).fetchone():
                        connection.execute(
                            """
                            INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                            VALUES('info', 'hash_rebase', ?, ?, ?)
                            """,
                            (
                                message, observed_at,
                                Database.json({
                                    "source_id": source["id"],
                                    "external_id": observation.external_id,
                                    "previous_hash_version": int(prior.get("observation_hash_version") or 1),
                                    "current_hash_version": observation.observation_hash_version,
                                }),
                            ),
                        )
                if "future" in observation.timestamp_status or "malformed" in observation.timestamp_status:
                    message = f"Publication timestamp fallback: {source['id']}:{observation.external_id}:{observation.timestamp_status}"
                    if not connection.execute(
                        "SELECT 1 FROM diagnostic_event WHERE event_type = 'publication_timestamp_fallback' AND message = ?",
                        (message,),
                    ).fetchone():
                        connection.execute(
                            """
                            INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                            VALUES('warning', 'publication_timestamp_fallback', ?, ?, ?)
                            """,
                            (
                                message, observed_at,
                                Database.json({
                                    "source_id": source["id"],
                                    "external_id": observation.external_id,
                                    "source_reported_at": observation.source_reported_at,
                                    "aggregator_published_at": observation.aggregator_published_at,
                                    "effective_published_at": observation.effective_published_at,
                                    "timestamp_status": observation.timestamp_status,
                                }),
                            ),
                        )
                story_id: str | None = None
                if not prior or editorial_changed:
                    existing_story = self._find_story(connection, observation) if not prior else None
                    qualification = qualify(
                        observation,
                        source,
                        observed_at=observed_at,
                        novelty=8 if atomic_claim_changed else 3 if existing_story else 10,
                    )
                    if qualification.relevant:
                        story_id = self._persist_story(
                            connection,
                            source,
                            observation,
                            qualification,
                            observed_at,
                            source_revision_number,
                            ingestion_context=(
                                "recovery" if trigger == "recovery" else
                                "extended" if trigger == "extended" else
                                "scheduled"
                            ),
                        )
                    changed += int(observation_changed)
                else:
                    existing_story = self._find_story(connection, observation)
                    story_id = str(existing_story["id"]) if existing_story else None
                if story_id:
                    self._record_discovery_leads(
                        connection, story_id, source, observation, observed_at
                    )
                    self._record_momentum_snapshot(
                        connection, story_id, source, observation, observed_at
                    )
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

    @staticmethod
    def _record_discovery_leads(
        connection: Any,
        story_id: str,
        source: dict[str, Any],
        observation: Observation,
        observed_at: str,
    ) -> None:
        leads = observation.metadata.get("selected_tweets", [])
        if not isinstance(leads, list):
            return
        for lead in leads:
            if not isinstance(lead, dict) or not lead.get("url"):
                continue
            identity = str(lead.get("author_handle") or "unknown").lower()[:120]
            external_id = str(lead.get("external_id") or lead["url"])
            connection.execute(
                """
                INSERT INTO discovery_lead(
                    story_id, via_source_id, external_id, identity_key,
                    display_name, url, published_at, lead_type,
                    metadata_json, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'public_post', ?, ?, ?)
                ON CONFLICT(via_source_id, external_id) DO UPDATE SET
                    story_id=excluded.story_id,
                    identity_key=excluded.identity_key,
                    display_name=excluded.display_name,
                    url=excluded.url,
                    published_at=excluded.published_at,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    story_id,
                    source["id"],
                    external_id,
                    identity,
                    f"@{identity}" if identity != "unknown" else "Public source post",
                    str(lead["url"]),
                    str(lead.get("published_at") or observation.published_at),
                    Database.json({
                        "text": str(lead.get("text") or "")[:4000],
                        "quoted_text": str(lead.get("quoted_text") or "")[:4000],
                        "discovery_via": source["name"],
                    }),
                    observed_at,
                    observed_at,
                ),
            )
        public_links = observation.metadata.get("public_links", [])
        if not isinstance(public_links, list):
            return
        for link in public_links:
            if not isinstance(link, dict) or not link.get("url"):
                continue
            final_url = str(link["url"])
            external_id = hashlib.sha256(final_url.encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO discovery_lead(
                    story_id, via_source_id, external_id, identity_key,
                    display_name, url, published_at, lead_type,
                    metadata_json, created_at, updated_at
                ) VALUES(?, ?, ?, ?, 'Public link discovered via HuggingNews', ?, ?, 'public_link', ?, ?, ?)
                ON CONFLICT(via_source_id, external_id) DO UPDATE SET
                    story_id=excluded.story_id, url=excluded.url,
                    metadata_json=excluded.metadata_json, updated_at=excluded.updated_at
                """,
                (
                    story_id,
                    source["id"],
                    external_id,
                    (urlsplit(final_url).hostname or "unknown").lower(),
                    final_url,
                    observation.published_at,
                    Database.json({
                        "short_url": str(link.get("short_url") or ""),
                        "discovery_via": source["name"],
                    }),
                    observed_at,
                    observed_at,
                ),
            )
            evidence = connection.execute(
                "SELECT id FROM evidence_source WHERE story_id = ? AND canonical_url = ?",
                (story_id, final_url),
            ).fetchone()
            if evidence:
                continue
            evidence_cursor = connection.execute(
                """
                INSERT INTO evidence_source(
                    story_id, requested_url, canonical_url, publisher_key,
                    acquisition_method, proposed_role, status, created_at, updated_at
                ) VALUES(?, ?, ?, ?, 'discovery_enrichment', 'Reporting', 'queued', ?, ?)
                """,
                (
                    story_id,
                    final_url,
                    final_url,
                    publisher_key(final_url),
                    observed_at,
                    observed_at,
                ),
            )
            evidence_id = int(evidence_cursor.lastrowid)
            idempotency_key = "evidence:" + story_id + ":" + hashlib.sha256(
                final_url.encode("utf-8")
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO work_item(
                    kind, story_id, status, priority, payload_json, created_at,
                    updated_at, idempotency_key, available_at
                ) VALUES('evidence_enrichment', ?, 'queued', 70, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
                """,
                (
                    story_id,
                    Database.json({"schema_version": 1, "evidence_source_id": evidence_id}),
                    observed_at,
                    observed_at,
                    idempotency_key,
                    observed_at,
                ),
            )

    @staticmethod
    def _record_momentum_snapshot(
        connection: Any,
        story_id: str,
        source: dict[str, Any],
        observation: Observation,
        observed_at: str,
    ) -> None:
        metadata = observation.metadata
        post_count = int(metadata.get("post_count") or 0)
        account_count = max(
            int(metadata.get("account_count") or 0),
            int(metadata.get("distinct_identity_count") or 0),
        )
        daily_rank = int(metadata.get("daily_rank") or 0)
        native_score = float(metadata.get("native_score") or 0)
        if not any((post_count, account_count, daily_rank, native_score)):
            return
        connection.execute(
            """
            INSERT INTO momentum_snapshot(
                story_id, source_id, captured_at, native_score, post_count,
                account_count, daily_rank, distinct_identity_count, metadata_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(story_id, source_id, captured_at) DO UPDATE SET
                native_score=excluded.native_score,
                post_count=excluded.post_count,
                account_count=excluded.account_count,
                daily_rank=excluded.daily_rank,
                distinct_identity_count=excluded.distinct_identity_count,
                metadata_json=excluded.metadata_json
            """,
            (
                story_id,
                source["id"],
                observed_at,
                native_score,
                post_count or None,
                account_count or None,
                daily_rank or None,
                account_count,
                Database.json({"adapter": source["adapter"]}),
            ),
        )

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

    @staticmethod
    def _story_claim_set_signature(connection: Any, story_id: str) -> str:
        rows = connection.execute(
            "SELECT text FROM claim WHERE story_id = ? ORDER BY id", (story_id,)
        ).fetchall()
        return atomic_claim_signature(str(row["text"]) for row in rows)

    @staticmethod
    def _story_source_set_signature(connection: Any, story_id: str) -> str:
        rows = connection.execute(
            """
            SELECT id, source_role, verification_status,
                   COALESCE(canonical_url, url) AS canonical_url, title, passage
            FROM source_item WHERE story_id = ? ORDER BY id
            """,
            (story_id,),
        ).fetchall()
        return _signature(
            [
                {
                    "id": int(row["id"]),
                    "role": str(row["source_role"]),
                    "verification_status": str(row["verification_status"]),
                    "canonical_url": str(row["canonical_url"]),
                    "title": str(row["title"]),
                    "passage": str(row["passage"]),
                }
                for row in rows
            ]
        )

    @staticmethod
    def _apply_story_revision_change(
        connection: Any,
        story_id: str,
        *,
        before_claim_signature: str,
        before_source_signature: str,
        observed_at: str,
        headline: str,
    ) -> None:
        after_claim_signature = Collector._story_claim_set_signature(connection, story_id)
        after_source_signature = Collector._story_source_set_signature(connection, story_id)
        claim_changed = before_claim_signature != after_claim_signature
        source_changed = before_source_signature != after_source_signature
        if not claim_changed and not source_changed:
            return
        connection.execute(
            """
            UPDATE story_cluster
            SET story_revision = story_revision + 1,
                material_revision = material_revision + ?,
                material_update = CASE WHEN ? = 1 THEN 1 ELSE material_update END,
                material_updated_at = CASE WHEN ? = 1 THEN ? ELSE material_updated_at END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                int(claim_changed), int(claim_changed), int(claim_changed),
                observed_at, observed_at, story_id,
            ),
        )
        connection.execute(
            """
            UPDATE work_item
            SET status = 'needs_reapproval', last_error_class = 'story_revision_changed',
                updated_at = ?
            WHERE story_id = ? AND kind = 'draft'
              AND status IN ('pending', 'queued', 'running', 'generating', 'waiting')
            """,
            (observed_at, story_id),
        )
        affected = connection.execute(
            "UPDATE draft SET status = 'Needs Review', updated_at = ? WHERE story_id = ? AND status = 'Current'",
            (observed_at, story_id),
        ).rowcount
        if affected and not connection.execute(
            "SELECT 1 FROM alert WHERE story_id = ? AND kind = 'correction' AND read_at IS NULL",
            (story_id,),
        ).fetchone():
            connection.execute(
                """
                INSERT INTO alert(story_id, kind, severity, title, body, created_at)
                VALUES(?, 'correction', 'high', ?,
                       'Approved claim or source content changed; the draft requires renewed approval.', ?)
                """,
                (story_id, headline, observed_at),
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
        source_revision_number: int,
        ingestion_context: str,
    ) -> str:
        story = self._find_story(connection, observation)
        created_story = story is None
        role = str(source["monitoring_role"])
        if story:
            story_id = str(story["id"])
            before_claim_signature = self._story_claim_set_signature(connection, story_id)
            before_source_signature = self._story_source_set_signature(connection, story_id)
        else:
            story_id = "story-" + hashlib.sha256(
                f"{observation.fingerprint}:{observation.published_at}".encode("utf-8")
            ).hexdigest()[:20]
            evidence_gate = role == "Event"
            status = "candidate" if evidence_gate and qualification.importance_gate else "watch" if role == "Discovery" and qualification.score >= 65 and qualification.impact_score >= 26 else "signal"
            priority = "High potential" if status == "watch" else qualification.priority
            slug = re.sub(r"[^a-z0-9]+", "-", observation.title.lower()).strip("-")[:100]
            connection.execute(
                """
                INSERT INTO story_cluster(
                    id, slug, headline, summary, lane, openness_class, status,
                    priority, priority_score, freshness, first_public_at, detected_at,
                    opportunity_strength, relevance_bridge, counterargument,
                    watch_expires_at, watch_status, material_update, created_at, updated_at,
                    importance_score, importance_json, material_updated_at, ingestion_context,
                    story_revision, material_revision
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    0, observed_at, observed_at,
                    qualification.importance_score,
                    Database.json({
                        "material_importance": qualification.impact_score,
                        "novelty": qualification.novelty_score,
                        "source_significance": qualification.source_significance,
                        "total": qualification.importance_score,
                    }),
                    None,
                    ingestion_context,
                    1,
                    1,
                ),
            )
            before_claim_signature = atomic_claim_signature([])
            before_source_signature = _signature([])

        existing_item = connection.execute(
            """
            SELECT id, content_hash FROM source_item
            WHERE story_id = ? AND (
                url = ? OR (source_registry_id = ? AND fingerprint = ?)
            ) ORDER BY id LIMIT 1
            """,
            (story_id, observation.url, source["id"], observation.fingerprint),
        ).fetchone()
        if existing_item:
            connection.execute(
                """
                UPDATE source_item SET title = ?, url = ?, canonical_url = ?,
                    published_at = ?, passage = ?,
                    content_hash = ?, first_seen_at = COALESCE(first_seen_at, ?),
                    source_revision = ?, source_reported_at = ?,
                    aggregator_published_at = ?, effective_published_at = ?,
                    timestamp_status = ?
                WHERE id = ?
                """,
                (
                    observation.title, observation.url, observation.url,
                    observation.effective_published_at,
                    _editorial_passage(observation), observation.content_hash,
                    observed_at, max(1, source_revision_number),
                    observation.source_reported_at,
                    observation.aggregator_published_at,
                    observation.effective_published_at,
                    observation.timestamp_status, existing_item["id"],
                ),
            )
            linked_claim = connection.execute(
                """
                SELECT c.id FROM claim c
                JOIN evidence_link e ON e.claim_id = c.id
                WHERE e.source_item_id = ? ORDER BY c.id LIMIT 1
                """,
                (existing_item["id"],),
            ).fetchone()
            if linked_claim:
                connection.execute(
                    "UPDATE claim SET text = ? WHERE id = ?",
                    (observation.title, linked_claim["id"]),
                )
            self._apply_story_revision_change(
                connection,
                story_id,
                before_claim_signature=before_claim_signature,
                before_source_signature=before_source_signature,
                observed_at=observed_at,
                headline=observation.title,
            )
            return story_id

        cursor = connection.execute(
            """
            INSERT INTO source_item(
                story_id, source_name, source_role, title, url, published_at,
                language, verification_status, passage, canonical_url,
                source_registry_id, fingerprint, content_hash, first_seen_at,
                source_revision, source_reported_at, aggregator_published_at,
                effective_published_at, timestamp_status
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                story_id, source["name"], role, observation.title, observation.url,
                observation.effective_published_at, observation.language,
                "supports" if role in {"Event", "Reporting"} else "trace",
                _editorial_passage(observation), observation.url, source["id"],
                observation.fingerprint, observation.content_hash, observed_at,
                max(1, source_revision_number), observation.source_reported_at,
                observation.aggregator_published_at,
                observation.effective_published_at, observation.timestamp_status,
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
            "SELECT MIN(effective_published_at) AS value FROM source_item WHERE story_id = ?",
            (story_id,),
        ).fetchone()["value"]
        legacy_evidence = connection.execute(
            "SELECT source_role, canonical_url, url FROM source_item WHERE story_id = ?",
            (story_id,),
        ).fetchall()
        manual_evidence = connection.execute(
            """
            SELECT confirmed_role, reporting_origin_key, origin_status FROM evidence_source
            WHERE story_id = ? AND status = 'confirmed'
            """,
            (story_id,),
        ).fetchall()
        event_count = sum(row["source_role"] == "Event" for row in legacy_evidence) + sum(
            row["confirmed_role"] == "Event" for row in manual_evidence
        )
        reporting_publishers = {
            str(row["reporting_origin_key"])
            for row in manual_evidence
            if row["confirmed_role"] == "Reporting"
            and row["origin_status"] == "confirmed"
            and row["reporting_origin_key"]
        }
        evidence_gate = event_count >= 1 or len(reporting_publishers) >= 2
        current = connection.execute("SELECT status, priority_score, importance_score FROM story_cluster WHERE id = ?", (story_id,)).fetchone()
        new_status = current["status"]
        if evidence_gate and qualification.importance_gate and new_status in {"signal", "watch"}:
            new_status = "candidate"
        priority = qualification.priority if new_status != "watch" else "High potential"
        connection.execute(
            """
            UPDATE story_cluster SET first_public_at = ?, status = ?, priority = ?,
                priority_score = MAX(priority_score, ?),
                importance_score = MAX(importance_score, ?), importance_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                first_public, new_status, priority, qualification.score,
                qualification.importance_score,
                Database.json({
                    "material_importance": qualification.impact_score,
                    "novelty": qualification.novelty_score,
                    "source_significance": qualification.source_significance,
                    "total": qualification.importance_score,
                }),
                observed_at, story_id,
            ),
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
                qualification.importance_score, Database.json({
                    "reasons": qualification.reasons,
                    "impact": qualification.impact_score,
                    "importance": qualification.importance_score,
                    "ranking_version": 2,
                }),
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
        if not created_story:
            self._apply_story_revision_change(
                connection,
                story_id,
                before_claim_signature=before_claim_signature,
                before_source_signature=before_source_signature,
                observed_at=observed_at,
                headline=observation.title,
            )
        return story_id
