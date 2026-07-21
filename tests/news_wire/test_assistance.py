from __future__ import annotations

import json
import subprocess
import threading
from importlib.resources import files
from pathlib import Path

import pytest

from open_source_ai_news_wire import assistance as assistance_module
from open_source_ai_news_wire.assistance import (
    ApprovalInvalidated,
    AssistanceDeferred,
    AssistanceConfigurationError,
    AssistanceError,
    AssistanceService,
    AssistanceTransientError,
    CodexInvoker,
    InvocationResult,
    _draft_source_rows,
    build_packet,
    render_citation_tokens,
    run_isolation_canary,
    run_assistance_work,
    sandbox_profile,
    validate_result,
)
from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.services import DashboardService
from open_source_ai_news_wire.storage import Database


class FakeInvoker:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.packets: list[dict[str, object]] = []

    def invoke(self, packet: dict[str, object]) -> InvocationResult:
        self.packets.append(packet)
        if self.fail:
            raise AssistanceError("blocked")
        claim_ids = [int(item["id"]) for item in packet.get("claims", [])]  # type: ignore[index, union-attr]
        operation = str(packet["operation"])
        citation = next(
            (
                str(source.get("citation_token"))
                for source in packet.get("sources", [])  # type: ignore[union-attr]
                if source.get("citation_allowed")
            ),
            "",
        )
        brief = (
            f"According to {citation}, the supplied evidence supports this development.\n\n"
            "The remaining uncertainty is stated separately from the confirmed facts."
            if operation.startswith("draft_") and citation
            else "The supplied evidence supports this compact neutral brief."
        )
        payload = {
            "schema_version": 1,
            "operation": operation,
            "supported_claim_ids": claim_ids[:2],
            "headline": "Evidence-backed AI development",
            "factual_brief": brief,
            "lens": "A separate, bounded open-source lens with a tradeoff." if operation == "draft_lens" else "",
            "notes": "",
        }
        return InvocationResult(payload, "test-model", 100, 80)


def _service(tmp_path: Path) -> tuple[Database, DashboardService]:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    return database, DashboardService(database)


