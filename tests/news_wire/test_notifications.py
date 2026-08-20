from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.notifications import NativeNotifier, RelevanceNativeNotifier
from open_source_ai_news_wire.storage import Database
from open_source_ai_news_wire.web import create_app


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_burst_notifications_are_grouped_and_never_duplicated(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    now = _now()
    for index in range(6):
        database.execute(
            """
            INSERT INTO alert(kind, severity, title, body, created_at)
            VALUES('candidate', 'high', ?, 'Ready for review', ?)
            """,
            (f"Candidate {index}", now),
        )
    database.set_state("notifications_enabled", "true", now)
    database.set_state("shadow_mode", "false", now)
    database.set_state("pilot_notification_watermark", now, now)
    commands: list[list[str]] = []

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    notifier = NativeNotifier(database, runner=runner, terminal_notifier=tmp_path / "missing")
    assert notifier.dispatch_pending() == 6
    assert notifier.dispatch_pending() == 0
    assert len(commands) == 1
    assert "6 new updates" in commands[0][-1]
    assert database.one("SELECT COUNT(*) AS count FROM notification_delivery") == {"count": 6}


def test_shadow_mode_allows_health_but_suppresses_content_notices(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    now = _now()
    database.execute(
        "INSERT INTO alert(kind, severity, title, body, created_at) VALUES('candidate', 'high', 'Content', 'Body', ?)",
        (now,),
    )
    database.execute(
        "INSERT INTO alert(kind, severity, title, body, created_at) VALUES('health', 'high', 'Source outage', 'Coverage degraded', ?)",
        (now,),
    )
    database.set_state("notifications_enabled", "true", now)
    database.set_state("shadow_mode", "true", now)
    database.set_state("pilot_notification_watermark", now, now)
    commands: list[list[str]] = []

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    delivered = NativeNotifier(
        database, runner=runner, terminal_notifier=tmp_path / "missing"
    ).dispatch_pending()

    assert delivered == 1
    assert commands[0][-2:] == ["Source outage", "Coverage degraded"]


def test_terminal_notifier_click_command_uses_fixed_launcher_and_story_id(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    now = _now()
    database.execute(
        "INSERT INTO alert(story_id, kind, severity, title, body, created_at) VALUES(NULL, 'health', 'high', 'Health', 'Body', ?)",
        (now,),
    )
    database.set_state("notifications_enabled", "true", now)
    database.set_state("pilot_notification_watermark", now, now)
    notifier_binary = tmp_path / "terminal-notifier"
    notifier_binary.write_text("binary", encoding="utf-8")
    launcher = tmp_path / "open-source-ai-news-wire"
    launcher.write_text("launcher", encoding="utf-8")
    commands: list[list[str]] = []

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    NativeNotifier(
        database,
        launcher=launcher,
        runner=runner,
        terminal_notifier=notifier_binary,
    ).dispatch_pending()
    assert commands[0][0] == str(notifier_binary)
    assert "-execute" not in commands[0]


def test_activation_watermark_and_severity_filter_suppress_old_or_standard_alerts(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    old = "2026-07-17T08:00:00Z"
    watermark = "2026-07-17T09:00:00Z"
    current = "2026-07-17T09:01:00Z"
    for created_at, kind, severity, title in (
        (old, "candidate", "high", "Historical candidate"),
        (current, "candidate", "standard", "Standard candidate"),
        (current, "candidate", "high", "Current candidate"),
    ):
        database.execute(
            "INSERT INTO alert(kind,severity,title,body,created_at) VALUES(?,?,?,'Body',?)",
            (kind, severity, title, created_at),
        )
    database.set_state("notifications_enabled", "true", current)
    database.set_state("shadow_mode", "false", current)
    database.set_state("pilot_notification_watermark", watermark, current)
    commands: list[list[str]] = []

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    delivered = NativeNotifier(
        database, runner=runner, terminal_notifier=tmp_path / "missing"
    ).dispatch_pending()

    assert delivered == 1
    assert commands[0][-2:] == ["Current candidate", "Body"]
    assert database.one("SELECT COUNT(*) AS count FROM notification_delivery") == {"count": 1}


def test_notification_canary_records_machine_verifiable_result(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    runner = lambda args: subprocess.CompletedProcess(args, 0, "", "")

    assert NativeNotifier(database, runner=runner, terminal_notifier=tmp_path / "missing").send_canary() is True
    assert database.get_state("notification_canary_status") == "passed"
    assert database.one("SELECT event_type FROM diagnostic_event") == {"event_type": "notification_canary"}


def test_dashboard_auth_link_can_open_a_specific_story(tmp_path: Path) -> None:
    data_root = tmp_path / "wire-data"
    database = Database(resolve_runtime_paths(data_root))
    database.initialize()
    seed_demo_data(database)
    app = create_app(data_root=str(data_root), auth_token="token")
    response = app.test_client().get(
        "/auth/token?next=/stories/story-demo-runtime-001"
    )
    assert response.status_code == 303
    assert response.headers["Location"] == "/stories/story-demo-runtime-001"


def _relevance_event(database: Database, detected_at: str) -> int:
    with database.transaction() as connection:
        cursor = connection.execute(
            """
            INSERT INTO relevance_notification_event(
                article_key, story_id, canonical_url, title, publisher, category,
                context, provenance, source_type, detected_at, created_at
            ) VALUES(
                printf('%064d', 1), 'story-native', 'https://example.com/native',
                'Open model weights released', 'Example Lab', 'Open Ecosystem News',
                'Example Lab reports: The project released model weights. This context comes from the monitored publisher excerpt.',
                'publisher_excerpt', 'announcement', ?, ?
            )
            """,
            (detected_at, detected_at),
        )
        event_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO relevance_notification_outbox(event_id, updated_at) VALUES(?, ?)",
            (event_id, detected_at),
        )
    return event_id


def test_relevance_native_fallback_is_individual_neutral_and_deduplicated(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    event_id = _relevance_event(database, "2026-08-20T09:01:00Z")
    database.set_state("relevance_notifications_enabled", "true", "2026-08-20T09:02:00Z")
    database.set_state("relevance_native_notifications_enabled", "true", "2026-08-20T09:02:00Z")
    database.set_state("relevance_notification_watermark", "2026-08-20T09:00:00Z", "2026-08-20T09:02:00Z")
    commands: list[list[str]] = []

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    notifier = RelevanceNativeNotifier(
        database, runner=runner, terminal_notifier=tmp_path / "missing"
    )
    assert notifier.dispatch_pending() == 1
    assert notifier.dispatch_pending() == 0
    assert len(commands) == 1
    assert commands[0][-2] == "Open model weights released"
    assert "Example Lab · Open Ecosystem News · announcement" in commands[0][-1]
    assert "Urgent" not in commands[0][-1]
    assert database.one(
        "SELECT status, attempt_count FROM relevance_native_delivery WHERE event_id = ?",
        (event_id,),
    ) == {"status": "delivered", "attempt_count": 1}


def test_relevance_native_fallback_respects_dismiss_and_retries_failures(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    event_id = _relevance_event(database, "2026-08-20T09:01:00Z")
    for key in ("relevance_notifications_enabled", "relevance_native_notifications_enabled"):
        database.set_state(key, "true", "2026-08-20T09:02:00Z")
    database.set_state("relevance_notification_watermark", "2026-08-20T09:00:00Z", "2026-08-20T09:02:00Z")
    runner = lambda args: subprocess.CompletedProcess(args, 1, "", "failed")

    assert RelevanceNativeNotifier(
        database, runner=runner, terminal_notifier=tmp_path / "missing"
    ).dispatch_pending() == 0
    failed = database.one(
        "SELECT status, attempt_count, next_attempt_at FROM relevance_native_delivery WHERE event_id = ?",
        (event_id,),
    )
    assert failed and failed["status"] == "failed" and failed["attempt_count"] == 1
    assert failed["next_attempt_at"]
    database.execute(
        "UPDATE relevance_notification_outbox SET delivery_state = 'dismissed' WHERE event_id = ?",
        (event_id,),
    )
    database.execute(
        "UPDATE relevance_native_delivery SET next_attempt_at = '2026-08-20T09:00:00Z' WHERE event_id = ?",
        (event_id,),
    )
    assert RelevanceNativeNotifier(
        database, runner=runner, terminal_notifier=tmp_path / "missing"
    ).dispatch_pending() == 0
