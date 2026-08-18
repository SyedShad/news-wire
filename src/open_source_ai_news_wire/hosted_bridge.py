"""Outbound-only bridge between the local runtime and the hosted dashboard."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import plistlib
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from . import __version__
from .config import RuntimePaths, ensure_runtime_layout
from .runner import Worker
from .scheduler import LaunchAgentManager
from .services import DashboardService
from .storage import Database


BRIDGE_VERSION = "2.0.0"
BRIDGE_LABEL = "com.opensourceainewswire.hostedbridge"
BRIDGE_KEYCHAIN_SERVICE = "com.opensourceainewswire.hostedbridge.secret"
BRIDGE_CONFIG_NAME = "hosted-bridge.json"
BRIDGE_HISTORY_NAME = "hosted-bridge-command-history.json"
MAX_SNAPSHOT_BYTES = 1_450_000
STORY_CHUNK_SIZE = 100
SYNC_CHUNK_TARGET_BYTES = 1_250_000
ALLOWED_OPERATIONS = frozenset(
    {
        "story.review",
        "story.evidence.inspect",
        "story.evidence.confirm",
        "story.evidence.exclude",
        "story.content.create",
        "story.draft.retry",
        "draft.save",
        "source.toggle",
        "schedule.action",
        "schedule.catch_up",
        "diagnostics.purge",
        "alerts.mark_read",
        "bridge.healthcheck",
    }
)


class HostedBridgeError(RuntimeError):
    """Raised for safe, user-actionable bridge failures."""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validated_base_url(value: str) -> str:
    parsed = urlparse(value.strip())
    local_development = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if (
        (parsed.scheme != "https" and not local_development)
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise HostedBridgeError("Hosted bridge URL must be HTTPS, except for localhost development")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise HostedBridgeError("Hosted bridge URL must contain only an origin")
    return f"{parsed.scheme}://{parsed.netloc}"


def _atomic_private_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def save_bridge_config(paths: RuntimePaths, base_url: str) -> Path:
    ensure_runtime_layout(paths)
    destination = paths.config / BRIDGE_CONFIG_NAME
    _atomic_private_json(destination, {"schema_version": 1, "base_url": _validated_base_url(base_url)})
    return destination


def load_bridge_config(paths: RuntimePaths) -> str:
    path = paths.config / BRIDGE_CONFIG_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HostedBridgeError("Hosted bridge is not configured") from error
    if payload.get("schema_version") != 1:
        raise HostedBridgeError("Hosted bridge configuration is unsupported")
    return _validated_base_url(str(payload.get("base_url") or ""))


def _keychain_account(base_url: str) -> str:
    return urlparse(_validated_base_url(base_url)).netloc.lower()


def store_bridge_secret(base_url: str, secret: str) -> None:
    """Store the bridge secret in the current macOS user's Keychain."""
    if len(secret) < 32:
        raise HostedBridgeError("Bridge secret must contain at least 32 characters")
    try:
        subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-a",
                _keychain_account(base_url),
                "-s",
                BRIDGE_KEYCHAIN_SERVICE,
                "-w",
                secret,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise HostedBridgeError("The bridge secret could not be stored in Keychain") from error


def read_bridge_secret(base_url: str) -> str:
    override = os.environ.get("OPEN_SOURCE_AI_NEWS_WIRE_BRIDGE_SECRET", "").strip()
    if override:
        if len(override) < 32:
            raise HostedBridgeError("Bridge secret environment value is too short")
        return override
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-w",
                "-a",
                _keychain_account(base_url),
                "-s",
                BRIDGE_KEYCHAIN_SERVICE,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise HostedBridgeError("Hosted bridge secret is not available in Keychain") from error
    secret = result.stdout.strip()
    if len(secret) < 32:
        raise HostedBridgeError("Hosted bridge secret in Keychain is invalid")
    return secret


