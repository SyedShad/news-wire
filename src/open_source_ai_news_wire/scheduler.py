"""User-level macOS LaunchAgent lifecycle with no administrator privileges."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .storage import Database


LABEL = "com.opensourceainewswire.scanner"


class SchedulerError(RuntimeError):
    """Raised when launchd cannot apply the requested lifecycle action."""


def next_scheduled_run(now: datetime | None = None) -> datetime:
    """Return the next half-hour LaunchAgent boundary in UTC."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    boundary = current.replace(
        minute=30 if current.minute < 30 else 0,
        second=0,
        microsecond=0,
    )
    if current.minute >= 30:
        boundary += timedelta(hours=1)
    return boundary


@dataclass(frozen=True, slots=True)
class SchedulerStatus:
    installed: bool
    loaded: bool
    state: str
    plist_path: Path


class LaunchAgentManager:
    def __init__(self, database: Database, *, launcher: Path | None = None):
        self.database = database
        selected_launcher = (launcher or Path(sys.argv[0])).expanduser()
        self.launcher = Path(os.path.abspath(selected_launcher))
        self.plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        self.domain = f"gui/{os.getuid()}"

    def _plist(self) -> dict[str, object]:
        logs = self.database.paths.operations / "logs"
        logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        return {
            "Label": LABEL,
            "ProgramArguments": [str(self.launcher), "scan", "--trigger", "scheduled"],
            "RunAtLoad": True,
            "StartCalendarInterval": [{"Minute": 0}, {"Minute": 30}],
            "ProcessType": "Background",
            "LowPriorityIO": True,
            "StandardOutPath": str(logs / "scheduler.stdout.log"),
            "StandardErrorPath": str(logs / "scheduler.stderr.log"),
            "EnvironmentVariables": {
                "OPEN_SOURCE_AI_NEWS_WIRE_DATA": str(self.database.paths.root),
                "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            },
        }

    def _run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["/bin/launchctl", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise SchedulerError(detail or f"launchctl {' '.join(arguments)} failed")
        return result

    def status(self) -> SchedulerStatus:
        installed = self.plist_path.exists()
        result = self._run("print", f"{self.domain}/{LABEL}", check=False)
        loaded = result.returncode == 0
        state = "active" if loaded else "paused" if installed else "not_installed"
        return SchedulerStatus(installed, loaded, state, self.plist_path)

    def install(self) -> SchedulerStatus:
        if not self.launcher.exists():
            raise SchedulerError(f"Stable launcher does not exist: {self.launcher}")
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = plistlib.dumps(self._plist(), fmt=plistlib.FMT_XML, sort_keys=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{LABEL}.", dir=self.plist_path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self.plist_path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        current = self.status()
        if current.loaded:
            self._run("bootout", self.domain, str(self.plist_path), check=False)
        self._run("bootstrap", self.domain, str(self.plist_path))
        return self._record_status("active")

    def pause(self) -> SchedulerStatus:
        if not self.plist_path.exists():
            raise SchedulerError("Local scheduler is not installed")
        self._run("bootout", self.domain, str(self.plist_path), check=False)
        return self._record_status("paused")

    def resume(self) -> SchedulerStatus:
        if not self.plist_path.exists():
            raise SchedulerError("Local scheduler is not installed")
        if not self.status().loaded:
            self._run("bootstrap", self.domain, str(self.plist_path))
        return self._record_status("active")

    def uninstall(self) -> SchedulerStatus:
        if self.plist_path.exists():
            self._run("bootout", self.domain, str(self.plist_path), check=False)
            self.plist_path.unlink()
        return self._record_status("not_installed")

    def kickstart(self) -> None:
        if not self.status().loaded:
            raise SchedulerError("Local scheduler is not active")
        self._run("kickstart", "-k", f"{self.domain}/{LABEL}")

    def _record_status(self, state: str) -> SchedulerStatus:
        now = datetime.now(UTC).replace(microsecond=0)
        installed = self.plist_path.exists()
        self.database.set_state("schedule_installed", "true" if installed else "false", now.isoformat().replace("+00:00", "Z"))
        self.database.set_state("schedule_status", state, now.isoformat().replace("+00:00", "Z"))
        next_run = next_scheduled_run(now)
        self.database.set_state(
            "next_scan_at",
            next_run.isoformat().replace("+00:00", "Z") if state == "active" else "Not scheduled",
            now.isoformat().replace("+00:00", "Z"),
        )
        current = self.status()
        return SchedulerStatus(installed, current.loaded, state, self.plist_path)
