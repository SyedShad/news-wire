from __future__ import annotations

import html
import json
import subprocess
import threading
from datetime import UTC, datetime
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
    CodexIdentity,
    CodexInvoker,
    InvocationResult,
    ISOLATION_CANARY_VERSION,
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
from open_source_ai_news_wire.network import FetchResult, UnsafeRequest
from open_source_ai_news_wire.services import DashboardService
from open_source_ai_news_wire.storage import Database


FAKE_IDENTITY = CodexIdentity(
    path="/Applications/ChatGPT.app/Contents/Resources/codex",
    version="test-codex",
    team_id="2DC432GLL2",
    identifier="codex",
    sha256="a" * 64,
    cdhash="b" * 40,
)
TEST_AUTH_JSON = json.dumps(
    {
        "auth_mode": "chatgpt",
        "tokens": {"access_token": "obvious-dummy"},
        "OPENAI_API_KEY": None,
    }
)


def _identity_for(path: Path) -> CodexIdentity:
    return CodexIdentity(
        path=str(path.resolve()),
        version="test-codex",
        team_id="2DC432GLL2",
        identifier="codex",
        sha256="a" * 64,
        cdhash="b" * 40,
    )


class _FakeBroker:
    endpoint = ("127.0.0.1", 43123)
    proxy_url = "http://127.0.0.1:43123"
    failure_code = ""

    def start(self):
        return self

    def stop(self):
        return None


def _codex_invoker(**kwargs) -> CodexInvoker:
    kwargs.setdefault("identity_verifier", _identity_for)
    kwargs.setdefault("broker_factory", _FakeBroker)
    return CodexInvoker(**kwargs)


class FakeInvoker:
    private_network_isolation_proven = True
    identity = FAKE_IDENTITY

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


class _FakeVolatileSourceClient:
    def __init__(
        self,
        passages: dict[str, str],
        *,
        error: Exception | None = None,
        fetched_urls: list[str] | None = None,
    ) -> None:
        self.passages = passages
        self.error = error
        self.fetched_urls = fetched_urls

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def fetch(self, url: str) -> FetchResult:
        if self.fetched_urls is not None:
            self.fetched_urls.append(url)
        if self.error is not None:
            raise self.error
        passage = self.passages[url]
        body = (
            '<html><head><meta name="description" content="'
            + html.escape(passage, quote=True)
            + '"></head><body></body></html>'
        ).encode()
        return FetchResult(url, 200, {"content-type": "text/html; charset=utf-8"}, body)


def _approved_volatile_source_factory(
    database: Database,
    *,
    passage_override: str | None = None,
    error: Exception | None = None,
    fetched_urls: list[str] | None = None,
):
    work = database.one(
        "SELECT payload_json FROM work_item WHERE kind='draft' ORDER BY id DESC LIMIT 1"
    )
    snapshot = json.loads(work["payload_json"])
    volatile_ids = {
        claim["id"]
        for claim in snapshot["claims"]
        if claim.get("volatility") == "volatile"
    }
    passages = {
        str(source.get("canonical_url") or source["url"]): (
            passage_override
            if passage_override is not None
            else str(source.get("passage") or source.get("summary") or "")
        )
        for source in snapshot["sources"]
        if volatile_ids
        & {
            relationship.get("claim_id")
            for relationship in source.get("claim_relationships", [])
        }
    }

    def factory(_host: str) -> _FakeVolatileSourceClient:
        return _FakeVolatileSourceClient(
            passages, error=error, fetched_urls=fetched_urls
        )

    return factory


def _service(tmp_path: Path) -> tuple[Database, DashboardService]:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    return database, DashboardService(database)


def _pass_isolation(database: Database) -> None:
    current = datetime.now(UTC).replace(microsecond=0)
    now = current.isoformat().replace("+00:00", "Z")
    database.set_state("assistance_isolation_gate", "passed", now)
    database.set_state("assistance_isolation_version", ISOLATION_CANARY_VERSION, now)
    database.set_state(
        assistance_module.ISOLATION_ATTESTATION_STATE,
        Database.json(assistance_module._attestation_payload(FAKE_IDENTITY, current)),
        now,
    )


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
    shell = database.one(
        "SELECT id, status, provenance_json FROM draft WHERE story_id='story-demo-runtime-001'"
    )
    assert shell["status"] == "Editable Shell"
    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")

    draft_id = AssistanceService(
        database,
        FakeInvoker(),
        volatile_source_client_factory=_approved_volatile_source_factory(database),
    ).process_next()

    assert draft_id is not None
    assert database.one("SELECT mode, status, version FROM draft WHERE id = ?", (draft_id,)) == {
        "mode": "Neutral News Brief",
        "status": "Current",
        "version": 2,
    }
    assert database.one(
        "SELECT status FROM draft WHERE id = ?", (shell["id"],)
    ) == {"status": "Superseded"}
    assert database.one(
        "SELECT supersedes_id FROM draft WHERE id = ?", (draft_id,)
    ) == {"supersedes_id": shell["id"]}
    assert database.one(
        "SELECT COUNT(*) AS count FROM draft WHERE story_id='story-demo-runtime-001' AND status='Current'"
    ) == {"count": 1}
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
    assert '"prompt_version":"v4-reddit-posts-1.0.1"' in draft["provenance_json"]


