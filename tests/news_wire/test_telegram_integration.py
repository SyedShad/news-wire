from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from open_source_ai_news_wire.adapters import Observation
from open_source_ai_news_wire.collector import Collector
from open_source_ai_news_wire.config import ensure_runtime_layout, resolve_runtime_paths
from open_source_ai_news_wire.hosted_bridge import build_notification_projection
from open_source_ai_news_wire.network import SafeHttpClient
from open_source_ai_news_wire.qualification import qualify
from open_source_ai_news_wire.services import DashboardService
from open_source_ai_news_wire.storage import MIGRATIONS, SCHEMA_V1, SCHEMA_VERSION, Database
from open_source_ai_news_wire.telegram import TelegramDispatcher, TelegramService
from open_source_ai_news_wire.source_registry import synchronize_sources


PUBLIC_IP = lambda _host: ["93.184.216.34"]


def _database(tmp_path: Path) -> Database:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    return database


def _database_with_openai_source(tmp_path: Path) -> Database:
    database = _database(tmp_path)
    synchronize_sources(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE source_registry SET enabled = CASE WHEN id = 'openai-news' THEN 1 ELSE 0 END"
        )
        connection.execute(
            "UPDATE source_state SET enabled = CASE WHEN source_id = 'openai-news' THEN 1 ELSE 0 END"
        )
    return database


def _feed(items: list[tuple[str, str, str | None]]) -> bytes:
    rows = []
    for external_id, title, published_at in items:
        date = f"<pubDate>{published_at}</pubDate>" if published_at else ""
        rows.append(
            f"<item><guid>{external_id}</guid><title>{title}</title>"
            f"<link>https://openai.com/news/{external_id}</link>"
            f"{date}<description>The publisher reports a public model release with security details."
            "</description></item>"
        )
    return ("<rss><channel>" + "".join(rows) + "</channel></rss>").encode()


class _ScanClock:
    value = "2026-09-15T09:00:00Z"

    def __call__(self) -> str:
        return self.value


class _TelegramFixture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, method: str, payload: dict[str, object], *, timeout: float) -> dict[str, object]:
        self.calls.append((method, payload))
        assert timeout > 0
        return {"message_id": 77}


