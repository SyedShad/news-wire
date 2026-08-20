from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import httpx

from open_source_ai_news_wire.adapters import Observation, notification_context
from open_source_ai_news_wire.assistance import InvocationResult
from open_source_ai_news_wire.collector import Collector
from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.hosted_bridge import LocalCommandExecutor, build_notification_projection
from open_source_ai_news_wire.network import FetchResult, SafeHttpClient
from open_source_ai_news_wire.qualification import qualify
from open_source_ai_news_wire.research import SourceResearchService
from open_source_ai_news_wire.services import DashboardService
from open_source_ai_news_wire.source_registry import synchronize_sources
from open_source_ai_news_wire.storage import Database, SCHEMA_VERSION


def _database(tmp_path: Path) -> Database:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    return database


def _insert_event(database: Database, *, story_id: str, suffix: str = "one") -> dict[str, object]:
    canonical_url = f"https://example.com/{suffix}"
    article_key = hashlib.sha256(canonical_url.encode()).hexdigest()
    with database.transaction() as connection:
        event = connection.execute(
            """
            INSERT INTO relevance_notification_event(
                article_key, story_id, canonical_url, title, publisher, category,
                context, provenance, source_type, published_at, detected_at, created_at
            ) VALUES(?, ?, ?, 'Open model release', 'Example', 'Open Ecosystem News',
                     'Example reports: The project released model weights. This context comes from the monitored publisher excerpt.',
                     'publisher_excerpt', 'article', '2026-08-20T09:00:00Z',
                     '2026-08-20T09:01:00Z', '2026-08-20T09:01:00Z')
            """,
            (article_key, story_id, canonical_url),
        )
        event_id = int(event.lastrowid)
        connection.execute(
            "INSERT INTO relevance_notification_outbox(event_id, updated_at) VALUES(?, '2026-08-20T09:01:00Z')",
            (event_id,),
        )
    return {"event_id": event_id, "article_key": article_key, "story_id": story_id}


def test_schema9_is_additive_off_by_default_and_does_not_backfill(tmp_path: Path) -> None:
    database = _database(tmp_path)
    seed_demo_data(database)

    assert database.schema_version() == SCHEMA_VERSION == 9
    assert database.one("SELECT COUNT(*) AS count FROM relevance_notification_event") == {"count": 0}
    state = DashboardService(database).relevance_notification_state()
    assert state["capture_enabled"] is True
    assert state["delivery_enabled"] is False
    assert state["shadow_mode"] is True
    assert state["native_enabled"] is False
    assert state["watermark"]


def test_article_fingerprint_normalizes_tracking_and_query_order() -> None:
    first = Observation(
        "one", "Same title", "https://EXAMPLE.com:443/item?b=2&utm_source=x&a=1#part",
        "2026-08-20T09:00:00Z",
    )
    second = Observation(
        "two", "Changed title", "https://example.com/item?a=1&b=2",
        "2026-08-20T09:00:00Z",
    )
    distinct = Observation(
        "three", "Same title", "https://example.com/other?a=1&b=2",
        "2026-08-20T09:00:00Z",
    )

    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != distinct.fingerprint


def test_relevant_new_urls_emit_individual_neutral_events(tmp_path: Path) -> None:
    database = _database(tmp_path)
    synchronize_sources(database)
    database.set_state(
        "relevance_notification_watermark", "2026-08-20T08:00:00Z", "2026-08-20T08:00:00Z"
    )
    source = database.one("SELECT * FROM source_registry WHERE id = 'openai-news'")
    collector = Collector(database)
    title = "Open-source AI model release publishes weights and security details"
    for index in (1, 2):
        item = Observation(
            str(index), title, f"https://openai.com/news/release-{index}",
            "2026-08-20T09:00:00Z",
            "The publisher released model weights and documented the public security evaluation.",
        )
        result = qualify(item, source, observed_at="2026-08-20T09:01:00Z")
        assert result.relevant is True
        with database.transaction() as connection:
            collector._persist_story(
                connection, source, item, result, "2026-08-20T09:01:00Z", 1, "scheduled"
            )

    events = database.query(
        "SELECT title, context, canonical_url FROM relevance_notification_event ORDER BY id"
    )
    assert len(events) == 2
    assert len({event["canonical_url"] for event in events}) == 2
    assert all("reports:" in event["context"] for event in events)
    assert database.one("SELECT COUNT(*) AS count FROM story_cluster") == {"count": 1}


