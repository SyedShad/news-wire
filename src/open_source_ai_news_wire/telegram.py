"""Local Telegram delivery for relevance notifications."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import re
import time
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

import httpx

from .storage import Database
from .telegram_topics import TOPICS


TELEGRAM_METHODS = {
    "getMe",
    "getWebhookInfo",
    "getUpdates",
    "getChat",
    "getChatMember",
    "sendMessage",
}
MAX_MESSAGE_LENGTH = 4096
MAX_SENDS_PER_RUN = 12
MIN_SEND_INTERVAL_SECONDS = 4.0
DISPATCH_DEADLINE_SECONDS = 50.0
MAX_PENDING_ROWS = 100


class TelegramError(RuntimeError):
    """A safe Telegram integration error that never contains the bot token."""


class TelegramAPIError(TelegramError):
    """A Telegram API or transport failure with redacted classification."""

    def __init__(
        self,
        error_class: str,
        *,
        retry_after: int | None = None,
        permanent: bool = False,
        channel_blocked: bool = False,
    ):
        self.error_class = error_class[:120]
        self.retry_after = retry_after
        self.permanent = permanent
        self.channel_blocked = channel_blocked
        super().__init__(self.error_class)


class _TokenRedactor(logging.Filter):
    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            return True
        if self.token and self.token in rendered:
            record.msg = rendered.replace(self.token, "<redacted-token>")
            record.args = ()
        return True


class _RedactedHTTPLogging:
    def __init__(self, token: str):
        self.filter = _TokenRedactor(token)
        self.loggers = (logging.getLogger("httpx"), logging.getLogger("httpcore"))
        self.handlers: list[logging.Handler] = []

    def __enter__(self) -> _RedactedHTTPLogging:
        for logger in self.loggers:
            logger.addFilter(self.filter)
        # Propagated records bypass ancestor logger filters.  Attach the
        # filter to active handlers as well, including pytest's caplog handler.
        seen: set[int] = set()
        for handler in list(logging.getLogger().handlers):
            if id(handler) not in seen:
                handler.addFilter(self.filter)
                self.handlers.append(handler)
                seen.add(id(handler))
        for name, logger in logging.Logger.manager.loggerDict.items():
            if not isinstance(logger, logging.Logger) or not name.startswith(("httpx", "httpcore")):
                continue
            for handler in list(logger.handlers):
                if id(handler) not in seen:
                    handler.addFilter(self.filter)
                    self.handlers.append(handler)
                    seen.add(id(handler))
        return self

    def __exit__(self, *_: object) -> None:
        for logger in self.loggers:
            logger.removeFilter(self.filter)
        for handler in self.handlers:
            handler.removeFilter(self.filter)


class _FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle: object | None = None

    def acquire(self, *, blocking: bool) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
        handle = self.handle
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        self.handle = None

    def __enter__(self) -> _FileLock:
        if not self.acquire(blocking=True):
            raise TelegramError("telegram_lock_unavailable")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class TelegramClient:
    """Small fixed-host Bot API client with bounded, non-proxied HTTPS calls."""

    def __init__(self, token: str, *, transport: httpx.AsyncBaseTransport | None = None):
        token = str(token or "").strip()
        if not re.fullmatch(r"[0-9]{1,20}:[A-Za-z0-9_-]{1,256}", token):
            raise TelegramError("telegram_token_invalid")
        self._token = token
        self._transport = transport

    def call(self, method: str, payload: dict[str, object], *, timeout: float = 10) -> object:
        if method not in TELEGRAM_METHODS:
            raise TelegramError("telegram_method_not_allowed")
        if not isinstance(payload, dict):
            raise TelegramError("telegram_payload_invalid")
        bounded_timeout = max(0.1, min(10.0, float(timeout)))
        request_payload = dict(payload)
        if method == "sendMessage":
            request_payload["allow_paid_broadcast"] = False
            request_payload["link_preview_options"] = {"is_disabled": True}
            request_payload.pop("disable_web_page_preview", None)
        try:
            with _RedactedHTTPLogging(self._token):
                status_code, body = asyncio.run(self._request(method, request_payload, bounded_timeout))
        except TelegramError:
            raise
        except (TimeoutError, httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError):
            raise TelegramAPIError("telegram_network_timeout") from None
        except httpx.HTTPError:
            raise TelegramAPIError("telegram_http_error") from None
        except OSError:
            raise TelegramAPIError("telegram_transport_error") from None
        try:
            decoded = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            self._raise_api_error(status_code, {})
        if status_code < 200 or status_code >= 300:
            self._raise_api_error(status_code, decoded)
        if not isinstance(decoded, dict) or decoded.get("ok") is not True:
            self._raise_api_error(status_code, decoded)
        result = decoded.get("result")
        if method == "getUpdates":
            if not isinstance(result, list):
                raise TelegramAPIError("telegram_invalid_response")
        elif not isinstance(result, dict):
            raise TelegramAPIError("telegram_invalid_response")
        if method == "sendMessage":
            message_id = result.get("message_id")
            if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
                raise TelegramAPIError("telegram_missing_acknowledgement")
            if request_payload.get("message_thread_id") and (
                    result.get("message_thread_id") != request_payload["message_thread_id"]
                    or not isinstance(result.get("chat"), dict)
                    or result["chat"].get("id") != request_payload.get("chat_id")):
                raise TelegramAPIError("telegram_topic_acknowledgement_mismatch")
        return result

    async def _request(self, method: str, payload: dict[str, object], timeout: float) -> tuple[int, bytes]:
        # An outer deadline bounds the entire request, not just each socket read.
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                follow_redirects=False, trust_env=False, verify=True,
                timeout=httpx.Timeout(timeout), transport=self._transport,
            ) as client:
                async with client.stream(
                    "POST", f"https://api.telegram.org/bot{self._token}/{method}", json=payload,
                ) as response:
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        total += len(chunk)
                        if total > 1_000_000:
                            raise TelegramAPIError("telegram_response_too_large")
                        chunks.append(chunk)
                    return response.status_code, b"".join(chunks)

    @staticmethod
    def _raise_api_error(status_code: int, decoded: object) -> None:
        payload = decoded if isinstance(decoded, dict) else {}
        code = payload.get("error_code")
        try:
            numeric_code = int(code)
        except (TypeError, ValueError):
            numeric_code = status_code
        parameters = payload.get("parameters")
        retry_after: int | None = None
        if isinstance(parameters, dict):
            try:
                retry_after = max(1, int(parameters.get("retry_after")))
            except (TypeError, ValueError):
                retry_after = None
        if numeric_code == 429 and retry_after is None:
            retry_after = 30
        if retry_after is not None and retry_after > 31_536_000:
            raise TelegramAPIError("telegram_retry_delay_invalid", permanent=True, channel_blocked=True)
        description = str(payload.get("description") or "").casefold()
        channel_blocked = numeric_code in {401, 403} or any(
            marker in description
            for marker in ("chat not found", "bot was kicked", "bot was blocked", "not enough rights")
        )
        permanent = numeric_code in {400, 401, 403} and numeric_code != 429
        raise TelegramAPIError(
            f"telegram_api_{numeric_code}",
            retry_after=retry_after,
            permanent=permanent,
            channel_blocked=channel_blocked,
        )


class TelegramService:
    """Durable local pairing, activation, and per-event delivery state."""

    LOCK_NAME = "telegram-dispatch.lock"

    def __init__(self, database: Database):
        self.database = database
        self.lock_path = database.paths.operations / "locks" / self.LOCK_NAME

    def _lock(self) -> _FileLock:
        return _FileLock(self.lock_path)

    def _set(self, connection, key: str, value: str, now: str) -> None:
        connection.execute(
            """
            INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, now),
        )

    def _value(self, key: str, default: str = "") -> str:
        return self.database.get_state(key, default)

    def _status_unlocked(self) -> dict[str, object]:
        chat_raw = self._value("telegram_chat_id", "")
        try:
            chat_id: int | None = int(chat_raw) if chat_raw else None
        except ValueError:
            chat_id = None
        try:
            activation_id = max(0, int(self._value("telegram_activation_event_id", "0")))
        except ValueError:
            activation_id = 0
        counts = {
            str(row["status"]): int(row["count"])
            for row in self.database.query(
                "SELECT status, COUNT(*) AS count FROM relevance_telegram_delivery GROUP BY status"
            )
        }
        paired = bool(chat_id is not None and self._value("telegram_bot_id", ""))
        activation_id = int(activation_id)
        if self._value("telegram_enabled", "false") == "true" and paired:
            pending = self.database.one(
                """
                SELECT COUNT(*) AS count
                FROM relevance_notification_event e
                LEFT JOIN relevance_telegram_delivery d ON d.event_id = e.id
                WHERE e.id > ?
                  AND (d.event_id IS NULL OR (
                       d.status = 'failed'
                       AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?)
                  ))
                """,
                (activation_id, _now_value()),
            )
            counts["pending"] = int((pending or {}).get("count") or 0)
        routing_mode = self._value("telegram_routing_mode", "single")
        if routing_mode == "topics":
            counts = {str(row["status"]): int(row["count"]) for row in self.database.query(
                "SELECT status,COUNT(*) AS count FROM relevance_telegram_topic_delivery GROUP BY status")}
            pending = self.database.one(
                """SELECT COUNT(*) AS count FROM relevance_telegram_topic_delivery
                   WHERE event_id > ? AND (status='pending' OR
                     (status='failed' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)))""",
                (activation_id, _now_value()),
            )
            counts["pending"] = int((pending or {}).get("count") or 0)
        return {
            "enabled": self._value("telegram_enabled", "false") == "true",
            "paired": paired,
            "chat_id": chat_id,
            "title": self._value("telegram_chat_title", "") or None,
            "bot_id": int(self._value("telegram_bot_id", "0") or 0) or None,
            "bot_username": self._value("telegram_bot_username", "") or None,
            "test_passed": self._value("telegram_test_passed", "false") == "true",
            "activation_event_id": activation_id,
            "blocked_reason": self._value("telegram_blocked_reason", "") or None,
            "last_success": self._value("telegram_last_success", "") or None,
            "cooldown_until": self._value("telegram_cooldown_until", "") or None,
            "last_error": self._value("telegram_last_error", "") or None,
            "counts": counts,
            "routing_mode": routing_mode,
            "topics": self.database.query(
                "SELECT topic_key,title,chat_id,message_thread_id FROM telegram_topic_route ORDER BY topic_key"),
        }

    def status(self) -> dict[str, object]:
        return self._status_unlocked()

    def pair(self, chat_id: int, title: str, bot_id: int, bot_username: str) -> dict[str, object]:
        try:
            chat_value = int(chat_id)
            bot_value = int(bot_id)
        except (TypeError, ValueError) as error:
            raise TelegramError("telegram_pairing_invalid") from error
        if not title.strip() or not bot_value or len(title) > 200 or len(bot_username) > 200:
            raise TelegramError("telegram_pairing_invalid")
        now = _now_value()
        with self._lock():
            with self.database.transaction() as connection:
                self._set(connection, "telegram_enabled", "false", now)
                self._set(connection, "telegram_test_passed", "false", now)
                self._set(connection, "telegram_chat_id", str(chat_value), now)
                self._set(connection, "telegram_chat_title", title.strip(), now)
                self._set(connection, "telegram_bot_id", str(bot_value), now)
                self._set(connection, "telegram_bot_username", bot_username.strip(), now)
                self._set(connection, "telegram_activation_event_id", "0", now)
                self._set(connection, "telegram_activation_at", "", now)
                self._set(connection, "telegram_blocked_reason", "", now)
                self._set(connection, "telegram_last_error", "", now)
                self._set(connection, "telegram_cooldown_until", "", now)
                self._set(connection, "telegram_routing_mode", "single", now)
                connection.execute("DELETE FROM telegram_topic_route")
        return self.status()

    def configure_topics(self, chat_id: int, title: str, routes: dict[str, int]) -> dict[str, object]:
        """Bind three explicit destinations. Testing/enablement follow separately.

        The caller verifies the private forum and bot membership with Telegram.
        Reconfiguration never replays events or retargets queued deliveries.
        """
        if (isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id >= 0
                or not title.strip() or len(title) > 200 or set(routes) != set(TOPICS)
                or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in routes.values())
                or len(set(routes.values())) != len(TOPICS)):
            raise TelegramError("telegram_topics_invalid")
        now = _now_value()
        with self._lock():
            with self.database.transaction() as connection:
                if not self._value("telegram_bot_id"):
                    raise TelegramError("telegram_not_paired")
                # Do not discard or send remaining work into a replacement group.
                if connection.execute("""SELECT 1 FROM relevance_telegram_topic_delivery
                    WHERE status IN ('pending','failed') AND event_id > ? LIMIT 1""",
                    (int(self._value("telegram_activation_event_id", "0")),)).fetchone():
                    raise TelegramError("telegram_topics_pending_deliveries")
                connection.execute("DELETE FROM telegram_topic_route")
                for key, thread_id in routes.items():
                    connection.execute("""INSERT INTO telegram_topic_route(
                        topic_key,chat_id,message_thread_id,title,configured_at) VALUES(?,?,?,?,?)""",
                        (key, chat_id, thread_id, TOPICS[key], now))
                for key, value in (("telegram_enabled", "false"), ("telegram_test_passed", "false"),
                        ("telegram_chat_id", str(chat_id)), ("telegram_chat_title", title.strip()),
                        ("telegram_routing_mode", "topics"), ("telegram_blocked_reason", ""),
                        ("telegram_last_error", ""), ("telegram_cooldown_until", "")):
                    self._set(connection, key, value, now)
                current = connection.execute("SELECT COALESCE(MAX(id),0) AS id FROM relevance_notification_event").fetchone()
                self._set(connection, "telegram_activation_event_id", str(current["id"]), now)
        return self.status()

    def mark_tested(self) -> dict[str, object]:
        now = _now_value()
        with self._lock():
            with self.database.transaction() as connection:
                if not self._value("telegram_chat_id", ""):
                    raise TelegramError("telegram_not_paired")
                self._set(connection, "telegram_test_passed", "true", now)
                self._set(connection, "telegram_blocked_reason", "", now)
        return self.status()

    def enable(self) -> dict[str, object]:
        now = _now_value()
        with self._lock():
            with self.database.transaction() as connection:
                if not self._value("telegram_chat_id", ""):
                    raise TelegramError("telegram_not_paired")
                if self._value("telegram_test_passed", "false") != "true":
                    raise TelegramError("telegram_test_required")
                self._set(connection, "relevance_notification_capture_enabled", "true", now)
                current = connection.execute(
                    "SELECT COALESCE(MAX(id), 0) AS value FROM relevance_notification_event"
                ).fetchone()
                activation_id = int(current["value"] or 0)
                if self._value("telegram_routing_mode", "single") == "topics":
                    connection.execute("""UPDATE relevance_telegram_topic_delivery
                        SET status='skipped',error_class='before_activation',updated_at=?
                        WHERE event_id <= ? AND status IN ('pending','failed')""", (now, activation_id))
                self._set(connection, "telegram_enabled", "true", now)
                self._set(connection, "telegram_activation_event_id", str(activation_id), now)
                self._set(connection, "telegram_activation_at", now, now)
                self._set(connection, "telegram_blocked_reason", "", now)
                self._set(connection, "telegram_last_error", "", now)
                self._set(connection, "telegram_cooldown_until", "", now)
        return self.status()

    def disable(self, *, require_test: bool = False) -> dict[str, object]:
        now = _now_value()
        with self._lock():
            with self.database.transaction() as connection:
                self._set(connection, "telegram_enabled", "false", now)
                if require_test:
                    self._set(connection, "telegram_test_passed", "false", now)
        return self.status()

    def unpair(self) -> dict[str, object]:
        now = _now_value()
        with self._lock():
            with self.database.transaction() as connection:
                for key, value in (
                    ("telegram_enabled", "false"),
                    ("telegram_test_passed", "false"),
                    ("telegram_chat_id", ""),
                    ("telegram_chat_title", ""),
                    ("telegram_bot_id", ""),
                    ("telegram_bot_username", ""),
                    ("telegram_activation_event_id", "0"),
                    ("telegram_activation_at", ""),
                    ("telegram_blocked_reason", ""),
                    ("telegram_last_error", ""),
                    ("telegram_cooldown_until", ""),
                    ("telegram_routing_mode", "single"),
                ):
                    self._set(connection, key, value, now)
                connection.execute("DELETE FROM telegram_topic_route")
        return self.status()

    def block(self, reason: str) -> None:
        now = _now_value()
        with self._lock():
            with self.database.transaction() as connection:
                self._block_unlocked(connection, reason, now)

    def _block_unlocked(self, connection, reason: str, now: str) -> None:
        self._set(connection, "telegram_enabled", "false", now)
        self._set(connection, "telegram_blocked_reason", reason[:120], now)

    def pending(
        self, *, limit: int = MAX_PENDING_ROWS, after_event_id: int = 0,
        after_delivery_id: int = 0, now: str | None = None
    ) -> list[dict[str, object]]:
        state = self._status_unlocked()
        if not state["enabled"] or not state["paired"] or state["blocked_reason"]:
            return []
        now = now or _now_value()
        if state["routing_mode"] == "topics":
            return self.database.query(
                """SELECT e.id AS event_id,e.article_key,e.canonical_url,e.title,
                    e.publisher,e.context,e.source_type,e.provenance,e.published_at,
                    e.source_reported_at,e.timestamp_status,e.detected_at,
                    d.id AS delivery_id,d.topic_key,d.chat_id,d.message_thread_id,d.attempt_count
                    FROM relevance_telegram_topic_delivery d
                    JOIN relevance_notification_event e ON e.id=d.event_id
                    WHERE e.id > ? AND d.id > ? AND
                      (d.status='pending' OR (d.status='failed' AND
                        (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?)))
                    ORDER BY d.id LIMIT ?""",
                (int(state["activation_event_id"]), after_delivery_id, now, max(1,min(1000,int(limit)))),
            )
        return self.database.query(
            """
            SELECT e.id AS event_id, e.article_key, e.canonical_url, e.title,
                   e.publisher, e.context, e.source_type, e.provenance,
                   e.published_at, e.source_reported_at, e.timestamp_status,
                   e.detected_at,
                   COALESCE(d.attempt_count, 0) AS attempt_count
            FROM relevance_notification_event e
            LEFT JOIN relevance_telegram_delivery d ON d.event_id = e.id
            WHERE e.id > ?
              AND (d.event_id IS NULL OR (
                    d.status = 'failed'
                    AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?)
              ))
            ORDER BY e.id
            LIMIT ?
            """,
            (max(int(state["activation_event_id"]), after_event_id), now, max(1, min(1000, int(limit)))),
        )

    def record(
        self,
        event_id: int,
        *,
        status: str,
        error_class: str = "",
        retry_at: str | None = None,
        provider_message_id: int | None = None,
        attempted: bool = False,
    ) -> dict[str, object]:
        allowed = {"pending", "delivered", "failed", "skipped", "expired", "dead_lettered"}
        if status not in allowed:
            raise TelegramError("telegram_delivery_status_invalid")
        now = _now_value()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM relevance_telegram_delivery WHERE event_id = ?", (event_id,)
            ).fetchone()
            terminal = {"delivered", "skipped", "expired", "dead_lettered"}
            if current and str(current["status"]) in terminal:
                row = dict(current)
            else:
                attempts = int(current["attempt_count"] or 0) if current else 0
                if attempted or status in {"failed", "delivered"}:
                    attempts += 1
                if current:
                    connection.execute(
                        """
                        UPDATE relevance_telegram_delivery
                        SET status = ?, attempt_count = ?, delivered_at = ?,
                            next_attempt_at = ?, error_class = ?,
                            provider_message_id = COALESCE(?, provider_message_id), updated_at = ?
                        WHERE event_id = ?
                        """,
                        (
                            status,
                            attempts,
                            now if status == "delivered" else None,
                            retry_at if status == "failed" else None,
                            error_class[:120],
                            provider_message_id,
                            now,
                            event_id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO relevance_telegram_delivery(
                            event_id, status, attempt_count, delivered_at,
                            next_attempt_at, error_class, provider_message_id, updated_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id,
                            status,
                            attempts,
                            now if status == "delivered" else None,
                            retry_at if status == "failed" else None,
                            error_class[:120],
                            provider_message_id,
                            now,
                        ),
                    )
                if status == "delivered":
                    self._set(connection, "telegram_last_success", now, now)
                row = connection.execute(
                    "SELECT * FROM relevance_telegram_delivery WHERE event_id = ?", (event_id,)
                ).fetchone()
        return dict(row) if row else {}

    def record_topic(self, delivery_id: int, *, status: str, error_class: str = "",
                     retry_at: str | None = None, provider_message_id: int | None = None,
                     attempted: bool = False) -> None:
        if status not in {"delivered", "failed", "skipped", "dead_lettered"}:
            raise TelegramError("telegram_delivery_status_invalid")
        now = _now_value()
        with self.database.transaction() as connection:
            changed = connection.execute(
                """UPDATE relevance_telegram_topic_delivery SET status=?,
                    attempt_count=attempt_count+?,delivered_at=?,next_attempt_at=?,
                    error_class=?,provider_message_id=COALESCE(?,provider_message_id),updated_at=?
                    WHERE id=? AND status IN ('pending','failed')""",
                (status, int(attempted or status in {"delivered", "failed"}),
                 now if status == "delivered" else None, retry_at if status == "failed" else None,
                 error_class[:120], provider_message_id, now, delivery_id),
            ).rowcount
            if changed and status == "delivered":
                self._set(connection, "telegram_last_success", now, now)


class TelegramDispatcher:
    def __init__(
        self,
        database: Database,
        *,
        token_provider: Callable[[], str],
        client_factory: Callable[[str], TelegramClient] = TelegramClient,
        now: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.database = database
        self.token_provider = token_provider
        self.client_factory = client_factory
        self.now = now or (lambda: datetime.now(UTC))
        self.monotonic = monotonic_clock
        self.sleep = sleep
        self.service = TelegramService(database)

    def _events(self):
        # Read committed eligible events in stable order across pages.
        # The per-run send limit bounds how quickly the queue drains.
        after_event_id = 0
        after_delivery_id = 0
        while True:
            rows = self.service.pending(after_event_id=after_event_id,
                after_delivery_id=after_delivery_id, now=_format_time(self.now()))
            if not rows:
                return
            for event in rows:
                after_event_id = int(event["event_id"])
                after_delivery_id = int(event.get("delivery_id") or 0)
                yield event
            if len(rows) < MAX_PENDING_ROWS:
                return

    def _record(self, event: dict[str, object], **values) -> None:
        if event.get("delivery_id"):
            self.service.record_topic(int(event["delivery_id"]), **values)
        else:
            self.service.record(int(event["event_id"]), **values)

    def dispatch(self) -> dict[str, object]:
        deadline = self.monotonic() + DISPATCH_DEADLINE_SECONDS
        lock = self.service._lock()
        if not lock.acquire(blocking=False):
            return self._result("locked")
        try:
            state = self.service.status()
            if not state["enabled"]:
                return self._result("disabled")
            if not state["paired"] or not state["test_passed"]:
                return self._result("not_ready")
            if state["blocked_reason"]:
                return self._result("blocked")
            if self._cooldown_active(state.get("cooldown_until")):
                return self._result("cooldown", cooldown_until=state.get("cooldown_until"))

            client: TelegramClient | None = None
            sent = attempted = skipped = failed = 0
            last_started: float | None = None
            for event in self._events():
                if self.monotonic() >= deadline:
                    break
                if attempted >= MAX_SENDS_PER_RUN:
                    break
                next_start = (last_started + MIN_SEND_INTERVAL_SECONDS) if last_started is not None else self.monotonic()
                wait = next_start - self.monotonic()
                persisted_slot = self.service._value("telegram_next_send_at")
                if persisted_slot:
                    try:
                        wait = max(wait, (datetime.fromisoformat(persisted_slot) - self.now()).total_seconds())
                    except (ValueError, TypeError):
                        pass
                if wait > 0:
                    if self.monotonic() + wait >= deadline:
                        break
                    self.sleep(wait)
                if self.monotonic() >= deadline:
                    break
                try:
                    message = self._message(event)
                except TelegramError as error:
                    failed += 1
                    self._record(
                        event, status="dead_lettered", error_class=str(error)
                    )
                    continue
                if client is None:
                    try:
                        token = self.token_provider()
                        client = self.client_factory(token)
                    except Exception:
                        with self.database.transaction() as connection:
                            self.service._set(connection, "telegram_last_error", "token_unavailable", _now_value())
                        return self._result("token_unavailable", attempted=attempted, skipped=skipped)
                if self.monotonic() + 0.1 >= deadline:
                    break
                # Reserve the pacing slot durably before sending, so a restart
                # or another manual dispatch still respects the group interval.
                with self.database.transaction() as connection:
                    next_slot = (self.now() + timedelta(seconds=MIN_SEND_INTERVAL_SECONDS)).isoformat()
                    self.service._set(connection, "telegram_next_send_at", next_slot, _now_value())
                    self.service._set(connection, "telegram_last_error", "", _now_value())
                last_started = self.monotonic()
                attempted += 1
                try:
                    result = client.call(
                        "sendMessage",
                        {
                            "chat_id": int(event.get("chat_id") or self.service._value("telegram_chat_id")),
                            **({"message_thread_id": int(event["message_thread_id"])}
                               if event.get("message_thread_id") else {}),
                            "text": message,
                            "link_preview_options": {"is_disabled": True},
                            "allow_paid_broadcast": False,
                        },
                        timeout=min(10.0, max(0.1, deadline - self.monotonic())),
                    )
                except TelegramAPIError as error:
                    failed += 1
                    attempts = int(event.get("attempt_count") or 0) + 1
                    if error.channel_blocked:
                        self._record(event, status="dead_lettered", error_class=error.error_class, attempted=True)
                        with self.database.transaction() as connection:
                            self.service._block_unlocked(connection, error.error_class, _now_value())
                        break
                    if error.permanent:
                        self._record(event, status="dead_lettered", error_class=error.error_class, attempted=True)
                        continue
                    delay = error.retry_after if error.retry_after is not None else min(30 * 60, 30 * (2 ** max(0, attempts - 1)))
                    retry_at = _format_time(self.now() + timedelta(seconds=delay))
                    self._record(
                        event,
                        status="failed",
                        error_class=error.error_class,
                        retry_at=retry_at,
                    )
                    if error.retry_after is not None:
                        with self.database.transaction() as connection:
                            self.service._set(
                                connection,
                                "telegram_cooldown_until",
                                retry_at,
                                _now_value(),
                            )
                        break
                    continue
                except TelegramError as error:
                    failed += 1
                    attempts = int(event.get("attempt_count") or 0) + 1
                    delay = min(30 * 60, 30 * (2 ** max(0, attempts - 1)))
                    self._record(
                        event,
                        status="failed",
                        error_class=str(error),
                        retry_at=_format_time(self.now() + timedelta(seconds=delay)),
                    )
                    continue
                message_id = result.get("message_id") if isinstance(result, dict) else None
                try:
                    provider_message_id = int(message_id) if message_id is not None else None
                except (TypeError, ValueError):
                    provider_message_id = None
                self._record(
                    event,
                    status="delivered",
                    provider_message_id=provider_message_id,
                )
                sent += 1
            status = "sent" if sent else "processed" if skipped else "failed" if failed else "idle"
            return self._result(status, attempted=attempted, delivered=sent, skipped=skipped, failed=failed)
        finally:
            lock.release()

    def _result(self, status: str, **values: object) -> dict[str, object]:
        return {
            "status": status,
            "attempted": int(values.pop("attempted", 0)),
            "delivered": int(values.pop("delivered", 0)),
            "skipped": int(values.pop("skipped", 0)),
            "failed": int(values.pop("failed", 0)),
            **values,
        }

    def _cooldown_active(self, value: object) -> bool:
        if not value:
            return False
        try:
            cooldown = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return False
        return self.now().astimezone(UTC) < cooldown.astimezone(UTC)

    def _verified_publication(self, event: dict[str, object]) -> str | None:
        """Return the original-source publication time only when it is verified."""
        if str(event.get("source_type") or "") == "discovery":
            return None
        if str(event.get("timestamp_status") or "") not in {
            "valid",
            "future_aggregator_ignored",
            "malformed_aggregator_ignored",
        }:
            return None
        source_time = str(event.get("source_reported_at") or "")
        published = str(event.get("published_at") or "")
        if not source_time or not published:
            return None
        try:
            source_moment = datetime.fromisoformat(source_time.replace("Z", "+00:00"))
            published_moment = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if source_moment.tzinfo is None or published_moment.tzinfo is None:
                return None
            source_moment = source_moment.astimezone(UTC)
            published_moment = published_moment.astimezone(UTC)
            if source_moment != published_moment:
                return None
            if source_moment > self.now().astimezone(UTC):
                return None
        except (ValueError, OverflowError):
            return None
        return published

    @staticmethod
    def _clean(value: object, maximum: int | None) -> str:
        text = " ".join(str(value or "").split())
        text = "".join(
            character
            for character in text
            if unicodedata.category(character) not in {"Cc", "Cf"}
        )
        return text[:maximum].strip() if maximum is not None else text.strip()

    def _message(self, event: dict[str, object]) -> str:
        title = self._clean(event.get("title"), 500)
        publisher = self._clean(event.get("publisher"), 120)
        context = self._clean(event.get("context"), 900)
        publication = self._clean(self._verified_publication(event), 64)
        fetched = self._clean(event.get("detected_at"), 64) or "time unavailable"
        source = self._clean(event.get("canonical_url"), None)
        if not title:
            raise TelegramError("headline_missing")
        if not publisher:
            raise TelegramError("publisher_missing")
        parsed = urlsplit(source)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise TelegramError("source_link_invalid")
        publication_line = (
            f"Published: {publication}" if publication else "Original publication date: not verified"
        )
        prefix = f"{title}\n\n{publisher}\nFetched: {fetched}\n{publication_line}\n"
        suffix = f"\n\nSource: {source}"
        available = MAX_MESSAGE_LENGTH - len((prefix + suffix).encode("utf-16-le")) // 2
        if available < 0:
            raise TelegramError("telegram_message_too_large")
        context = context.encode("utf-16-le")[:available * 2].decode("utf-16-le", errors="ignore").rstrip()
        return prefix + context + suffix


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _now_value() -> str:
    return _format_time(datetime.now(UTC))