def test_rss_collector_event_reaches_telegram_fixture_without_backfill(tmp_path: Path) -> None:
    database = _database_with_openai_source(tmp_path)
    database.set_state(
        "relevance_notification_watermark", "2026-09-15T08:00:00Z", "2026-09-15T08:00:00Z"
    )
    clock = _ScanClock()
    body = {
        "value": _feed(
            [("one", "Open-source AI model release improves inference security", "Tue, 15 Sep 2026 08:30:00 GMT")]
        )
    }

    def factory(source: dict[str, object]) -> SafeHttpClient:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=body["value"],
                headers={"content-type": "application/rss+xml"},
                request=request,
            )
        )
        return SafeHttpClient(
            allowed_hosts=set(json.loads(str(source["base_hosts_json"]))),
            resolver=PUBLIC_IP,
            transport=transport,
        )

    collector = Collector(database, client_factory=factory, now=clock)
    assert collector.scan(trigger="manual").result == "success"
    first = database.one(
        "SELECT id, published_at, source_reported_at, timestamp_status FROM relevance_notification_event"
    )
    assert first["published_at"] == "2026-09-15T08:30:00Z"
    assert first["source_reported_at"] == "2026-09-15T08:30:00Z"
    assert first["timestamp_status"] == "valid"

    service = TelegramService(database)
    service.pair(-100123, "Wire team", 123, "wire_bot")
    service.mark_tested()
    service.enable()
    assert service.pending() == []
    # Browser delivery is off and its watermark moves into the future. Telegram
    # must continue capturing independently, including the activation second.
    database.set_state("relevance_notifications_enabled", "false", clock.value)
    database.set_state("hosted_dashboard_enabled", "false", clock.value)
    database.set_state("relevance_notification_watermark", "2027-01-01T00:00:00Z", clock.value)

    # A later undated RSS item is captured and will still be sent.
    clock.value = "2026-09-15T09:05:00Z"
    body["value"] = _feed(
        [
            ("one", "Open-source AI model release improves inference security", "Tue, 15 Sep 2026 08:30:00 GMT"),
            ("two", "Open-source AI model release adds a security benchmark", None),
        ]
    )
    assert collector.scan(trigger="manual").result == "success"

    # The next source-dated item is newer than the cursor and is deliverable too.
    clock.value = "2026-09-15T09:10:00Z"
    body["value"] = _feed(
        [
            ("one", "Open-source AI model release improves inference security", "Tue, 15 Sep 2026 08:30:00 GMT"),
            ("two", "Open-source AI model release adds a security benchmark", None),
            ("three", "Open-source AI model release publishes a safer runtime", "Tue, 15 Sep 2026 09:06:00 GMT"),
        ]
    )
    assert collector.scan(trigger="manual").result == "success"

    undated = database.one(
        """
        SELECT id, source_reported_at, timestamp_status
        FROM relevance_notification_event
        WHERE article_key = (
            SELECT article_key FROM relevance_notification_event ORDER BY id LIMIT 1 OFFSET 1
        )
        """
    )
    assert undated["source_reported_at"] is None
    assert undated["timestamp_status"] == "missing_source_fallback_detection"

    fixture = _TelegramFixture()
    dispatcher = TelegramDispatcher(
        database,
        token_provider=lambda: "test-token",
        client_factory=lambda _token: fixture,
        now=lambda: datetime(2026, 9, 15, 9, 10, tzinfo=UTC),
        monotonic_clock=lambda: 0.0,
        sleep=lambda _seconds: None,
    )
    result = dispatcher.dispatch()
    assert result["status"] == "sent"
    assert result["delivered"] == 2
    assert result["skipped"] == 0
    assert [call[0] for call in fixture.calls] == ["sendMessage", "sendMessage"]
    undated_payload, dated_payload = [call[1] for call in fixture.calls]
    assert undated_payload["chat_id"] == -100123
    assert "Open-source AI model release adds a security benchmark" in str(undated_payload["text"])
    assert "Fetched:" in str(undated_payload["text"])
    assert "Original publication date: not verified" in str(undated_payload["text"])
    assert "Published:" not in str(undated_payload["text"])
    assert "Open-source AI model release publishes a safer runtime" in str(dated_payload["text"])
    assert "Fetched:" in str(dated_payload["text"])
    assert "Published: 2026-09-15T09:06:00Z" in str(dated_payload["text"])
    assert "Source: https://openai.com/news/three" in str(dated_payload["text"])
    assert dated_payload["allow_paid_broadcast"] is False
    assert database.one(
        "SELECT status FROM relevance_telegram_delivery WHERE event_id = ?", (undated["id"],)
    ) == {"status": "delivered"}


@pytest.mark.parametrize("ingestion_context", ["recovery", "extended"])
def test_old_recovery_items_reach_telegram_without_browser_notification(
    tmp_path: Path, ingestion_context: str
) -> None:
    database = _database_with_openai_source(tmp_path)
    observed_at = "2026-09-15T12:00:00Z"
    database.set_state("relevance_notification_watermark", "2026-09-16T00:00:00Z", observed_at)
    source = database.one("SELECT * FROM source_registry WHERE id = 'openai-news'")
    observation = Observation(
        "old-recovery-item",
        "Open-source AI model release publishes public weights",
        "https://openai.com/news/old-recovery-item",
        "2026-09-10T12:00:00Z",
        "The publisher released open model weights and a security evaluation.",
        metadata={
            "source_reported_at": "2026-09-10T12:00:00Z",
            "effective_published_at": "2026-09-10T12:00:00Z",
            "timestamp_status": "valid",
        },
    )
    qualification = qualify(observation, source, observed_at=observed_at)
    assert qualification.relevant

    telegram = TelegramService(database)
    telegram.pair(-100123, "Wire team", 123, "wire_bot")
    telegram.mark_tested()
    telegram.enable()
    with database.transaction() as connection:
        collector = Collector(database)
        collector._persist_story(
            connection,
            source,
            observation,
            qualification,
            observed_at,
            1,
            ingestion_context,
        )

    event = database.one(
        "SELECT id, published_at FROM relevance_notification_event WHERE canonical_url = ?",
        (observation.url,),
    )
    assert event == {"id": 1, "published_at": "2026-09-10T12:00:00Z"}
    assert database.one(
        "SELECT event_id FROM relevance_notification_outbox WHERE event_id = ?", (event["id"],)
    ) is None
    projection, _digest = build_notification_projection(DashboardService(database))
    assert projection == []

    fixture = _TelegramFixture()
    result = TelegramDispatcher(
        database,
        token_provider=lambda: "test-token",
        client_factory=lambda _token: fixture,
        now=lambda: datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
        monotonic_clock=lambda: 0.0,
        sleep=lambda _seconds: None,
    ).dispatch()
    assert result["delivered"] == 1
    assert "Published: 2026-09-10T12:00:00Z" in str(fixture.calls[0][1]["text"])


