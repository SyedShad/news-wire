"""Deterministic, human-confirmed evidence enrichment and qualification."""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from .adapters import canonical_url, parse_public_time
from .content_store import ContentStore
from .network import SafeHttpClient, UnsafeRequest
from .storage import Database


ALLOWED_ROLES = {"Event", "Reporting", "Discovery"}
ALLOWED_RELATIONSHIPS = {"supports", "attributes", "contradicts", "context"}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_public_https_url(value: str) -> str:
    raw = value.strip()
    if not raw or len(raw) > 2048:
        raise ValueError("Enter one bounded public HTTPS URL")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Evidence URLs must use public HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("Evidence URLs cannot contain credentials")
    if parsed.port not in {None, 443}:
        raise ValueError("Evidence URLs must use the standard HTTPS port")
    return canonical_url(raw, raw)


def publisher_key(value: str) -> str:
    host = (urlsplit(value).hostname or "").lower().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("Evidence URL has an invalid publisher host") from error
    labels = host.split(".")
    if len(labels) <= 2 or re.fullmatch(r"[0-9a-f:.]+", host, re.I):
        return host
    # Conservative publisher grouping: subdomains never count as independent.
    return ".".join(labels[-2:])


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    title: str
    passage: str
    published_at: str | None
    language: str


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.description = ""
        self.published = ""
        self.language = "und"
        self._in_title = False
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        lowered = tag.lower()
        if lowered == "html" and attributes.get("lang"):
            self.language = attributes["lang"][:16]
        if lowered == "title":
            self._in_title = True
        if lowered in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if lowered == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            content = attributes.get("content", "").strip()
            if key in {"description", "og:description", "twitter:description"} and not self.description:
                self.description = content
            if key in {"article:published_time", "date", "datepublished", "publishdate"} and not self.published:
                self.published = content

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = False
        if lowered in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if not clean:
            return
        if self._in_title:
            self.title_parts.append(clean)
        if not self._ignored_depth:
            self.text_parts.append(clean)


def extract_page(payload: bytes, content_type: str) -> ExtractedPage:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Evidence page is not valid UTF-8 text") from error
    if "html" not in content_type.lower():
        passage = " ".join(text.split())[:4000]
        return ExtractedPage("", passage, None, "und")
    parser = _PageParser()
    parser.feed(text)
    title = html.unescape(" ".join(parser.title_parts).strip())[:500]
    passage = html.unescape(parser.description or " ".join(parser.text_parts))
    passage = " ".join(passage.split())[:4000]
    published: str | None = None
    if parser.published:
        try:
            published = parse_public_time(parser.published, utc_now())
        except ValueError:
            published = None
    return ExtractedPage(title, passage, published, parser.language)


ClientFactory = Callable[[str], SafeHttpClient]


def _default_client_factory(host: str) -> SafeHttpClient:
    return SafeHttpClient(allowed_hosts={host}, maximum_bytes=2_000_000)