def delete_bridge_secret(base_url: str) -> None:
    subprocess.run(
        [
            "/usr/bin/security",
            "delete-generic-password",
            "-a",
            _keychain_account(base_url),
            "-s",
            BRIDGE_KEYCHAIN_SERVICE,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _json_safe(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(marker in lowered for marker in ("password", "secret", "token", "verifier")):
        return None
    if lowered in {"data_root", "database_path", "plist_path"}:
        return None
    if isinstance(value, dict):
        return {
            str(item_key): _json_safe(item_value, key=str(item_key))
            for item_key, item_value in value.items()
            if item_key not in {"definition_json", "payload_json", "approval_snapshot_json"}
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return re.sub(
            r"/(?:Users|private|var|tmp)/[^\s\"']+",
            "[local path]",
            value,
        )
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)


def _all_story_summaries(
    service: DashboardService,
    *,
    window: str = "all",
    sort: str = "priority",
    kind: str | None = None,
) -> list[dict[str, Any]]:
    stories: list[dict[str, Any]] = []
    cursor = ""
    while True:
        page = service.list_story_page(
            window=window,
            sort=sort,
            kind=kind,
            cursor=cursor,
            page_size=100,
        )
        stories.extend(page["stories"])
        cursor = str(page.get("next_cursor") or "")
        if not cursor:
            return stories


def build_dashboard_snapshot(service: DashboardService) -> dict[str, Any]:
    overview = service.overview()
    summaries = _all_story_summaries(service, window="review_now")
    stories: list[dict[str, Any]] = []
    for summary in summaries:
        detailed = service.get_story(str(summary["id"])) or summary
        stories.append(_json_safe(detailed))
    drafts = [_json_safe(item) for item in service.list_drafts()[:100]]
    sources = [_json_safe(item) for item in service.sources()["rows"]]
    settings = service.settings()
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "runtime_version": __version__,
        "overview": {
            "counts": _json_safe(overview["counts"]),
            "sources": _json_safe(overview["sources"]),
            "queue_count": int(overview["queue_count"]),
            "queue_lag": str(overview["queue_lag"]),
            "top_stories": _json_safe(overview["top_stories"]),
            "alerts": _json_safe(overview["alerts"]),
            "scans": _json_safe(overview["scans"]),
        },
        "stories": stories,
        "drafts": drafts,
        "sources": sources,
        "schedule": _json_safe(overview["schedule"]),
        "notices": _json_safe(service.list_inbox_notices()),
        "settings": _json_safe(
            {key: value for key, value in settings.items() if key != "diagnostics"}
        ),
        "diagnostics": _json_safe(settings["diagnostics"]),
    }
    while len(json.dumps(snapshot, separators=(",", ":")).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        if len(snapshot["stories"]) > 5:
            snapshot["stories"].pop()
        elif len(snapshot["drafts"]) > 5:
            snapshot["drafts"].pop()
        elif snapshot["diagnostics"]:
            snapshot["diagnostics"].pop()
        else:
            raise HostedBridgeError("Dashboard snapshot exceeds the hosted size limit")
    return snapshot


def build_story_projection(service: DashboardService) -> tuple[list[dict[str, Any]], str]:
    """Build a normalized, redacted index with stable local ordering ranks."""
    priority_rows = _all_story_summaries(service, window="all", sort="priority")
    newest_rows = _all_story_summaries(service, window="all", sort="newest")
    correction_ids = {
        str(item["id"])
        for item in _all_story_summaries(service, window="all", kind="correction")
    }
    newest_rank = {str(item["id"]): index for index, item in enumerate(newest_rows)}
    stories: list[dict[str, Any]] = []
    for index, item in enumerate(priority_rows):
        story = _json_safe(item)
        if not isinstance(story, dict):
            continue
        # These values drift on every call even when the corpus has not changed.
        # Freshness/current-window fields retain the meaningful time boundary.
        story.pop("age_hours", None)
        story.pop("ranking_age_hours", None)
        story["priority_rank"] = index
        story["newest_rank"] = newest_rank.get(str(item["id"]), index)
        story["is_correction"] = str(item["id"]) in correction_ids
        stories.append(story)
    encoded = json.dumps(stories, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return stories, hashlib.sha256(encoded).hexdigest()


def build_resource_projection(
    service: DashboardService,
) -> tuple[list[dict[str, Any]], str]:
    """Build normalized projections for every non-index dashboard surface."""
    resources: list[dict[str, Any]] = []

    def append(resource_type: str, resource_id: str, rank: int, payload: Any) -> None:
        safe = _json_safe(payload)
        if isinstance(safe, dict):
            if resource_type == "story_detail":
                safe.pop("age_hours", None)
                safe.pop("ranking_age_hours", None)
            resources.append({
                "resource_type": resource_type,
                "resource_id": resource_id,
                "rank": rank,
                "payload": safe,
            })

    current = [
        item for item in _all_story_summaries(service, window="review_now")
        if item.get("status") not in {"archived", "withdrawn"}
    ]
    for rank, summary in enumerate(current):
        story_id = str(summary["id"])
        append("story_detail", story_id, rank, service.get_story(story_id) or summary)

    drafts = service.list_drafts()
    for rank, draft in enumerate(drafts):
        kind = str(draft.get("entry_kind") or "draft")
        draft_id = str(draft.get("id") or rank)
        append("draft_summary", f"{kind}:{draft_id}", rank, draft)
        if kind == "draft" and draft_id.isdigit():
            detail = service.get_draft(int(draft_id))
            if detail:
                append("draft_detail", draft_id, rank, detail)

    for rank, source in enumerate(service.sources()["rows"]):
        append("source", str(source.get("id") or rank), rank, source)
    for rank, notice in enumerate(service.list_inbox_notices()):
        append("notice", str(notice.get("id") or rank), rank, notice)

    schedule = service.schedule_status()
    settings = service.settings()
    append("schedule", "current", 0, schedule)
    append("settings", "current", 0, {key: value for key, value in settings.items() if key != "diagnostics"})
    for rank, diagnostic in enumerate(settings["diagnostics"]):
        append("diagnostic", str(diagnostic.get("id") or rank), rank, diagnostic)

    resources.sort(key=lambda item: (item["resource_type"], item["rank"], item["resource_id"]))
    encoded = json.dumps(resources, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return resources, hashlib.sha256(encoded).hexdigest()


def _projection_chunks(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 0
    for item in items:
        item_bytes = len(json.dumps(item, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        if item_bytes > SYNC_CHUNK_TARGET_BYTES:
            raise HostedBridgeError("One redacted projection record exceeds the hosted size limit")
        if current and (len(current) >= STORY_CHUNK_SIZE or current_bytes + item_bytes > SYNC_CHUNK_TARGET_BYTES):
            chunks.append(current)
            current = []
            current_bytes = 0
        current.append(item)
        current_bytes += item_bytes
    if current:
        chunks.append(current)
    return chunks


class CommandHistory:
    def __init__(self, path: Path):
        self.path = path
        self._entries: dict[str, dict[str, Any]] = {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") == 1 and isinstance(payload.get("commands"), dict):
                self._entries = dict(payload["commands"])
        except (OSError, json.JSONDecodeError):
            pass

    def get(self, command_id: str) -> dict[str, Any] | None:
        return self._entries.get(command_id)

    def record(self, command_id: str, result: dict[str, Any]) -> None:
        self._entries[command_id] = result
        if len(self._entries) > 500:
            self._entries = dict(list(self._entries.items())[-500:])
        _atomic_private_json(self.path, {"schema_version": 1, "commands": self._entries})


class LocalCommandExecutor:
    def __init__(self, service: DashboardService):
        self.service = service

    @staticmethod
    def _string(payload: dict[str, Any], key: str, *, required: bool = True) -> str:
        value = payload.get(key)
        if value is None and not required:
            return ""
        if not isinstance(value, str) or (required and not value.strip()):
            raise ValueError(f"{key} is required")
        return value.strip()

    @staticmethod
    def _integer(payload: dict[str, Any], key: str) -> int:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} is required")
        return value

    def execute(self, operation: str, payload: dict[str, Any]) -> tuple[Any, bool]:
        if operation not in ALLOWED_OPERATIONS:
            raise ValueError("Unsupported owner operation")
        if operation == "story.review":
            result = self.service.review(
                self._string(payload, "story_id"),
                self._string(payload, "action"),
                self._string(payload, "reason", required=False),
                self._string(payload, "confirmation_version", required=False),
            )
        elif operation == "story.evidence.inspect":
            evidence_id, status = self.service.queue_evidence_inspection(
                self._string(payload, "story_id"),
                self._string(payload, "url"),
                self._string(payload, "acquisition_method"),
            )
            result = {"evidence_id": evidence_id, "status": status}
        elif operation == "story.evidence.confirm":
            raw_relationships = payload.get("relationships")
            if not isinstance(raw_relationships, dict):
                raise ValueError("relationships is required")
            result = self.service.confirm_evidence(
                self._string(payload, "story_id"),
                self._integer(payload, "evidence_id"),
                self._string(payload, "role"),
                {int(key): str(value) for key, value in raw_relationships.items()},
                first_party=bool(payload.get("first_party")),
                reason=self._string(payload, "reason", required=False),
                provenance_type=self._string(payload, "provenance_type", required=False) or "unknown",
                origin_name=self._string(payload, "origin_name", required=False),
                origin_url=self._string(payload, "origin_url", required=False),
            )
        elif operation == "story.evidence.exclude":
            result = self.service.exclude_evidence(
                self._string(payload, "story_id"),
                self._integer(payload, "evidence_id"),
                self._string(payload, "reason", required=False),
            )
        elif operation == "story.content.create":
            work_id, draft_id = self.service.create_content(
                self._string(payload, "story_id"),
                self._string(payload, "guidance", required=False),
            )
            result = {"work_item_id": work_id, "draft_id": draft_id}
        elif operation == "story.draft.retry":
            result = {"work_item_id": self.service.retry_draft(self._string(payload, "story_id"))}
        elif operation == "draft.save":
            result = {
                "draft_id": self.service.save_draft(
                    self._integer(payload, "draft_id"),
                    self._string(payload, "headline"),
                    self._string(payload, "metadata", required=False),
                    self._string(payload, "body"),
                    self._string(payload, "lens", required=False),
                    self._string(payload, "suggested_flair", required=False),
                )
            }
        elif operation == "source.toggle":
            result = {"enabled": self.service.toggle_source(self._string(payload, "source_id"))}
        elif operation == "schedule.action":
            result = {"status": self.service.schedule_action(self._string(payload, "action"))}
        elif operation == "schedule.catch_up":
            result = {
                "status": self.service.queue_extended_catchup(
                    self._string(payload, "start"), self._string(payload, "end")
                )
            }
        elif operation == "diagnostics.purge":
            result = {
                "purged": self.service.purge_operations(
                    self._string(payload, "confirmation"),
                    category_selected=payload.get("category_selected") is True,
                )
            }
        elif operation == "alerts.mark_read":
            result = {"updated": self.service.mark_alerts_read()}
        elif operation == "bridge.healthcheck":
            result = {"status": "ok", "runtime_version": __version__}
        else:
            raise ValueError("Unsupported owner operation")
        return _json_safe(result), False


class SignedHostedClient:
    def __init__(self, base_url: str, secret: str, *, client: httpx.Client | None = None):
        self.base_url = _validated_base_url(base_url)
        if len(secret) < 32:
            raise HostedBridgeError("Bridge secret must contain at least 32 characters")
        self.secret = secret.encode("utf-8")
        self.client = client or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))

    def _headers(self, method: str, path: str, body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        nonce = secrets.token_urlsafe(32)
        body_digest = hashlib.sha256(body).hexdigest()
        canonical = "\n".join(("v1", timestamp, nonce, method.upper(), path, body_digest))
        signature = hmac.new(self.secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "x-news-wire-timestamp": timestamp,
            "x-news-wire-nonce": nonce,
            "x-news-wire-signature": signature,
            "x-news-wire-bridge-version": BRIDGE_VERSION,
            "x-news-wire-runtime-version": __version__,
            "content-type": "application/json",
        }

    def request(self, method: str, path: str, payload: Any | None = None) -> dict[str, Any]:
        body = b"" if payload is None else json.dumps(
            payload, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        response = self.client.request(
            method,
            f"{self.base_url}{path}",
            content=body,
            headers=self._headers(method, path, body),
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise HostedBridgeError("Hosted dashboard returned an invalid response")
        return value


class HostedBridge:
    def __init__(
        self,
        service: DashboardService,
        client: SignedHostedClient,
        paths: RuntimePaths,
        *,
        interval_seconds: int = 10,
    ):
        self.service = service
        self.client = client
        self.paths = paths
        self.interval_seconds = max(10, interval_seconds)
        self.history = CommandHistory(paths.operations / BRIDGE_HISTORY_NAME)
        self.executor = LocalCommandExecutor(service)
        self.stop_requested = False

    def sync_once(self) -> dict[str, Any]:
        snapshot = build_dashboard_snapshot(self.service)
        stories, story_digest = build_story_projection(self.service)
        resources, resource_digest = build_resource_projection(self.service)
        sync_id = secrets.token_urlsafe(24)
        state = self.client.request(
            "PUT",
            "/api/bridge/sync",
            {
                "schema_version": 2,
                "kind": "state",
                "sync_id": sync_id,
                "story_digest": story_digest,
                "story_total": len(stories),
                "resource_digest": resource_digest,
                "resource_total": len(resources),
                "snapshot": snapshot,
            },
        )
        if state.get("stories_required") is True:
            for chunk in _projection_chunks(stories):
                self.client.request(
                    "PUT",
                    "/api/bridge/sync",
                    {
                        "schema_version": 2,
                        "kind": "stories",
                        "sync_id": sync_id,
                        "stories": chunk,
                    },
                )
            self.client.request(
                "PUT",
                "/api/bridge/sync",
                {
                    "schema_version": 2,
                    "kind": "complete",
                    "sync_id": sync_id,
                    "projection": "stories",
                    "digest": story_digest,
                    "total": len(stories),
                },
            )
        if state.get("resources_required") is True:
            for chunk in _projection_chunks(resources):
                self.client.request(
                    "PUT",
                    "/api/bridge/sync",
                    {
                        "schema_version": 2,
                        "kind": "resources",
                        "sync_id": sync_id,
                        "resources": chunk,
                    },
                )
            self.client.request(
                "PUT",
                "/api/bridge/sync",
                {
                    "schema_version": 2,
                    "kind": "complete",
                    "sync_id": sync_id,
                    "projection": "resources",
                    "digest": resource_digest,
                    "total": len(resources),
                },
            )
        command_response = self.client.request("GET", "/api/bridge/commands")
        commands = command_response.get("commands")
        if not isinstance(commands, list):
            raise HostedBridgeError("Hosted command response is invalid")
        completed = 0
        for command in commands:
            if not isinstance(command, dict):
                continue
            command_id = str(command.get("id") or "")
            cached = self.history.get(command_id)
            if cached is None:
                try:
                    payload = command.get("payload")
                    if not isinstance(payload, dict):
                        raise ValueError("Command payload is invalid")
                    result, should_stop = self.executor.execute(str(command.get("operation") or ""), payload)
                    cached = {"ok": True, "result": result}
                    self.stop_requested = self.stop_requested or should_stop
                except (LookupError, ValueError, RuntimeError) as error:
                    cached = {"ok": False, "error": str(error)[:2_000]}
                self.history.record(command_id, cached)
            self.client.request("POST", f"/api/bridge/commands/{command_id}/result", cached)
            completed += 1
        read_requests = command_response.get("read_requests")
        if not isinstance(read_requests, list):
            raise HostedBridgeError("Hosted read-request response is invalid")
        reads_completed = 0
        for request in read_requests:
            if not isinstance(request, dict):
                continue
            request_id = str(request.get("id") or "")
            resource_type = str(request.get("resourceType") or "")
            resource_id = str(request.get("resourceId") or "")
            try:
                if resource_type == "story":
                    payload = self.service.get_story(resource_id)
                elif resource_type == "draft":
                    payload = self.service.get_draft(int(resource_id))
                else:
                    raise ValueError("Unsupported read resource")
                if payload is None:
                    raise LookupError("The requested local record no longer exists")
                result = {"ok": True, "payload": _json_safe(payload)}
            except (LookupError, ValueError, RuntimeError) as error:
                result = {"ok": False, "error": str(error)[:2_000]}
            self.client.request(
                "POST", f"/api/bridge/reads/{request_id}/result", result
            )
            reads_completed += 1
        return {
            "digest": story_digest,
            "stories_uploaded": len(stories) if state.get("stories_required") is True else 0,
            "resources_uploaded": len(resources) if state.get("resources_required") is True else 0,
            "commands_completed": completed,
            "reads_completed": reads_completed,
        }

    def run_forever(self) -> None:
        delay = self.interval_seconds
        while not self.stop_requested:
            try:
                self.sync_once()
                delay = self.interval_seconds
            except (httpx.HTTPError, HostedBridgeError, OSError, ValueError) as error:
                print(
                    f"Hosted bridge sync failed ({type(error).__name__}); retrying.",
                    file=sys.stderr,
                    flush=True,
                )
                delay = min(300, max(self.interval_seconds, delay * 2))
            if not self.stop_requested:
                time.sleep(delay)


def _background_callbacks(database: Database) -> tuple[Callable[[], None], Callable[[int], None]]:
    def run_scan() -> None:
        threading.Thread(
            target=lambda: Worker(database).run(trigger="manual"),
            name="wire-hosted-manual-scan",
            daemon=True,
        ).start()

    def run_draft(work_item_id: int) -> None:
        def generate() -> None:
            try:
                from .assistance import run_assistance_work
                from .research import run_content_pipeline

                work = database.one("SELECT kind FROM work_item WHERE id = ?", (work_item_id,))
                if work and work["kind"] == "content":
                    run_content_pipeline(database, work_item_id)
                else:
                    run_assistance_work(database, work_item_id)
            except Exception:
                return

        threading.Thread(
            target=generate,
            name=f"wire-hosted-draft-{work_item_id}",
            daemon=True,
        ).start()

    return run_scan, run_draft


def create_hosted_bridge(
    database: Database,
    base_url: str,
    *,
    interval_seconds: int = 10,
    http_client: httpx.Client | None = None,
) -> HostedBridge:
    run_scan, run_draft = _background_callbacks(database)
    scheduler = LaunchAgentManager(database, launcher=Path.home() / ".local/bin/open-source-ai-news-wire")
    service = DashboardService(
        database,
        scheduler=scheduler,
        run_callback=run_scan,
        draft_callback=run_draft,
    )
    client = SignedHostedClient(base_url, read_bridge_secret(base_url), client=http_client)
    return HostedBridge(service, client, database.paths, interval_seconds=interval_seconds)


@dataclass(frozen=True, slots=True)
class HostedBridgeStatus:
    installed: bool
    loaded: bool
    running: bool
    state: str
    plist_path: Path
    last_exit_code: int | None


class HostedBridgeLaunchAgent:
    def __init__(self, database: Database, base_url: str, *, launcher: Path | None = None):
        self.database = database
        self.base_url = _validated_base_url(base_url)
        self.launcher = Path(launcher or (Path.home() / ".local/bin/open-source-ai-news-wire")).expanduser().resolve()
        self.plist_path = Path.home() / "Library" / "LaunchAgents" / f"{BRIDGE_LABEL}.plist"
        self.domain = f"gui/{os.getuid()}"

    def _run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["/bin/launchctl", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if check and result.returncode != 0:
            raise HostedBridgeError((result.stderr or result.stdout).strip() or "launchctl failed")
        return result

    def status(self) -> HostedBridgeStatus:
        installed = self.plist_path.exists()
        result = self._run("print", f"{self.domain}/{BRIDGE_LABEL}", check=False)
        loaded = result.returncode == 0
        output = f"{result.stdout}\n{result.stderr}"
        running = bool(loaded and re.search(r"\bstate\s*=\s*running\b", output))
        exit_match = re.search(r"\blast exit code\s*=\s*(-?\d+)\b", output)
        last_exit_code = int(exit_match.group(1)) if exit_match else None
        if not installed:
            state = "not_installed"
        elif running:
            state = "running"
        elif loaded and last_exit_code not in {None, 0}:
            state = "failed"
        elif loaded:
            state = "stopped"
        else:
            state = "installed"
        return HostedBridgeStatus(installed, loaded, running, state, self.plist_path, last_exit_code)

    def install(self) -> HostedBridgeStatus:
        if not self.launcher.exists():
            raise HostedBridgeError(f"Stable launcher does not exist: {self.launcher}")
        read_bridge_secret(self.base_url)
        logs = self.database.paths.operations / "logs"
        logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = plistlib.dumps(
            {
                "Label": BRIDGE_LABEL,
                "ProgramArguments": [str(self.launcher), "--data-root", str(self.database.paths.root), "hosted-bridge", "run", "--interval-seconds", "10"],
                "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},
                "ThrottleInterval": 30,
                "ProcessType": "Background",
                "StandardOutPath": str(logs / "hosted-bridge.stdout.log"),
                "StandardErrorPath": str(logs / "hosted-bridge.stderr.log"),
                "EnvironmentVariables": {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
            },
            fmt=plistlib.FMT_XML,
            sort_keys=True,
        )
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{BRIDGE_LABEL}.", dir=self.plist_path.parent)
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
        self._run("bootout", self.domain, str(self.plist_path), check=False)
        self._run("bootstrap", self.domain, str(self.plist_path))
        return self.status()

    def start(self) -> HostedBridgeStatus:
        if not self.plist_path.exists():
            raise HostedBridgeError("Hosted bridge LaunchAgent is not installed")
        current = self.status()
        if not current.loaded:
            self._run("bootstrap", self.domain, str(self.plist_path))
        else:
            self._run("kickstart", "-k", f"{self.domain}/{BRIDGE_LABEL}")
        return self.status()

    def stop(self) -> HostedBridgeStatus:
        self._run("bootout", self.domain, str(self.plist_path), check=False)
        return self.status()

    def uninstall(self) -> HostedBridgeStatus:
        self._run("bootout", self.domain, str(self.plist_path), check=False)
        if self.plist_path.exists():
            self.plist_path.unlink()
        return self.status()


def generate_bridge_secret() -> str:
    return secrets.token_urlsafe(48)