@pytest.mark.parametrize("ingestion_context", ["recovery", "extended"])
def test_old_recovery_item_keeps_existing_age_policy_when_telegram_is_disabled(
    tmp_path: Path, ingestion_context: str
) -> None:
    database = _database_with_openai_source(tmp_path)
    observed_at = "2026-09-15T12:00:00Z"
    database.set_state("relevance_notification_watermark", "2026-09-15T11:00:00Z", observed_at)
    source = database.one("SELECT * FROM source_registry WHERE id = 'openai-news'")
    observation = Observation(
        "old-disabled-recovery-item",
        "Open-source AI model release publishes public weights",
        "https://openai.com/news/old-disabled-recovery-item",
        "2026-09-10T12:00:00Z",
        "The publisher released open model weights and a security evaluation.",
        metadata={
            "source_reported_at": "2026-09-10T12:00:00Z",
            "effective_published_at": "2026-09-10T12:00:00Z",
            "timestamp_status": "valid",
        },
    )
    qualification = qualify(observation, source, observed_at=observed_at)
    assert qualification.relevant
    with database.transaction() as connection:
        Collector(database)._persist_story(
            connection,
            source,
            observation,
            qualification,
            observed_at,
            1,
            ingestion_context,
        )

    assert database.one("SELECT COUNT(*) AS count FROM relevance_notification_event") == {"count": 0}


def test_schema9_migration_preserves_existing_event_as_unknown_without_replay(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(tmp_path / "wire-data")
    ensure_runtime_layout(paths)
    article_key = "a" * 64
    with sqlite3.connect(paths.database) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA_V1)
        for version in range(2, 10):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '9')")
        connection.execute(
            """
            INSERT INTO relevance_notification_event(
                article_key, story_id, canonical_url, title, publisher, category,
                context, provenance, source_type, published_at, detected_at, created_at
            ) VALUES(?, 'legacy-story', 'https://example.com/legacy', 'Legacy AI release',
                     'Example', 'Open Ecosystem News', 'Publisher context',
                     'publisher_excerpt', 'article', '2026-09-14T08:00:00Z',
                     '2026-09-14T08:01:00Z', '2026-09-14T08:01:00Z')
            """,
            (article_key,),
        )
        connection.execute(
            "UPDATE app_state SET value = '2026-09-14T08:00:00Z' WHERE key = 'relevance_notification_watermark'"
        )
        connection.commit()

    database = Database(paths)
    assert database.migrate() == SCHEMA_VERSION == 11
    legacy = database.one(
        "SELECT source_reported_at, timestamp_status FROM relevance_notification_event WHERE article_key = ?",
        (article_key,),
    )
    assert legacy == {"source_reported_at": None, "timestamp_status": "unknown"}
    assert database.one(
        "SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table' AND name = 'relevance_telegram_delivery'"
    ) == {"count": 1}

    service = TelegramService(database)
    service.pair(-100123, "Wire team", 123, "wire_bot")
    service.mark_tested()
    service.enable()
    assert service.pending() == []
    assert database.get_state("relevance_notification_watermark") == "2026-09-14T08:00:00Z"