class EvidenceEnricher:
    def __init__(
        self,
        database: Database,
        *,
        client_factory: ClientFactory = _default_client_factory,
        now: Callable[[], str] = utc_now,
    ) -> None:
        self.database = database
        self.client_factory = client_factory
        self.now = now

    def process_next(self) -> int | None:
        work = self.database.one(
            """
            SELECT * FROM work_item
            WHERE kind = 'evidence_enrichment' AND status IN ('pending', 'queued')
            ORDER BY priority DESC, created_at LIMIT 1
            """
        )
        if not work:
            return None
        payload = json.loads(work.get("payload_json") or "{}")
        evidence_id = int(payload.get("evidence_source_id") or 0)
        evidence = self.database.one("SELECT * FROM evidence_source WHERE id = ?", (evidence_id,))
        if not evidence or evidence["story_id"] != work.get("story_id"):
            self._fail(work, evidence_id, ValueError("Evidence work item is stale"))
            return evidence_id
        self.database.execute(
            """
            UPDATE work_item SET status = 'running', attempt_count = attempt_count + 1,
                updated_at = ? WHERE id = ?
            """,
            (self.now(), work["id"]),
        )
        try:
            url = normalize_public_https_url(str(evidence["requested_url"]))
            host = (urlsplit(url).hostname or "").lower().rstrip(".")
            with self.client_factory(host) as client:
                fetched = client.fetch(url)
            final = normalize_public_https_url(fetched.url)
            if publisher_key(final) != publisher_key(url):
                raise UnsafeRequest("Evidence redirects cannot change publisher identity")
            content_type = fetched.headers.get("content-type", "text/html").split(";", 1)[0].lower()
            extracted = extract_page(fetched.body, content_type)
            if not extracted.title and not extracted.passage:
                raise ValueError("Evidence page did not contain usable public text")
            stored = ContentStore(self.database.paths.content).put(
                fetched.body, category="primary-snapshots"
            )
            now = self.now()
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO retained_content(digest, category, byte_count, media_type, source_url, created_at)
                    VALUES(?, 'primary-snapshots', ?, ?, ?, ?)
                    ON CONFLICT(digest) DO NOTHING
                    """,
                    (stored.digest, stored.size, content_type, final, now),
                )
                connection.execute(
                    """
                    UPDATE evidence_source SET final_url = ?, canonical_url = ?, publisher_key = ?,
                        title = ?, passage = ?, published_at = ?, language = ?, proposed_role = ?,
                        status = 'fetched', content_digest = ?, content_type = ?, fetched_at = ?,
                        error_class = NULL, updated_at = ? WHERE id = ?
                    """,
                    (
                        final, final, publisher_key(final), extracted.title or final,
                        extracted.passage, extracted.published_at, extracted.language,
                        "Event" if evidence["acquisition_method"] == "linked" else "Reporting",
                        stored.digest, content_type, now, now, evidence_id,
                    ),
                )
                connection.execute(
                    "UPDATE work_item SET status = 'completed', updated_at = ?, last_error_class = NULL WHERE id = ?",
                    (now, work["id"]),
                )
            return evidence_id
        except Exception as error:
            self._fail(work, evidence_id, error)
            return evidence_id

    def _fail(self, work: dict[str, Any], evidence_id: int, error: Exception) -> None:
        now = self.now()
        error_class = type(error).__name__
        with self.database.transaction() as connection:
            if evidence_id:
                connection.execute(
                    "UPDATE evidence_source SET status = 'failed', error_class = ?, updated_at = ? WHERE id = ?",
                    (error_class, now, evidence_id),
                )
            connection.execute(
                "UPDATE work_item SET status = 'failed', last_error_class = ?, updated_at = ? WHERE id = ?",
                (error_class, now, work["id"]),
            )
            connection.execute(
                """
                INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
                VALUES('warning', 'evidence_enrichment', 'A requested evidence page could not be inspected safely.', ?, ?)
                """,
                (now, Database.json({"error_class": error_class, "story_id": work.get("story_id")})),
            )


def _legacy_evidence(database: Database, story_id: str) -> tuple[bool, set[str]]:
    rows = database.query(
        "SELECT source_role, canonical_url, url FROM source_item WHERE story_id = ?",
        (story_id,),
    )
    has_event = any(row["source_role"] == "Event" for row in rows)
    reporting = {
        publisher_key(str(row.get("canonical_url") or row["url"]))
        for row in rows
        if row["source_role"] == "Reporting"
    }
    return has_event, reporting


def qualification_state(database: Database, story_id: str) -> dict[str, Any]:
    candidate = database.one("SELECT * FROM candidate WHERE story_id = ?", (story_id,)) or {}
    legacy_event, reporting = _legacy_evidence(database, story_id)
    confirmed = database.query(
        """
        SELECT confirmed_role, publisher_key FROM evidence_source
        WHERE story_id = ? AND status = 'confirmed'
        """,
        (story_id,),
    )
    has_event = legacy_event or any(row["confirmed_role"] == "Event" for row in confirmed)
    reporting.update(
        str(row["publisher_key"])
        for row in confirmed
        if row["confirmed_role"] == "Reporting"
    )
    evidence_gate = has_event or len(reporting) >= 2
    automated_importance = bool(candidate.get("importance_gate"))
    importance_override = bool(candidate.get("importance_override"))
    effective_importance = automated_importance or importance_override
    return {
        "evidence_gate": evidence_gate,
        "event_count": int(has_event),
        "reporting_count": len(reporting),
        "automated_importance": automated_importance,
        "importance_override": importance_override,
        "importance_override_reason": candidate.get("importance_override_reason", ""),
        "effective_importance": effective_importance,
        "qualified": evidence_gate and effective_importance,
    }


def recalculate_story_qualification(database: Database, story_id: str) -> dict[str, Any]:
    story = database.one("SELECT * FROM story_cluster WHERE id = ?", (story_id,))
    if not story:
        raise LookupError("Story not found")
    existing = database.one("SELECT * FROM candidate WHERE story_id = ?", (story_id,))
    if not existing:
        database.execute(
            """
            INSERT INTO candidate(story_id, evidence_gate, importance_gate, score, score_json)
            VALUES(?, 0, 0, ?, '{}')
            """,
            (story_id, int(story["priority_score"])),
        )
    state = qualification_state(database, story_id)
    now = utc_now()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE candidate SET evidence_gate = ? WHERE story_id = ?",
            (int(state["evidence_gate"]), story_id),
        )
        if state["qualified"] and story["status"] in {"signal", "watch"}:
            connection.execute(
                "UPDATE story_cluster SET status = 'candidate', updated_at = ? WHERE id = ?",
                (now, story_id),
            )
            connection.execute(
                "UPDATE candidate SET qualified_at = COALESCE(qualified_at, ?) WHERE story_id = ?",
                (now, story_id),
            )
        elif not state["evidence_gate"] and story["status"] in {"candidate", "approved", "draft_ready"}:
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
                UPDATE work_item SET status = 'needs_reapproval', last_error_class = 'evidence_invalidated',
                    updated_at = ? WHERE story_id = ? AND kind = 'draft'
                    AND status IN ('pending', 'queued', 'running', 'generating')
                """,
                (now, story_id),
            )
            connection.execute(
                "UPDATE draft SET status = 'Needs Review', updated_at = ? WHERE story_id = ? AND status = 'Current'",
                (now, story_id),
            )
            already_alerted = connection.execute(
                "SELECT 1 FROM alert WHERE story_id = ? AND kind = 'correction' AND read_at IS NULL",
                (story_id,),
            ).fetchone()
            if not already_alerted:
                connection.execute(
                    """
                    INSERT INTO alert(story_id, kind, severity, title, body, created_at)
                    VALUES(?, 'correction', 'high', ?, 'Confirmed evidence no longer passes the drafting gate.', ?)
                    """,
                    (story_id, story["headline"], now),
                )
    return qualification_state(database, story_id)