def test_approval_keeps_editable_shell_even_when_assistance_is_ready(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")

    service.review("story-demo-runtime-001", "approve_neutral")

    assert database.one(
        "SELECT status FROM draft WHERE story_id = 'story-demo-runtime-001'"
    ) == {"status": "Editable Shell"}


def test_ai_completion_wins_before_stale_shell_save_without_duplicate_current(
    tmp_path: Path,
) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    shell = database.one(
        "SELECT * FROM draft WHERE story_id = 'story-demo-runtime-001'"
    )
    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")
    AssistanceService(
        database,
        FakeInvoker(),
        volatile_source_client_factory=_approved_volatile_source_factory(database),
    ).process_next()

    with pytest.raises(ValueError, match="current draft version or editable shell"):
        service.save_draft(
            int(shell["id"]),
            str(shell["headline"]),
            str(shell["metadata"]),
            str(shell["body"]),
            "",
        )

    assert database.one(
        "SELECT COUNT(*) AS count FROM draft WHERE story_id = 'story-demo-runtime-001' AND status = 'Current'"
    ) == {"count": 1}


def test_lens_draft_requires_and_preserves_separate_approved_mode(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-policy-002", "approve_lens")
    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")

    draft_id = AssistanceService(
        database,
        FakeInvoker(),
        volatile_source_client_factory=_approved_volatile_source_factory(database),
    ).process_next()

    draft = database.one("SELECT mode, lens FROM draft WHERE id = ?", (draft_id,))
    assert draft["mode"] == "Open-Source Lens Brief"
    assert "separate" in draft["lens"]


@pytest.mark.parametrize(
    ("action", "expected_operation"),
    (
        ("manual_approve_neutral", "draft_neutral"),
        ("manual_approve_lens", "draft_lens"),
    ),
)
def test_manual_override_uses_discovery_sources_without_prompting_with_gate_labels(
    tmp_path: Path, action: str, expected_operation: str
) -> None:
    database, service = _service(tmp_path)
    service.review(
        "story-demo-watch-003",
        action,
        confirmation_version="manual_override_v1",
    )
    work = database.one("SELECT id FROM work_item WHERE kind = 'draft'")

    packet = build_packet(database, int(work["id"]))

    assert packet["operation"] == expected_operation
    assert packet["policy"]["stored_discovery_citations_allowed"] is True
    assert packet["policy"]["discovery_sources_cannot_support_claims"] is False
    assert all("status" not in claim for claim in packet["claims"])
    assert all(source["source_role"] == "Discovery" for source in packet["sources"])
    assert all(source["citation_allowed"] for source in packet["sources"])
    serialized = json.dumps(packet).casefold()
    assert "unverified" not in serialized
    assert "provisional" not in serialized
    assert "evidence_gate" not in serialized
    assert "importance_gate" not in serialized

    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")
    draft_id = AssistanceService(
        database,
        FakeInvoker(),
        volatile_source_client_factory=_approved_volatile_source_factory(database),
    ).process_next()
    draft = database.one(
        "SELECT mode, body, lens, sources_json, approval_snapshot_json FROM draft WHERE id = ?",
        (draft_id,),
    )
    assert draft["mode"] == (
        "Open-Source Lens Brief" if expected_operation == "draft_lens" else "Neutral News Brief"
    )
    assert "https://example.invalid/conference/session" in draft["body"]
    assert "unverified" not in (draft["body"] + draft["lens"]).casefold()
    assert "provisional" not in (draft["body"] + draft["lens"]).casefold()
    assert json.loads(draft["approval_snapshot_json"])["approval_basis"] == "manual_override"
    assert any(source["role"] == "Discovery" for source in json.loads(draft["sources_json"]))
    markdown = service.draft_markdown(int(draft_id))
    html_export = service.draft_html(int(draft_id))
    for exported in (markdown, html_export):
        assert "Manually approved" not in exported
        assert "manual override" not in exported.casefold()
        assert "unverified" not in exported.casefold()
        assert "provisional" not in exported.casefold()
    if expected_operation == "draft_lens":
        revised_id = service.save_draft(
            int(draft_id),
            draft["mode"],
            "AGI Development · Breaking",
            draft["body"],
            draft["lens"] + "\n\nA human revision preserves the tradeoff.",
        )
        revised = service.get_draft(revised_id)
        assert revised["manual_override_active"] is True
        assert "human revision" in revised["lens"].casefold()


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

    database.set_state("assistance_isolation_gate", "passed", now)
    database.set_state("assistance_isolation_version", "0.3.5", now)
    database.set_state("assistance_enabled", "true", now)
    with pytest.raises(AssistanceDeferred, match="isolation"):
        AssistanceService(database, FakeInvoker()).process_next()

    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")
    database.execute(
        """
        INSERT INTO usage_ledger(category, operation, effort_units, model, result, created_at)
        VALUES('background', 'triage', 8, 'test', 'accepted', datetime('now'))
        """
    )
    # v0.4.0 records background usage but does not use the local allowance as
    # an editorial blocker.
    AssistanceService(database, FakeInvoker()).process_next()
    assert database.one(
        "SELECT status FROM work_item WHERE kind = 'semantic' ORDER BY id DESC LIMIT 1"
    ) == {"status": "completed"}

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
    _pass_isolation(database)
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
    _pass_isolation(database)
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
        volatile_source_client_factory=_approved_volatile_source_factory(database),
    ).process_next()

    assert draft_id is not None
    assert database.one(
        "SELECT status, attempt_count, last_error_class FROM work_item WHERE kind='draft'"
    ) == {"status": "completed", "attempt_count": 2, "last_error_class": None}
    assert database.one(
        "SELECT retry_count FROM usage_ledger WHERE category='draft' ORDER BY id DESC LIMIT 1"
    ) == {"retry_count": 1}


def test_usage_exhaustion_waits_without_consuming_retry_or_charge(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", assistance_module._now())

    class ExhaustedInvoker(FakeInvoker):
        def invoke(self, packet):
            self.packets.append(packet)
            raise AssistanceDeferred(
                "waiting_for_usage_reset: Saved ChatGPT allowance is exhausted"
            )

    work = database.one("SELECT id FROM work_item WHERE kind='draft'")
    usage_before = database.one("SELECT COUNT(*) AS count FROM usage_ledger")
    with pytest.raises(AssistanceDeferred, match="waiting_for_usage_reset"):
        AssistanceService(
            database,
            ExhaustedInvoker(),
            volatile_source_client_factory=_approved_volatile_source_factory(database),
        ).process(int(work["id"]))

    assert database.one(
        "SELECT status, attempt_count, last_error_class FROM work_item WHERE id=?",
        (work["id"],),
    ) == {
        "status": "waiting",
        "attempt_count": 1,
        "last_error_class": "waiting_for_usage_reset",
    }
    assert database.one("SELECT COUNT(*) AS count FROM usage_ledger") == usage_before


def test_volatile_source_is_refetched_unchanged_before_model_and_charged_once(
    tmp_path: Path,
) -> None:
    database, service = _service(tmp_path)
    volatile_claim = database.one(
        "SELECT id FROM claim WHERE story_id='story-demo-policy-002' AND volatility='volatile'"
    )
    second_source = database.one(
        "SELECT id FROM source_item WHERE story_id='story-demo-policy-002' ORDER BY id DESC LIMIT 1"
    )
    database.execute(
        "INSERT INTO evidence_link(claim_id, source_item_id, relationship) VALUES(?, ?, 'supports')",
        (volatile_claim["id"], second_source["id"]),
    )
    service.review("story-demo-policy-002", "approve_neutral")
    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", assistance_module._now())
    invoker = FakeInvoker()
    fetched_urls: list[str] = []
    usage_before = database.one("SELECT COUNT(*) AS count FROM usage_ledger")["count"]

    draft_id = AssistanceService(
        database,
        invoker,
        volatile_source_client_factory=_approved_volatile_source_factory(
            database, fetched_urls=fetched_urls
        ),
    ).process_next()

    assert draft_id is not None
    assert len(invoker.packets) == 1
    assert len(fetched_urls) == 2
    assert database.one("SELECT COUNT(*) AS count FROM usage_ledger") == {
        "count": usage_before + 1
    }


def test_volatile_source_drift_requires_reapproval_without_model_or_charge(
    tmp_path: Path,
) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-policy-002", "approve_neutral")
    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", assistance_module._now())
    invoker = FakeInvoker()
    usage_before = database.one("SELECT COUNT(*) AS count FROM usage_ledger")

    with pytest.raises(ApprovalInvalidated, match="volatile_source_drift"):
        AssistanceService(
            database,
            invoker,
            volatile_source_client_factory=_approved_volatile_source_factory(
                database, passage_override="The live source now states materially different facts."
            ),
        ).process_next()

    assert invoker.packets == []
    assert database.one("SELECT COUNT(*) AS count FROM usage_ledger") == usage_before
    assert database.one(
        "SELECT status, last_error_class FROM work_item WHERE kind='draft'"
    ) == {"status": "needs_reapproval", "last_error_class": "volatile_source_drift"}
    assert database.one(
        "SELECT status FROM story_cluster WHERE id='story-demo-policy-002'"
    ) == {"status": "candidate"}


def test_unavailable_volatile_source_waits_without_model_or_charge(
    tmp_path: Path,
) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-policy-002", "approve_neutral")
    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", assistance_module._now())
    invoker = FakeInvoker()
    usage_before = database.one("SELECT COUNT(*) AS count FROM usage_ledger")

    with pytest.raises(AssistanceDeferred, match="volatile_source_unavailable"):
        AssistanceService(
            database,
            invoker,
            volatile_source_client_factory=_approved_volatile_source_factory(
                database, error=ConnectionError("temporary source outage")
            ),
        ).process_next()

    assert invoker.packets == []
    assert database.one("SELECT COUNT(*) AS count FROM usage_ledger") == usage_before
    assert database.one(
        "SELECT status, last_error_class FROM work_item WHERE kind='draft'"
    ) == {"status": "waiting", "last_error_class": "volatile_source_unavailable"}


def test_unsafe_volatile_source_fails_without_model_or_charge(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-policy-002", "approve_neutral")
    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", assistance_module._now())
    invoker = FakeInvoker()
    usage_before = database.one("SELECT COUNT(*) AS count FROM usage_ledger")

    with pytest.raises(AssistanceConfigurationError, match="volatile_source_unsafe"):
        AssistanceService(
            database,
            invoker,
            volatile_source_client_factory=_approved_volatile_source_factory(
                database, error=UnsafeRequest("private address blocked")
            ),
        ).process_next()

    assert invoker.packets == []
    assert database.one("SELECT COUNT(*) AS count FROM usage_ledger") == usage_before
    assert database.one(
        "SELECT status, last_error_class FROM work_item WHERE kind='draft'"
    ) == {"status": "failed", "last_error_class": "volatile_source_unsafe"}


def test_exact_claim_does_not_process_another_draft_or_reclaim_live_lease(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    service.review("story-demo-policy-002", "approve_lens")
    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")
    work = database.one(
        "SELECT id FROM work_item WHERE story_id='story-demo-policy-002' AND kind='draft'"
    )

    draft_id = AssistanceService(
        database,
        FakeInvoker(),
        volatile_source_client_factory=_approved_volatile_source_factory(database),
    ).process(int(work["id"]))

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
    _pass_isolation(database)
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
            AssistanceService(
                database,
                BlockingInvoker(),
                volatile_source_client_factory=_approved_volatile_source_factory(database),
            ).process(int(work["id"]))
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
    ) == {"count": 2}
    assert database.one(
        "SELECT COUNT(*) AS count FROM draft WHERE story_id = 'story-demo-runtime-001' AND status = 'Current'"
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
    ) == {"status": "waiting", "last_error_class": "security_revalidation"}

    database.execute(
        "UPDATE work_item SET status = 'queued', last_error_class = NULL WHERE id = ?",
        (work["id"],),
    )
    _pass_isolation(database)
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
    _pass_isolation(database)
    database.set_state("assistance_enabled", "true", "2026-07-14T00:00:00Z")
    database.execute(
        "UPDATE work_item SET status='generating', available_at='2020-01-01T00:00:00Z', attempt_count=1 WHERE story_id='story-demo-runtime-001'"
    )
    work = database.one(
        "SELECT id FROM work_item WHERE story_id='story-demo-runtime-001'"
    )

    draft_id = AssistanceService(
        database,
        FakeInvoker(),
        volatile_source_client_factory=_approved_volatile_source_factory(database),
    ).process(int(work["id"]))

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
        ("This remains unverified pending another source.", "internal qualification"),
        ("This provisional report describes the proposal.", "internal qualification"),
        ("The evidence gate has not passed.", "internal qualification"),
        ("According to https://news.yahoo.example/report, it happened.", "citation tokens"),
        ("According to [[source:registry:3]], it happened.", "not approved"),
        ("According to [[source:missing]], it happened.", "not approved"),
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


def test_approval_invalidation_does_not_resurrect_archived_story(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-policy-002", "approve_neutral")
    work = database.one("SELECT id FROM work_item WHERE kind = 'draft'")
    database.execute(
        "UPDATE story_cluster SET status = 'archived', material_update = 1 "
        "WHERE id = 'story-demo-policy-002'"
    )

    with pytest.raises(ApprovalInvalidated):
        build_packet(database, int(work["id"]))

    assert database.one(
        "SELECT status FROM story_cluster WHERE id = 'story-demo-policy-002'"
    ) == {"status": "archived"}
    assert database.one("SELECT status FROM work_item WHERE id = ?", (work["id"],)) == {
        "status": "needs_reapproval"
    }


@pytest.mark.parametrize(
    "mutation",
    (
        "story_id",
        "mode",
        "approval_basis",
        "review_action_id",
        "story_revision",
        "story_field_type",
        "opportunity_type",
        "claims_shape",
        "sources_shape",
        "snapshot_signature_mismatch",
        "action_provenance",
        "action_signature_mismatch",
        "signature_list_shape",
        "signature_record_shape",
        "claim_identifier",
        "source_identifier",
        "signature_digest",
        "signature_order",
    ),
)
def test_approval_snapshot_validation_fails_closed(
    tmp_path: Path, mutation: str
) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    work = database.one("SELECT id, payload_json FROM work_item WHERE kind = 'draft'")
    payload = json.loads(work["payload_json"])

    if mutation == "story_id":
        payload["story_id"] = "another-story"
    elif mutation == "mode":
        payload["mode"] = "unsafe"
    elif mutation == "approval_basis":
        payload["approval_basis"] = "implicit"
    elif mutation == "review_action_id":
        payload["review_action_id"] = True
    elif mutation == "story_revision":
        payload["story_revision"] += 1
    elif mutation == "story_field_type":
        payload["story"]["headline"] = 1
    elif mutation == "opportunity_type":
        payload["story"]["opportunity_strength"] = 1
    elif mutation == "claims_shape":
        payload["claims"] = {}
    elif mutation == "sources_shape":
        payload["sources"] = [None]
    elif mutation == "snapshot_signature_mismatch":
        payload["claim_signatures"][0]["signature"] = "0" * 64
    elif mutation == "action_provenance":
        database.execute(
            "UPDATE review_action SET action = 'reject' WHERE id = ?",
            (payload["review_action_id"],),
        )
    elif mutation == "action_signature_mismatch":
        database.execute(
            "UPDATE review_action SET claim_signatures_json = ? WHERE id = ?",
            (json.dumps([{**payload["claim_signatures"][0], "signature": "0" * 64}]), payload["review_action_id"]),
        )
    elif mutation == "signature_list_shape":
        payload["claim_signatures"] = None
    elif mutation == "signature_record_shape":
        payload["claim_signatures"] = [{"id": 1, "signature": "0" * 64, "extra": 1}]
    elif mutation == "claim_identifier":
        payload["claim_signatures"][0]["id"] = True
    elif mutation == "source_identifier":
        payload["source_signatures"][0]["id"] = ""
    elif mutation == "signature_digest":
        payload["claim_signatures"][0]["signature"] = "not-a-digest"
    elif mutation == "signature_order":
        payload["claim_signatures"].append(dict(payload["claim_signatures"][0]))

    database.execute(
        "UPDATE work_item SET payload_json = ? WHERE id = ?",
        (json.dumps(payload), work["id"]),
    )
    with pytest.raises(ApprovalInvalidated):
        build_packet(database, int(work["id"]))


def test_packet_uses_signed_approval_snapshot_if_rows_change_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, service = _service(tmp_path)
    service.review("story-demo-runtime-001", "approve_neutral")
    work = database.one("SELECT id, payload_json FROM work_item WHERE kind = 'draft'")
    approved = json.loads(work["payload_json"])
    approved_text = approved["claims"][0]["text"]
    original_revalidate = assistance_module._revalidate_approval

    def validate_then_change_rows(database, work, story, payload):
        original_revalidate(database, work, story, payload)
        database.execute(
            "UPDATE claim SET text = text || ' changed after validation' WHERE story_id = ?",
            (story["id"],),
        )

    monkeypatch.setattr(
        assistance_module, "_revalidate_approval", validate_then_change_rows
    )
    packet = build_packet(database, int(work["id"]))

    assert packet["claims"][0]["text"] == approved_text
    assert "changed after validation" not in json.dumps(packet)


def test_manual_approval_is_invalidated_when_approved_claim_or_passage_changes(
    tmp_path: Path,
) -> None:
    database, service = _service(tmp_path)
    service.review(
        "story-demo-watch-003",
        "manual_approve_neutral",
        confirmation_version="manual_override_v1",
    )
    work = database.one("SELECT id FROM work_item WHERE kind = 'draft'")
    database.execute(
        "UPDATE source_item SET passage = passage || ' Material update.' WHERE story_id = 'story-demo-watch-003'"
    )

    with pytest.raises(ApprovalInvalidated):
        build_packet(database, int(work["id"]))

    assert database.one("SELECT status FROM work_item WHERE id = ?", (work["id"],)) == {
        "status": "needs_reapproval"
    }
    state = service.get_story("story-demo-watch-003")
    assert state["manual_override_active"] is True
    assert state["status"] == "candidate"


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

    assert run_isolation_canary(
        database,
        FakeInvoker(),
        server_factory=FakeServer,
        boundary_canary=lambda: (True, ""),
    ) is True
    assert database.get_state("assistance_isolation_gate") == "passed"
    assert database.get_state("assistance_isolation_version") == ISOLATION_CANARY_VERSION
    assert database.get_state("assistance_enabled", "false") == "false"

    assert run_isolation_canary(
        database,
        FakeInvoker(fail=True),
        server_factory=FakeServer,
        boundary_canary=lambda: (True, ""),
    ) is False
    assert database.get_state("assistance_isolation_gate") == "failed"
    assert database.get_state("assistance_isolation_version") == ""
    assert database.get_state("assistance_enabled") == "false"

    unproven = FakeInvoker()
    unproven.private_network_isolation_proven = False
    assert run_isolation_canary(
        database,
        unproven,
        server_factory=FakeServer,
        boundary_canary=lambda: (True, ""),
    ) is False
    assert database.get_state("assistance_isolation_gate") == "failed"
    assert database.get_state("assistance_unavailable_reason") == (
        "private_network_isolation_unproven"
    )


def test_outer_sandbox_profile_allows_only_task_auth_system_and_codex_paths(tmp_path: Path) -> None:
    task = tmp_path / "task"
    codex = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    auth = tmp_path / "private-auth" / "auth.json"
    profile = sandbox_profile(task, codex, auth, 43123)

    assert str(task) in profile
    assert str(auth) in profile
    assert "/Applications/ChatGPT.app" in profile
    assert "(deny default)" in profile
    assert '(allow network-outbound (remote tcp "localhost:43123"))' in profile
    assert '*:443' not in profile
    assert '(literal "/")' in profile
    assert "(allow file-write*" in profile
    assert "(allow process-fork)" not in profile
    assert "(allow process-exec)" not in profile


def test_outer_sandbox_profile_handles_shallow_executable_path(tmp_path: Path) -> None:
    profile = sandbox_profile(
        tmp_path / "task", Path("/codex"), tmp_path / "auth.json", 43123
    )

    assert '(literal "/codex")' in profile
    assert '(subpath "/")' not in profile


def test_codex_discovery_uses_explicit_for_tests_and_bundled_chatgpt_in_production(
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

    monkeypatch.setattr(
        assistance_module, "_known_codex_binary_paths", lambda: (app_binary,)
    )
    assert _codex_invoker(codex_binary=explicit).codex_binary == explicit.resolve()
    assert _codex_invoker().codex_binary == app_binary.resolve()
    assert _codex_invoker().codex_binary != path_binary.resolve()


def test_codex_discovery_rejects_non_files_and_non_executables_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    non_executable = tmp_path / "not-executable"
    non_executable.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(
        assistance_module,
        "_known_codex_binary_paths",
        lambda: (non_executable,),
    )

    with pytest.raises(AssistanceError, match="^codex_unavailable:"):
        _codex_invoker(codex_binary=tmp_path / "missing")


def test_codex_invoker_builds_isolated_command_and_validates_result(tmp_path: Path) -> None:
    codex = tmp_path / "Codex.app" / "Contents" / "Resources" / "codex"
    codex.parent.mkdir(parents=True)
    codex.write_text("binary", encoding="utf-8")
    codex.chmod(0o755)
    auth = tmp_path / "auth" / "auth.json"
    auth.parent.mkdir()
    auth.write_text(TEST_AUTH_JSON, encoding="utf-8")
    captured: dict[str, object] = {}

    def profile_runner(arguments, cwd, environment):
        captured.update(
            preflight_arguments=arguments,
            preflight_cwd=cwd,
            preflight_environment=environment,
        )
        return subprocess.CompletedProcess(arguments, 0, "codex help", "")

    def runner(arguments, input_text, cwd, environment):
        profile_path = Path(arguments[arguments.index("-f") + 1])
        isolated_auth = Path(environment["CODEX_HOME"]) / "auth.json"
        captured.update(
            arguments=arguments,
            input=input_text,
            cwd=cwd,
            environment=environment,
            profile=profile_path.read_text(encoding="utf-8"),
            auth_copied=json.loads(isolated_auth.read_text(encoding="utf-8"))
            == json.loads(TEST_AUTH_JSON),
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

    invoker = _codex_invoker(
        codex_binary=codex, sandbox_binary=Path("/usr/bin/sandbox-exec"),
        auth_file=auth, runner=runner, profile_runner=profile_runner,
    )
    result = invoker.invoke({"operation": "triage", "claims": [{"id": 1}]})

    assert result.payload["supported_claim_ids"] == [1]
    arguments = captured["arguments"]
    assert "--ignore-user-config" in arguments
    assert "--strict-config" in arguments
    assert 'approval_policy="never"' in arguments
    assert "analytics.enabled=false" in arguments
    assert "feedback.enabled=false" in arguments
    assert not str(captured["environment"]["CODEX_HOME"]).startswith(str(captured["cwd"]))
    assert captured["environment"]["HOME"] != str(Path.home())
    assert captured["environment"]["CFFIXED_USER_HOME"] == captured["environment"]["HOME"]
    assert str(auth) not in str(captured["profile"])
    assert captured["auth_copied"] is True
    assert captured["auth_mode"] == 0o600
    assert str(tmp_path) not in str(captured["input"])
    assert "--json" in arguments
    assert "--ignore-rules" in arguments
    for feature in assistance_module._DISABLED_CODEX_FEATURES:
        assert ["--disable", feature] == arguments[
            arguments.index(feature) - 1 : arguments.index(feature) + 1
        ]
    assert captured["preflight_cwd"] == captured["cwd"]
    assert captured["preflight_environment"] == captured["environment"]
    assert captured["preflight_arguments"][-1] == "--help"

    class FailoverBroker(_FakeBroker):
        failure_code = "broker_transport_failed"

    failover_result = _codex_invoker(
        codex_binary=codex,
        sandbox_binary=Path("/usr/bin/sandbox-exec"),
        auth_file=auth,
        runner=runner,
        profile_runner=profile_runner,
        broker_factory=FailoverBroker,
    ).invoke({"operation": "triage", "claims": [{"id": 1}]})
    assert failover_result.payload["supported_claim_ids"] == [1]


@pytest.mark.skipif(
    not Path("/usr/bin/sandbox-exec").is_file(),
    reason="macOS sandbox-exec is unavailable",
)
def test_outer_sandbox_profile_compiles_with_real_sandbox_exec(tmp_path: Path) -> None:
    task = tmp_path / "task"
    task.mkdir()
    auth = tmp_path / "credential-home" / "auth.json"
    auth.parent.mkdir()
    auth.write_text(TEST_AUTH_JSON, encoding="utf-8")
    profile = tmp_path / "sandbox.sb"
    profile.write_text(
        sandbox_profile(task, Path("/usr/bin/true"), auth, 43123), encoding="utf-8"
    )

    completed = subprocess.run(
        ["/usr/bin/sandbox-exec", "-f", str(profile), "/usr/bin/true"],
        check=False,
        capture_output=True,
        text=True,
    )

    if completed.returncode == 71 and "sandbox_apply: Operation not permitted" in completed.stderr:
        pytest.skip("the outer test sandbox prohibits nested sandbox application")
    assert completed.returncode == 0, completed.stderr


def test_codex_invoker_fails_on_process_malformed_and_oversized_results(tmp_path: Path) -> None:
    codex = tmp_path / "codex"
    codex.write_text("binary", encoding="utf-8")
    codex.chmod(0o755)
    auth = tmp_path / "auth.json"
    auth.write_text(TEST_AUTH_JSON, encoding="utf-8")

    def failed(arguments, _input, _cwd, _environment):
        return subprocess.CompletedProcess(arguments, 2, "", "SECRET_PACKET denied")

    with pytest.raises(AssistanceError, match="codex_process_failed") as failure:
        _codex_invoker(codex_binary=codex, auth_file=auth, runner=failed).invoke(
            {"operation": "triage", "claims": []}
        )
    assert "SECRET_PACKET" not in str(failure.value)

    def malformed(arguments, _input, _cwd, _environment):
        Path(arguments[arguments.index("--output-last-message") + 1]).write_text("not-json", encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    with pytest.raises(AssistanceError, match="malformed"):
        _codex_invoker(codex_binary=codex, auth_file=auth, runner=malformed).invoke(
            {"operation": "triage", "claims": []}
        )
    with pytest.raises(AssistanceError, match="64 KB"):
        _codex_invoker(codex_binary=codex, auth_file=auth, runner=malformed).invoke(
            {"operation": "triage", "claims": [], "large": "x" * 65_000}
        )


def test_codex_invoker_fails_closed_on_auth_preflight_timeout_and_missing_result(
    tmp_path: Path,
) -> None:
    codex = tmp_path / "codex"
    codex.write_text("binary", encoding="utf-8")
    codex.chmod(0o755)
    missing_auth = tmp_path / "missing-auth.json"
    packet = {"operation": "triage", "claims": []}

    with pytest.raises(AssistanceConfigurationError, match="authentication_unavailable"):
        _codex_invoker(codex_binary=codex, auth_file=missing_auth).invoke(packet)

    invalid_auth = tmp_path / "invalid-auth.json"
    invalid_auth.write_text("not-json", encoding="utf-8")
    with pytest.raises(AssistanceConfigurationError, match="not valid JSON"):
        _codex_invoker(codex_binary=codex, auth_file=invalid_auth).invoke(
            packet, credential_canary="marker"
        )
    invalid_auth.write_text("[]", encoding="utf-8")
    with pytest.raises(AssistanceConfigurationError, match="invalid shape"):
        _codex_invoker(codex_binary=codex, auth_file=invalid_auth).invoke(
            packet, credential_canary="marker"
        )

    auth = tmp_path / "auth.json"
    auth.write_text(TEST_AUTH_JSON, encoding="utf-8")

    def preflight_error(_arguments, _cwd, _environment):
        raise OSError("sandbox unavailable")

    with pytest.raises(AssistanceConfigurationError, match="preflight could not run"):
        _codex_invoker(
            codex_binary=codex,
            auth_file=auth,
            runner=lambda *_args: pytest.fail("main invocation must not run"),
            profile_runner=preflight_error,
        ).invoke(packet)

    def preflight_rejected(arguments, _cwd, _environment):
        return subprocess.CompletedProcess(arguments, 2, "", "rejected")

    with pytest.raises(AssistanceConfigurationError, match="flags were rejected"):
        _codex_invoker(
            codex_binary=codex,
            auth_file=auth,
            runner=lambda *_args: pytest.fail("main invocation must not run"),
            profile_runner=preflight_rejected,
        ).invoke(packet)

    def timeout(*_args):
        raise subprocess.TimeoutExpired("codex", 300)

    with pytest.raises(AssistanceTransientError, match="codex_timeout"):
        _codex_invoker(codex_binary=codex, auth_file=auth, runner=timeout).invoke(packet)

    def missing_result(arguments, _input, _cwd, _environment):
        return subprocess.CompletedProcess(arguments, 0, "", "")

    with pytest.raises(AssistanceTransientError, match="result_missing"):
        _codex_invoker(codex_binary=codex, auth_file=auth, runner=missing_result).invoke(packet)


def test_codex_event_stream_rejects_malformed_and_nested_tool_events() -> None:
    with pytest.raises(AssistanceTransientError, match="event_stream_invalid"):
        assistance_module._reject_tool_events("not-json")
    with pytest.raises(AssistanceConfigurationError, match="tool_invocation_blocked"):
        assistance_module._reject_tool_events(
            json.dumps({"events": [{"type": "custom_tool_call"}]})
        )
    assistance_module._reject_tool_events("\n" + json.dumps({"type": "completed"}))


def test_codex_invoker_rejects_any_reported_tool_execution(tmp_path: Path) -> None:
    codex = tmp_path / "codex"
    codex.write_text("binary", encoding="utf-8")
    codex.chmod(0o755)
    auth = tmp_path / "auth.json"
    auth.write_text(TEST_AUTH_JSON, encoding="utf-8")

    def used_tool(arguments, _input, _cwd, _environment):
        output = Path(arguments[arguments.index("--output-last-message") + 1])
        output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": "triage",
                    "supported_claim_ids": [],
                    "headline": "",
                    "factual_brief": "",
                    "lens": "",
                    "notes": "",
                }
            ),
            encoding="utf-8",
        )
        event = json.dumps(
            {"type": "item.completed", "item": {"type": "command_execution"}}
        )
        return subprocess.CompletedProcess(arguments, 0, event, "")

    with pytest.raises(AssistanceConfigurationError, match="tool_invocation_blocked"):
        _codex_invoker(codex_binary=codex, auth_file=auth, runner=used_tool).invoke(
            {"operation": "triage", "claims": []}
        )


def test_codex_canary_targets_the_actual_copied_credential(tmp_path: Path) -> None:
    codex = tmp_path / "codex"
    codex.write_text("binary", encoding="utf-8")
    codex.chmod(0o755)
    auth = tmp_path / "auth.json"
    auth.write_text(TEST_AUTH_JSON, encoding="utf-8")
    marker = "WIRE_CREDENTIAL_FILE_CANARY_" + "a" * 48
    observed: dict[str, object] = {}

    def runner(arguments, input_text, _cwd, environment):
        isolated_auth = Path(environment["CODEX_HOME"]) / "auth.json"
        copied = json.loads(isolated_auth.read_text(encoding="utf-8"))
        observed.update(
            copied_marker=copied.get("wire_canary"),
            input_text=input_text,
            isolated_auth=str(isolated_auth),
        )
        output = Path(arguments[arguments.index("--output-last-message") + 1])
        output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": "isolation_canary",
                    "supported_claim_ids": [],
                    "headline": "",
                    "factual_brief": "",
                    "lens": "",
                    "notes": "Access was blocked.",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(arguments, 0, "", "")

    _codex_invoker(codex_binary=codex, auth_file=auth, runner=runner).invoke(
        {
            "operation": "isolation_canary",
            "claims": [],
            "human_guidance": "Run the isolation checks.",
        },
        credential_canary=marker,
    )

    assert observed["copied_marker"] == marker
    assert observed["isolated_auth"] in str(observed["input_text"])
    assert "wire_canary marker from the copied credential" in str(observed["input_text"])
    assert marker not in str(observed["input_text"])


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
    with pytest.raises(AssistanceError, match="Material evidence changed"):
        build_packet(database, lens_id)

    neutral_id = database.execute(
        "INSERT INTO work_item(story_id, kind, status, priority, payload_json, created_at, updated_at) VALUES('story-demo-runtime-001','draft','queued',1,?, ?, ?)",
        (json.dumps({"mode": "Neutral News Brief", "story": {"material_update": True}}), now, now),
    )
    with pytest.raises(AssistanceError, match="Material evidence changed"):
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
    assert "allOf" not in schema
    reddit_schema = json.loads(
        files("open_source_ai_news_wire")
        .joinpath("schemas", "assistance-reddit-result.schema.json")
        .read_text(encoding="utf-8")
    )
    assert set(reddit_schema["required"]) == set(reddit_schema["properties"])
    assert reddit_schema["properties"]["subreddit_reminder"]["const"] == (
        "Verify rules before posting"
    )
