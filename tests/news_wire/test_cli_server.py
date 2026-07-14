from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from open_source_ai_news_wire import cli
from open_source_ai_news_wire.server import ActivityClock, DashboardServer


def test_cli_init_seed_and_status(tmp_path: Path, capsys) -> None:
    root = tmp_path / "wire-data"
    assert cli.main(["--data-root", str(root), "init", "--demo"]) == 0
    assert "Fictional demo data added" in capsys.readouterr().out

    assert cli.main(["--data-root", str(root), "seed-demo"]) == 0
    assert "Existing data left unchanged" in capsys.readouterr().out

    assert cli.main(["--data-root", str(root), "seed-demo", "--force"]) == 0
    capsys.readouterr()
    assert cli.main(["--data-root", str(root), "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["overview"]["candidates"] == 2
    assert payload["schedule"]["background_units"] == 3


def test_cli_init_without_demo(tmp_path: Path, capsys) -> None:
    assert cli.main(["--data-root", str(tmp_path / "empty"), "init"]) == 0
    assert "Initialized local data" in capsys.readouterr().out


class FakeDashboard:
    instances: list["FakeDashboard"] = []
    should_interrupt = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.port = 43123
        self.auth_url = "http://127.0.0.1:43123/auth/test"
        self.app = SimpleNamespace(
            config={"DATABASE": SimpleNamespace(paths=SimpleNamespace(root=Path("/tmp/fake-wire")))}
        )
        self.served: bool | None = None
        self.stopped = False
        self.__class__.instances.append(self)

    def serve(self, *, open_browser: bool) -> None:
        self.served = open_browser
        if self.should_interrupt:
            raise KeyboardInterrupt

    def request_stop(self) -> None:
        self.stopped = True


def test_cli_dashboard_modes(monkeypatch, capsys) -> None:
    FakeDashboard.instances.clear()
    FakeDashboard.should_interrupt = False
    monkeypatch.setattr(cli, "DashboardServer", FakeDashboard)
    assert cli.main(["dashboard", "--no-browser", "--inactivity-minutes", "0"]) == 0
    first = FakeDashboard.instances[-1]
    assert first.kwargs["inactivity_seconds"] == 60
    assert first.served is False
    assert "One-time access link" in capsys.readouterr().out

    FakeDashboard.should_interrupt = True
    assert cli.main(["dashboard"]) == 0
    second = FakeDashboard.instances[-1]
    assert second.served is True
    assert second.stopped is True
    assert "opening in the default browser" in capsys.readouterr().out


class FakeWSGIServer:
    server_port = 48765

    def __init__(self):
        self.shutdown_called = threading.Event()
        self.closed = False
        self.served = False

    def shutdown(self) -> None:
        self.shutdown_called.set()

    def serve_forever(self) -> None:
        self.served = True

    def server_close(self) -> None:
        self.closed = True


def make_dashboard(monkeypatch, tmp_path: Path) -> tuple[DashboardServer, FakeWSGIServer]:
    app = SimpleNamespace(config={"AUTH_TOKEN": "token"})
    fake_server = FakeWSGIServer()
    monkeypatch.setattr("open_source_ai_news_wire.server.create_app", lambda **_kwargs: app)
    monkeypatch.setattr("open_source_ai_news_wire.server.make_server", lambda *_args, **_kwargs: fake_server)
    dashboard = DashboardServer(data_root=str(tmp_path), port=0, inactivity_seconds=1)
    return dashboard, fake_server


def test_activity_clock_and_dashboard_server_lifecycle(monkeypatch, tmp_path: Path) -> None:
    clock = ActivityClock()
    clock.touch()
    assert clock.idle_for() >= 0

    dashboard, fake_server = make_dashboard(monkeypatch, tmp_path)
    assert dashboard.port == 48765
    assert dashboard.auth_url == "http://127.0.0.1:48765/auth/token"
    dashboard.serve(open_browser=False)
    assert fake_server.served is True
    assert fake_server.closed is True


def test_dashboard_opens_browser_and_stop_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    dashboard, fake_server = make_dashboard(monkeypatch, tmp_path)
    opened: list[str] = []
    monkeypatch.setattr("open_source_ai_news_wire.server.webbrowser.open", opened.append)
    dashboard.serve(open_browser=True)
    assert opened == [dashboard.auth_url]

    dashboard = DashboardServer.__new__(DashboardServer)
    dashboard.server = fake_server
    dashboard._stopping = threading.Event()
    dashboard.request_stop()
    assert fake_server.shutdown_called.wait(1)
    dashboard.request_stop()


class ImmediateEvent:
    def __init__(self):
        self.stopped = False

    def wait(self, _seconds: float) -> bool:
        return False

    def is_set(self) -> bool:
        return self.stopped

    def set(self) -> None:
        self.stopped = True


def test_inactivity_monitor_requests_stop(monkeypatch) -> None:
    dashboard = DashboardServer.__new__(DashboardServer)
    dashboard._stopping = ImmediateEvent()
    dashboard.inactivity_seconds = 1
    dashboard.clock = mock.Mock(idle_for=mock.Mock(return_value=2))
    dashboard.server = FakeWSGIServer()
    dashboard._monitor_inactivity()
    assert dashboard._stopping.is_set()