def recalculate_claim_statuses(database: Database, story_id: str) -> None:
    claims = database.query("SELECT id FROM claim WHERE story_id = ?", (story_id,))
    with database.transaction() as connection:
        for claim in claims:
            claim_id = int(claim["id"])
            legacy = connection.execute(
                """
                SELECT si.source_role, si.canonical_url, si.url, el.relationship
                FROM evidence_link el JOIN source_item si ON si.id = el.source_item_id
                WHERE el.claim_id = ?
                """,
                (claim_id,),
            ).fetchall()
            manual = connection.execute(
                """
                SELECT es.confirmed_role AS source_role, es.publisher_key, esc.relationship
                FROM evidence_source_claim esc JOIN evidence_source es ON es.id = esc.evidence_source_id
                WHERE esc.claim_id = ? AND es.status = 'confirmed'
                """,
                (claim_id,),
            ).fetchall()
            relationships = {str(row["relationship"]) for row in manual}
            if "contradicts" in relationships:
                status = "disputed"
            else:
                event_attribution = any(
                    row["source_role"] == "Event" and row["relationship"] in {"supports", "attributes"}
                    for row in legacy
                ) or any(
                    row["source_role"] == "Event" and row["relationship"] in {"supports", "attributes"}
                    for row in manual
                )
                reporting_publishers = {
                    publisher_key(str(row["canonical_url"] or row["url"]))
                    for row in legacy
                    if row["source_role"] == "Reporting" and row["relationship"] == "supports"
                }
                reporting_publishers.update(
                    str(row["publisher_key"])
                    for row in manual
                    if row["source_role"] == "Reporting" and row["relationship"] == "supports"
                )
                attributed = event_attribution or any(
                    row["relationship"] == "attributes" for row in manual
                )
                if len(reporting_publishers) >= 2:
                    status = "verified"
                elif attributed or reporting_publishers or any(row["source_role"] == "Reporting" for row in legacy):
                    status = "attributed"
                else:
                    status = "unverified"
            connection.execute("UPDATE claim SET status = ? WHERE id = ?", (status, claim_id))
