"""Single-instance bounded worker for scheduled, manual, and recovery scans."""

from __future__ import annotations

import fcntl
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .collector import Collector, ScanSummary, utc_now
from .assistance import AssistanceDeferred, run_assistance_work
from .evidence import EvidenceEnricher
from .notifications import NativeNotifier
from .pilot import PilotManager
from .scheduler import next_scheduled_run
from .storage import Database


class ScanAlreadyRunning(RuntimeError):
    """Raised internally when another runner owns the scan lock."""


class ScanLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle: object | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = self.path.open("a+", encoding="utf-8")
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise ScanAlreadyRunning("A scan is already running") from error
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self.handle = handle

    def release(self) -> None:
        if self.handle is None:
            return
        handle = self.handle
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        self.handle = None

    def __enter__(self) -> ScanLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


@dataclass(frozen=True, slots=True)
class WorkerResult:
    status: str
    summary: ScanSummary | None
    coalesced: bool = False


class Worker:
    def __init__(self, database: Database, *, collector: Collector | None = None):
        self.database = database
        self.collector = collector or Collector(database)
        self.lock = ScanLock(database.paths.operations / "locks" / "scan.lock")

    def run(
        self,
        *,
        trigger: str = "manual",
        interval_start: str | None = None,
        interval_end: str | None = None,
    ) -> WorkerResult:
        try:
            self.lock.acquire()
        except ScanAlreadyRunning:
            self._coalesce(trigger, interval_start, interval_end)
            return WorkerResult("coalesced", None, True)
        try:
            deadline = datetime.now(UTC) + timedelta(minutes=25)
            self._process_assistance(drafts_only=True)
            actual_trigger, start, end = self._recovery_interval(
                trigger, interval_start, interval_end
            )
            queued_ids = self._claim_trigger_work(actual_trigger, start, end)
            try:
                summary = self.collector.scan(
                    trigger=actual_trigger,
                    interval_start=start,
                    interval_end=end,
                    deadline=deadline,
                )
            except Exception:
                self._finish_trigger_work(queued_ids, "failed")
                raise
            self._finish_trigger_work(queued_ids, "completed")
            now_value = utc_now()
            if self.database.get_state("schedule_status", "not_installed") == "active":
                self.database.set_state(
                    "next_scan_at",
                    next_scheduled_run().isoformat().replace("+00:00", "Z"),
                    now_value,
                )
            self._maintain_watches()
            self._process_evidence(deadline)
            # A draft may need two five-minute Codex attempts plus its retry
            # delay. Do not start end-of-run assistance unless that full bound
            # fits inside the worker's 25-minute deadline.
            if datetime.now(UTC) < deadline - timedelta(minutes=11):
                self._process_assistance()
            PilotManager(self.database).try_auto_activate()
            NativeNotifier(self.database).dispatch_pending()
            return WorkerResult(summary.result, summary)
        finally:
            self.lock.release()

    def _process_evidence(self, deadline: datetime) -> None:
        enricher = EvidenceEnricher(self.database)
        processed = 0
        while processed < 4 and datetime.now(UTC) < deadline - timedelta(minutes=2):
            if enricher.process_next() is None:
                return
            processed += 1

    def _process_assistance(self, *, drafts_only: bool = False) -> None:
        if self.database.get_state("assistance_enabled", "false") != "true":
            return
        try:
            run_assistance_work(self.database, drafts_only=drafts_only)
        except AssistanceDeferred:
            return
        except Exception:
            return

    def _maintain_watches(self) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        rows = self.database.query(
            """
            SELECT w.*, s.status AS story_status, s.detected_at
            FROM watch_notice w JOIN story_cluster s ON s.id = w.story_id
            WHERE w.status = 'active'
            """
        )
        with self.database.transaction() as connection:
            for watch in rows:
                expires = datetime.fromisoformat(str(watch["expires_at"]).replace("Z", "+00:00"))
                if watch["story_status"] == "candidate":
                    connection.execute(
                        "UPDATE watch_notice SET status = 'promoted', outcome = 'candidate', updated_at = ? WHERE id = ?",
                        (now.isoformat().replace("+00:00", "Z"), watch["id"]),
                    )
                    connection.execute(
                        "UPDATE story_cluster SET watch_status = 'Promoted' WHERE id = ?",
                        (watch["story_id"],),
                    )
                    continue
                if now >= expires:
                    connection.execute(
                        "UPDATE watch_notice SET status = 'expired', outcome = 'unconfirmed', updated_at = ? WHERE id = ?",
                        (now.isoformat().replace("+00:00", "Z"), watch["id"]),
                    )
                    connection.execute(
                        "UPDATE story_cluster SET status = 'signal', priority = 'Standard', watch_status = 'Expired', updated_at = ? WHERE id = ? AND status = 'watch'",
                        (now.isoformat().replace("+00:00", "Z"), watch["story_id"]),
                    )
                    continue
                created = datetime.fromisoformat(str(watch["created_at"]).replace("Z", "+00:00"))
                cadence = timedelta(minutes=30) if now - created <= timedelta(hours=6) else timedelta(hours=2)
                connection.execute(
                    "UPDATE watch_notice SET next_check_at = ?, updated_at = ? WHERE id = ?",
                    (
                        (now + cadence).isoformat().replace("+00:00", "Z"),
                        now.isoformat().replace("+00:00", "Z"), watch["id"],
                    ),
                )

    def _recovery_interval(
        self,
        trigger: str,
        start: str | None,
        end: str | None,
    ) -> tuple[str, str | None, str | None]:
        if trigger != "scheduled" or start or end:
            return trigger, start, end
        last_value = self.database.get_state("last_scan_at", "")
        now = datetime.now(UTC).replace(microsecond=0)
        if not last_value or last_value == "Never":
            return "recovery", (now - timedelta(hours=72)).isoformat().replace("+00:00", "Z"), now.isoformat().replace("+00:00", "Z")
        try:
            last = datetime.fromisoformat(last_value.replace("Z", "+00:00"))
        except ValueError:
            return "recovery", (now - timedelta(hours=72)).isoformat().replace("+00:00", "Z"), now.isoformat().replace("+00:00", "Z")
        if now - last <= timedelta(minutes=45):
            return trigger, start, end
        recovery_start = max(last, now - timedelta(hours=72))
        return "recovery", recovery_start.isoformat().replace("+00:00", "Z"), now.isoformat().replace("+00:00", "Z")

    def _coalesce(self, trigger: str, start: str | None, end: str | None) -> None:
        now = utc_now()
        key = "coalesced-catch-up" if trigger == "extended" else "coalesced-scout-scan"
        payload = Database.json({"trigger": trigger, "start": start, "end": end})
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO work_item(
                    kind, status, priority, payload_json, created_at, updated_at,
                    idempotency_key, available_at
                ) VALUES('scout_scan', 'queued', 100, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) WHERE idempotency_key IS NOT NULL DO UPDATE SET
                    payload_json=excluded.payload_json,
                    created_at=CASE
                        WHEN work_item.status = 'running' THEN work_item.created_at
                        ELSE excluded.created_at
                    END,
                    updated_at=excluded.updated_at,
                    available_at=CASE
                        WHEN work_item.status = 'running' THEN work_item.available_at
                        ELSE excluded.available_at
                    END,
                    last_error_class=NULL,
                    status=CASE WHEN work_item.status = 'running' THEN work_item.status ELSE 'queued' END
                """,
                (payload, now, now, key, now),
            )

    def _claim_trigger_work(self, trigger: str, start: str | None, end: str | None) -> list[int]:
        rows = self.database.query(
            """
            SELECT id, kind, payload_json FROM work_item
            WHERE kind IN ('scout_scan', 'catch_up') AND status IN ('pending', 'queued')
            ORDER BY priority DESC, created_at
            """
        )
        if not rows:
            return []
        now = utc_now()
        identifiers = [int(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in identifiers)
        self.database.execute(
            f"UPDATE work_item SET status = 'running', updated_at = ?, attempt_count = attempt_count + 1 WHERE id IN ({placeholders})",
            (now, *identifiers),
        )
        return identifiers

    def _finish_trigger_work(self, identifiers: list[int], status: str) -> None:
        if not identifiers:
            return
        placeholders = ",".join("?" for _ in identifiers)
        self.database.execute(
            f"UPDATE work_item SET status = ?, updated_at = ? WHERE id IN ({placeholders})",
            (status, utc_now(), *identifiers),
        )
