from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from open_source_ai_news_wire import cli
from open_source_ai_news_wire.collector import ScanSummary
from open_source_ai_news_wire.installer import InstalledRelease
from open_source_ai_news_wire.pilot import PilotReadiness, PilotStatus
from open_source_ai_news_wire.runner import WorkerResult
from open_source_ai_news_wire.scheduler import SchedulerStatus
from open_source_ai_news_wire.storage import SCHEMA_VERSION


class FakeWorker:
    calls: list[dict[str, object]] = []

    def __init__(self, _database):
        pass

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return WorkerResult("success", ScanSummary(7, "success", 2, 0, 3, False))


def test_cli_migrate_scan_and_catch_up(monkeypatch, tmp_path: Path, capsys) -> None:
    root = tmp_path / "runtime"
    assert cli.main(["--data-root", str(root), "migrate"]) == 0
    assert f"version {SCHEMA_VERSION}" in capsys.readouterr().out

    FakeWorker.calls.clear()
    monkeypatch.setattr(cli, "Worker", FakeWorker)
    assert cli.main(["--data-root", str(root), "scan", "--trigger", "recovery"]) == 0
    scan = json.loads(capsys.readouterr().out)
    assert scan["scan"]["discovered_count"] == 3
    assert FakeWorker.calls[-1]["trigger"] == "recovery"

    assert cli.main([
        "--data-root", str(root), "catch-up", "--start", "2026-07-01", "--end", "2026-07-03"
    ]) == 0
    assert FakeWorker.calls[-1] == {
        "trigger": "extended",
        "interval_start": "2026-07-01T00:00:00Z",
        "interval_end": "2026-07-03T23:59:59Z",
    }
    with pytest.raises(SystemExit):
        cli.main(["--data-root", str(root), "catch-up", "--start", "bad", "--end", "2026-07-03"])
    with pytest.raises(SystemExit):
        cli.main(["--data-root", str(root), "catch-up", "--start", "2026-07-04", "--end", "2026-07-03"])


class FakeScheduler:
    actions: list[str] = []

    def __init__(self, _database, *, launcher=None):
        self.launcher = launcher

    def _status(self, action: str) -> SchedulerStatus:
        self.actions.append(action)
        return SchedulerStatus(True, action != "pause", action, Path("/tmp/test.plist"))

    def install(self):
        return self._status("install")

    def pause(self):
        return self._status("pause")

    def resume(self):
        return self._status("resume")

    def uninstall(self):
        return self._status("uninstall")

    def status(self):
        return self._status("status")


