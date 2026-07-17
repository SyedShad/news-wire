"""Native macOS notification routing with burst and duplicate suppression."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .storage import Database


NotificationRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


class NativeNotifier:
    def __init__(
        self,
        database: Database,
        *,
        launcher: Path | None = None,
        runner: NotificationRunner = _run,
        terminal_notifier: Path | None = None,
    ):
        self.database = database
        self.launcher = launcher or Path.home() / ".local" / "bin" / "open-source-ai-news-wire"
        discovered = shutil.which("terminal-notifier")
        self.terminal_notifier = terminal_notifier or (Path(discovered) if discovered else None)
        self.runner = runner

    def dispatch_pending(self) -> int:
        if self.database.get_state("notifications_enabled", "false") != "true":
            return 0
        shadow = self.database.get_state("shadow_mode", "true") == "true"
        watermark = self.database.get_state("pilot_notification_watermark", "")
        if not watermark:
            return 0
        condition = "AND a.kind IN ('health', 'recovery')" if shadow else """
        AND (
          (a.kind = 'candidate' AND a.severity IN ('high', 'urgent', 'critical')) OR
          (a.kind = 'watch' AND a.severity IN ('watch', 'high', 'urgent', 'critical')) OR
          a.kind = 'correction' OR
          (a.kind IN ('health', 'recovery') AND a.severity IN ('high', 'urgent', 'critical'))
        )
        """
        rows = self.database.query(
            f"""
            SELECT a.* FROM alert a
            LEFT JOIN notification_delivery n ON n.alert_id = a.id
            WHERE n.id IS NULL AND a.read_at IS NULL AND a.created_at >= ? {condition}
            ORDER BY a.created_at, a.id
            """,
            (watermark,),
        )
        if not rows:
            return 0
        recent_cutoff = datetime.now(UTC) - timedelta(minutes=10)
        recent = [
            row for row in rows
            if datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")) >= recent_cutoff
        ]
        if len(recent) > 5:
            counts: dict[str, int] = {}
            for row in recent:
                counts[str(row["kind"])] = counts.get(str(row["kind"]), 0) + 1
            body = ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
            success, error_class = self._notify(
                "Open Source AI News Wire",
                f"{len(recent)} new updates: {body}",
                group="news-wire-burst",
            )
            self._record(recent, "news-wire-burst", success, error_class)
            return len(recent) if success else 0
        delivered = 0
        for row in rows:
            success, error_class = self._notify(
                str(row["title"])[:180],
                str(row["body"])[:500],
                group=f"news-wire-alert-{row['id']}",
                story_id=str(row["story_id"]) if row.get("story_id") else None,
            )
            self._record([row], f"news-wire-alert-{row['id']}", success, error_class)
            delivered += int(success)
        return delivered

    def send_canary(self) -> bool:
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        success, error_class = self._notify(
            "Open Source AI News Wire",
            "Notification canary passed. Content notifications remain disabled during validation.",
            group="news-wire-canary",
        )
        self.database.set_state("notification_canary_status", "passed" if success else "failed", now)
        self.database.set_state("notification_canary_at", now, now)
        self.database.execute(
            """
            INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
            VALUES(?, 'notification_canary', ?, ?, ?)
            """,
            (
                "info" if success else "warning",
                "Native notification canary passed." if success else "Native notification canary failed.",
                now,
                Database.json({"error_class": error_class}),
            ),
        )
        return success

    def _notify(
        self,
        title: str,
        body: str,
        *,
        group: str,
        story_id: str | None = None,
    ) -> tuple[bool, str | None]:
        if self.terminal_notifier and self.terminal_notifier.exists():
            arguments = [
                str(self.terminal_notifier), "-title", title, "-message", body,
                "-group", group, "-sender", "com.openai.chat",
            ]
            if story_id and self.launcher.exists():
                arguments.extend(
                    ["-execute", shlex.join([str(self.launcher), "dashboard", "--story", story_id])]
                )
        else:
            arguments = [
                "/usr/bin/osascript",
                "-e", "on run argv",
                "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e", "end run",
                "--", title, body,
            ]
        try:
            result = self.runner(arguments)
        except Exception as error:
            return False, type(error).__name__
        return result.returncode == 0, None if result.returncode == 0 else "NotificationCommandFailed"

    def _record(
        self,
        rows: list[dict[str, Any]],
        group: str,
        success: bool,
        error_class: str | None,
    ) -> None:
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.database.transaction() as connection:
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO notification_delivery(
                        alert_id, group_key, status, delivered_at, error_class
                    ) VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(alert_id) DO NOTHING
                    """,
                    (row["id"], group, "delivered" if success else "failed", now if success else None, error_class),
                )
