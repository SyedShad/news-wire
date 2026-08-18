from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import httpx

from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.hosted_bridge import (
    BRIDGE_VERSION,
    HostedBridge,
    HostedBridgeLaunchAgent,
    HostedBridgeStatus,
    SignedHostedClient,
    build_dashboard_snapshot,
    build_resource_projection,
    build_story_projection,
    load_bridge_config,
    save_bridge_config,
)
from open_source_ai_news_wire.services import DashboardService
from open_source_ai_news_wire.storage import Database


def _service(tmp_path: Path) -> DashboardService:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    return DashboardService(database)


def test_snapshot_contains_dashboard_surfaces_without_local_paths_or_credentials(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.database.execute(
        "INSERT INTO diagnostic_event(level, event_type, message, created_at) VALUES(?, ?, ?, ?)",
        ("error", "fixture", f"Could not open {service.database.paths.root}/private.txt", "2026-08-03T00:00:00Z"),
    )
    snapshot = build_dashboard_snapshot(service)

    assert snapshot["schema_version"] == 1
    assert snapshot["overview"]["counts"]["review_now"] == 4
    assert snapshot["stories"]
    assert snapshot["drafts"]
    assert snapshot["sources"]
    assert snapshot["notices"]
    assert snapshot["settings"]["app_version"]
    encoded = json.dumps(snapshot)
    assert str(service.database.paths.root) not in encoded
    assert "[local path]" in encoded
    assert "password" not in encoded.lower()
    assert len(encoded.encode("utf-8")) < 1_450_000


def test_signed_client_covers_method_path_timestamp_nonce_and_body(tmp_path: Path) -> None:
    secret = "test-bridge-secret-with-at-least-thirty-two-characters"

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        timestamp = request.headers["x-news-wire-timestamp"]
        nonce = request.headers["x-news-wire-nonce"]
        canonical = "\n".join(
            (
                "v1",
                timestamp,
                nonce,
                request.method,
                request.url.path,
                hashlib.sha256(body).hexdigest(),
            )
        )
        expected = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        assert hmac.compare_digest(request.headers["x-news-wire-signature"], expected)
        assert request.headers["x-news-wire-bridge-version"] == BRIDGE_VERSION
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = SignedHostedClient(
        "https://dashboard.example.test",
        secret,
        client=httpx.Client(transport=transport),
    )
    assert client.request("PUT", "/api/bridge/snapshot", {"value": 1})["ok"] is True


class _FakeHostedClient:
    def __init__(self, command: dict[str, object] | None):
        self.command = command
        self.results: list[dict[str, object]] = []
        self.requests: list[tuple[str, str, object]] = []
        self.projection_current = False

    def request(self, method: str, path: str, payload=None):
        self.requests.append((method, path, payload))
        if method == "PUT":
            if isinstance(payload, dict) and payload.get("kind") == "state":
                return {
                    "ok": True,
                    "stories_required": not self.projection_current,
                    "resources_required": not self.projection_current,
                }
            if isinstance(payload, dict) and payload.get("kind") == "complete" and payload.get("projection") == "resources":
                self.projection_current = True
            return {"ok": True}
        if method == "GET":
            return {
                "ok": True,
                "commands": [self.command] if self.command else [],
                "read_requests": [],
            }
        if "/commands/" in path:
            self.results.append(payload)
        return {"ok": True}


def test_repeated_claim_uses_local_result_without_reexecuting_command(tmp_path: Path) -> None:
    service = _service(tmp_path)
    source = service.sources()["rows"][0]
    initial = bool(source["enabled"])
    command = {
        "id": "00000000-0000-4000-8000-000000000001",
        "operation": "source.toggle",
        "payload": {"source_id": source["id"]},
    }
    hosted = _FakeHostedClient(command)
    bridge = HostedBridge(service, hosted, service.database.paths, interval_seconds=10)  # type: ignore[arg-type]

    bridge.sync_once()
    after_first = bool(service.database.one(
        "SELECT enabled FROM source_registry WHERE id = ?", (source["id"],)
    )["enabled"])
    bridge.sync_once()
    after_second = bool(service.database.one(
        "SELECT enabled FROM source_registry WHERE id = ?", (source["id"],)
    )["enabled"])

    assert after_first is not initial
    assert after_second is after_first
    assert len(hosted.results) == 2
    assert hosted.results[0] == hosted.results[1]
    assert any(path == "/api/bridge/sync" for _, path, _ in hosted.requests)
    assert any(
        isinstance(payload, dict) and payload.get("kind") == "stories"
        for _, _, payload in hosted.requests
    )
    assert any(
        isinstance(payload, dict) and payload.get("kind") == "resources"
        for _, _, payload in hosted.requests
    )


def test_story_projection_is_normalized_ranked_and_stable(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first, first_digest = build_story_projection(service)
    second, second_digest = build_story_projection(service)

    assert first
    assert first == second
    assert first_digest == second_digest
    assert len(first_digest) == 64
    assert [story["priority_rank"] for story in first] == list(range(len(first)))
    assert sorted(int(story["newest_rank"]) for story in first) == list(range(len(first)))

    resources, resource_digest = build_resource_projection(service)
    assert resources
    assert len(resource_digest) == 64
    assert {item["resource_type"] for item in resources} >= {
        "story_detail", "draft_summary", "draft_detail", "source", "schedule", "settings"
    }


def test_on_demand_read_request_returns_redacted_detail(tmp_path: Path) -> None:
    service = _service(tmp_path)
    story_id = str(service.list_story_page(window="all", page_size=1)["stories"][0]["id"])

    class ReadClient(_FakeHostedClient):
        def request(self, method: str, path: str, payload=None):
            if method == "GET":
                return {
                    "ok": True,
                    "commands": [],
                    "read_requests": [{
                        "id": "00000000-0000-4000-8000-000000000002",
                        "resourceType": "story",
                        "resourceId": story_id,
                    }],
                }
            return super().request(method, path, payload)

    hosted = ReadClient(None)
    result = HostedBridge(service, hosted, service.database.paths).sync_once()  # type: ignore[arg-type]
    read_results = [
        payload for method, path, payload in hosted.requests
        if method == "POST" and "/reads/" in path
    ]
    assert result["reads_completed"] == 1
    assert read_results and read_results[0]["ok"] is True
    assert str(service.database.paths.root) not in json.dumps(read_results[0])


def test_start_kickstarts_a_loaded_but_stopped_launch_agent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    manager = HostedBridgeLaunchAgent(
        service.database,
        "https://dashboard.example.test",
        launcher=tmp_path / "launcher",
    )
    manager.plist_path = tmp_path / "bridge.plist"
    manager.plist_path.write_text("fixture", encoding="utf-8")
    stopped = HostedBridgeStatus(True, True, False, "stopped", manager.plist_path, 0)
    running = HostedBridgeStatus(True, True, True, "running", manager.plist_path, 0)
    statuses = iter((stopped, running))
    manager.status = lambda: next(statuses)  # type: ignore[method-assign]
    calls: list[tuple[str, ...]] = []
    manager._run = lambda *args, **kwargs: calls.append(args)  # type: ignore[method-assign]

    assert manager.start().state == "running"
    assert calls == [("kickstart", "-k", f"{manager.domain}/com.opensourceainewswire.hostedbridge")]


def test_bridge_config_accepts_https_and_localhost_development_origins(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(tmp_path / "wire-data")
    save_bridge_config(paths, "https://dashboard.example.test")
    assert load_bridge_config(paths) == "https://dashboard.example.test"
    save_bridge_config(paths, "http://localhost:3000")
    assert load_bridge_config(paths) == "http://localhost:3000"
