from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.notifications import NativeNotifier
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