def test_cli_schedule_and_sources(monkeypatch, tmp_path: Path, capsys) -> None:
    root = tmp_path / "runtime"
    FakeScheduler.actions.clear()
    monkeypatch.setattr(cli, "LaunchAgentManager", FakeScheduler)
    monkeypatch.setattr(cli, "Worker", FakeWorker)
    for action in ("install", "pause", "resume", "status", "uninstall", "run-now"):
        arguments = ["--data-root", str(root), "schedule", action]
        if action == "install":
            arguments.extend(["--launcher", str(tmp_path / "wire")])
        assert cli.main(arguments) == 0
        assert json.loads(capsys.readouterr().out)["status" if action == "run-now" else "state"]
    assert FakeScheduler.actions == ["install", "pause", "resume", "status", "uninstall"]

    assert cli.main(["--data-root", str(root), "sources", "list"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 21
    assert cli.main(["--data-root", str(root), "sources", "disable", "openai-news"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert next(row for row in rows if row["id"] == "openai-news")["enabled"] == 0
    assert cli.main(["--data-root", str(root), "sources", "health"]) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit):
        cli.main(["--data-root", str(root), "sources", "enable"])


def test_cli_diagnostics_and_purge(tmp_path: Path, capsys) -> None:
    root = tmp_path / "runtime"
    output = tmp_path / "diagnostics.json"
    assert cli.main(["--data-root", str(root), "diagnostics", "export", "--output", str(output)]) == 0
    assert output.exists()
    capsys.readouterr()

    assert cli.main([
        "--data-root", str(root), "purge", "preview", "--category", "diagnostics"
    ]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert cli.main([
        "--data-root", str(root), "purge", "execute", "--plan-id", plan["plan_id"]
    ]) == 0
    assert "diagnostics" in json.loads(capsys.readouterr().out)
    with pytest.raises(SystemExit):
        cli.main(["--data-root", str(root), "purge", "execute"])


class FakeInstaller:
    uninstalled = False

    def __init__(self, _source_root, _paths, **_kwargs):
        self.launcher = Path("/tmp/wire")
        self.release = InstalledRelease("0.2.0-test", Path("/tmp/release"), True, "0.2.0", 2)

    def install(self):
        return self.release

    def list_releases(self):
        return [self.release]

    def rollback(self, release_id):
        assert release_id == self.release.release_id
        return self.release

    def uninstall_application(self):
        self.__class__.uninstalled = True


def test_cli_application_lifecycle(monkeypatch, tmp_path: Path, capsys) -> None:
    root = tmp_path / "runtime"
    monkeypatch.setattr(cli, "LocalInstaller", FakeInstaller)
    report = tmp_path / "validation.json"
    report.write_text("{}", encoding="utf-8")
    assert cli.main([
        "--data-root", str(root), "app", "install", "--source-root", str(tmp_path),
        "--validation-report", str(report),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["release_id"] == "0.2.0-test"
    assert cli.main(["--data-root", str(root), "app", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)[0]
    assert listed["schema_version"] == 2
    assert listed["verified"] is True
    assert listed["provenance"] == "verified"
    assert cli.main(["--data-root", str(root), "app", "rollback", "--release-id", "0.2.0-test"]) == 0
    assert "Active release" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["--data-root", str(root), "app", "rollback"])
    assert cli.main(["--data-root", str(root), "app", "uninstall"]) == 0
    assert FakeInstaller.uninstalled is True


def test_cli_install_does_not_initialize_prior_schema_before_installer(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    root = tmp_path / "runtime"
    database = cli._uninitialized_database(str(root))
    database.migrate()
    database.execute(
        "UPDATE meta SET value = ? WHERE key = 'schema_version'",
        (str(SCHEMA_VERSION - 1),),
    )
    monkeypatch.setattr(cli, "LocalInstaller", FakeInstaller)
    report = tmp_path / "validation.json"
    report.write_text("{}", encoding="utf-8")

    assert cli.main([
        "--data-root", str(root), "app", "install", "--source-root", str(tmp_path),
        "--validation-report", str(report),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["release_id"] == "0.2.0-test"


def test_cli_assistance_and_pilot(monkeypatch, tmp_path: Path, capsys) -> None:
    root = tmp_path / "runtime"
    monkeypatch.setattr(cli, "CodexInvoker", lambda: object())

    def pass_canary(database, _invoker):
        database.set_state("assistance_isolation_gate", "passed", "2026-07-14T00:00:00Z")
        return True

    monkeypatch.setattr(cli, "run_isolation_canary", pass_canary)
    assert cli.main(["--data-root", str(root), "assistance", "check-isolation"]) == 0
    capsys.readouterr()
    assert cli.main(["--data-root", str(root), "assistance", "enable"]) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is True
    assert cli.main(["--data-root", str(root), "assistance", "disable"]) == 0
    capsys.readouterr()

    monkeypatch.setattr(cli, "run_assistance_work", lambda _database: 42)
    assert cli.main(["--data-root", str(root), "assistance", "run-pending"]) == 0
    assert json.loads(capsys.readouterr().out)["output_id"] == 42

    class FakePilot:
        extend_calls = []

        def __init__(self, _database):
            pass

        def _status(self, state):
            return PilotStatus(state, "2026-07-14T00:00:00Z", 73, state == "shadow", state == "notifications")

        def start_shadow(self):
            return self._status("shadow")

        def activate_notifications(self, *, human_review_confirmed):
            assert human_review_confirmed is True
            return self._status("notifications")

        def readiness(self):
            return PilotReadiness(False, "2026-07-14T00:00:00Z", None, 0, False, {"validation_started": False}, ("not started",))

        def run_notification_canary(self):
            return True

        def extend_validation(self, *, auto_activate):
            self.__class__.extend_calls.append(auto_activate)
            return self._status("extended_shadow")

        def stop_notifications(self):
            return self._status("paused")

        def status(self):
            return self._status("shadow")

    monkeypatch.setattr(cli, "PilotManager", FakePilot)
    for action, extra in (
        ("start-shadow", []), ("status", []),
        ("extend-validation", []),
        ("extend-validation", ["--auto-activate"]),
        ("activate-notifications", ["--confirm-reviewed"]), ("stop-notifications", []),
    ):
        assert cli.main(["--data-root", str(root), "pilot", action, *extra]) == 0
        assert json.loads(capsys.readouterr().out)["state"]
    assert FakePilot.extend_calls == [False, True]
    assert cli.main(["--data-root", str(root), "pilot", "readiness"]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is False
    assert cli.main(["--data-root", str(root), "pilot", "notification-canary"]) == 0
    assert json.loads(capsys.readouterr().out)["notification_canary"] == "passed"
