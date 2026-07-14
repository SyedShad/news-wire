from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from open_source_ai_news_wire.assistance import (
    ApprovalInvalidated,
    AssistanceDeferred,
    AssistanceError,
    AssistanceService,
    CodexInvoker,
    InvocationResult,
    build_packet,
    run_isolation_canary,
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
        payload = {
            "schema_version": 1,
            "operation": operation,
            "supported_claim_ids": claim_ids[:2],
            "headline": "Evidence-backed AI development",
            "factual_brief": "The supplied evidence supports this compact neutral brief.",
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
    assert packet["claims"]
    assert packet["sources"]
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
    service.review("story-demo-watch-003", "research")
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
    assert "(allow file-write*" in profile


def test_codex_invoker_builds_isolated_command_and_validates_result(tmp_path: Path) -> None:
    codex = tmp_path / "Codex.app" / "Contents" / "Resources" / "codex"
    codex.parent.mkdir(parents=True)
    codex.write_text("binary", encoding="utf-8")
    auth = tmp_path / "auth" / "auth.json"
    auth.parent.mkdir()
    auth.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def runner(arguments, input_text, cwd, environment):
        captured.update(arguments=arguments, input=input_text, cwd=cwd, environment=environment)
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
    assert captured["environment"]["CODEX_HOME"] == str(auth.parent)
    assert str(tmp_path) not in str(captured["input"])


def test_codex_invoker_fails_on_process_malformed_and_oversized_results(tmp_path: Path) -> None:
    codex = tmp_path / "codex"
    codex.write_text("binary", encoding="utf-8")
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")

    def failed(arguments, _input, _cwd, _environment):
        return subprocess.CompletedProcess(arguments, 2, "", "denied")

    with pytest.raises(AssistanceError, match="denied"):
        CodexInvoker(codex_binary=codex, auth_file=auth, runner=failed).invoke(
            {"operation": "triage", "claims": []}
        )

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
    with pytest.raises(AssistanceError, match="must be text"):
        validate_result(packet, {**base, "notes": 1})
    with pytest.raises(AssistanceError, match="requires a headline"):
        validate_result(packet, {**base, "headline": ""})
