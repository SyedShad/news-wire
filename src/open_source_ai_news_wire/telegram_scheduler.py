"""Per-user macOS LaunchAgent lifecycle for Telegram delivery."""

from __future__ import annotations

import os
import fcntl
import plistlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


TELEGRAM_LABEL = "com.opensourceainewswire.telegram"
DISPATCH_INTERVAL_SECONDS = 60
DISPATCH_MAX_SECONDS = 50


class TelegramSchedulerError(RuntimeError):
    """Raised when the Telegram LaunchAgent cannot be managed."""


@dataclass(frozen=True, slots=True)
class TelegramSchedulerStatus:
    installed: bool
    loaded: bool
    state: str
    plist_path: Path
    error: str | None = None


class TelegramSetupLock:
    """Serialize Telegram lifecycle changes for one local runtime."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self, *, blocking: bool = True) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        handle = self.path.open("a+", encoding="utf-8")
        os.chmod(self.path, 0o600)
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None

    def __enter__(self):
        if not self.acquire():
            raise TelegramSchedulerError("telegram_setup_lock_unavailable")
        return self

    def __exit__(self, *_object) -> None:
        self.release()


class TelegramLaunchAgent:
    """Manage a short-lived sender process owned by the current macOS user."""

    def __init__(
        self,
        database,
        *,
        launcher: Path | None = None,
        runner=None,
    ):
        self.database = database
        selected = launcher or Path.home() / ".local" / "bin" / "open-source-ai-news-wire"
        # Preserve the stable launcher symlink. Resolving it here would pin the
        # LaunchAgent to one immutable release and break the next app upgrade.
        self.launcher = Path(os.path.abspath(Path(selected).expanduser()))
        self.plist_path = Path.home() / "Library" / "LaunchAgents" / f"{TELEGRAM_LABEL}.plist"
        self.domain = f"gui/{os.getuid()}"
        self.runner = runner or self._default_runner

    @staticmethod
    def _default_runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/launchctl", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def setup_lock(self) -> TelegramSetupLock:
        return TelegramSetupLock(self.database.paths.operations / "locks" / "telegram-setup.lock")

    def _run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = self.runner(list(arguments))
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise TelegramSchedulerError(detail or "launchctl operation failed")
        return result

    def _plist(self) -> dict[str, object]:
        logs = self.database.paths.operations / "logs"
        logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        logs.chmod(0o700)
        return {
            "Label": TELEGRAM_LABEL,
            "ProgramArguments": [
                str(self.launcher),
                "--data-root",
                str(self.database.paths.root),
                "telegram",
                "dispatch",
                "--max-seconds",
                str(DISPATCH_MAX_SECONDS),
            ],
            "RunAtLoad": True,
            "StartInterval": DISPATCH_INTERVAL_SECONDS,
            "ProcessType": "Background",
            "StandardOutPath": str(logs / "telegram.stdout.log"),
            "StandardErrorPath": str(logs / "telegram.stderr.log"),
            "EnvironmentVariables": {
                "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            },
        }

    def status(self) -> TelegramSchedulerStatus:
        installed = self.plist_path.exists()
        try:
            result = self._run("print", f"{self.domain}/{TELEGRAM_LABEL}", check=False)
        except (OSError, subprocess.SubprocessError) as error:
            return TelegramSchedulerStatus(
                installed,
                False,
                "unavailable" if installed else "not_installed",
                self.plist_path,
                "launchctl_status_unavailable",
            )
        loaded = result.returncode == 0
        if installed and loaded:
            state = "active"
        elif installed:
            state = "paused"
        elif loaded:
            state = "orphaned"
        else:
            state = "not_installed"
        return TelegramSchedulerStatus(installed, loaded, state, self.plist_path)

    def install(self) -> TelegramSchedulerStatus:
        if not self.launcher.exists():
            raise TelegramSchedulerError(f"Stable launcher does not exist: {self.launcher}")
        self.plist_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = plistlib.dumps(self._plist(), fmt=plistlib.FMT_XML, sort_keys=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{TELEGRAM_LABEL}.", dir=self.plist_path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self.plist_path)
            os.chmod(self.plist_path, 0o600)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        self._run("bootout", self.domain, str(self.plist_path), check=False)
        self._run("bootstrap", self.domain, str(self.plist_path))
        return self.status()

    def enable(self) -> TelegramSchedulerStatus:
        # Rewrite the desired plist on every enable so a stale plist from a
        # different runtime cannot be reused under the global LaunchAgent label.
        return self.install()

    def disable(self) -> TelegramSchedulerStatus:
        if self.plist_path.exists():
            self._run("bootout", self.domain, str(self.plist_path), check=False)
        return self.status()

    pause = disable

    def resume(self) -> TelegramSchedulerStatus:
        return self.enable()

    def uninstall(self) -> TelegramSchedulerStatus:
        self._run("bootout", self.domain, str(self.plist_path), check=False)
        if self.plist_path.exists():
            self.plist_path.unlink()
        return self.status()


TelegramScheduler = TelegramLaunchAgent