def test_packet_contains_only_bounded_story_evidence(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral", "Keep this factual")
    work = database.one("SELECT id FROM work_item WHERE kind = 'draft'")

    packet = build_packet(database, int(work["id"]))
    rendered = json.dumps(packet)

    assert packet["operation"] == "draft_neutral"
    assert packet["schema_version"] == 2
    assert packet["claims"]
    assert packet["sources"]
    assert any(source["citation_token"] for source in packet["sources"])
    assert str(database.paths.root) not in rendered
    assert "Keep this factual" in rendered


def test_approved_draft_is_generated_versioned_and_accounted(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    database.set_state("assistance_isolation_gate", "passed", "2026-07-14T00:00:00Z")
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")

    draft_id = AssistanceService(database, FakeInvoker()).process_next()

    assert draft_id is not None
    assert database.one("SELECT mode, status, version FROM draft WHERE id = ?", (draft_id,)) == {
        "mode": "Neutral News Brief",
        "status": "Current",
        "version": 1,
    }
    assert database.one("SELECT status FROM story_cluster WHERE id = 'story-demo-runtime-001'") == {
        "status": "draft_ready"
    }
    assert database.one("SELECT category, effort_units FROM usage_ledger WHERE operation = 'draft_neutral'") == {
        "category": "draft",
        "effort_units": 2,
    }
    draft = database.one("SELECT body, sources_json, provenance_json FROM draft WHERE id = ?", (draft_id,))
    assert "According to [Demo Runtime Project](<https://example.invalid/runtime/release>)" in draft["body"]
    assert "[[source:" not in draft["body"]
    assert '"prompt_version":"v2"' in draft["provenance_json"]


def test_lens_draft_requires_and_preserves_separate_approved_mode(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-policy-002", "approve_lens")
    database.set_state("assistance_isolation_gate", "passed", "2026-07-14T00:00:00Z")
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")

    draft_id = AssistanceService(database, FakeInvoker()).process_next()

    draft = database.one("SELECT mode, lens FROM draft WHERE id = ?", (draft_id,))
    assert draft["mode"] == "Open-Source Lens Brief"
    assert "separate" in draft["lens"]


def test_assistance_fails_closed_on_gate_budget_and_foreign_claims(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    now = "2026-07-14T00:00:00Z"
    database.execute(
        """
        INSERT INTO work_item(kind, story_id, status, priority, payload_json, created_at, updated_at)
        VALUES('semantic', 'story-demo-watch-003', 'pending', 70, '{}', ?, ?)
        """,
        (now, now),
    )
    with pytest.raises(AssistanceDeferred, match="isolation"):
        AssistanceService(database, FakeInvoker()).process_next()

    database.set_state("assistance_isolation_gate", "passed", "2026-07-14T00:00:00Z")
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")
    database.execute(
        """
        INSERT INTO usage_ledger(category, operation, effort_units, model, result, created_at)
        VALUES('background', 'triage', 8, 'test', 'accepted', datetime('now'))
        """
    )
    with pytest.raises(AssistanceDeferred, match="exhausted"):
        AssistanceService(database, FakeInvoker()).process_next()

    packet = {
        "schema_version": 1,
        "operation": "draft_neutral",
        "claims": [{"id": 1}],
    }
    result = {
        "schema_version": 1,
        "operation": "draft_neutral",
        "supported_claim_ids": [999],
        "headline": "Title",
        "factual_brief": "Body",
        "lens": "",
        "notes": "",
    }
    with pytest.raises(AssistanceError, match="outside"):
        validate_result(packet, result)


def test_assistance_disabled_empty_queue_and_invocation_failure_are_explicit(tmp_path: Path) -> None:
    database, _ = _service(tmp_path)
    database.set_state("assistance_isolation_gate", "passed", "2026-07-14T00:00:00Z")
    with pytest.raises(AssistanceDeferred, match="disabled"):
        AssistanceService(database, FakeInvoker()).process_next()

    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")
    assert AssistanceService(database, FakeInvoker()).process_next() is None

    now = "2026-07-14T00:00:00Z"
    work_id = database.execute(
        """
        INSERT INTO work_item(kind, story_id, status, priority, payload_json, created_at, updated_at)
        VALUES('semantic', 'story-demo-watch-003', 'pending', 70, '{}', ?, ?)
        """,
        (now, now),
    )
    with pytest.raises(AssistanceError, match="blocked"):
        AssistanceService(database, FakeInvoker(fail=True)).process_next()
    assert database.one(
        "SELECT status, last_error_class FROM work_item WHERE id = ?", (work_id,)
    ) == {"status": "failed", "last_error_class": "assistance_validation_error"}


def test_transient_draft_failure_retries_once_and_accounts_retry(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    database.set_state("assistance_isolation_gate", "passed", "2026-07-14T00:00:00Z")
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")

    class FlakyInvoker(FakeInvoker):
        def invoke(self, packet):
            if not self.packets:
                self.packets.append(packet)
                raise AssistanceTransientError("codex_timeout: temporary")
            return super().invoke(packet)

    draft_id = AssistanceService(
        database,
        FlakyInvoker(),
        retry_delay_seconds=0,
        sleeper=lambda _seconds: None,
    ).process_next()

    assert draft_id is not None
    assert database.one(
        "SELECT status, attempt_count, last_error_class FROM work_item WHERE kind='draft'"
    ) == {"status": "completed", "attempt_count": 2, "last_error_class": None}
    assert database.one(
        "SELECT retry_count FROM usage_ledger WHERE category='draft' ORDER BY id DESC LIMIT 1"
    ) == {"retry_count": 1}


def test_exact_claim_does_not_process_another_draft_or_reclaim_live_lease(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    service.review("story-demo-policy-002", "approve_lens")
    database.set_state("assistance_isolation_gate", "passed", "2026-07-14T00:00:00Z")
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")
    work = database.one(
        "SELECT id FROM work_item WHERE story_id='story-demo-policy-002' AND kind='draft'"
    )

    draft_id = AssistanceService(database, FakeInvoker()).process(int(work["id"]))

    assert draft_id is not None
    assert database.one(
        "SELECT status FROM work_item WHERE story_id='story-demo-runtime-001' AND kind='draft'"
    ) == {"status": "queued"}
    database.execute(
        "UPDATE work_item SET status='generating', available_at='2999-01-01T00:00:00Z' WHERE story_id='story-demo-runtime-001'"
    )
    runtime_work = database.one(
        "SELECT id FROM work_item WHERE story_id='story-demo-runtime-001'"
    )
    assert AssistanceService(database, FakeInvoker()).process(int(runtime_work["id"])) is None


def test_immediate_and_scheduled_workers_cannot_claim_the_same_draft(
    tmp_path: Path,
) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    database.set_state("assistance_isolation_gate", "passed", "2026-07-14T00:00:00Z")
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")
    work = database.one("SELECT id FROM work_item WHERE kind = 'draft'")
    entered = threading.Event()
    release = threading.Event()
    completed: list[int | None] = []

    class BlockingInvoker(FakeInvoker):
        def invoke(self, packet):
            entered.set()
            assert release.wait(timeout=2)
            return super().invoke(packet)

    thread = threading.Thread(
        target=lambda: completed.append(
            AssistanceService(database, BlockingInvoker()).process(int(work["id"]))
        )
    )
    thread.start()
    assert entered.wait(timeout=2)

    assert AssistanceService(database, FakeInvoker()).process(int(work["id"])) is None
    release.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(completed) == 1 and completed[0] is not None
    assert database.one(
        "SELECT COUNT(*) AS count FROM draft WHERE story_id = 'story-demo-runtime-001'"
    ) == {"count": 1}
    assert database.one("SELECT COUNT(*) AS count FROM assistance_result") == {"count": 1}


def test_exact_draft_waits_with_visible_prerequisite_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    work = database.one("SELECT id FROM work_item WHERE kind = 'draft'")
    monkeypatch.setattr(assistance_module, "CodexInvoker", FakeInvoker)

    with pytest.raises(AssistanceDeferred, match="isolation"):
        run_assistance_work(database, int(work["id"]))
    assert database.one(
        "SELECT status, last_error_class FROM work_item WHERE id = ?", (work["id"],)
    ) == {"status": "waiting", "last_error_class": "isolation_not_passed"}

    database.execute(
        "UPDATE work_item SET status = 'queued', last_error_class = NULL WHERE id = ?",
        (work["id"],),
    )
    database.set_state("assistance_isolation_gate", "passed", "2026-07-14T00:00:00Z")
    with pytest.raises(AssistanceDeferred, match="disabled"):
        run_assistance_work(database, int(work["id"]))
    assert database.one(
        "SELECT status, last_error_class FROM work_item WHERE id = ?", (work["id"],)
    ) == {"status": "waiting", "last_error_class": "assistance_disabled"}


def test_runtime_bootstrap_failure_is_visible_on_the_preserved_work_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")

    class MissingCodex:
        def __init__(self):
            raise AssistanceConfigurationError("codex_unavailable: missing")

    monkeypatch.setattr(assistance_module, "CodexInvoker", MissingCodex)

    with pytest.raises(AssistanceConfigurationError, match="codex_unavailable"):
        run_assistance_work(database)

    assert database.one(
        "SELECT status, last_error_class FROM work_item WHERE story_id='story-demo-runtime-001' AND kind='draft'"
    ) == {"status": "failed", "last_error_class": "codex_unavailable"}
    assert database.one(
        "SELECT event_type FROM diagnostic_event WHERE event_type='assistance' ORDER BY id DESC LIMIT 1"
    ) == {"event_type": "assistance"}
    trace = (database.paths.operations / "logs" / "assistance.stderr.log").read_text(
        encoding="utf-8"
    )
    assert "work_item=" in trace and "codex_unavailable" in trace
    assert "AssistanceConfigurationError" in trace
    assert "codex_unavailable: missing" not in trace


def test_bootstrap_failure_cannot_clobber_an_active_lease_or_future_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    work = database.one("SELECT id FROM work_item WHERE kind = 'draft'")

    class MissingCodex:
        def __init__(self):
            raise AssistanceConfigurationError("codex_unavailable: missing")

    monkeypatch.setattr(assistance_module, "CodexInvoker", MissingCodex)
    database.execute(
        "UPDATE work_item SET status = 'generating', available_at = '2999-01-01T00:00:00Z', attempt_count = 1 WHERE id = ?",
        (work["id"],),
    )
    assert run_assistance_work(database, int(work["id"])) is None
    assert database.one(
        "SELECT status, attempt_count, last_error_class FROM work_item WHERE id = ?",
        (work["id"],),
    ) == {"status": "generating", "attempt_count": 1, "last_error_class": None}

    database.execute(
        "UPDATE work_item SET status = 'queued', available_at = '2999-01-01T00:00:00Z' WHERE id = ?",
        (work["id"],),
    )
    assert run_assistance_work(database, int(work["id"])) is None
    assert database.one(
        "SELECT status, last_error_class FROM work_item WHERE id = ?", (work["id"],)
    ) == {"status": "queued", "last_error_class": None}


def test_bootstrap_failure_loses_race_without_clobbering_new_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    work = database.one("SELECT id FROM work_item WHERE kind = 'draft'")

    class RacingMissingCodex:
        def __init__(self):
            database.execute(
                "UPDATE work_item SET status = 'generating', attempt_count = attempt_count + 1, available_at = '2999-01-01T00:00:00Z' WHERE id = ?",
                (work["id"],),
            )
            raise AssistanceConfigurationError("codex_unavailable: missing")

    monkeypatch.setattr(assistance_module, "CodexInvoker", RacingMissingCodex)

    assert run_assistance_work(database, int(work["id"])) is None
    assert database.one(
        "SELECT status, attempt_count, available_at, last_error_class FROM work_item WHERE id = ?",
        (work["id"],),
    ) == {
        "status": "generating",
        "attempt_count": 1,
        "available_at": "2999-01-01T00:00:00Z",
        "last_error_class": None,
    }
    assert database.one(
        "SELECT COUNT(*) AS count FROM diagnostic_event WHERE event_type = 'assistance'"
    ) == {"count": 0}


def test_expired_generation_lease_is_reclaimed_without_duplicate_result(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    database.set_state("assistance_isolation_gate", "passed", "2026-07-14T00:00:00Z")
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")
    database.execute(
        "UPDATE work_item SET status='generating', available_at='2020-01-01T00:00:00Z', attempt_count=1 WHERE story_id='story-demo-runtime-001'"
    )
    work = database.one(
        "SELECT id FROM work_item WHERE story_id='story-demo-runtime-001'"
    )

    draft_id = AssistanceService(database, FakeInvoker()).process(int(work["id"]))

    assert draft_id is not None
    assert database.one(
        "SELECT status, attempt_count FROM work_item WHERE id=?", (work["id"],)
    ) == {"status": "completed", "attempt_count": 2}
    assert database.one("SELECT COUNT(*) AS count FROM assistance_result") == {"count": 1}


def test_neutral_result_cannot_smuggle_a_lens() -> None:
    packet = {"schema_version": 1, "operation": "draft_neutral", "claims": [{"id": 1}]}
    result = {
        "schema_version": 1,
        "operation": "draft_neutral",
        "supported_claim_ids": [1],
        "headline": "Title",
        "factual_brief": "Body",
        "lens": "Unauthorized advocacy",
        "notes": "",
    }
    with pytest.raises(AssistanceError, match="unauthorized"):
        validate_result(packet, result)


def test_natural_attribution_tokens_are_validated_rendered_and_deduplicated() -> None:
    packet = {
        "schema_version": 2,
        "operation": "draft_neutral",
        "claims": [{"id": 1}],
        "sources": [
            {
                "evidence_key": "enriched:1",
                "source_role": "Reporting",
                "title": "Hosted Axios report",
                "url": "https://news.yahoo.example/report",
                "citation_allowed": True,
                "citation_label": "Axios, via Yahoo",
                "citation_url": "https://news.yahoo.example/report",
                "dedupe_key": "reporting:name:axios",
                "hosting_publisher_name": "Yahoo",
                "reporting_origin_name": "Axios",
                "provenance_type": "syndicated",
            },
            {
                "evidence_key": "enriched:2",
                "source_role": "Reporting",
                "title": "Second Axios copy",
                "url": "https://times.example/report",
                "citation_allowed": True,
                "citation_label": "Axios, via Economic Times",
                "citation_url": "https://times.example/report",
                "dedupe_key": "reporting:name:axios",
                "hosting_publisher_name": "Economic Times",
                "reporting_origin_name": "Axios",
                "provenance_type": "cites",
            },
            {
                "evidence_key": "registry:3",
                "source_role": "Discovery",
                "title": "Discovery copy",
                "url": "https://news.yahoo.example/report",
                "citation_allowed": False,
                "citation_label": "Discovery",
                "citation_url": "https://news.yahoo.example/report",
                "dedupe_key": "discovery:3",
            },
        ],
    }
    result = {
        "schema_version": 1,
        "operation": "draft_neutral",
        "supported_claim_ids": [1],
        "headline": "Policy proposal returns",
        "factual_brief": "According to [[source:enriched:1]], the proposal is under discussion.\n\nNo final order has been issued.",
        "lens": "",
        "notes": "",
    }
    validate_result(packet, result)
    rendered = render_citation_tokens(packet, result["factual_brief"])
    assert "[Axios, via Yahoo](<https://news.yahoo.example/report>)" in rendered
    rows = _draft_source_rows(packet)
    assert len([row for row in rows if row["role"] == "Reporting"]) == 1
    assert all(row["role"] != "Discovery" for row in rows)

    for bad_body, message in (
        ("Reporting attributes the proposal to officials.", "artificial"),
        ("According to https://news.yahoo.example/report, it happened.", "citation tokens"),
        ("According to [[source:registry:3]], it happened.", "not confirmed"),
        ("According to [[source:missing]], it happened.", "not confirmed"),
        ("According to <script>alert(1)</script>, it happened.", "unsafe HTML"),
    ):
        with pytest.raises(AssistanceError, match=message):
            validate_result(packet, {**result, "factual_brief": bad_body})


def test_material_change_invalidates_approval_before_drafting(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-policy-002", "approve_neutral")
    work = database.one("SELECT id FROM work_item WHERE kind = 'draft'")
    database.execute(
        "UPDATE story_cluster SET material_update = 1 WHERE id = 'story-demo-policy-002'"
    )

    with pytest.raises(ApprovalInvalidated):
        build_packet(database, int(work["id"]))

    assert database.one("SELECT status FROM work_item WHERE id = ?", (work["id"],)) == {
        "status": "needs_reapproval"
    }
    assert database.one("SELECT status FROM story_cluster WHERE id = 'story-demo-policy-002'") == {
        "status": "candidate"
    }


def test_isolation_canary_records_pass_and_failure_without_enabling_assistance(tmp_path: Path) -> None:
    database, _ = _service(tmp_path)

    class FakeServer:
        server_address = ("127.0.0.1", 43123)

        def __init__(self, *_: object):
            pass

        def serve_forever(self) -> None:
            return None

        def shutdown(self) -> None:
            return None

        def server_close(self) -> None:
            return None

    assert run_isolation_canary(database, FakeInvoker(), server_factory=FakeServer) is True
    assert database.get_state("assistance_isolation_gate") == "passed"
    assert database.get_state("assistance_enabled", "false") == "false"

    assert run_isolation_canary(database, FakeInvoker(fail=True), server_factory=FakeServer) is False
    assert database.get_state("assistance_isolation_gate") == "failed"
    assert database.get_state("assistance_enabled") == "false"


def test_outer_sandbox_profile_allows_only_task_auth_system_and_codex_paths(tmp_path: Path) -> None:
    task = tmp_path / "task"
    codex = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    auth = tmp_path / "private-auth" / "auth.json"
    profile = sandbox_profile(task, codex, auth)

    assert str(task) in profile
    assert str(auth) in profile
    assert "/Applications/ChatGPT.app" in profile
    assert "(deny default)" in profile
    assert '(deny network-outbound (remote ip "localhost:*"))' in profile
    assert '(literal "/")' in profile
    assert "(allow file-write*" in profile


def test_outer_sandbox_profile_handles_shallow_executable_path(tmp_path: Path) -> None:
    profile = sandbox_profile(
        tmp_path / "task", Path("/codex"), tmp_path / "auth.json"
    )

    assert '(literal "/codex")' in profile
    assert '(subpath "/")' not in profile


def test_codex_discovery_prefers_explicit_then_path_then_chatgpt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit-codex"
    path_binary = tmp_path / "path-codex"
    app_binary = (
        tmp_path
        / "Applications"
        / "ChatGPT.app"
        / "Contents"
        / "Resources"
        / "codex"
    )
    for candidate in (explicit, path_binary, app_binary):
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("binary", encoding="utf-8")
        candidate.chmod(0o755)

    monkeypatch.setattr(assistance_module.shutil, "which", lambda _name: str(path_binary))
    monkeypatch.setattr(
        assistance_module, "_known_codex_binary_paths", lambda: (app_binary,)
    )
    assert CodexInvoker(codex_binary=explicit).codex_binary == explicit.resolve()
    assert CodexInvoker().codex_binary == path_binary.resolve()

    monkeypatch.setattr(assistance_module.shutil, "which", lambda _name: None)
    assert CodexInvoker().codex_binary == app_binary.resolve()


def test_codex_discovery_rejects_non_files_and_non_executables_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    non_executable = tmp_path / "not-executable"
    non_executable.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(assistance_module.shutil, "which", lambda _name: str(directory))
    monkeypatch.setattr(
        assistance_module,
        "_known_codex_binary_paths",
        lambda: (non_executable,),
    )

    with pytest.raises(AssistanceError, match="^codex_unavailable:"):
        CodexInvoker(codex_binary=tmp_path / "missing")


def test_codex_invoker_builds_isolated_command_and_validates_result(tmp_path: Path) -> None:
    codex = tmp_path / "Codex.app" / "Contents" / "Resources" / "codex"
    codex.parent.mkdir(parents=True)
    codex.write_text("binary", encoding="utf-8")
    codex.chmod(0o755)
    auth = tmp_path / "auth" / "auth.json"
    auth.parent.mkdir()
    auth.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def runner(arguments, input_text, cwd, environment):
        profile_path = Path(arguments[arguments.index("-f") + 1])
        isolated_auth = Path(environment["CODEX_HOME"]) / "auth.json"
        captured.update(
            arguments=arguments,
            input=input_text,
            cwd=cwd,
            environment=environment,
            profile=profile_path.read_text(encoding="utf-8"),
            auth_copied=isolated_auth.read_text(encoding="utf-8") == "{}",
            auth_mode=isolated_auth.stat().st_mode & 0o777,
        )
        output = Path(arguments[arguments.index("--output-last-message") + 1])
        output.write_text(json.dumps({
            "schema_version": 1,
            "operation": "triage",
            "supported_claim_ids": [1],
            "headline": "Evidence title",
            "factual_brief": "Evidence brief",
            "lens": "",
            "notes": "",
        }), encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    invoker = CodexInvoker(
        codex_binary=codex, sandbox_binary=Path("/usr/bin/sandbox-exec"),
        auth_file=auth, runner=runner,
    )
    result = invoker.invoke({"operation": "triage", "claims": [{"id": 1}]})

    assert result.payload["supported_claim_ids"] == [1]
    arguments = captured["arguments"]
    assert "--ignore-user-config" in arguments
    assert "--strict-config" in arguments
    assert 'approval_policy="never"' in arguments
    assert str(captured["environment"]["CODEX_HOME"]).startswith(str(captured["cwd"]))
    assert captured["environment"]["HOME"] != str(Path.home())
    assert captured["environment"]["CFFIXED_USER_HOME"] == captured["environment"]["HOME"]
    assert str(auth) not in str(captured["profile"])
    assert captured["auth_copied"] is True
    assert captured["auth_mode"] == 0o600
    assert str(tmp_path) not in str(captured["input"])


def test_codex_invoker_fails_on_process_malformed_and_oversized_results(tmp_path: Path) -> None:
    codex = tmp_path / "codex"
    codex.write_text("binary", encoding="utf-8")
    codex.chmod(0o755)
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")

    def failed(arguments, _input, _cwd, _environment):
        return subprocess.CompletedProcess(arguments, 2, "", "SECRET_PACKET denied")

    with pytest.raises(AssistanceError, match="codex_process_failed") as failure:
        CodexInvoker(codex_binary=codex, auth_file=auth, runner=failed).invoke(
            {"operation": "triage", "claims": []}
        )
    assert "SECRET_PACKET" not in str(failure.value)

    def malformed(arguments, _input, _cwd, _environment):
        Path(arguments[arguments.index("--output-last-message") + 1]).write_text("not-json", encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    with pytest.raises(AssistanceError, match="malformed"):
        CodexInvoker(codex_binary=codex, auth_file=auth, runner=malformed).invoke(
            {"operation": "triage", "claims": []}
        )
    with pytest.raises(AssistanceError, match="64 KB"):
        CodexInvoker(codex_binary=codex, auth_file=auth, runner=malformed).invoke(
            {"operation": "triage", "claims": [], "large": "x" * 65_000}
        )


def test_build_packet_rejects_missing_ineligible_and_unapproved_work(tmp_path: Path) -> None:
    database, _service_instance = _service(tmp_path)
    with pytest.raises(AssistanceError, match="missing its story"):
        database.execute(
            "INSERT INTO work_item(kind, status, priority, payload_json, created_at, updated_at) VALUES('draft','queued',1,'{}','2026-07-14T00:00:00Z','2026-07-14T00:00:00Z')"
        )
        work = database.one("SELECT MAX(id) AS id FROM work_item")
        build_packet(database, int(work["id"]))

    now = "2026-07-14T00:00:00Z"
    lens_id = database.execute(
        "INSERT INTO work_item(story_id, kind, status, priority, payload_json, created_at, updated_at) VALUES('story-demo-watch-003','draft','queued',1,?, ?, ?)",
        (json.dumps({"mode": "Open-Source Lens Brief"}), now, now),
    )
    with pytest.raises(AssistanceError, match="not eligible"):
        build_packet(database, lens_id)

    neutral_id = database.execute(
        "INSERT INTO work_item(story_id, kind, status, priority, payload_json, created_at, updated_at) VALUES('story-demo-runtime-001','draft','queued',1,?, ?, ?)",
        (json.dumps({"mode": "Neutral News Brief", "story": {"material_update": True}}), now, now),
    )
    with pytest.raises(AssistanceError, match="approved story"):
        build_packet(database, neutral_id)


def test_result_schema_rejects_wrong_shape_types_and_empty_draft() -> None:
    packet = {"schema_version": 1, "operation": "draft_neutral", "claims": [{"id": 1}]}
    base = {
        "schema_version": 1, "operation": "draft_neutral", "supported_claim_ids": [1],
        "headline": "Title", "factual_brief": "Brief", "lens": "", "notes": "",
    }
    with pytest.raises(AssistanceError, match="closed schema"):
        validate_result(packet, {**base, "extra": True})
    with pytest.raises(AssistanceError, match="version or operation"):
        validate_result(packet, {**base, "operation": "triage"})
    with pytest.raises(AssistanceError, match="invalid claim"):
        validate_result(packet, {**base, "supported_claim_ids": ["1"]})
    with pytest.raises(AssistanceError, match="repeats claim"):
        validate_result(packet, {**base, "supported_claim_ids": [1, 1]})
    with pytest.raises(AssistanceError, match="must be text"):
        validate_result(packet, {**base, "notes": 1})
    with pytest.raises(AssistanceError, match="requires a headline"):
        validate_result(packet, {**base, "headline": ""})


def test_structured_output_schema_types_const_and_enum_fields() -> None:
    schema = json.loads(
        files("open_source_ai_news_wire")
        .joinpath("schemas", "assistance-result.schema.json")
        .read_text(encoding="utf-8")
    )

    assert schema["properties"]["schema_version"]["type"] == "integer"
    assert schema["properties"]["operation"]["type"] == "string"
    assert "uniqueItems" not in schema["properties"]["supported_claim_ids"]
