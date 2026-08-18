"""Automatic, bounded source research for discovery and fresh draft context."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit

from .assistance import (
    AssistanceConfigurationError,
    AssistanceDeferred,
    CodexInvoker,
    InvocationResult,
    _error_code,
    assistance_isolation_current,
)
from .evidence import (
    extract_page,
    normalize_public_https_url,
    publisher_display_name,
    publisher_identity_key,
    publisher_key,
)
from .network import SafeHttpClient
from .storage import Database


SEARCH_BUDGET_SECONDS = 30
MAX_SEARCH_SOURCES = 3
TERMINAL_RESEARCH_STATES = {"complete", "partial", "failed", "unavailable"}


class SearchInvoker(Protocol):
    def invoke_search(self, packet: dict[str, Any]) -> InvocationResult: ...


ClientFactory = Callable[[str], SafeHttpClient]


def _client_factory(host: str) -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts={host},
        connect_timeout=8,
        read_timeout=12,
        maximum_bytes=2_000_000,
        user_agent="OpenSourceAINewsWire/0.4.0 (+local research)",
    )


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _moment(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _distinctive_tokens(value: str) -> set[str]:
    stop = {
        "about", "after", "against", "artificial", "from", "into", "launches",
        "model", "models", "more", "news", "over", "release", "research", "says",
        "that", "their", "this", "using", "with", "will", "would", "intelligence",
    }
    return {
        token for token in re.findall(r"[a-z0-9][a-z0-9.+-]{2,}", value.casefold())
        if token not in stop and len(token) >= 4
    }


def _relevant(story: dict[str, Any], title: str, passage: str) -> bool:
    expected = _distinctive_tokens(str(story.get("headline") or ""))
    observed = _distinctive_tokens(f"{title} {passage}")
    overlap = expected & observed
    return bool(len(overlap) >= 2 or any(len(token) >= 8 for token in overlap))


def _publisher_url(value: str) -> tuple[str, str]:
    """Normalize a public publisher URL before the DNS safety boundary."""
    normalized = normalize_public_https_url(value)
    host = (urlsplit(normalized).hostname or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("Search results require a registered publisher host")
    else:
        raise ValueError("Search results cannot use IP-literal publishers")
    return normalized, host


def queue_research_attempt(
    database: Database,
    story_id: str,
    *,
    purpose: str,
    priority: int = 80,
) -> tuple[int, int]:
    """Create one durable attempt and work item.

    Draft refreshes are intentionally never idempotent across completed clicks;
    callers prevent only duplicate active content work.
    """
    if purpose not in {"background", "draft_refresh"}:
        raise ValueError("Unknown research purpose")
    now = _now()
    with database.transaction() as connection:
        story = connection.execute(
            "SELECT id, story_revision FROM story_cluster WHERE id = ?", (story_id,)
        ).fetchone()
        if not story:
            raise LookupError("Story not found")
        if purpose == "background":
            existing = connection.execute(
                """
                SELECT ra.id AS attempt_id, w.id AS work_id
                FROM research_attempt ra
                JOIN work_item w ON json_extract(w.payload_json, '$.research_attempt_id') = ra.id
                WHERE ra.story_id = ? AND ra.purpose = 'background'
                ORDER BY ra.id DESC LIMIT 1
                """,
                (story_id,),
            ).fetchone()
            if existing:
                return int(existing["attempt_id"]), int(existing["work_id"])
        cursor = connection.execute(
            """
            INSERT INTO research_attempt(story_id, purpose, status, created_at, updated_at)
            VALUES(?, ?, 'queued', ?, ?)
            """,
            (story_id, purpose, now, now),
        )
        attempt_id = int(cursor.lastrowid)
        work = connection.execute(
            """
            INSERT INTO work_item(
                kind, story_id, status, priority, payload_json, created_at,
                updated_at, idempotency_key, available_at
            ) VALUES('source_research', ?, 'queued', ?, ?, ?, ?, ?, ?)
            """,
            (
                story_id,
                max(0, min(100, int(priority))),
                Database.json({
                    "schema_version": 1,
                    "purpose": purpose,
                    "research_attempt_id": attempt_id,
                    "story_revision": int(story["story_revision"]),
                }),
                now,
                now,
                f"source-research:{purpose}:{story_id}:{attempt_id}",
                now,
            ),
        )
        return attempt_id, int(work.lastrowid)


class SourceResearchService:
    def __init__(
        self,
        database: Database,
        *,
        invoker: SearchInvoker | None = None,
        client_factory: ClientFactory = _client_factory,
        now: Callable[[], str] = _now,
    ) -> None:
        self.database = database
        self.invoker = invoker
        self.client_factory = client_factory
        self.now = now

    def process_next(
        self,
        work_item_id: int | None = None,
        *,
        deadline: datetime | None = None,
    ) -> int | None:
        parameters: tuple[Any, ...]
        identifier = ""
        if work_item_id is not None:
            identifier = "AND w.id = ?"
            parameters = (work_item_id,)
        else:
            parameters = ()
        work = self.database.one(
            f"""
            SELECT w.* FROM work_item w
            WHERE w.kind = 'source_research' AND w.status IN ('pending', 'queued')
              {identifier}
            ORDER BY w.priority DESC, w.created_at LIMIT 1
            """,
            parameters,
        )
        if not work:
            return None
        try:
            payload = json.loads(str(work.get("payload_json") or "{}"))
            attempt_id = int(payload.get("research_attempt_id") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            attempt_id = 0
        attempt = self.database.one(
            "SELECT * FROM research_attempt WHERE id = ?", (attempt_id,)
        )
        if not attempt or attempt["story_id"] != work.get("story_id"):
            self._finish(work, attempt_id, "failed", 0, "stale_research_work", "Research work was stale.")
            return attempt_id or None
        story = self.database.one(
            "SELECT * FROM story_cluster WHERE id = ?", (work["story_id"],)
        )
        if not story or story["status"] in {"archived", "withdrawn"}:
            self._finish(work, attempt_id, "failed", 0, "inactive_story", "Story is no longer active.")
            return attempt_id
        started = self.now()
        purpose = str(attempt["purpose"])
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE work_item SET status = 'running', attempt_count = attempt_count + 1,
                    updated_at = ? WHERE id = ?
                """,
                (started, work["id"]),
            )
            connection.execute(
                "UPDATE research_attempt SET status = 'running', started_at = ?, updated_at = ? WHERE id = ?",
                (started, started, attempt_id),
            )
            if purpose == "background":
                connection.execute(
                    "UPDATE story_cluster SET research_status = 'researching', updated_at = ? WHERE id = ?",
                    (started, story["id"]),
                )
        budget_deadline = datetime.now(UTC) + timedelta(seconds=SEARCH_BUDGET_SECONDS)
        if deadline is not None:
            normalized = deadline if deadline.tzinfo else deadline.replace(tzinfo=UTC)
            budget_deadline = min(budget_deadline, normalized.astimezone(UTC))
        try:
            if self.invoker is None and not assistance_isolation_current(self.database):
                raise AssistanceConfigurationError(
                    "assistance_isolation_unavailable: Reviewed isolation is not current"
                )
            invoker = self.invoker or CodexInvoker()
            known_publishers = self._known_publishers(str(story["id"]))
            packet = {
                "schema_version": 1,
                "operation": "source_search",
                "purpose": purpose,
                "headline": str(story["headline"])[:500],
                "summary": str(story["summary"])[:2000],
                "lane": str(story["lane"]),
                "first_public_at": str(story["first_public_at"]),
                "known_publishers": sorted(known_publishers),
                "maximum_results": MAX_SEARCH_SOURCES,
                "deadline_seconds": SEARCH_BUDGET_SECONDS,
            }
            invocation = invoker.invoke_search(packet)
            query = str(story["headline"])[:500]
            added, rejection_codes = self._attach_results(
                story,
                attempt_id,
                purpose,
                invocation.payload.get("results", []),
                known_publishers,
                budget_deadline,
            )
            status = "complete" if added else "partial"
            detail = (
                f"Attached {added} independent source{'s' if added != 1 else ''}."
                if added
                else "Search completed without an attachable independent source."
            )
            if rejection_codes:
                detail += " Rejected: " + ", ".join(sorted(set(rejection_codes))) + "."
            self._record_usage(invocation, "success")
            self._finish(work, attempt_id, status, added, None, detail, query=query)
        except AssistanceDeferred as error:
            code = _error_code(error)
            self._record_usage(None, code)
            self._finish(work, attempt_id, "unavailable", 0, code, "Search is unavailable because the account requires action.")
        except Exception as error:
            code = _error_code(error)
            self._record_usage(None, code)
            terminal = "unavailable" if code.startswith(("codex_", "assistance_")) else "failed"
            self._finish(work, attempt_id, terminal, 0, code, "The bounded source search could not complete.")
        return attempt_id

    def _known_publishers(self, story_id: str) -> set[str]:
        result: set[str] = set()
        for row in self.database.query(
            "SELECT COALESCE(canonical_url, url) AS url FROM source_item WHERE story_id = ?",
            (story_id,),
        ):
            try:
                result.add(publisher_key(str(row["url"])))
            except ValueError:
                continue
        for row in self.database.query(
            "SELECT canonical_url FROM evidence_source WHERE story_id = ? AND status = 'confirmed'",
            (story_id,),
        ):
            try:
                result.add(publisher_key(str(row["canonical_url"])))
            except ValueError:
                continue
        return result

    def _attach_results(
        self,
        story: dict[str, Any],
        attempt_id: int,
        purpose: str,
        results: list[dict[str, Any]],
        known_publishers: set[str],
        deadline: datetime,
    ) -> tuple[int, list[str]]:
        added = 0
        rejected: list[str] = []
        claim = self.database.one(
            "SELECT id FROM claim WHERE story_id = ? ORDER BY id LIMIT 1", (story["id"],)
        )
        for item in results[:MAX_SEARCH_SOURCES]:
            if datetime.now(UTC) >= deadline:
                rejected.append("timeout")
                break
            try:
                requested, host = _publisher_url(str(item.get("url") or ""))
                publisher = publisher_key(requested)
                if publisher in known_publishers:
                    rejected.append("duplicate_publisher")
                    continue
                with self.client_factory(host) as client:
                    fetched = client.fetch(requested, deadline=deadline)
                final = normalize_public_https_url(fetched.url)
                final_publisher = publisher_key(final)
                if final_publisher != publisher or final_publisher in known_publishers:
                    rejected.append("publisher_redirect_or_duplicate")
                    continue
                content_type = fetched.headers.get("content-type", "text/html").split(";", 1)[0].lower()
                page = extract_page(fetched.body, content_type)
                if not _relevant(story, page.title, page.passage):
                    rejected.append("irrelevant")
                    continue
                published = page.published_at or str(item.get("published_at") or "") or None
                published_moment = _moment(published)
                first_public = _moment(str(story.get("first_public_at") or ""))
                if published_moment and first_public and published_moment < first_public - timedelta(days=14):
                    rejected.append("stale")
                    continue
                now = self.now()
                provenance = "background_search" if purpose == "background" else "draft_search"
                publisher_name = page.hosting_publisher_name or str(item.get("publisher") or "") or publisher_display_name(final)
                with self.database.transaction() as connection:
                    cursor = connection.execute(
                        """
                        INSERT INTO evidence_source(
                            story_id, requested_url, final_url, canonical_url,
                            publisher_key, acquisition_method, title, passage,
                            published_at, language, proposed_role, confirmed_role,
                            first_party_confirmed, confirmation_reason, status,
                            fetched_at, confirmed_at, created_at, updated_at,
                            hosting_publisher_name, proposed_origin_name,
                            reporting_origin_name, reporting_origin_key,
                            provenance_type, origin_status, origin_confirmed_at,
                            research_attempt_id, source_provenance
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Reporting', 'Reporting',
                                 0, 'Automatic bounded public-source research', 'confirmed',
                                 ?, ?, ?, ?, ?, ?, ?, ?, 'original', 'confirmed', ?, ?, ?)
                        ON CONFLICT(story_id, canonical_url) DO NOTHING
                        """,
                        (
                            story["id"], requested, final, final, final_publisher,
                            provenance, page.title[:500], page.passage[:4000],
                            published, page.language, now, now, now, now,
                            publisher_name[:120], publisher_name[:120],
                            publisher_name[:120], publisher_identity_key(publisher_name),
                            now, attempt_id, provenance,
                        ),
                    )
                    if cursor.rowcount != 1:
                        rejected.append("duplicate_url")
                        continue
                    evidence_id = int(cursor.lastrowid)
                    if claim:
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO evidence_source_claim(
                                evidence_source_id, claim_id, relationship
                            ) VALUES(?, ?, 'context')
                            """,
                            (evidence_id, claim["id"]),
                        )
                known_publishers.add(final_publisher)
                added += 1
            except Exception as error:
                rejected.append(_error_code(error))
        return added, rejected

    def _record_usage(self, invocation: InvocationResult | None, result: str) -> None:
        self.database.execute(
            """
            INSERT INTO usage_ledger(
                category, operation, effort_units, model, result, created_at,
                prompt_version, input_size, output_size, retry_count
            ) VALUES('research', 'source_search', 1, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                invocation.model if invocation else "account-default",
                result,
                self.now(),
                "source-search-v1",
                invocation.input_size if invocation else 0,
                invocation.output_size if invocation else 0,
            ),
        )

    def _finish(
        self,
        work: dict[str, Any],
        attempt_id: int,
        status: str,
        result_count: int,
        error_class: str | None,
        detail: str,
        *,
        query: str = "",
    ) -> None:
        now = self.now()
        purpose = ""
        if attempt_id:
            attempt = self.database.one(
                "SELECT purpose FROM research_attempt WHERE id = ?", (attempt_id,)
            )
            purpose = str((attempt or {}).get("purpose") or "")
        with self.database.transaction() as connection:
            if attempt_id:
                connection.execute(
                    """
                    UPDATE research_attempt
                    SET status = ?, query = ?, result_count = ?, error_class = ?,
                        detail = ?, completed_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, query, result_count, error_class, detail, now, now, attempt_id),
                )
            connection.execute(
                """
                UPDATE work_item SET status = 'completed', last_error_class = ?,
                    available_at = NULL, updated_at = ? WHERE id = ?
                """,
                (error_class, now, work["id"]),
            )
            if purpose == "background":
                connection.execute(
                    """
                    UPDATE story_cluster
                    SET research_status = ?, priority_floor_applied = 1,
                        review_score = MAX(organic_score, 80),
                        priority_score = MAX(organic_score, 80), priority = 'Urgent',
                        verification_notice = 'Verify this yourself', updated_at = ?
                    WHERE id = ?
                    """,
                    (status, now, work["story_id"]),
                )
            elif purpose == "draft_refresh" and attempt_id:
                # Content starts in waiting so a recovery worker cannot draft
                # before this distinct fresh-search attempt reaches a terminal
                # state. Finishing the search makes the paired work claimable.
                connection.execute(
                    """
                    UPDATE work_item
                    SET status = 'queued', available_at = ?, updated_at = ?
                    WHERE kind = 'content' AND story_id = ? AND status = 'waiting'
                      AND json_extract(payload_json, '$.search_attempt_id') = ?
                    """,
                    (now, now, work["story_id"], attempt_id),
                )


def run_source_research(
    database: Database,
    work_item_id: int | None = None,
    *,
    deadline: datetime | None = None,
) -> int | None:
    """Production entry point used by the worker and content callback."""
    return SourceResearchService(database).process_next(
        work_item_id,
        deadline=deadline,
    )


def run_content_pipeline(database: Database, content_work_item_id: int) -> int | None:
    """Run a fresh source search, then draft from the latest stored packet."""
    content = database.one(
        "SELECT * FROM work_item WHERE id = ? AND kind = 'content'",
        (content_work_item_id,),
    )
    if not content:
        return None
    try:
        payload = json.loads(str(content.get("payload_json") or "{}"))
    except json.JSONDecodeError:
        payload = {}
    research_work_id = int(payload.get("search_work_item_id") or 0)
    if research_work_id:
        research_work = database.one(
            "SELECT status FROM work_item WHERE id = ? AND kind = 'source_research'",
            (research_work_id,),
        )
        if research_work and research_work["status"] not in {"completed", "cancelled"}:
            run_source_research(database, research_work_id)
    now = _now()
    with database.transaction() as connection:
        current = connection.execute(
            "SELECT story_revision FROM story_cluster WHERE id = ?",
            (content["story_id"],),
        ).fetchone()
        locked = connection.execute(
            "SELECT status, payload_json FROM work_item WHERE id = ? AND kind = 'content'",
            (content_work_item_id,),
        ).fetchone()
        if not locked or locked["status"] in {"completed", "cancelled"}:
            return content_work_item_id
        try:
            refreshed = json.loads(str(locked["payload_json"] or "{}"))
        except json.JSONDecodeError:
            refreshed = {}
        if current:
            refreshed["story_revision"] = int(current["story_revision"] or 1)
        connection.execute(
            """
            UPDATE work_item SET status = 'queued', payload_json = ?,
                available_at = ?, updated_at = ? WHERE id = ?
            """,
            (Database.json(refreshed), now, now, content_work_item_id),
        )
    from .assistance import run_assistance_work

    return run_assistance_work(database, content_work_item_id)