def test_relevance_is_the_only_gate_across_locked_eligibility_classes(tmp_path: Path) -> None:
    database = _database(tmp_path)
    synchronize_sources(database)
    database.set_state(
        "relevance_notification_watermark", "2026-08-20T08:00:00Z", "2026-08-20T08:00:00Z"
    )
    base_source = database.one("SELECT * FROM source_registry WHERE id = 'openai-news'")
    collector = Collector(database)
    observed_at = "2026-08-20T09:01:00Z"
    base_observation = Observation(
        "base", "Open-source AI model publishes weights",
        "https://openai.com/news/base", "2026-08-20T09:00:00Z",
        "The publisher released model weights for public use.",
    )
    base_qualification = qualify(base_observation, base_source, observed_at=observed_at)
    assert base_qualification.relevant is True

    profiles = [
        ("standard", {}, {"priority": "Standard", "score": 20, "importance_gate": False}),
        ("older", {}, {"freshness": "Older", "priority": "Standard", "importance_gate": False}),
        ("unverified", {"monitoring_role": "Discovery"}, {"priority": "Standard", "importance_gate": False}),
        ("research-required", {"trust_class": "research_required"}, {"priority": "Standard", "importance_gate": False}),
        (
            "discovery",
            {"monitoring_role": "Discovery", "family": "Aggregation", "trust_class": "research_required"},
            {"priority": "Standard", "importance_gate": False},
        ),
    ]
    for name, source_changes, qualification_changes in profiles:
        source = {**base_source, **source_changes}
        observation = Observation(
            name,
            f"Open-source AI model publishes weights ({name})",
            f"https://openai.com/news/{name}",
            "2026-08-18T09:00:00Z" if name == "older" else "2026-08-20T09:00:00Z",
            "The monitored source reports public model weights.",
            metadata={"notification_provenance": "discovery_metadata"} if name == "discovery" else {},
        )
        qualification = replace(base_qualification, **qualification_changes)
        with database.transaction() as connection:
            collector._persist_story(
                connection, source, observation, qualification, observed_at, 1, "scheduled"
            )

    irrelevant = replace(base_qualification, relevant=False, priority="Urgent", importance_gate=True)
    with database.transaction() as connection:
        collector._persist_story(
            connection,
            base_source,
            Observation(
                "irrelevant", "Office renovation update",
                "https://openai.com/news/irrelevant", "2026-08-20T09:00:00Z",
            ),
            irrelevant,
            observed_at,
            1,
            "scheduled",
        )

    events = database.query(
        "SELECT canonical_url, source_type, provenance FROM relevance_notification_event ORDER BY id"
    )
    assert len(events) == len(profiles)
    assert {event["canonical_url"].rsplit("/", 1)[-1] for event in events} == {
        name for name, _source, _qualification in profiles
    }
    assert any(event["source_type"] == "discovery" for event in events)
    assert any(event["provenance"] == "discovery_metadata" for event in events)


def test_thin_relevant_context_fetches_once_but_irrelevant_does_not() -> None:
    calls: list[str] = []

    class Client:
        def fetch(self, url: str, **_kwargs) -> FetchResult:
            calls.append(url)
            return FetchResult(
                url,
                200,
                {"content-type": "text/html"},
                b"<html><head><title>Release</title><meta property='og:site_name' content='Publisher'></head><body><p>The project released open model weights and a technical report for public use.</p></body></html>",
            )

    source = {
        "name": "Feed",
        "family": "Official AI organizations",
        "monitoring_role": "Event",
    }
    relevant = Observation(
        "relevant", "Open-source AI model releases public weights",
        "https://example.com/relevant", "2026-08-20T09:00:00Z",
    )
    irrelevant = Observation(
        "irrelevant", "Company publishes an ordinary office update",
        "https://example.com/irrelevant", "2026-08-20T09:00:00Z",
    )
    collector = object.__new__(Collector)
    collector.now = lambda: "2026-08-20T09:01:00Z"
    hydrated = collector._hydrate_notification_context(  # type: ignore[arg-type]
        Client(), source, [relevant, irrelevant], deadline=None
    )

    assert calls == [relevant.url]
    context, provenance, _source_type = notification_context(hydrated[0], source)
    assert provenance == "publisher_excerpt"
    assert context.startswith("Publisher reports:")
    assert len(context) <= 700


