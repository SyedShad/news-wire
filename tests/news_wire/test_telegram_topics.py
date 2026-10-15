from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from open_source_ai_news_wire import cli
from open_source_ai_news_wire.adapters import Observation
from open_source_ai_news_wire.collector import Collector
from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.qualification import classify
from open_source_ai_news_wire.source_registry import synchronize_sources
from open_source_ai_news_wire.storage import Database
from open_source_ai_news_wire.telegram import TelegramAPIError, TelegramClient, TelegramDispatcher, TelegramError, TelegramService
from open_source_ai_news_wire.telegram_topics import TOPICS, content_topics, parse_topic_link

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
STAMP = NOW.isoformat()
ROUTES = {"open_source_ai": 11, "ai_policy_law": 22, "ai_models_products": 33}


@pytest.mark.parametrize("title,summary,expected", [
    ("Open-source AI model released", "", ("open_source_ai", "ai_models_products")),
    ("EU updates AI policy for open-source models", "", ("open_source_ai", "ai_policy_law")),
    ("Open-source AI model released under a new license", "", tuple(TOPICS)),
    ("GPT-6.1 improves reasoning", "", ("ai_models_products",)),
    ("ChatGPT adds new capabilities", "", ("ai_models_products",)),
    ("AI startup raises funding", "", ()),
    ("AI service suffered a data breach", "", ()),
    ("New AI law regulates models", "", ("ai_policy_law",)),
    ("AI policy consultation", "An open-source AI licensing proposal.", ("open_source_ai", "ai_policy_law")),
    ("New office opening", "The company updated its website.", ()),
    ("Open-weight Qwen3 model released", "", ("open_source_ai", "ai_models_products")),
])
def test_topics_overlap_without_publisher_or_category_precedence(title, summary, expected):
    assert content_topics(title, summary) == expected


@pytest.mark.parametrize("link,expected", [
    ("https://t.me/c/1234567/11", (-1001234567, 11)),
    ("https://t.me/c/1234567/11/99", (-1001234567, 11)),
    ("https://t.me/c/1234567/99?thread=11", (-1001234567, 11)),
])
def test_private_topic_links(link, expected):
    assert parse_topic_link(link) == expected


@pytest.mark.parametrize("link", [
    "http://t.me/c/123/11", "https://evil.example/c/123/11", "https://t.me/name/11",
    "https://t.me/c/123/0", "https://t.me/c/123/11?thread=0", "https://t.me/c/123/11?thread=2&thread=3",
    "https://user:password@t.me/c/123/11", "https://t.me/c/123/11?redirect=foo",
])
def test_invalid_topic_links_are_rejected(link):
    with pytest.raises(ValueError):
        parse_topic_link(link)


@pytest.fixture
def db(tmp_path):
    database = Database(resolve_runtime_paths(tmp_path / "wire"))
    database.initialize()
    synchronize_sources(database)
    service = TelegramService(database)
    service.pair(-999, "Old private group", 7, "newswire_bot")
    service.mark_tested()
    service.enable()
    return database


def activate(db):
    service = TelegramService(db)
    service.configure_topics(-1001234567, "Topic group", ROUTES)
    service.mark_tested()
    service.enable()
    return service


def ingest(db, title, suffix="one", summary=""):
    source = db.one("SELECT * FROM source_registry WHERE id='openai-news'")
    observation = Observation(suffix, title, f"https://openai.com/news/{suffix}", STAMP, summary)
    qualification = classify(observation, source, observed_at=STAMP)
    with db.transaction() as connection:
        Collector(db)._persist_story(connection, source, observation, qualification, STAMP, 1, "scheduled")


class Client:
    def __init__(self, failing_thread=None):
        self.calls = []
        self.failing_thread = failing_thread

    def call(self, method, payload, *, timeout):
        self.calls.append(payload)
        if payload["message_thread_id"] == self.failing_thread:
            raise TelegramAPIError("telegram_network_timeout")
        return {"message_id": len(self.calls)}


