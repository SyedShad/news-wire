"""On-demand loopback dashboard server with inactivity shutdown."""

from __future__ import annotations

import threading
import time
import webbrowser
from dataclasses import dataclass, field
from urllib.parse import quote

from werkzeug.serving import BaseWSGIServer, make_server

from .web import create_app


@dataclass(slots=True)
class ActivityClock:
    last_seen: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def touch(self) -> None:
        with self.lock:
            self.last_seen = time.monotonic()

    def idle_for(self) -> float:
        with self.lock:
            return time.monotonic() - self.last_seen


class DashboardServer:
    def __init__(
        self,
        *,
        data_root: str | None = None,
        port: int = 0,
        inactivity_seconds: int = 1800,
        start_path: str | None = None,
    ) -> None:
        self.clock = ActivityClock()
        self.inactivity_seconds = inactivity_seconds
        self._stopping = threading.Event()
        self.start_path = start_path if start_path and start_path.startswith("/stories/") else None
        self.app = create_app(data_root=data_root, operational_controls=True)
        self.server: BaseWSGIServer = make_server("127.0.0.1", port, self.app, threaded=True)
        self.app.config["STOP_CALLBACK"] = self.request_stop
        self.app.config["TOUCH_CALLBACK"] = self.clock.touch

    @property
    def port(self) -> int:
        return int(self.server.server_port)

    @property
    def auth_url(self) -> str:
        base = f"http://127.0.0.1:{self.port}/auth/{self.app.config['AUTH_TOKEN']}"
        return f"{base}?next={quote(self.start_path, safe='/')}" if self.start_path else base

    def request_stop(self) -> None:
        if self._stopping.is_set():
            return
        self._stopping.set()
        threading.Thread(target=self._graceful_shutdown, name="wire-dashboard-stop", daemon=True).start()

    def _graceful_shutdown(self) -> None:
        # Let the final HTML response request its bundled stylesheet and favicon
        # before the loopback listener disappears.
        time.sleep(0.5)
        self.server.shutdown()

    def _monitor_inactivity(self) -> None:
        while not self._stopping.wait(5):
            if self.clock.idle_for() >= self.inactivity_seconds:
                self.request_stop()
                return

    def serve(self, *, open_browser: bool = True) -> None:
        monitor = threading.Thread(
            target=self._monitor_inactivity,
            name="wire-dashboard-inactivity",
            daemon=True,
        )
        monitor.start()
        if open_browser:
            webbrowser.open(self.auth_url)
        try:
            self.server.serve_forever()
        finally:
            self._stopping.set()
            self.server.server_close()
