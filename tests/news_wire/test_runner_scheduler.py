from __future__ import annotations

import json
import os
import plistlib
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from open_source_ai_news_wire.collector import ScanSummary
from open_source_ai_news_wire.assistance import AssistanceDeferred
from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.runner import ScanLock, Worker
from open_source_ai_news_wire.scheduler import LABEL, LaunchAgentManager, SchedulerError, next_scheduled_run
from open_source_ai_news_wire.storage import Database


class RecordingCollector:
    def __init__(self):
        self.calls: list[dict[str, str | None]] = []

    def scan(self, **kwargs: str | None) -> ScanSummary:
        self.calls.append(kwargs)
        return ScanSummary(1, "success", 1, 0, 2, False)


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (datetime(2026, 7, 17, 10, 0, tzinfo=UTC), datetime(2026, 7, 17, 10, 30, tzinfo=UTC)),
        (datetime(2026, 7, 17, 10, 29, 59, tzinfo=UTC), datetime(2026, 7, 17, 10, 30, tzinfo=UTC)),
        (datetime(2026, 7, 17, 10, 30, tzinfo=UTC), datetime(2026, 7, 17, 11, 0, tzinfo=UTC)),
        (datetime(2026, 7, 17, 10, 59, 59, tzinfo=UTC), datetime(2026, 7, 17, 11, 0, tzinfo=UTC)),
    ],
)
def test_next_scheduled_run_uses_strict_half_hour_boundaries(current: datetime, expected: datetime) -> None:
    assert next_scheduled_run(current) == expected


