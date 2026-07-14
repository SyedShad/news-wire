from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.pilot import PilotGateError, PilotManager
from open_source_ai_news_wire.storage import Database


def test_shadow_window_requires_time_and_explicit_human_review(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    start = datetime(2026, 7, 14, 10, tzinfo=UTC)
    manager = PilotManager(database, now=lambda: start)

    status = manager.start_shadow()

    assert status.state == "shadow"
    assert status.shadow_mode is True
    assert status.notifications_enabled is False
    with pytest.raises(PilotGateError, match="confirmation"):
        manager.activate_notifications(human_review_confirmed=False)
    with pytest.raises(PilotGateError, match="72-hour"):
        manager.activate_notifications(human_review_confirmed=True)

    completed = PilotManager(
        database, now=lambda: start + timedelta(hours=73)
    ).activate_notifications(human_review_confirmed=True)
    assert completed.state == "notifications"
    assert completed.shadow_mode is False
    assert completed.notifications_enabled is True


def test_critical_diagnostic_blocks_notifications(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    start = datetime(2026, 7, 10, tzinfo=UTC)
    PilotManager(database, now=lambda: start).start_shadow()
    database.execute(
        """
        INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
        VALUES('critical', 'security', 'Blocking issue', '2026-07-10T00:00:00Z', '{}')
        """
    )

    with pytest.raises(PilotGateError, match="Critical"):
        PilotManager(database, now=lambda: start + timedelta(hours=80)).activate_notifications(
            human_review_confirmed=True
        )


def test_notifications_can_be_stopped_without_deleting_state(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    now = datetime(2026, 7, 14, tzinfo=UTC)
    database.set_state("notifications_enabled", "true", "2026-07-14T00:00:00Z")

    status = PilotManager(database, now=lambda: now).stop_notifications()

    assert status.state == "paused"
    assert status.notifications_enabled is False
    assert database.paths.database.exists()
