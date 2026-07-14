"""Explicit 72-hour shadow and seven-day notification pilot gates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from .storage import Database


class PilotGateError(RuntimeError):
    """Raised when live notifications cannot be activated safely."""


@dataclass(frozen=True, slots=True)
class PilotStatus:
    state: str
    started_at: str | None
    hours_elapsed: float
    shadow_mode: bool
    notifications_enabled: bool


class PilotManager:
    def __init__(self, database: Database, *, now: Callable[[], datetime] | None = None):
        self.database = database
        self.now = now or (lambda: datetime.now(UTC))

    def start_shadow(self) -> PilotStatus:
        now = self.now().astimezone(UTC).replace(microsecond=0)
        value = now.isoformat().replace("+00:00", "Z")
        self.database.set_state("pilot_status", "shadow", value)
        self.database.set_state("pilot_started_at", value, value)
        self.database.set_state("shadow_mode", "true", value)
        self.database.set_state("notifications_enabled", "false", value)
        return self.status()

    def activate_notifications(self, *, human_review_confirmed: bool) -> PilotStatus:
        status = self.status()
        if not human_review_confirmed:
            raise PilotGateError("Human confirmation of the shadow review is required")
        if not status.started_at or status.hours_elapsed < 72:
            raise PilotGateError("The 72-hour shadow window has not completed")
        if self.database.integrity_check() != "ok":
            raise PilotGateError("Database integrity must pass before notifications")
        critical = self.database.one(
            "SELECT COUNT(*) AS count FROM diagnostic_event WHERE level = 'critical'"
        )["count"]
        if int(critical):
            raise PilotGateError("Critical diagnostic findings block notifications")
        if (
            self.database.get_state("assistance_enabled", "false") == "true"
            and self.database.get_state("assistance_isolation_gate", "not_run") != "passed"
        ):
            raise PilotGateError("Enabled assistance has not passed packet isolation")
        now = self.now().astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.database.set_state("pilot_status", "notifications", now)
        self.database.set_state("shadow_mode", "false", now)
        self.database.set_state("notifications_enabled", "true", now)
        self.database.set_state("pilot_notifications_started_at", now, now)
        return self.status()

    def stop_notifications(self) -> PilotStatus:
        now = self.now().astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.database.set_state("notifications_enabled", "false", now)
        self.database.set_state("pilot_status", "paused", now)
        return self.status()

    def status(self) -> PilotStatus:
        started = self.database.get_state("pilot_started_at", "") or None
        elapsed = 0.0
        if started:
            moment = datetime.fromisoformat(started.replace("Z", "+00:00"))
            elapsed = max(0.0, (self.now().astimezone(UTC) - moment.astimezone(UTC)).total_seconds() / 3600)
        return PilotStatus(
            state=self.database.get_state("pilot_status", "not_started"),
            started_at=started,
            hours_elapsed=round(elapsed, 2),
            shadow_mode=self.database.get_state("shadow_mode", "true") == "true",
            notifications_enabled=self.database.get_state("notifications_enabled", "false") == "true",
        )
