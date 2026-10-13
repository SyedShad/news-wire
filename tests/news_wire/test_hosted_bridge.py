from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from pathlib import Path

import httpx

from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.generation import GenerationService
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
    assert snapshot["overview"]["counts"]["total"] == service.list_articles()["total"]
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


def test_bulk_projection_preserves_existing_resources_digest_and_order(tmp_path: Path, monkeypatch) -> None:
    import open_source_ai_news_wire.hosted_bridge as bridge

    service = _service(tmp_path)
    database = service.database
    original = database.one("SELECT * FROM fetched_article WHERE story_id IS NOT NULL LIMIT 1")
    assert original
    for identifier, story_id in (("compat-related", original["story_id"]), ("compat-unclustered", None)):
        row = {**original, "id": identifier, "canonical_url": f"https://example.test/{identifier}", "story_id": story_id}
        columns = list(row)
        database.execute(f"INSERT INTO fetched_article ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                         tuple(row[key] for key in columns))
    draft = database.one("SELECT * FROM draft LIMIT 1")
    assert draft
    for version in (4, 7):
        row = {key: value for key, value in draft.items() if key != "id"}
        row.update(story_id=original["story_id"], version=version)
        columns = list(row)
        database.execute(f"INSERT INTO draft ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                         tuple(row[key] for key in columns))
    manager = GenerationService(database)
    monkeypatch.setattr(manager, "catalog", lambda: {"models": [], "skills": []})
    service.generation_service = manager
    for job_id in ("compat-a", "compat-z"):
        directory = manager.root / job_id
        directory.mkdir(parents=True)
        (directory / "proof.txt").write_text("Preserved artifact bytes")
        artifacts = [{"id": "proof", "name": "proof.txt", "mime_type": "text/plain", "relative_path": "proof.txt"}]
        database.execute("""INSERT INTO generation_job
            (id,article_id,task_type,model,request_id,source_snapshot_json,status,output_text,artifacts_json,created_at,updated_at)
            VALUES(?,?,'text','test-model',?,'{}','completed',?,?,?,?)""",
            (job_id, original["id"], job_id, f"Output stored at {directory}/proof.txt", json.dumps(artifacts), "2026-10-13", "2026-10-13"))
    database.execute("INSERT INTO diagnostic_event(level,event_type,message,created_at) VALUES('info','compat',?,'2026-10-13')",
                     (f"Review file {database.paths.root}/private.txt",))
    schedule = service.schedule_status()
    monkeypatch.setattr(service, "schedule_status", lambda: schedule)
    optimized, optimized_digest = build_resource_projection(service)

    # The pre-optimization implementation obtained these values through the
    # existing detail/list APIs, one article at a time. Keep that as an oracle.
    def previous_reads(service, manager, articles):
        return ({str(article["id"]): service.get_article(str(article["id"])) for article in articles},
                {str(article["id"]): manager.list_jobs(str(article["id"])) for article in articles})
    monkeypatch.setattr(bridge, "_bulk_article_resources", previous_reads)
    previous, previous_digest = build_resource_projection(service)
    assert optimized == previous
    assert optimized_digest == previous_digest
    detail = next(row["payload"] for row in optimized if row["resource_type"] == "article_detail" and row["resource_id"] == original["id"])
    assert "compat-related" in {row["id"] for row in detail["related_sources"]}
    assert [row["version"] for row in detail["drafts"]][:2] == [7, 4]
    unclustered = next(row["payload"] for row in optimized if row["resource_id"] == "compat-unclustered")
    assert unclustered["related_sources"] == unclustered["drafts"] == []
    assert str(database.paths.root) not in json.dumps(optimized)
    assert "[local path]" in json.dumps(optimized)
    assert {row["resource_type"] for row in optimized} >= {"generation_job", "generation_artifact", "generation_artifact_chunk"}


def test_bulk_article_reads_use_one_read_only_snapshot_independent_of_feed_size(tmp_path: Path, monkeypatch) -> None:
    import open_source_ai_news_wire.hosted_bridge as bridge

    service = _service(tmp_path)
    original = service.database.one("SELECT * FROM fetched_article LIMIT 1")
    assert original
    for index in range(125):
        row = {**original, "id": f"bulk-{index}", "canonical_url": f"https://example.test/bulk-{index}"}
        columns = list(row)
        service.database.execute(f"INSERT INTO fetched_article ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                                 tuple(row[key] for key in columns))
    articles = service.list_articles(page_size=None)["articles"]
    real_connect = sqlite3.connect
    connections = []
    statements = []
    def connect(database, *args, **kwargs):
        connections.append((database, kwargs))
        connection = real_connect(database, *args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(bridge.sqlite3, "connect", connect)
    manager = GenerationService(service.database)
    details, jobs = bridge._bulk_article_resources(service, manager, articles)
    assert len(details) == len(articles) > 125
    assert jobs == {}
    assert len(connections) == 1
    assert "mode=ro" in connections[0][0] and connections[0][1]["uri"] is True
    assert statements[:2] == ["PRAGMA query_only = ON", "BEGIN"]
    assert len([sql for sql in statements if sql.startswith("SELECT")]) == 3
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)


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
        assert request.headers["oai-sites-authorization"].startswith("Bearer sites-owner-gate-token-")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = SignedHostedClient(
        "https://dashboard.example.test",
        secret,
        sites_access_token="sites-owner-gate-token-with-at-least-thirty-two-characters",
        client=httpx.Client(transport=transport),
    )
    assert client.request("PUT", "/api/bridge/snapshot", {"value": 1})["ok"] is True


def test_maximum_unicode_revision_syncs_in_bounded_signed_utf8_body(tmp_path: Path) -> None:
    service = _service(tmp_path)
    article_id = service.list_articles()["articles"][0]["id"]
    service.database.execute(
        """INSERT INTO generation_job(id,article_id,task_type,model,request_id,
           source_snapshot_json,status,output_text,created_at,updated_at)
           VALUES('unicode-original',?,'text','test-model','unicode-request','{}',
                  'completed','Original version','2026-10-13','2026-10-13')""",
        (article_id,),
    )
    text = "\U0001f680" * 200000
    revision = GenerationService(service.database).save_text("unicode-original", text)
    resources, resource_digest = build_resource_projection(service)
    # Content manifest hashes retain their existing canonical serialization.
    assert resource_digest == hashlib.sha256(
        json.dumps(resources, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    secret = "test-bridge-secret-with-at-least-thirty-two-characters"
    received_jobs = {}
    resource_body_sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        canonical = "\n".join((
            "v1", request.headers["x-news-wire-timestamp"],
            request.headers["x-news-wire-nonce"], request.method,
            request.url.path, hashlib.sha256(body).hexdigest(),
        ))
        signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        assert hmac.compare_digest(request.headers["x-news-wire-signature"], signature)
        if request.method == "GET":
            return httpx.Response(200, json={"ok": True, "commands": [], "read_requests": []})
        payload = json.loads(body)
        if payload.get("kind") == "resources":
            resource_body_sizes.append(len(body))
            assert len(body) < 1_450_000
            for resource in payload["resources"]:
                if resource["resource_type"] == "generation_job":
                    received_jobs[resource["resource_id"]] = resource["payload"]["output_text"]
                    if resource["resource_id"] == revision["id"]:
                        assert text.encode("utf-8") in body
                        assert b"\\ud83d\\ude80" not in body
        return httpx.Response(200, json={
            "ok": True, "stories_required": True, "resources_required": True,
        })

    client = SignedHostedClient(
        "https://dashboard.example.test", secret,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    bridge = HostedBridge(service, client, service.database.paths)
    bridge.sync_once()
    assert received_jobs[revision["id"]] == text
    assert received_jobs["unicode-original"] == "Original version"
    assert resource_body_sizes


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
    assert all("priority_rank" not in story and "review_score" not in story for story in first)
    assert sorted(int(story["newest_rank"]) for story in first) == list(range(len(first)))

    resources, resource_digest = build_resource_projection(service)
    assert resources
    assert len(resource_digest) == 64
    assert {item["resource_type"] for item in resources} >= {
        "article_detail", "generation_catalog", "draft_summary", "draft_detail", "source", "schedule", "settings"
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


def test_projection_changes_resume_as_small_deltas(tmp_path: Path) -> None:
    service = _service(tmp_path)

    class DeltaClient:
        def __init__(self):
            self.story_digest = ""
            self.resource_digest = ""
            self.requests: list[tuple[str, str, object]] = []

        def request(self, method: str, path: str, payload=None):
            self.requests.append((method, path, payload))
            if method == "PUT" and isinstance(payload, dict):
                if payload.get("kind") == "state":
                    return {
                        "ok": True,
                        "stories_required": payload["story_digest"] != self.story_digest,
                        "resources_required": payload["resource_digest"] != self.resource_digest,
                        "story_digest": self.story_digest,
                        "resource_digest": self.resource_digest,
                    }
                if payload.get("kind") == "complete":
                    if payload.get("projection") == "stories":
                        self.story_digest = str(payload["digest"])
                    else:
                        self.resource_digest = str(payload["digest"])
                return {"ok": True}
            if method == "GET":
                return {"ok": True, "commands": [], "read_requests": []}
            return {"ok": True}

    hosted = DeltaClient()
    bridge = HostedBridge(service, hosted, service.database.paths)  # type: ignore[arg-type]
    bridge.sync_once()
    first_request_count = len(hosted.requests)

    source = service.sources()["rows"][0]
    service.toggle_source(str(source["id"]))
    result = bridge.sync_once()
    delta_requests = hosted.requests[first_request_count:]
    resource_chunks = [
        payload for method, _, payload in delta_requests
        if method == "PUT"
        and isinstance(payload, dict)
        and payload.get("kind") == "resources"
    ]

    assert result["stories_uploaded"] == 0
    assert 0 < result["resources_uploaded"] < len(build_resource_projection(service)[0])
    assert resource_chunks
    assert all(payload["mode"] == "delta" for payload in resource_chunks)


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


def test_large_operational_history_does_not_stop_snapshot_sync(tmp_path: Path) -> None:
    service = _service(tmp_path)
    for index in range(40):
        service.database.execute(
            "INSERT INTO alert(kind,severity,title,body,created_at) VALUES('health','info',?,?,?)",
            (f"Source notice {index}", "Public source detail. " * 2500, "2026-10-13T00:00:00Z"),
        )
    total = service.operational_unread_count()
    snapshot = build_dashboard_snapshot(service)
    assert len(json.dumps(snapshot, separators=(",", ":")).encode()) <= 1_450_000
    assert snapshot["overview"]["counts"]["operational_unread"] == total
    assert len(snapshot["notices"]) < total
    resources, _ = build_resource_projection(service)
    assert len([r for r in resources if r["resource_type"] == "notice"]) == total