def test_context_strips_markup_and_uses_an_honest_limited_fallback() -> None:
    source = {"name": "Publisher", "family": "Public newsletters", "monitoring_role": "Reporting"}
    marked_up = Observation(
        "markup", "Open model release", "https://example.com/markup",
        "2026-08-20T09:00:00Z", "<p>The project published model weights.</p>",
    )
    context, provenance, _source_type = notification_context(marked_up, source)
    assert "<p>" not in context
    assert "The project published model weights." in context
    assert provenance == "publisher_excerpt"

    missing = Observation(
        "missing", "Open model release notes", "https://example.com/missing",
        "2026-08-20T09:00:00Z",
    )
    fallback, fallback_provenance, _source_type = notification_context(missing, source)
    assert fallback_provenance == "limited_context"
    assert "Only limited context was available" in fallback
    assert "Open model release notes" in fallback
    assert len(fallback) <= 700


class _SearchInvoker:
    def invoke_search(self, _packet):
        return InvocationResult(
            {
                "schema_version": 1,
                "operation": "source_search",
                "results": [{
                    "url": "https://publisher.example/report",
                    "title": "Independent transparent inference runtime report",
                    "publisher": "Publisher",
                    "published_at": "2026-08-20T09:00:00Z",
                    "snippet": "Independent report on signed reproducible runtime builds.",
                }],
                "notes": "",
            },
            "test", 10, 10,
        )


def _research_client(host: str) -> SafeHttpClient:
    body = b"<html><head><title>Transparent inference runtime supply-chain report</title><meta name='description' content='Independent coverage of signed reproducible runtime builds and supply-chain audit metadata.'><meta property='og:site_name' content='Publisher'></head></html>"
    return SafeHttpClient(
        allowed_hosts={host},
        resolver=lambda _host: ["93.184.216.34"],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=body, headers={"content-type": "text/html"}, request=request
            )
        ),
    )


def test_start_research_is_terminal_idempotent_and_never_creates_draft(tmp_path: Path) -> None:
    database = _database(tmp_path)
    seed_demo_data(database)
    event = _insert_event(database, story_id="story-demo-runtime-001")
    before_drafts = database.one("SELECT COUNT(*) AS count FROM draft")
    service = DashboardService(database)
    service.research_callback = lambda work_id: SourceResearchService(
        database, invoker=_SearchInvoker(), client_factory=_research_client
    ).process_next(work_id)

    first = service.start_notification_research(**event)  # type: ignore[arg-type]
    second = service.start_notification_research(**event)  # type: ignore[arg-type]

    assert first == second
    assert first["status"] == "complete", first
    assert first["result_count"] == 1
    assert first["publishers"] == ["Publisher"]
    assert database.one(
        "SELECT COUNT(*) AS count FROM research_attempt WHERE purpose = 'operator_review'"
    ) == {"count": 1}
    assert database.one(
        "SELECT COUNT(*) AS count FROM work_item WHERE kind IN ('content', 'draft')"
    ) == {"count": 0}
    assert database.one("SELECT COUNT(*) AS count FROM draft") == before_drafts


def test_dismiss_is_global_idempotent_notification_only_and_bridge_shape_is_clean(tmp_path: Path) -> None:
    database = _database(tmp_path)
    seed_demo_data(database)
    event = _insert_event(database, story_id="story-demo-runtime-001")
    service = DashboardService(database)
    before = database.one(
        "SELECT status FROM story_cluster WHERE id = 'story-demo-runtime-001'"
    )

    first = service.dismiss_notification_article(**event)  # type: ignore[arg-type]
    second = service.dismiss_notification_article(**event)  # type: ignore[arg-type]
    projection, digest = build_notification_projection(service)
    command = LocalCommandExecutor(service).execute(
        "article_alert.dismiss",
        {**event, "event_id": str(event["event_id"])},
    )[0]

    assert first == second == command
    assert database.one(
        "SELECT status FROM story_cluster WHERE id = 'story-demo-runtime-001'"
    ) == before
    assert projection and len(digest) == 64
    assert set(projection[0]) == {
        "event_id", "article_key", "story_id", "canonical_url", "title",
        "publisher", "category", "context", "provenance", "source_type",
        "published_at", "detected_at",
    }
    assert not ({"priority", "score", "quality", "importance"} & set(projection[0]))
    database.set_state(
        "relevance_notifications_enabled", "true", "2026-08-20T09:02:00Z"
    )
    database.set_state(
        "relevance_native_notifications_enabled", "true", "2026-08-20T09:02:00Z"
    )
    database.set_state(
        "relevance_notification_watermark", "2026-08-20T08:00:00Z", "2026-08-20T09:02:00Z"
    )
    assert service.list_pending_native_relevance_events() == []
