from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.storage import Database
from open_source_ai_news_wire.telegram import (
    TelegramAPIError,
    TelegramError,
    TelegramClient,
    TelegramDispatcher,
    TelegramService,
)


def _database(tmp_path: Path) -> Database:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    return database


def _time(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _event(
    database: Database,
    published_at: str | None,
    *,
    status: str = "valid",
    source_type: str = "article",
    provenance: str = "publisher_excerpt",
) -> int:
    now = _time(datetime.now(UTC))
    url_timestamp = (published_at or now).replace(":", "").replace("-", "")
    with database.transaction() as connection:
        cursor = connection.execute(
            """
            INSERT INTO relevance_notification_event(
                article_key, story_id, canonical_url, title, publisher, category,
                context, provenance, source_type, published_at, source_reported_at,
                timestamp_status, detected_at, created_at
            ) VALUES(printf('%064d', ABS(RANDOM())), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "story-telegram",
                f"https://example.com/{source_type}/{url_timestamp}",
                "Open model release",
                "Example Publisher",
                "Open Ecosystem News",
                "Example Publisher reports: public model weights were released.",
                provenance,
                source_type,
                published_at,
                published_at,
                status,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


class FakeClient:
    def __init__(self, result: dict[str, object] | None = None, error: Exception | None = None):
        self.result = result or {"message_id": 42}
        self.error = error
        self.calls: list[tuple[str, dict[str, object], float]] = []

    def call(self, method: str, payload: dict[str, object], *, timeout: float = 10) -> dict[str, object]:
        self.calls.append((method, payload, timeout))
        if self.error:
            raise self.error
        return self.result


def _enabled(database: Database, *, now: datetime) -> TelegramService:
    service = TelegramService(database)
    service.pair(-100123, "Private team", 7, "news_wire_bot")
    service.mark_tested()
    service.enable()
    return service


def test_enable_uses_max_event_id_and_sends_only_new_events(tmp_path: Path) -> None:
    database = _database(tmp_path)
    first = _event(database, _time(datetime.now(UTC) - timedelta(hours=1)))
    service = _enabled(database, now=datetime.now(UTC))
    assert service.status()["activation_event_id"] == first
    second = _event(database, _time(datetime.now(UTC) - timedelta(minutes=5)))
    client = FakeClient()
    dispatcher = TelegramDispatcher(
        database,
        token_provider=lambda: "123:secret-token",
        client_factory=lambda _token: client,
        now=lambda: datetime.now(UTC),
        monotonic_clock=lambda: 0.0,
        sleep=lambda _seconds: None,
    )

    result = dispatcher.dispatch()

    assert result["delivered"] == 1
    assert [call[1]["chat_id"] for call in client.calls] == [-100123]
    assert client.calls[0][1]["link_preview_options"] == {"is_disabled": True}
    assert "Open model release" in str(client.calls[0][1]["text"])
    assert database.one(
        "SELECT status, provider_message_id FROM relevance_telegram_delivery WHERE event_id = ?",
        (second,),
    ) == {"status": "delivered", "provider_message_id": 42}
    assert dispatcher.dispatch()["delivered"] == 0


@pytest.mark.parametrize(
    ("status", "published_at", "expected_publication"),
    [
        ("unknown", None, "Original publication date: not verified"),
        (
            "malformed_source_fallback_detection",
            "not-a-timestamp",
            "Original publication date: not verified",
        ),
        ("valid", "2026-09-15T10:00:00", "Original publication date: not verified"),
        ("valid", "not-a-timestamp", "Original publication date: not verified"),
        ("valid", "0001-01-01T00:00:00+14:00", "Original publication date: not verified"),
        ("valid", "2026-09-13T10:00:00Z", "Published: 2026-09-13T10:00:00Z"),
        ("valid", "2026-09-15T13:00:00Z", "Original publication date: not verified"),
    ],
)
def test_dispatch_delivers_missing_malformed_old_and_future_publication_times(
    tmp_path: Path, status: str, published_at: str | None, expected_publication: str
) -> None:
    database = _database(tmp_path)
    service = _enabled(database, now=datetime.now(UTC))
    event_id = _event(database, published_at, status=status)
    client = FakeClient()
    result = TelegramDispatcher(
        database,
        token_provider=lambda: "unused",
        client_factory=lambda _token: client,
        now=lambda: datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
        monotonic_clock=lambda: 0.0,
        sleep=lambda _seconds: None,
    ).dispatch()

    assert result["delivered"] == 1
    assert database.one(
        "SELECT status FROM relevance_telegram_delivery WHERE event_id = ?", (event_id,)
    ) == {"status": "delivered"}
    text = str(client.calls[0][1]["text"])
    assert "Fetched:" not in text
    assert "Published:" not in text
    assert "not verified" not in text


def test_dispatch_delivers_discovery_date_without_mislabeling_it_as_publication(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _enabled(database, now=datetime.now(UTC))
    published_at = _time(datetime.now(UTC) - timedelta(minutes=5))
    discovery_id = _event(
        database,
        published_at,
        source_type="discovery",
        provenance="discovery_metadata",
    )
    client = FakeClient()
    result = TelegramDispatcher(
        database,
        token_provider=lambda: "123:secret-token",
        client_factory=lambda _token: client,
        now=lambda: datetime.now(UTC),
        monotonic_clock=lambda: 0.0,
        sleep=lambda _seconds: None,
    ).dispatch()

    assert result["skipped"] == 0
    assert result["delivered"] == 1
    assert database.one(
        "SELECT status FROM relevance_telegram_delivery WHERE event_id = ?",
        (discovery_id,),
    ) == {"status": "delivered"}
    text = str(client.calls[0][1]["text"])
    assert "Fetched:" not in text
    assert "Original publication date:" not in text
    assert "Published:" not in text


def test_429_persists_cooldown_and_retries_later(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _enabled(database, now=datetime.now(UTC))
    event_id = _event(database, "2026-09-15T11:55:00Z")
    second_id = _event(database, "2026-09-15T11:54:00Z")
    error = TelegramAPIError("telegram_api_429", retry_after=60)
    now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    dispatcher = TelegramDispatcher(
        database,
        token_provider=lambda: "unused",
        client_factory=lambda _token: FakeClient(error=error),
        now=lambda: now,
        monotonic_clock=lambda: 0.0,
        sleep=lambda _seconds: None,
    )

    result = dispatcher.dispatch()

    assert result["failed"] == 1
    assert result["attempted"] == 1
    assert database.one(
        "SELECT status, attempt_count FROM relevance_telegram_delivery WHERE event_id = ?",
        (event_id,),
    ) == {"status": "failed", "attempt_count": 1}
    assert database.one(
        "SELECT status FROM relevance_telegram_delivery WHERE event_id = ?", (second_id,)
    ) is None
    assert database.get_state("telegram_cooldown_until") == "2026-09-15T12:01:00Z"
    assert dispatcher.dispatch()["status"] == "cooldown"


def test_permanent_event_error_dead_letters_without_blocking_channel(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _enabled(database, now=datetime.now(UTC))
    event_id = _event(database, "2026-09-15T11:55:00Z")
    now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    dispatcher = TelegramDispatcher(
        database,
        token_provider=lambda: "unused",
        client_factory=lambda _token: FakeClient(
            error=TelegramAPIError("telegram_api_400", permanent=True)
        ),
        now=lambda: now,
        monotonic_clock=lambda: 0.0,
        sleep=lambda _seconds: None,
    )

    result = dispatcher.dispatch()

    assert result["failed"] == 1
    assert database.one(
        "SELECT status FROM relevance_telegram_delivery WHERE event_id = ?", (event_id,)
    ) == {"status": "dead_lettered"}
    assert TelegramService(database).status()["enabled"] is True


def test_status_counts_unattempted_events_as_pending(tmp_path: Path) -> None:
    database = _database(tmp_path)
    service = _enabled(database, now=datetime.now(UTC))
    _event(database, "2026-09-15T11:55:00Z")

    assert service.status()["counts"]["pending"] == 1


def test_client_redacts_token_from_http_logs(caplog) -> None:
    import httpx

    token = "123:secret-token"
    logger = logging.getLogger("httpx")
    async def respond(request):
        logger.debug("request URL=%s", request.url)
        return httpx.Response(200, json={"ok": True, "result": {}})
    with caplog.at_level(logging.DEBUG, logger="httpx"):
        TelegramClient(token, transport=httpx.MockTransport(respond)).call("getMe", {})
    assert token not in caplog.text
    assert "<redacted-token>" in caplog.text


class _AdvancingClock:
    def __init__(self, value=None):
        self.value = value or datetime(2026, 9, 15, 12, tzinfo=UTC)
        self.elapsed = 0.0

    def now(self):
        return self.value + timedelta(seconds=self.elapsed)

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


def _dispatcher(database, clock, client, **kwargs):
    return TelegramDispatcher(
        database, token_provider=kwargs.pop("token_provider", lambda: "unused"),
        client_factory=lambda _token: client, now=clock.now,
        monotonic_clock=clock.monotonic, sleep=clock.sleep, **kwargs,
    )


def test_dispatch_caps_attempts_and_paces_across_process_restarts(tmp_path):
    database = _database(tmp_path)
    clock = _AdvancingClock()
    _enabled(database, now=clock.now())
    for i in range(14):
        _event(database, _time(clock.now() - timedelta(minutes=i + 1)))
    client = FakeClient()
    result = _dispatcher(database, clock, client).dispatch()
    assert result["attempted"] == result["delivered"] == 12
    assert clock.elapsed == 44
    restarted = Database(database.paths)
    assert _dispatcher(restarted, clock, client).dispatch()["delivered"] == 2
    assert clock.elapsed == 52  # The next process must also wait four seconds.
    assert _dispatcher(restarted, clock, client).dispatch()["status"] == "idle"


def test_old_events_are_delivered_across_bounded_runs_before_newest_event(tmp_path):
    database = _database(tmp_path)
    clock = _AdvancingClock()
    _enabled(database, now=clock.now())
    for i in range(105):
        _event(database, _time(clock.now() - timedelta(hours=25, minutes=i)))
    _event(database, _time(clock.now() - timedelta(minutes=1)))
    client = FakeClient()
    results = [_dispatcher(database, clock, client).dispatch() for _ in range(9)]
    assert sum(result["skipped"] for result in results) == 0
    assert sum(result["delivered"] for result in results) == 106


def test_old_items_pass_24_hour_boundary_and_dispatch_deadline_is_preserved(tmp_path):
    database = _database(tmp_path)
    clock = _AdvancingClock()
    _enabled(database, now=clock.now())
    _event(database, _time(clock.now() - timedelta(hours=24)))
    second = _event(database, _time(clock.now() - timedelta(hours=24) + timedelta(seconds=1)))
    result = _dispatcher(database, clock, FakeClient()).dispatch()
    assert result["delivered"] == 2
    assert result["skipped"] == 0
    assert database.one("SELECT status FROM relevance_telegram_delivery WHERE event_id = ?", (second,))["status"] == "delivered"
    for i in range(10):
        _event(database, _time(clock.now() - timedelta(minutes=i + 1)))

    class SlowClient(FakeClient):
        def call(self, method, payload, *, timeout):
            clock.sleep(min(timeout, 10))
            raise TelegramAPIError("telegram_network_timeout")

    before = clock.elapsed
    result = _dispatcher(database, clock, SlowClient()).dispatch()
    assert result["attempted"] == 5
    assert clock.elapsed - before <= 50


def test_failed_retry_delivers_after_24_hours_and_unavailable_keychain_is_actionable(tmp_path):
    database = _database(tmp_path)
    clock = _AdvancingClock()
    service = _enabled(database, now=clock.now())
    event_id = _event(database, _time(clock.now() - timedelta(hours=23, minutes=59)))
    failed = FakeClient(error=TelegramAPIError("telegram_network_timeout"))
    assert _dispatcher(database, clock, failed).dispatch()["failed"] == 1
    clock.sleep(25 * 60 * 60)
    retry = _dispatcher(Database(database.paths), clock, FakeClient()).dispatch()
    assert retry["delivered"] == 1
    assert database.one("SELECT status FROM relevance_telegram_delivery WHERE event_id = ?", (event_id,))["status"] == "delivered"
    _event(database, _time(clock.now() - timedelta(minutes=1)))
    def unavailable():
        raise RuntimeError("private backend detail")
    assert _dispatcher(database, clock, FakeClient(), token_provider=unavailable).dispatch()["status"] == "token_unavailable"
    assert service.status()["last_error"] == "token_unavailable"
    assert service.status()["enabled"] is True


def test_overlapping_dispatch_and_lost_group_access(tmp_path):
    database = _database(tmp_path)
    clock = _AdvancingClock()
    service = _enabled(database, now=clock.now())
    event_id = _event(database, _time(clock.now() - timedelta(minutes=1)))
    client = FakeClient(error=TelegramAPIError("telegram_api_403", permanent=True, channel_blocked=True))
    with service._lock():
        assert _dispatcher(database, clock, client).dispatch()["status"] == "locked"
    assert client.calls == []
    assert _dispatcher(database, clock, client).dispatch()["failed"] == 1
    assert service.status()["enabled"] is False
    assert service.status()["blocked_reason"] == "telegram_api_403"
    assert database.one("SELECT attempt_count FROM relevance_telegram_delivery WHERE event_id = ?", (event_id,))["attempt_count"] == 1


def test_reenable_excludes_old_queue_and_keeps_browser_watermark(tmp_path):
    database = _database(tmp_path)
    clock = _AdvancingClock()
    service = _enabled(database, now=clock.now())
    old = _event(database, _time(clock.now() - timedelta(minutes=2)))
    watermark = database.get_state("relevance_notification_watermark")
    service.disable()
    service.enable()
    assert service.status()["activation_event_id"] == old
    new = _event(database, _time(clock.now() - timedelta(minutes=1)))
    assert _dispatcher(database, clock, FakeClient()).dispatch()["delivered"] == 1
    assert database.one("SELECT event_id FROM relevance_telegram_delivery")["event_id"] == new
    assert database.get_state("relevance_notification_watermark") == watermark


def test_interrupted_repair_cannot_reuse_old_test(tmp_path):
    database = _database(tmp_path)
    service = _enabled(database, now=datetime.now(UTC))
    service.disable(require_test=True)
    assert service.status()["enabled"] is False
    with pytest.raises(TelegramError, match="telegram_test_required"):
        service.enable()
