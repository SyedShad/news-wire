from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.pilot import PilotGateError, PilotManager
from open_source_ai_news_wire.scheduler import SchedulerStatus
from open_source_ai_news_wire.storage import Database


def _scheduler(state: str = "active") -> SchedulerStatus:
    return SchedulerStatus(
        installed=state != "not_installed",
        loaded=state == "active",
        state=state,
        plist_path=Path("/tmp/com.opensourceainewswire.scanner.plist"),
    )


def _manager(database: Database, now: datetime, *, scheduler: str = "active") -> PilotManager:
    return PilotManager(
        database,
        now=lambda: now,
        scheduler_status=lambda: _scheduler(scheduler),
    )


def test_shadow_window_requires_current_validation_and_explicit_human_review(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    start = datetime(2026, 7, 14, 10, tzinfo=UTC)
    manager = _manager(database, start)

    status = manager.start_shadow()

    assert status.state == "shadow"
    assert status.shadow_mode is True
    assert status.notifications_enabled is False
    with pytest.raises(PilotGateError, match="confirmation"):
        manager.activate_notifications(human_review_confirmed=False)
    with pytest.raises(PilotGateError, match="72-hour"):
        manager.activate_notifications(human_review_confirmed=True)

    with pytest.raises(PilotGateError, match="Extended validation has not started"):
        _manager(database, start + timedelta(hours=73)).activate_notifications(
            human_review_confirmed=True
        )


def test_critical_diagnostic_blocks_notifications(tmp_path: Path) -> None:
    original = datetime(2026, 7, 10, tzinfo=UTC)
    validation = datetime(2026, 7, 17, tzinfo=UTC)
    database = _armable_database(tmp_path, validation)
    _manager(database, original).start_shadow()
    _manager(database, validation).extend_validation(auto_activate=False)
    database.execute(
        """
        INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
        VALUES('critical', 'security', 'Blocking issue', '2026-07-18T00:00:00Z', '{}')
        """
    )

    with pytest.raises(PilotGateError, match="critical diagnostic"):
        _manager(database, validation + timedelta(hours=80)).activate_notifications(
            human_review_confirmed=True
        )


def test_notifications_can_be_stopped_without_deleting_state(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    now = datetime(2026, 7, 14, tzinfo=UTC)
    database.set_state("notifications_enabled", "true", "2026-07-14T00:00:00Z")

    status = _manager(database, now).stop_notifications()

    assert status.state == "paused"
    assert status.notifications_enabled is False
    assert database.paths.database.exists()


def _armable_database(tmp_path: Path, start: datetime) -> Database:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    now = start.isoformat().replace("+00:00", "Z")
    story_id = "story-demo-eval-004"
    database.execute("DELETE FROM work_item")
    database.execute("UPDATE source_registry SET enabled=0, health='paused'")
    database.execute("UPDATE source_state SET enabled=0, health='paused'")
    database.execute("UPDATE source_registry SET enabled=1, health='healthy' WHERE id='official-labs'")
    database.execute(
        """
        INSERT INTO source_state(source_id,enabled,health,failure_streak,last_success_at)
        VALUES('official-labs',1,'healthy',0,?)
        ON CONFLICT(source_id) DO UPDATE SET enabled=1,health='healthy',failure_streak=0,last_success_at=excluded.last_success_at
        """,
        (now,),
    )
    database.execute(
        """
        INSERT INTO evidence_source(
            story_id,requested_url,final_url,canonical_url,publisher_key,acquisition_method,
            title,passage,proposed_role,confirmed_role,first_party_confirmed,status,
            fetched_at,confirmed_at,created_at,updated_at
        ) VALUES(?,?,?,?,?,'manual_url','Primary evidence','Passage','Event','Event',1,'confirmed',?,?,?,?)
        """,
        (story_id, "https://example.com/evidence", "https://example.com/evidence", "https://example.com/evidence", "example.com", now, now, now, now),
    )
    database.execute(
        "INSERT INTO review_action(story_id,action,reason,draft_mode,created_at) VALUES(?,'approve_neutral','Pilot workflow','Neutral News Brief',?)",
        (story_id, now),
    )
    database.execute("UPDATE draft SET version=2 WHERE story_id=?", (story_id,))
    database.set_state("notification_canary_status", "passed", now)
    database.set_state("schedule_installed", "true", now)
    database.set_state("schedule_status", "active", now)
    return database


def test_extended_validation_preserves_original_start_and_auto_activates_fail_closed(tmp_path: Path) -> None:
    original = datetime(2026, 7, 10, tzinfo=UTC)
    validation = datetime(2026, 7, 17, 10, tzinfo=UTC)
    database = _armable_database(tmp_path, validation)
    _manager(database, original).start_shadow()

    armed = _manager(database, validation).extend_validation(auto_activate=True)

    assert armed.started_at == "2026-07-10T00:00:00Z"
    assert armed.validation_started_at == "2026-07-17T10:00:00Z"
    assert armed.auto_activate_armed is True
    waiting = _manager(database, validation + timedelta(hours=71)).try_auto_activate()
    assert waiting.notifications_enabled is False
    activated = _manager(database, validation + timedelta(hours=73)).try_auto_activate()
    assert activated.notifications_enabled is True
    assert activated.notification_watermark == "2026-07-20T11:00:00Z"


def test_extended_validation_can_remain_unarmed_without_activation_prerequisites(
    tmp_path: Path,
) -> None:
    original = datetime(2026, 7, 10, tzinfo=UTC)
    validation = datetime(2026, 7, 17, 10, tzinfo=UTC)
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    _manager(database, original).start_shadow()

    status = _manager(database, validation).extend_validation(
        auto_activate=False
    )

    assert status.state == "extended_shadow"
    assert status.started_at == "2026-07-10T00:00:00Z"
    assert status.validation_started_at == "2026-07-17T10:00:00Z"
    assert status.auto_activate_armed is False
    assert status.shadow_mode is True
    assert status.notifications_enabled is False
    after_window = _manager(database, validation + timedelta(hours=73)).try_auto_activate()
    assert after_window.auto_activate_armed is False
    assert after_window.notifications_enabled is False


def test_extended_readiness_reports_source_queue_and_canary_blockers(tmp_path: Path) -> None:
    validation = datetime(2026, 7, 17, 10, tzinfo=UTC)
    database = _armable_database(tmp_path, validation)
    _manager(database, validation).extend_validation(auto_activate=True)
    database.execute(
        "UPDATE source_state SET health='degraded', failure_streak=3 WHERE source_id='official-labs'"
    )
    database.execute(
        "INSERT INTO work_item(kind,status,priority,payload_json,created_at,updated_at) VALUES('scout_scan','queued',1,'{}',?,?)",
        ("2026-07-20T11:00:00Z", "2026-07-20T11:00:00Z"),
    )
    database.set_state("notification_canary_status", "failed", "2026-07-20T11:00:00Z")

    readiness = _manager(database, validation + timedelta(hours=73)).readiness()

    assert readiness.ready is False
    assert readiness.gates["source_health"] is False
    assert readiness.gates["work_queue"] is False
    assert readiness.gates["notification_canary"] is False


def test_extend_validation_requires_canary_and_completed_live_workflow(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    now = datetime(2026, 7, 17, tzinfo=UTC)
    manager = _manager(database, now)
    with pytest.raises(PilotGateError, match="canary"):
        manager.extend_validation(auto_activate=True)
    database.set_state("notification_canary_status", "passed", "2026-07-17T00:00:00Z")
    with pytest.raises(PilotGateError, match="revised live draft"):
        manager.extend_validation(auto_activate=True)


def test_manual_activation_uses_complete_current_repair_readiness(tmp_path: Path) -> None:
    original = datetime(2026, 7, 10, tzinfo=UTC)
    validation = datetime(2026, 7, 17, 10, tzinfo=UTC)
    database = _armable_database(tmp_path, validation)
    _manager(database, original).start_shadow()
    _manager(database, validation).extend_validation(auto_activate=False)

    completed = _manager(database, validation + timedelta(hours=73)).activate_notifications(
        human_review_confirmed=True
    )

    assert completed.state == "notifications"
    assert completed.shadow_mode is False
    assert completed.notifications_enabled is True
    assert completed.notification_watermark == "2026-07-20T11:00:00Z"


def test_readiness_uses_live_scheduler_instead_of_cached_app_state(tmp_path: Path) -> None:
    validation = datetime(2026, 7, 17, 10, tzinfo=UTC)
    database = _armable_database(tmp_path, validation)
    _manager(database, validation).extend_validation(auto_activate=False)
    database.set_state("schedule_installed", "false", "2026-07-17T10:00:00Z")
    database.set_state("schedule_status", "not_installed", "2026-07-17T10:00:00Z")

    live = _manager(database, validation + timedelta(hours=73)).readiness()
    paused = _manager(
        database,
        validation + timedelta(hours=73),
        scheduler="paused",
    ).readiness()

    assert live.gates["scheduler"] is True
    assert paused.gates["scheduler"] is False
    assert any("live state: paused" in blocker for blocker in paused.blockers)


def test_stale_assistance_attestation_blocks_only_when_assistance_is_enabled(
    tmp_path: Path,
) -> None:
    validation = datetime(2026, 7, 17, 10, tzinfo=UTC)
    database = _armable_database(tmp_path, validation)
    _manager(database, validation).extend_validation(auto_activate=False)
    now = validation + timedelta(hours=73)
    isolation_checks: list[str] = []

    def stale_attestation(_database: Database) -> bool:
        isolation_checks.append("checked")
        return False

    manager = PilotManager(
        database,
        now=lambda: now,
        scheduler_status=lambda: _scheduler(),
        assistance_isolation_current=stale_attestation,
    )

    disabled = manager.readiness()
    assert isolation_checks == []
    database.set_state("assistance_enabled", "true", now.isoformat().replace("+00:00", "Z"))
    enabled = manager.readiness()

    assert disabled.gates["assistance_isolation"] is True
    assert enabled.gates["assistance_isolation"] is False
    assert isolation_checks == ["checked"]


def test_armed_validation_cannot_activate_outside_shadow_containment(tmp_path: Path) -> None:
    validation = datetime(2026, 7, 17, 10, tzinfo=UTC)
    database = _armable_database(tmp_path, validation)
    _manager(database, validation).extend_validation(auto_activate=True)
    database.set_state("shadow_mode", "false", "2026-07-20T11:00:00Z")

    status = _manager(database, validation + timedelta(hours=73)).try_auto_activate()
    readiness = _manager(database, validation + timedelta(hours=73)).readiness()

    assert status.notifications_enabled is False
    assert readiness.gates["shadow_containment"] is False