def test_worker_coalesces_overlapping_triggers(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    lock = ScanLock(database.paths.operations / "locks" / "scan.lock")
    lock.acquire()
    try:
        first = Worker(database, collector=RecordingCollector()).run(trigger="manual")
        second = Worker(database, collector=RecordingCollector()).run(trigger="manual")
    finally:
        lock.release()

    assert first.coalesced is True
    assert second.coalesced is True
    assert database.one("SELECT COUNT(*) AS count FROM work_item") == {"count": 1}
    assert database.one("SELECT status, idempotency_key FROM work_item") == {
        "status": "queued",
        "idempotency_key": "coalesced-scout-scan",
    }


def test_scheduled_worker_uses_a_capped_recovery_interval(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    old = datetime.now(UTC) - timedelta(days=10)
    database.set_state("last_scan_at", old.isoformat().replace("+00:00", "Z"), old.isoformat().replace("+00:00", "Z"))
    collector = RecordingCollector()

    result = Worker(database, collector=collector).run(trigger="scheduled")

    assert result.status == "success"
    assert collector.calls[0]["trigger"] == "recovery"
    start = datetime.fromisoformat(str(collector.calls[0]["interval_start"]).replace("Z", "+00:00"))
    end = datetime.fromisoformat(str(collector.calls[0]["interval_end"]).replace("Z", "+00:00"))
    assert timedelta(hours=71, minutes=59) <= end - start <= timedelta(hours=72, minutes=1)


def test_worker_claims_and_completes_dashboard_run_requests(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    now = "2026-07-14T10:00:00Z"
    database.execute(
        """
        INSERT INTO work_item(kind, status, priority, payload_json, created_at, updated_at, idempotency_key)
        VALUES('scout_scan', 'queued', 100, '{}', ?, ?, 'dashboard-run')
        """,
        (now, now),
    )

    Worker(database, collector=RecordingCollector()).run(trigger="manual")

    assert database.one("SELECT status, attempt_count FROM work_item") == {
        "status": "completed",
        "attempt_count": 1,
    }


def test_worker_refreshes_persisted_next_scan_after_each_active_run(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    database.set_state("schedule_status", "active", "2026-07-14T00:00:00Z")
    database.set_state("next_scan_at", "2020-01-01T00:00:00Z", "2026-07-14T00:00:00Z")

    Worker(database, collector=RecordingCollector()).run(trigger="manual")

    next_scan = datetime.fromisoformat(database.get_state("next_scan_at").replace("Z", "+00:00"))
    assert next_scan > datetime.now(UTC)
    assert next_scan.minute in {0, 30}
    assert next_scan.second == 0


def test_worker_prioritizes_recovery_draft_before_collection(tmp_path: Path, monkeypatch) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")
    order: list[str] = []

    def record_assistance(_database, *, drafts_only=False):
        order.append("draft" if drafts_only else "assistance")
        return None

    class OrderedCollector(RecordingCollector):
        def scan(self, **kwargs):
            order.append("collection")
            return super().scan(**kwargs)

    monkeypatch.setattr(
        "open_source_ai_news_wire.runner.run_assistance_work", record_assistance
    )

    Worker(database, collector=OrderedCollector()).run(trigger="scheduled")

    assert order == ["draft", "collection", "assistance"]


def test_worker_skips_end_of_run_assistance_when_full_retry_would_overrun(
    tmp_path: Path, monkeypatch
) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")
    order: list[str] = []
    start = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)

    class WorkerClock(datetime):
        calls = 0

        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            value = start if cls.calls == 1 else start + timedelta(minutes=20)
            return value if tz is not None else value.replace(tzinfo=None)

    def record_assistance(_database, *, drafts_only=False):
        order.append("draft" if drafts_only else "assistance")
        return None

    class OrderedCollector(RecordingCollector):
        def scan(self, **kwargs):
            order.append("collection")
            return super().scan(**kwargs)

    monkeypatch.setattr("open_source_ai_news_wire.runner.datetime", WorkerClock)
    monkeypatch.setattr(
        "open_source_ai_news_wire.runner.run_assistance_work", record_assistance
    )

    Worker(database, collector=OrderedCollector()).run(trigger="manual")

    assert order == ["draft", "collection"]


def test_launchagent_install_pause_resume_and_uninstall_are_atomic(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    launcher = tmp_path / "bin" / "open-source-ai-news-wire"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o700)
    manager = LaunchAgentManager(database, launcher=launcher)
    manager.plist_path = tmp_path / "LaunchAgents" / f"{LABEL}.plist"
    loaded = {"value": False}

    def fake_run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "print":
            return subprocess.CompletedProcess(arguments, 0 if loaded["value"] else 113, "", "")
        if arguments[0] == "bootstrap":
            loaded["value"] = True
        elif arguments[0] == "bootout":
            loaded["value"] = False
        return subprocess.CompletedProcess(arguments, 0, "", "")

    manager._run = fake_run  # type: ignore[method-assign]
    installed = manager.install()
    payload = plistlib.loads(manager.plist_path.read_bytes())

    assert installed.installed is True
    assert installed.loaded is True
    assert payload["ProgramArguments"] == [str(launcher.resolve()), "scan", "--trigger", "scheduled"]
    assert payload["StartCalendarInterval"] == [{"Minute": 0}, {"Minute": 30}]
    assert payload["RunAtLoad"] is True
    assert manager.install().loaded is True
    assert manager.pause().state == "paused"
    assert manager.resume().state == "active"
    assert manager.resume().state == "active"
    manager.kickstart()
    assert manager.uninstall().state == "not_installed"
    assert manager.uninstall().state == "not_installed"
    assert manager.plist_path.exists() is False
    assert database.get_state("schedule_installed") == "false"
    with pytest.raises(SchedulerError, match="not installed"):
        manager.pause()
    with pytest.raises(SchedulerError, match="not installed"):
        manager.resume()
    with pytest.raises(SchedulerError, match="not active"):
        manager.kickstart()


def test_launchagent_rejects_missing_launcher_and_reports_launchctl_errors(tmp_path: Path, monkeypatch) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    manager = LaunchAgentManager(database, launcher=tmp_path / "missing")
    manager.plist_path = tmp_path / "LaunchAgents" / f"{LABEL}.plist"
    with pytest.raises(SchedulerError, match="does not exist"):
        manager.install()

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "launchd denied"),
    )
    with pytest.raises(SchedulerError, match="launchd denied"):
        manager._run("bootstrap", "gui/1", "/tmp/test.plist")
    assert manager._run("print", "gui/1/test", check=False).returncode == 1


def test_launchagent_preserves_stable_launcher_symlink(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    release_launcher = tmp_path / "releases" / "v1" / "launcher"
    release_launcher.parent.mkdir(parents=True)
    release_launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    release_launcher.chmod(0o700)
    stable_launcher = tmp_path / "bin" / "open-source-ai-news-wire"
    stable_launcher.parent.mkdir()
    stable_launcher.symlink_to(release_launcher)

    manager = LaunchAgentManager(database, launcher=stable_launcher)

    assert manager.launcher == stable_launcher.absolute()
    assert manager._plist()["ProgramArguments"] == [
        str(stable_launcher.absolute()),
        "scan",
        "--trigger",
        "scheduled",
    ]


def test_worker_maintains_promoted_expired_and_active_watches(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    now = datetime.now(UTC).replace(microsecond=0)
    future = (now + timedelta(hours=4)).isoformat().replace("+00:00", "Z")
    past = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    old = (now - timedelta(hours=7)).isoformat().replace("+00:00", "Z")
    current = now.isoformat().replace("+00:00", "Z")
    database.execute("UPDATE story_cluster SET status='candidate' WHERE id='story-demo-runtime-001'")
    database.execute("UPDATE story_cluster SET status='watch' WHERE id='story-demo-policy-002'")
    database.execute("UPDATE story_cluster SET status='watch' WHERE id='story-demo-watch-003'")
    for story_id, expires, created in (
        ("story-demo-runtime-001", future, current),
        ("story-demo-policy-002", past, old),
        ("story-demo-watch-003", future, old),
    ):
        database.execute(
            "INSERT INTO watch_notice(story_id,reason,status,expires_at,created_at,updated_at) VALUES(?, 'test', 'active', ?, ?, ?)",
            (story_id, expires, created, created),
        )

    Worker(database, collector=RecordingCollector()).run()

    statuses = {
        row["story_id"]: row["status"]
        for row in database.query("SELECT story_id,status FROM watch_notice")
    }
    assert statuses == {
        "story-demo-runtime-001": "promoted",
        "story-demo-policy-002": "expired",
        "story-demo-watch-003": "active",
    }
    assert database.one(
        "SELECT next_check_at FROM watch_notice WHERE story_id='story-demo-watch-003'"
    )["next_check_at"] > current


def test_worker_marks_claimed_work_failed_and_contains_assistance_failure(tmp_path: Path, monkeypatch) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    database.execute(
        "INSERT INTO work_item(kind,status,priority,payload_json,created_at,updated_at) VALUES('scout_scan','queued',1,'{}',?,?)",
        (now, now),
    )

    class FailingCollector:
        def scan(self, **_kwargs):
            raise RuntimeError("collector failed")

    with pytest.raises(RuntimeError, match="collector failed"):
        Worker(database, collector=FailingCollector()).run()
    assert database.one("SELECT status FROM work_item") == {"status": "failed"}

    database.set_state("assistance_enabled", "true", now)

    def failing_assistance(*_args, **_kwargs):
        raise RuntimeError("safe failure")

    monkeypatch.setattr(
        "open_source_ai_news_wire.runner.run_assistance_work", failing_assistance
    )
    Worker(database, collector=RecordingCollector()).run()

    def deferred_assistance(*_args, **_kwargs):
        raise AssistanceDeferred("budget")

    monkeypatch.setattr(
        "open_source_ai_news_wire.runner.run_assistance_work", deferred_assistance
    )
    Worker(database, collector=RecordingCollector()).run()


def test_worker_recovery_and_extended_coalescing_edges(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    worker = Worker(database, collector=RecordingCollector())
    database.set_state("last_scan_at", "invalid", "2026-07-14T00:00:00Z")
    trigger, start, end = worker._recovery_interval("scheduled", None, None)
    assert trigger == "recovery" and start and end
    assert worker._recovery_interval("manual", None, None) == ("manual", None, None)

    lock = ScanLock(database.paths.operations / "locks" / "scan.lock")
    lock.acquire()
    try:
        result = Worker(database, collector=RecordingCollector()).run(
            trigger="extended", interval_start="2026-07-01T00:00:00Z", interval_end="2026-07-02T00:00:00Z"
        )
    finally:
        lock.release()
    assert result.coalesced is True
    assert database.one("SELECT idempotency_key FROM work_item") == {
        "idempotency_key": "coalesced-catch-up"
    }
