"""Fail-closed shadow validation and native-notification activation gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from .notifications import NativeNotifier
from .settings import load_settings
from .storage import Database


class PilotGateError(RuntimeError):
    """Raised when live notifications cannot be activated safely."""


@dataclass(frozen=True, slots=True)
class PilotReadiness:
    ready: bool
    checked_at: str
    validation_started_at: str | None
    validation_hours_elapsed: float
    auto_activate_armed: bool
    gates: dict[str, bool]
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PilotStatus:
    state: str
    started_at: str | None
    hours_elapsed: float
    shadow_mode: bool
    notifications_enabled: bool
    validation_started_at: str | None = None
    validation_hours_elapsed: float = 0.0
    auto_activate_armed: bool = False
    notification_watermark: str | None = None
    readiness_ready: bool = False
    readiness_blockers: tuple[str, ...] = ()


class PilotManager:
    def __init__(self, database: Database, *, now: Callable[[], datetime] | None = None):
        self.database = database
        self.now = now or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        return self.now().astimezone(UTC).replace(microsecond=0)

    def _now_value(self) -> str:
        return self._now().isoformat().replace("+00:00", "Z")

    def start_shadow(self) -> PilotStatus:
        value = self._now_value()
        if not self.database.get_state("pilot_started_at", ""):
            self.database.set_state("pilot_started_at", value, value)
        self.database.set_state("pilot_status", "shadow", value)
        self.database.set_state("shadow_mode", "true", value)
        self.database.set_state("notifications_enabled", "false", value)
        self.database.set_state("pilot_auto_activate_armed", "false", value)
        self.database.set_state("pilot_notification_watermark", "", value)
        return self.status()

    def run_notification_canary(self, notifier: NativeNotifier | None = None) -> bool:
        return (notifier or NativeNotifier(self.database)).send_canary()

    def extend_validation(self, *, auto_activate: bool) -> PilotStatus:
        if not auto_activate:
            raise PilotGateError("The extended validation command requires --auto-activate")
        if self.database.get_state("notification_canary_status", "not_run") != "passed":
            raise PilotGateError("A successful native notification canary is required")
        workflow = self.database.one(
            """
            SELECT 1 AS present
            FROM story_cluster s
            WHERE EXISTS (
                SELECT 1 FROM evidence_source e
                WHERE e.story_id = s.id AND e.status = 'confirmed'
            )
              AND EXISTS (
                SELECT 1 FROM review_action r
                WHERE r.story_id = s.id AND r.action IN ('approve_neutral', 'approve_lens')
            )
              AND EXISTS (
                SELECT 1 FROM draft d
                WHERE d.story_id = s.id AND d.version >= 2
            )
            LIMIT 1
            """
        )
        if not workflow:
            raise PilotGateError(
                "A confirmed-evidence, human-approved, revised live draft is required before validation can be armed"
            )
        now = self._now_value()
        if not self.database.get_state("pilot_started_at", ""):
            self.database.set_state("pilot_started_at", now, now)
        self.database.set_state("pilot_validation_started_at", now, now)
        self.database.set_state("pilot_auto_activate_armed", "true", now)
        self.database.set_state("pilot_status", "extended_shadow", now)
        self.database.set_state("shadow_mode", "true", now)
        self.database.set_state("notifications_enabled", "false", now)
        self.database.set_state("pilot_notification_watermark", "", now)
        self.readiness()
        return self.status()

    def readiness(self) -> PilotReadiness:
        now = self._now()
        now_value = now.isoformat().replace("+00:00", "Z")
        validation_started = self.database.get_state("pilot_validation_started_at", "") or None
        elapsed = 0.0
        if validation_started:
            started = datetime.fromisoformat(validation_started.replace("Z", "+00:00"))
            elapsed = max(0.0, (now - started.astimezone(UTC)).total_seconds() / 3600)

        gates: dict[str, bool] = {}
        blockers: list[str] = []

        def gate(name: str, passed: bool, message: str) -> None:
            gates[name] = bool(passed)
            if not passed:
                blockers.append(message)

        gate("validation_started", validation_started is not None, "Extended validation has not started")
        gate("validation_window", validation_started is not None and elapsed >= 72, "The additional 72-hour validation window is incomplete")
        gate("database_integrity", self.database.integrity_check() == "ok", "Database integrity did not pass")
        gate("schema", self.database.schema_version() == 3, "The runtime schema is not version 3")
        gate(
            "scheduler",
            self.database.get_state("schedule_installed", "false") == "true"
            and self.database.get_state("schedule_status", "not_installed") == "active",
            "The local scheduler is not installed and active",
        )

        enabled_count = int(
            self.database.one(
                """
                SELECT COUNT(*) AS count
                FROM source_registry r JOIN source_state s ON s.source_id = r.id
                WHERE r.enabled = 1 AND s.enabled = 1
                """
            )["count"]
        )
        unhealthy = self.database.query(
            """
            SELECT r.id, s.health, s.failure_streak, s.last_success_at
            FROM source_registry r JOIN source_state s ON s.source_id = r.id
            WHERE r.enabled = 1 AND s.enabled = 1
              AND (
                s.health != 'healthy' OR s.failure_streak != 0 OR
                s.last_success_at IS NULL OR s.last_success_at < ?
              )
            ORDER BY r.id
            """,
            (validation_started or now_value,),
        )
        gate(
            "source_health",
            validation_started is not None and enabled_count > 0 and not unhealthy,
            "One or more enabled sources have not passed a healthy post-repair scan",
        )

        critical_count = int(
            self.database.one(
                "SELECT COUNT(*) AS count FROM diagnostic_event WHERE level = 'critical' AND created_at >= ?",
                (validation_started or now_value,),
            )["count"]
        )
        gate("critical_diagnostics", critical_count == 0, "A critical diagnostic was recorded during validation")

        queue_count = int(
            self.database.one(
                "SELECT COUNT(*) AS count FROM work_item WHERE status IN ('pending', 'queued', 'running', 'generating')"
            )["count"]
        )
        gate("work_queue", queue_count == 0, "The durable work queue is not empty")

        storage = load_settings(self.database.paths).section("storage")
        pressure = self.database.storage_pressure(
            warning_bytes=int(storage["warning_free_bytes"]),
            critical_bytes=int(storage["critical_free_bytes"]),
        )
        gate("disk", pressure.level != "critical", "Critical disk pressure blocks activation")

        assistance_ok = (
            self.database.get_state("assistance_enabled", "false") != "true"
            or self.database.get_state("assistance_isolation_gate", "not_run") == "passed"
        )
        gate("assistance_isolation", assistance_ok, "Enabled assistance has not passed isolation")
        gate(
            "notification_canary",
            self.database.get_state("notification_canary_status", "not_run") == "passed",
            "The native notification canary has not passed",
        )

        offline_rows = self.database.query(
            "SELECT created_at FROM diagnostic_event WHERE event_type = 'offline' AND created_at >= ? ORDER BY created_at",
            (validation_started or now_value,),
        )
        offline_times = [datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")) for row in offline_rows]
        storm = any(right - left < timedelta(minutes=10) for left, right in zip(offline_times, offline_times[1:]))
        gate("offline_alert_suppression", not storm, "Repeated offline diagnostics indicate an alert storm")

        armed = self.database.get_state("pilot_auto_activate_armed", "false") == "true"
        result = PilotReadiness(
            ready=not blockers,
            checked_at=now_value,
            validation_started_at=validation_started,
            validation_hours_elapsed=round(elapsed, 2),
            auto_activate_armed=armed,
            gates=gates,
            blockers=tuple(blockers),
        )
        self.database.set_state("pilot_readiness_json", Database.json(asdict(result)), now_value)
        return result

    def try_auto_activate(self) -> PilotStatus:
        if self.database.get_state("pilot_auto_activate_armed", "false") != "true":
            return self.status()
        if self.database.get_state("notifications_enabled", "false") == "true":
            return self.status()
        readiness = self.readiness()
        if readiness.ready:
            return self._enable_notifications()
        return self.status(readiness=readiness)

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
        return self._enable_notifications()

    def _enable_notifications(self) -> PilotStatus:
        now = self._now_value()
        self.database.set_state("pilot_status", "notifications", now)
        self.database.set_state("shadow_mode", "false", now)
        self.database.set_state("notifications_enabled", "true", now)
        self.database.set_state("pilot_auto_activate_armed", "false", now)
        self.database.set_state("pilot_notifications_started_at", now, now)
        self.database.set_state("pilot_notification_watermark", now, now)
        self.database.execute(
            """
            INSERT INTO diagnostic_event(level, event_type, message, created_at, detail_json)
            VALUES('info', 'pilot_activation', 'Native content notifications activated after validation gates passed.', ?, '{}')
            """,
            (now,),
        )
        return self.status()

    def stop_notifications(self) -> PilotStatus:
        now = self._now_value()
        self.database.set_state("notifications_enabled", "false", now)
        self.database.set_state("pilot_auto_activate_armed", "false", now)
        self.database.set_state("pilot_status", "paused", now)
        return self.status()

    def status(self, *, readiness: PilotReadiness | None = None) -> PilotStatus:
        now = self._now()
        started = self.database.get_state("pilot_started_at", "") or None
        validation_started = self.database.get_state("pilot_validation_started_at", "") or None

        def elapsed(value: str | None) -> float:
            if not value:
                return 0.0
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return max(0.0, (now - moment.astimezone(UTC)).total_seconds() / 3600)

        if readiness is None:
            raw = self.database.get_state("pilot_readiness_json", "")
            blockers: tuple[str, ...] = ()
            ready = False
            if raw:
                try:
                    import json

                    payload = json.loads(raw)
                    blockers = tuple(str(item) for item in payload.get("blockers", []))
                    ready = bool(payload.get("ready", False))
                except (TypeError, ValueError):
                    pass
        else:
            blockers = readiness.blockers
            ready = readiness.ready
        return PilotStatus(
            state=self.database.get_state("pilot_status", "not_started"),
            started_at=started,
            hours_elapsed=round(elapsed(started), 2),
            shadow_mode=self.database.get_state("shadow_mode", "true") == "true",
            notifications_enabled=self.database.get_state("notifications_enabled", "false") == "true",
            validation_started_at=validation_started,
            validation_hours_elapsed=round(elapsed(validation_started), 2),
            auto_activate_armed=self.database.get_state("pilot_auto_activate_armed", "false") == "true",
            notification_watermark=self.database.get_state("pilot_notification_watermark", "") or None,
            readiness_ready=ready,
            readiness_blockers=blockers,
        )