def dispatcher(db, client, now=NOW):
    return TelegramDispatcher(db, token_provider=lambda: "unused",
        client_factory=lambda _token: client, now=lambda: now,
        monotonic_clock=lambda: 0.0, sleep=lambda _seconds: None)


def test_one_event_cross_posts_and_rescan_does_not_duplicate(db):
    service = activate(db)
    ingest(db, "Open-source AI model released under a new license")
    ingest(db, "Open-source AI model released under a new license")
    assert len(service.pending()) == 3
    client = Client()
    assert dispatcher(db, client).dispatch()["delivered"] == 3
    assert {p["message_thread_id"] for p in client.calls} == {11,22,33}
    assert {p["chat_id"] for p in client.calls} == {-1001234567}
    assert len({p["text"] for p in client.calls}) == 1
    assert dispatcher(db, client).dispatch()["delivered"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM relevance_notification_event")["n"] == 1


def test_failed_topic_retries_independently_without_resending_delivered_topics(db):
    service = activate(db)
    ingest(db, "Open-source AI model released under a new license")
    client = Client(failing_thread=22)
    first = dispatcher(db, client).dispatch()
    assert first["delivered"] == 2 and first["failed"] == 1
    retry = Client()
    assert dispatcher(db, retry, NOW + timedelta(minutes=1)).dispatch()["delivered"] == 1
    assert [p["message_thread_id"] for p in retry.calls] == [22]
    assert service.status()["counts"]["delivered"] == 3


def test_unmatched_security_story_stays_on_dashboard_without_general_topic_fallback(db):
    activate(db)
    ingest(db, "AI service suffered a data breach")
    assert db.one("SELECT COUNT(*) AS n FROM fetched_article")["n"] == 1
    assert db.query("SELECT * FROM relevance_telegram_topic_delivery") == []
    client = Client()
    assert dispatcher(db, client).dispatch()["delivered"] == 0
    assert client.calls == []


def test_proprietary_model_improvement_is_collected_and_routed(db):
    activate(db)
    ingest(db, "GPT-6.1 improves reasoning")
    assert db.one("SELECT COUNT(*) AS n FROM fetched_article")["n"] == 1
    client = Client()
    assert dispatcher(db, client).dispatch()["delivered"] == 1
    assert client.calls[0]["message_thread_id"] == 33


def test_configuration_preserves_old_delivery_history_and_does_not_backfill(db):
    ingest(db, "Open-source AI model released")
    event_id = db.one("SELECT id FROM relevance_notification_event")["id"]
    TelegramService(db).record(event_id, status="delivered", provider_message_id=42)
    service = activate(db)
    assert service.pending() == []
    assert db.one("SELECT provider_message_id FROM relevance_telegram_delivery")["provider_message_id"] == 42
    assert db.query("SELECT * FROM relevance_telegram_topic_delivery") == []


def test_pending_keyset_does_not_lose_destinations_at_page_boundary(db):
    activate(db)
    for i in range(35):
        ingest(db, "Open-source AI model released under a new license", str(i))
    client = Client()
    sender = dispatcher(db, client)
    rows = list(sender._events())
    assert len(rows) == 105
    assert len({row["delivery_id"] for row in rows}) == 105
    for _ in range(9):
        sender.dispatch()
    assert len(client.calls) == 105


def test_cannot_retarget_queued_events_and_requires_all_three_distinct_topics(db):
    service = activate(db)
    ingest(db, "Open-source AI model released")
    with pytest.raises(TelegramError, match="pending_deliveries"):
        service.configure_topics(-1007654321, "Other group", ROUTES)
    assert {r["chat_id"] for r in service.pending()} == {-1001234567}
    with pytest.raises(TelegramError, match="topics_invalid"):
        service.configure_topics(-1001234567, "Topic group", {"open_source_ai":11})


def test_schema11_migration_preserves_configuration_and_never_backfills_topics(tmp_path):
    database = Database(resolve_runtime_paths(tmp_path / "wire"))
    database.initialize()
    with database.transaction() as connection:
        connection.execute("DROP TABLE relevance_telegram_topic_delivery")
        connection.execute("DROP TABLE telegram_topic_route")
        connection.execute("UPDATE meta SET value='11' WHERE key='schema_version'")
        connection.execute("INSERT OR REPLACE INTO app_state VALUES('telegram_enabled','true',?)", (STAMP,))
    assert database.migrate() == 12
    assert database.get_state("telegram_enabled") == "true"
    assert database.query("SELECT * FROM telegram_topic_route") == []
    assert TelegramService(database).status()["routing_mode"] == "single"


def test_transport_preserves_topic_and_checks_destination_acknowledgement():
    seen = []
    async def respond(request):
        payload = json.loads(request.content)
        seen.append(payload)
        return httpx.Response(200, json={"ok":True,"result":{
            "message_id":7,"message_thread_id":11,"chat":{"id":-1001234567}}})
    client = TelegramClient("123:dummy-token", transport=httpx.MockTransport(respond))
    client.call("sendMessage", {"chat_id":-1001234567,"message_thread_id":11,"text":"Test"})
    assert seen[0]["message_thread_id"] == 11
    assert seen[0]["allow_paid_broadcast"] is False
    with pytest.raises(TelegramAPIError, match="topic_acknowledgement_mismatch"):
        client.call("sendMessage", {"chat_id":-1001234567,"message_thread_id":22,"text":"Test"})


@pytest.mark.parametrize("chat_type,is_forum,member_status,expected", [
    ("supergroup", True, "member", 0),
    ("supergroup", True, "administrator", 0),
    ("group", False, "member", 2),
    ("supergroup", True, "left", 2),
    ("channel", False, "administrator", 2),
])
def test_cli_validates_existing_forum_and_membership_before_configuring(
        db, monkeypatch, capsys, chat_type, is_forum, member_status, expected):
    calls = []
    class ForumClient:
        def __init__(self, _token): pass
        def call(self, method, payload, *, timeout):
            calls.append(method)
            return {
                "getMe": {"id":7},
                "getChat": {"id":-1001234567,"type":chat_type,"is_forum":is_forum,"title":"Topic group"},
                "getChatMember": {"status":member_status},
            }[method]
    scheduler = SimpleNamespace(disable=lambda: None)
    monkeypatch.setattr(cli, "_telegram_components", lambda *_args: (
        db, SimpleNamespace(get_token=lambda: "unused"), scheduler, ForumClient, TelegramDispatcher, TelegramService))
    arguments = ["telegram", "topics"]
    for key, value in ROUTES.items():
        arguments.extend(["--topic", f"{key}=https://t.me/c/1234567/{value}"])
    assert cli.main(arguments) == expected
    assert "sendMessage" not in calls
    assert TelegramService(db).status()["routing_mode"] == ("topics" if expected == 0 else "single")


def test_topic_test_must_succeed_in_all_three_destinations_before_enable(db, monkeypatch, capsys):
    service = TelegramService(db)
    service.configure_topics(-1001234567, "Topic group", ROUTES)
    calls = []
    class TestClient:
        def __init__(self, _token): pass
        def call(self, method, payload, *, timeout):
            if method == "getMe": return {"id":7}
            calls.append(payload)
            if len(calls) == 2: raise TelegramAPIError("telegram_api_400", permanent=True)
            return {"message_id":1}
    monkeypatch.setattr(cli, "_telegram_components", lambda *_args: (
        db, SimpleNamespace(get_token=lambda: "unused"), SimpleNamespace(), TestClient, TelegramDispatcher, TelegramService))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    assert cli.main(["telegram", "send-test"]) == 2
    assert service.status()["test_passed"] is False
    assert service.status()["enabled"] is False
    assert len(calls) == 2
