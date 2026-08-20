from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from open_source_ai_news_wire import __version__
from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.evidence import qualification_state, recalculate_story_qualification
from open_source_ai_news_wire.services import DashboardService, human_bytes
from open_source_ai_news_wire.source_registry import synchronize_sources
from open_source_ai_news_wire.storage import Database


@pytest.fixture
def service(tmp_path: Path) -> DashboardService:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    return DashboardService(database)


def test_overview_separates_ready_researching_and_content(service: DashboardService) -> None:
    data = service.overview()
    assert data["counts"] == {
        "ready": 3,
        "researching": 0,
        "content_ready": 1,
        "urgent": 2,
        "review_now": 4,
    }
    assert data["sources"]["degraded"] == 1
    assert data["schedule"]["background_units"] == 3
    assert data["schedule"]["draft_units"] == 2
    assert data["queue_count"] == 0
    assert data["queue_lag"] == "Clear"

    service.schedule_action("run_now")
    queued = service.overview()
    assert queued["queue_count"] == 1
    assert queued["queue_lag"] == "<1 min"


def test_review_now_excludes_old_and_completed_work_and_supports_newest_sort(
    service: DashboardService,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    service.database.execute(
        """
        INSERT INTO story_cluster(
            id, slug, headline, summary, lane, openness_class, status, priority,
            priority_score, freshness, first_public_at, detected_at, created_at,
            updated_at, importance_score, importance_json, ingestion_context
        ) VALUES('old-urgent', 'old-urgent', 'Old urgent AI event', 'Old context',
                 'Broader AI News', 'not stated', 'signal', 'Urgent', 99, 'Breaking',
                 ?, ?, ?, ?, 60, '{}', 'legacy')
        """,
        tuple(
            (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
            for _ in range(4)
        ),
    )

    current = service.list_story_page(window="review_now", sort="priority")
    assert "old-urgent" not in {story["id"] for story in current["stories"]}
    assert "story-demo-eval-004" in {story["id"] for story in current["stories"]}
    assert all(story["freshness"] in {"Breaking", "Fresh", "Updated"} for story in current["stories"])

    newest = service.list_story_page(window="review_now", sort="newest")["stories"]
    anchors = [story["ranking_anchor_at"] for story in newest]
    assert anchors == sorted(anchors, reverse=True)
    older = service.list_story_page(window="older")["stories"]
    assert "old-urgent" in {story["id"] for story in older}


def test_legacy_aggregator_only_story_is_newly_surfaced_until_dated(
    service: DashboardService,
) -> None:
    story_id = "story-demo-watch-003"
    synchronize_sources(service.database)
    service.database.execute(
        """
        UPDATE source_item
        SET source_registry_id = 'hacker-news-ai',
            source_name = 'Hacker News AI Discovery', source_role = 'Discovery',
            timestamp_status = 'legacy_assumed'
        WHERE story_id = ?
        """,
        (story_id,),
    )

    surfaced = service.get_story(story_id)
    assert surfaced["original_publication_known"] is False
    assert surfaced["freshness"] == "Newly surfaced"
    assert surfaced["evidence_points"] == 0
    assert story_id not in {
        row["id"] for row in service.list_story_page(window="review_now")["stories"]
    }

    service.database.execute(
        """
        INSERT INTO evidence_source(
            story_id, requested_url, canonical_url, publisher_key,
            acquisition_method, proposed_role, status, published_at,
            created_at, updated_at
        ) VALUES(?, 'https://publisher.example/old-report',
                 'https://publisher.example/old-report', 'publisher.example',
                 'discovery_enrichment', 'Reporting', 'fetched',
                 '2021-04-03T12:00:00Z', '2026-07-23T09:00:00Z',
                 '2026-07-23T09:00:00Z')
        """,
        (story_id,),
    )
    service.database.execute(
        "UPDATE story_cluster SET first_public_at = '2021-04-03T12:00:00Z' WHERE id = ?",
        (story_id,),
    )
    dated = service.get_story(story_id)
    assert dated["original_publication_known"] is True
    assert dated["freshness"] == "Older"
    assert story_id in {
        row["id"] for row in service.list_story_page(window="older")["stories"]
    }


def test_material_update_returns_old_story_to_review_now(service: DashboardService) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    service.database.execute(
        """
        INSERT INTO story_cluster(
            id, slug, headline, summary, lane, openness_class, status, priority,
            priority_score, freshness, first_public_at, detected_at, created_at,
            updated_at, importance_score, importance_json, material_updated_at,
            ingestion_context
        ) VALUES('updated-old', 'updated-old', 'Updated AI policy event', 'New material claim',
                 'Broader AI News', 'not stated', 'signal', 'Standard', 40, 'Older',
                 ?, ?, ?, ?, 45, '{}', ?, 'scheduled')
        """,
        (
            (now - timedelta(days=5)).isoformat().replace("+00:00", "Z"),
            (now - timedelta(days=5)).isoformat().replace("+00:00", "Z"),
            (now - timedelta(days=5)).isoformat().replace("+00:00", "Z"),
            now.isoformat().replace("+00:00", "Z"),
            (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        ),
    )
    rows = service.list_story_page(window="review_now")["stories"]
    updated = next(story for story in rows if story["id"] == "updated-old")
    assert updated["freshness"] == "Updated"
    assert updated["is_material_update_current"] is True


def test_review_queue_paginates_tied_scores_without_duplicates(
    service: DashboardService,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    moment = (now - timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    with service.database.transaction() as connection:
        for index in range(30):
            story_id = f"page-story-{index:02d}"
            connection.execute(
                """
                INSERT INTO story_cluster(
                    id, slug, headline, summary, lane, openness_class, status,
                    priority, priority_score, freshness, first_public_at,
                    detected_at, created_at, updated_at, importance_score,
                    importance_json, ingestion_context
                ) VALUES(?, ?, ?, 'Current AI event', 'Broader AI News',
                         'not stated', 'signal', 'Urgent', 99, 'Breaking',
                         ?, ?, ?, ?, 40, '{}', 'scheduled')
                """,
                (story_id, story_id, f"Tied current story {index}", moment, moment, moment, moment),
            )

    first = service.list_story_page(window="review_now", page_size=25)
    second = service.list_story_page(
        window="review_now", page_size=25, cursor=first["next_cursor"]
    )
    first_ids = {story["id"] for story in first["stories"]}
    second_ids = {story["id"] for story in second["stories"]}

    assert len(first["stories"]) == 25
    assert first["next_cursor"]
    assert first_ids.isdisjoint(second_ids)
    assert len(first_ids | second_ids) == first["total"]

    forged = service.list_story_page(
        window="review_now", sort="newest", cursor=first["next_cursor"], page_size=25
    )
    assert forged["stories"]


def test_momentum_uses_velocity_and_rank_movement_without_verifying(
    service: DashboardService,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    with service.database.transaction() as connection:
        connection.executemany(
            """
            INSERT INTO momentum_snapshot(
                story_id, source_id, captured_at, native_score, post_count,
                account_count, daily_rank, distinct_identity_count
            ) VALUES(?, 'huggingnews', ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "story-demo-watch-003",
                    (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                    10,
                    6,
                    4,
                    12,
                    4,
                ),
                (
                    "story-demo-watch-003",
                    (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                    50,
                    28,
                    15,
                    2,
                    15,
                ),
                (
                    "story-demo-policy-002",
                    (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                    10,
                    5,
                    3,
                    5,
                    3,
                ),
                (
                    "story-demo-policy-002",
                    (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                    11,
                    6,
                    3,
                    5,
                    3,
                ),
            ],
        )

    story = service.get_story("story-demo-watch-003")
    assert story["momentum_velocity_points"] == 5
    assert story["momentum_daily_rank"] == 2
    # Reported account counts are not identities; only the measured velocity
    # contributes until concrete publisher/account keys are stored.
    assert story["attention_level"] == "Building attention"
    assert story["source_identity_count"] == 1
    assert story["evidence_points"] == 0
    assert story["evidence_state"] == "Verification pending"


def test_momentum_breadth_deduplicates_publishers_and_accounts(
    service: DashboardService,
) -> None:
    story_id = "story-demo-watch-003"
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with service.database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO source_item(
                story_id, source_name, source_role, title, url, published_at,
                language, verification_status, passage, canonical_url,
                fingerprint, content_hash, first_seen_at
            ) VALUES(?, 'Duplicate host', 'Discovery', 'Same publisher mention',
                     'https://news.example.invalid/another', ?, 'en', 'trace',
                     'A repeated mention.', 'https://news.example.invalid/another',
                     'same-publisher', 'same-publisher', ?)
            """,
            (story_id, now, now),
        )
        connection.executemany(
            """
            INSERT INTO discovery_lead(
                story_id, via_source_id, external_id, identity_key, display_name,
                url, published_at, metadata_json, created_at, updated_at
            ) VALUES(?, 'huggingnews', ?, ?, 'Public account', ?, ?, '{}', ?, ?)
            """,
            [
                (story_id, "post-a", "@same-account", "https://social.example/a", now, now, now),
                (story_id, "post-b", "@same-account", "https://social.example/b", now, now, now),
                (story_id, "post-c", "@other-account", "https://social.example/c", now, now, now),
            ],
        )

    story = service.get_story(story_id)
    # Two source rows share one normalized publisher, and the duplicate account
    # key is counted once. Raw row and reported engagement counts are irrelevant.
    assert story["source_count"] == 3
    assert story["source_identity_count"] == 3
    assert story["momentum_score"] == 3
    assert story["evidence_state"] == "Verification pending"


def test_human_approval_queues_draft_with_snapshot(service: DashboardService) -> None:
    status = service.review("story-demo-runtime-001", "approve_neutral", "Keep the scope tight.")
    assert status == "approved"

    work = service.database.one(
        "SELECT * FROM work_item WHERE story_id = ? AND kind = 'draft'",
        ("story-demo-runtime-001",),
    )
    assert work is not None
    snapshot = json.loads(work["payload_json"])
    assert snapshot["schema_version"] == 3
    assert snapshot["story_id"] == "story-demo-runtime-001"
    assert snapshot["mode"] == "Neutral News Brief"
    assert snapshot["approval_snapshot_at"]
    assert snapshot["story"]["id"] == "story-demo-runtime-001"
    assert snapshot["story"]["first_public_at"]
    assert len(snapshot["claims"]) == 2
    assert {claim["status"] for claim in snapshot["claims"]} == {"verified"}
    assert len(snapshot["sources"]) == 2
    assert snapshot["story_revision"] == 1
    assert len(snapshot["claim_signatures"]) == 2
    assert len(snapshot["source_signatures"]) == 2
    assert all(source["url"].startswith("https://") for source in snapshot["sources"])
    assert work["status"] == "queued"
    assert work["idempotency_key"].startswith("draft-approval:")
    assert work["available_at"]
    action = service.database.one(
        """
        SELECT approval_snapshot_json, story_revision, claim_signatures_json,
               source_signatures_json FROM review_action WHERE id = ?
        """,
        (snapshot["review_action_id"],),
    )
    assert json.loads(action["approval_snapshot_json"])["story"]["id"] == "story-demo-runtime-001"
    assert action["story_revision"] == snapshot["story_revision"]
    assert json.loads(action["claim_signatures_json"]) == snapshot["claim_signatures"]
    assert json.loads(action["source_signatures_json"]) == snapshot["source_signatures"]


def test_approval_aborts_if_story_revision_changes_before_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    service = DashboardService(database)
    original_transaction = database.transaction
    drifted = False

    @contextmanager
    def transaction_with_drift():
        nonlocal drifted
        with original_transaction() as connection:
            if not drifted:
                connection.execute(
                    "UPDATE story_cluster SET story_revision = story_revision + 1 WHERE id = ?",
                    ("story-demo-watch-003",),
                )
                drifted = True
            yield connection

    monkeypatch.setattr(database, "transaction", transaction_with_drift)
    with pytest.raises(ValueError, match="changed during approval"):
        service.review(
            "story-demo-watch-003",
            "manual_approve_neutral",
            confirmation_version="manual_override_v1",
        )
    assert database.one(
        "SELECT COUNT(*) AS count FROM work_item WHERE story_id = 'story-demo-watch-003'"
    ) == {"count": 0}


def test_human_approval_dispatches_exact_work_after_commit(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    observed: list[tuple[int, str, str]] = []

    def dispatch(work_item_id: int) -> None:
        work = database.one("SELECT status, story_id FROM work_item WHERE id = ?", (work_item_id,))
        story = database.one("SELECT status FROM story_cluster WHERE id = ?", (work["story_id"],))
        observed.append((work_item_id, work["status"], story["status"]))

    immediate = DashboardService(database, draft_callback=dispatch)
    assert immediate.review("story-demo-runtime-001", "approve_neutral") == "approved"
    assert observed and observed[0][1:] == ("queued", "approved")


def test_dispatch_failure_preserves_durable_queue(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)

    def fail_dispatch(_work_item_id: int) -> None:
        raise RuntimeError("test-only callback failure")

    immediate = DashboardService(database, draft_callback=fail_dispatch)
    immediate.review("story-demo-runtime-001", "approve_neutral")
    work = database.one("SELECT status FROM work_item WHERE kind = 'draft'")
    assert work == {"status": "queued"}
    diagnostic = database.one(
        "SELECT message, detail_json FROM diagnostic_event WHERE event_type = 'draft_dispatch'"
    )
    assert "durable queue was preserved" in diagnostic["message"]
    assert "test-only callback failure" not in diagnostic["detail_json"]


def test_pending_draft_approval_cannot_be_queued_twice(service: DashboardService) -> None:
    service.review("story-demo-runtime-001", "approve_neutral", "Keep the scope tight.")

    with pytest.raises(ValueError, match="already pending"):
        service.review("story-demo-runtime-001", "approve_neutral", "Duplicate request.")

    assert service.database.one(
        "SELECT COUNT(*) AS count FROM work_item WHERE story_id = ? AND kind = 'draft'",
        ("story-demo-runtime-001",),
    )["count"] == 1


def test_failed_draft_retries_same_work_without_new_approval(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    dispatched: list[int] = []
    immediate = DashboardService(database, draft_callback=dispatched.append)
    immediate.review("story-demo-runtime-001", "approve_neutral")
    work_id = dispatched[-1]
    database.execute(
        "UPDATE work_item SET status = 'failed', attempt_count = 2, last_error_class = 'codex_timeout' WHERE id = ?",
        (work_id,),
    )

    assert immediate.retry_draft("story-demo-runtime-001") == work_id
    assert dispatched == [work_id, work_id]
    assert database.one(
        "SELECT status, attempt_count, last_error_class FROM work_item WHERE id = ?", (work_id,)
    ) == {"status": "queued", "attempt_count": 0, "last_error_class": None}
    assert database.one(
        "SELECT COUNT(*) AS count FROM review_action WHERE action = 'approve_neutral'"
    )["count"] == 1
    assert database.one(
        "SELECT COUNT(*) AS count FROM review_action WHERE action = 'retry_draft'"
    )["count"] == 1


def test_draft_status_and_history_include_requests_before_a_draft_exists(service: DashboardService) -> None:
    service.review("story-demo-runtime-001", "approve_neutral")
    status = service.draft_status("story-demo-runtime-001")
    assert status["status"] == "starting"
    assert status["active"] is True
    requests = [entry for entry in service.list_drafts() if entry["entry_kind"] == "request"]
    assert len(requests) == 1
    assert requests[0]["display_status"] == "Starting"

    service.database.execute(
        "UPDATE work_item SET status = 'waiting', last_error_class = 'assistance_disabled' WHERE id = ?",
        (status["work_item_id"],),
    )
    waiting = service.draft_status("story-demo-runtime-001")
    assert waiting["status"] == "waiting_for_isolation"
    assert waiting["retryable"] is True
    assert waiting["manual_editor_available"] is True
    assert service.database.one(
        "SELECT COUNT(*) AS count FROM review_action WHERE story_id = ? AND action = 'approve_neutral'",
        ("story-demo-runtime-001",),
    )["count"] == 1


@pytest.mark.parametrize(
    ("raw_status", "attempts", "error_code", "expected_status"),
    (
        ("waiting", 0, "waiting_for_login", "waiting_for_login"),
        ("waiting", 0, "codex_authentication_invalid", "waiting_for_login"),
        ("waiting", 0, "waiting_for_usage_reset", "waiting_for_usage_reset"),
        ("waiting", 0, "volatile_source_unavailable", "waiting"),
        ("waiting", 0, "volatile_source_unsafe", "failed_security"),
        ("needs_reapproval", 0, "volatile_source_drift", "needs_reapproval"),
        ("generating", 1, "", "generating"),
        ("deferred", 1, "codex_unavailable", "failed"),
        ("completed", 1, "", "draft_ready"),
        ("cancelled", 1, "manual_draft_completed", "draft_ready"),
        ("waiting", 0, "codex_identity_mismatch", "failed_security"),
        ("waiting", 0, "attestation_expired", "waiting_for_isolation"),
        ("waiting", 0, "isolation_not_passed", "waiting_for_isolation"),
        ("waiting", 0, "story_revision_changed", "needs_reapproval"),
    ),
)
def test_draft_status_exposes_exact_operational_condition(
    service: DashboardService,
    raw_status: str,
    attempts: int,
    error_code: str,
    expected_status: str,
) -> None:
    service.review("story-demo-runtime-001", "approve_neutral")
    service.database.execute(
        "UPDATE work_item SET status = ?, attempt_count = ?, last_error_class = ? WHERE kind = 'draft'",
        (raw_status, attempts, error_code),
    )

    status = service.draft_status("story-demo-runtime-001")

    assert status["status"] == expected_status
    assert status["message"] != "Draft generation is awaiting a safe next step."


def test_editable_shell_save_rechecks_transactional_ownership(
    service: DashboardService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.review("story-demo-runtime-001", "approve_neutral")
    shell = service.database.one(
        "SELECT * FROM draft WHERE story_id = 'story-demo-runtime-001'"
    )
    real_get_draft = service.get_draft

    def stale_shell(draft_id: int):
        draft = real_get_draft(draft_id)
        service.database.execute(
            "UPDATE draft SET status = 'Superseded' WHERE id = ?", (draft_id,)
        )
        return draft

    monkeypatch.setattr(service, "get_draft", stale_shell)

    with pytest.raises(ValueError, match="changed while it was being edited"):
        service.save_draft(
            int(shell["id"]), shell["headline"], shell["metadata"], shell["body"], ""
        )


def test_editable_shell_save_loses_to_completed_generation(
    service: DashboardService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.review("story-demo-runtime-001", "approve_neutral")
    shell = service.database.one(
        "SELECT * FROM draft WHERE story_id = 'story-demo-runtime-001'"
    )
    real_get_draft = service.get_draft

    def completed_work(draft_id: int):
        draft = real_get_draft(draft_id)
        service.database.execute(
            "UPDATE work_item SET status = 'completed' WHERE kind = 'draft'"
        )
        return draft

    monkeypatch.setattr(service, "get_draft", completed_work)

    with pytest.raises(ValueError, match="generation completed"):
        service.save_draft(
            int(shell["id"]), shell["headline"], shell["metadata"], shell["body"], ""
        )


@pytest.mark.parametrize("provenance", ("{", "{}"))
def test_editable_shell_requires_work_provenance(
    service: DashboardService, provenance: str
) -> None:
    service.review("story-demo-runtime-001", "approve_neutral")
    shell = service.database.one(
        "SELECT * FROM draft WHERE story_id = 'story-demo-runtime-001'"
    )
    service.database.execute(
        "UPDATE draft SET provenance_json = ? WHERE id = ?", (provenance, shell["id"])
    )

    with pytest.raises(ValueError, match="not linked"):
        service.save_draft(
            int(shell["id"]), shell["headline"], shell["metadata"], shell["body"], ""
        )


def test_malformed_legacy_draft_json_remains_visible(service: DashboardService) -> None:
    service.review("story-demo-runtime-001", "approve_neutral")
    service.database.execute(
        "UPDATE draft SET approval_snapshot_json = '{' WHERE story_id = 'story-demo-runtime-001'"
    )
    service.database.execute(
        "UPDATE work_item SET payload_json = '{' WHERE story_id = 'story-demo-runtime-001'"
    )

    entries = service.list_drafts()

    assert {entry["entry_kind"] for entry in entries} == {"draft", "request"}
    assert all(entry["approval_basis"] == "verified" for entry in entries)


def test_completed_request_without_draft_is_visible_as_failed(
    service: DashboardService,
) -> None:
    service.review("story-demo-runtime-001", "approve_neutral")
    service.database.execute(
        "DELETE FROM draft WHERE story_id = 'story-demo-runtime-001'"
    )
    service.database.execute(
        "UPDATE work_item SET status = 'completed', payload_json = '{' WHERE story_id = 'story-demo-runtime-001'"
    )

    status = service.draft_status("story-demo-runtime-001")

    assert status["status"] == "failed"
    assert status["retryable"] is True


def test_editable_shell_builder_handles_invalid_and_duplicate_snapshot_rows(
    service: DashboardService,
) -> None:
    assert service._create_editable_draft_shell(999_999, {}) is None
    service.review("story-demo-runtime-001", "approve_neutral")
    work = service.database.one(
        "SELECT id, payload_json FROM work_item WHERE story_id = 'story-demo-runtime-001'"
    )
    shell = service.database.one(
        "SELECT id FROM draft WHERE story_id = 'story-demo-runtime-001'"
    )
    assert service._create_editable_draft_shell(int(work["id"]), {}) is None

    service.database.execute("DELETE FROM draft WHERE id = ?", (shell["id"],))
    snapshot = json.loads(work["payload_json"])
    snapshot["story"]["summary"] = ""
    snapshot["claims"] = [{"text": "Fallback claim text"}]
    snapshot["sources"] = [
        {
            "citation_label": "Unsafe source",
            "citation_url": "http://127.0.0.1/private",
            "source_role": "Discovery",
        },
        {
            "citation_label": "Unsafe source",
            "citation_url": "http://127.0.0.1/private",
            "source_role": "Discovery",
        },
        "invalid row",
    ]

    created = service._create_editable_draft_shell(int(work["id"]), snapshot)

    assert created is not None
    draft = service.get_draft(created)
    assert draft["body"] == "Fallback claim text"
    assert draft["sources"] == [
        {
            "display_label": "Unsafe source",
            "title": "Unsafe source",
            "url": "",
            "role": "Discovery",
            "hosting_publisher_name": "",
            "reporting_origin_name": "",
            "provenance_type": "unknown",
            "source_provenance": "configured",
        }
    ]
    assert service._create_editable_draft_shell(int(work["id"]), snapshot) == created


def test_unavailable_assistance_creates_an_editable_shell_that_can_be_completed(
    service: DashboardService,
) -> None:
    service.review(
        "story-demo-watch-003",
        "manual_approve_neutral",
        confirmation_version="manual_override_v1",
    )
    shell = service.database.one(
        "SELECT * FROM draft WHERE story_id = ? ORDER BY version DESC LIMIT 1",
        ("story-demo-watch-003",),
    )
    assert shell is not None
    assert shell["status"] == "Editable Shell"
    assert "unverified" not in shell["body"].casefold()
    assert "provisional" not in shell["body"].casefold()

    completed_id = service.save_draft(
        int(shell["id"]),
        shell["headline"],
        shell["metadata"],
        f"{shell['body']} Human-reviewed context.",
        "",
    )

    assert service.get_draft(completed_id)["status"] == "Current"
    assert service.database.one(
        "SELECT status, last_error_class FROM work_item WHERE story_id = ? AND kind = 'draft'",
        ("story-demo-watch-003",),
    ) == {"status": "cancelled", "last_error_class": "manual_draft_completed"}
    assert service.database.one(
        "SELECT status FROM story_cluster WHERE id = ?",
        ("story-demo-watch-003",),
    ) == {"status": "draft_ready"}
    status = service.draft_status("story-demo-watch-003")
    assert status["status"] == "draft_ready"
    assert status["draft_id"] == completed_id


def test_watch_cannot_be_approved_for_drafting(service: DashboardService) -> None:
    with pytest.raises(ValueError, match="Evidence and importance"):
        service.review("story-demo-watch-003", "approve_neutral")


@pytest.mark.parametrize(
    ("action", "mode"),
    (
        ("manual_approve_neutral", "Neutral News Brief"),
        ("manual_approve_lens", "Open-Source Lens Brief"),
    ),
)
def test_manual_approval_overrides_all_gates_and_dispatches_immediately(
    tmp_path: Path, action: str, mode: str
) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    dispatched: list[int] = []
    immediate = DashboardService(database, draft_callback=dispatched.append)

    assert immediate.review(
        "story-demo-watch-003",
        action,
        confirmation_version="manual_override_v1",
    ) == "approved"

    assert len(dispatched) == 1
    work = database.one("SELECT * FROM work_item WHERE id = ?", (dispatched[0],))
    payload = json.loads(work["payload_json"])
    assert payload["mode"] == mode
    assert payload["approval_basis"] == "manual_override"
    assert payload["manual_override"] is True
    assert payload["confirmation_version"] == "manual_override_v1"
    assert payload["qualification_snapshot"] == {
        "evidence_gate": False,
        "importance_gate": True,
        "automated_importance": True,
        "effective_importance": True,
        "lens_gate": False,
        "lens_eligible": False,
        "candidate_basis": "unqualified",
    }
    assert all(source["source_role"] == "Discovery" for source in payload["sources"])
    assert all(source["citation_allowed"] for source in payload["sources"])
    candidate = database.one(
        """
        SELECT evidence_gate, importance_gate, manual_override,
               manual_override_action_id, manual_override_snapshot_json
        FROM candidate WHERE story_id = 'story-demo-watch-003'
        """
    )
    assert candidate["evidence_gate"] == 0
    assert candidate["importance_gate"] == 1
    assert candidate["manual_override"] == 1
    assert candidate["manual_override_action_id"] == payload["review_action_id"]
    assert json.loads(candidate["manual_override_snapshot_json"])["evidence_gate"] is False
    action_row = database.one(
        "SELECT action, approval_snapshot_json FROM review_action WHERE id = ?",
        (payload["review_action_id"],),
    )
    assert action_row["action"] == action
    assert json.loads(action_row["approval_snapshot_json"])["review_action_id"] == payload["review_action_id"]
    state = immediate.get_story("story-demo-watch-003")
    assert state["candidate_basis"] == "manual_override"
    assert state["manual_override_active"] is True
    assert state["draft_eligible"] is True
    assert state["approval_basis"] == "manual_override"
    with pytest.raises(ValueError, match="already pending"):
        immediate.review(
            "story-demo-watch-003",
            action,
            confirmation_version="manual_override_v1",
        )
    assert database.one(
        "SELECT COUNT(*) AS count FROM work_item WHERE story_id = 'story-demo-watch-003'"
    )["count"] == 1


def test_manual_approval_requires_versioned_confirmation_and_preserves_gate_results(
    service: DashboardService,
) -> None:
    before = service.database.one(
        "SELECT evidence_gate, importance_gate FROM candidate WHERE story_id = 'story-demo-watch-003'"
    )

    for confirmation in ("", "manual_override_v0", "yes"):
        with pytest.raises(ValueError, match="current confirmation prompt"):
            service.review(
                "story-demo-watch-003",
                "manual_approve_neutral",
                confirmation_version=confirmation,
            )

    assert service.database.one(
        "SELECT evidence_gate, importance_gate, manual_override FROM candidate WHERE story_id = 'story-demo-watch-003'"
    ) == {**before, "manual_override": 0}
    assert service.database.one(
        "SELECT COUNT(*) AS count FROM review_action WHERE action LIKE 'manual_approve_%'"
    )["count"] == 0


def test_manual_approval_rejects_inactive_sourceless_and_claimless_stories(
    service: DashboardService,
) -> None:
    service.database.execute(
        "UPDATE story_cluster SET status = 'archived' WHERE id = 'story-demo-watch-003'"
    )
    with pytest.raises(ValueError, match="Archived or withdrawn"):
        service.review(
            "story-demo-watch-003",
            "manual_approve_neutral",
            confirmation_version="manual_override_v1",
        )

    service.database.execute(
        "UPDATE story_cluster SET status = 'watch' WHERE id = 'story-demo-watch-003'"
    )
    service.database.execute(
        "DELETE FROM source_item WHERE story_id = 'story-demo-watch-003'"
    )
    with pytest.raises(ValueError, match="safe public source"):
        service.review(
            "story-demo-watch-003",
            "manual_approve_neutral",
            confirmation_version="manual_override_v1",
        )

    service.database.execute(
        "DELETE FROM claim WHERE story_id = 'story-demo-watch-003'"
    )
    with pytest.raises(ValueError, match="stored claim"):
        service.review(
            "story-demo-watch-003",
            "manual_approve_neutral",
            confirmation_version="manual_override_v1",
        )


def test_manual_approval_rejects_unsafe_stored_urls(service: DashboardService) -> None:
    service.database.execute(
        "UPDATE source_item SET url = 'https://127.0.0.1/private/' || id, canonical_url = 'https://127.0.0.1/private/' || id WHERE story_id = 'story-demo-watch-003'"
    )

    with pytest.raises(ValueError, match="safe public source"):
        service.review(
            "story-demo-watch-003",
            "manual_approve_neutral",
            confirmation_version="manual_override_v1",
        )


def test_manual_selection_survives_recalculation_and_later_shows_evidence_basis(
    service: DashboardService,
) -> None:
    service.review(
        "story-demo-watch-003",
        "manual_approve_neutral",
        confirmation_version="manual_override_v1",
    )
    service.database.execute(
        "UPDATE story_cluster SET status = 'candidate' WHERE id = 'story-demo-watch-003'"
    )

    state = qualification_state(service.database, "story-demo-watch-003")
    assert state["candidate_basis"] == "manual_override"
    recalculate_story_qualification(service.database, "story-demo-watch-003")
    assert service.database.one(
        "SELECT status FROM story_cluster WHERE id = 'story-demo-watch-003'"
    ) == {"status": "candidate"}

    service.database.execute(
        "UPDATE source_item SET source_role = 'Event' WHERE story_id = 'story-demo-watch-003'"
    )
    state = recalculate_story_qualification(service.database, "story-demo-watch-003")
    assert state["qualified"] is True
    assert state["candidate_basis"] == "evidence"
    assert state["manual_override_active"] is True


def test_manual_override_does_not_silently_replace_a_completed_draft(
    service: DashboardService,
) -> None:
    with pytest.raises(ValueError, match="completed draft already exists"):
        service.review(
            "story-demo-eval-004",
            "manual_approve_lens",
            confirmation_version="manual_override_v1",
        )
    assert service.database.one(
        "SELECT COUNT(*) AS count FROM work_item WHERE story_id = 'story-demo-eval-004'"
    )["count"] == 0
    assert service.database.one(
        "SELECT COUNT(*) AS count FROM review_action WHERE story_id = 'story-demo-eval-004' AND action = 'manual_approve_lens'"
    )["count"] == 0

    service.database.execute("UPDATE draft SET status = 'Needs Review' WHERE id = 1")
    service.database.execute(
        "UPDATE story_cluster SET status = 'candidate' WHERE id = 'story-demo-eval-004'"
    )
    assert service.review(
        "story-demo-eval-004",
        "manual_approve_lens",
        confirmation_version="manual_override_v1",
    ) == "approved"
    assert service.database.one(
        "SELECT COUNT(*) AS count FROM work_item WHERE story_id = 'story-demo-eval-004'"
    )["count"] == 1


def test_lens_requires_strong_or_moderate_opportunity(service: DashboardService) -> None:
    with pytest.raises(ValueError, match="Strong or Moderate"):
        service.review("story-demo-eval-004", "approve_lens")

    assert service.review("story-demo-policy-002", "approve_lens") == "approved"


def test_draft_save_creates_version_and_preserves_history(service: DashboardService) -> None:
    new_id = service.save_draft(
        1,
        "Revised factual headline",
        "AGI Development · Fresh",
        "Revised factual brief with a clear evidence boundary.",
        "",
    )
    original = service.get_draft(1)
    revised = service.get_draft(new_id)

    assert original["status"] == "Superseded"
    assert [correction["title"] for correction in original["corrections"]] == ["Draft evidence needs review"]
    assert revised["version"] == 2
    assert revised["status"] == "Current"
    assert revised["supersedes_id"] == 1
    assert revised["comparison"]["direction"] == "Previous"
    assert revised["comparison"]["version"] == 1
    assert revised["comparison"]["changes"] == {
        "headline": True,
        "metadata": True,
        "body": True,
        "lens": False,
    }
    assert original["comparison"]["direction"] == "Next"
    assert original["comparison"]["version"] == 2

    with pytest.raises(ValueError, match="current draft version"):
        service.save_draft(1, "Branch headline", "Meta", "Body", "")


def test_neutral_draft_cannot_gain_unapproved_lens(service: DashboardService) -> None:
    with pytest.raises(ValueError, match="approved lens brief"):
        service.save_draft(1, "Headline", "Meta", "Body", "Unapproved analysis")

    service.database.execute(
        "UPDATE draft SET mode = 'Open-Source Lens Brief' WHERE id = 1"
    )
    service.database.execute(
        "UPDATE story_cluster SET opportunity_strength = 'Moderate' WHERE id = 'story-demo-eval-004'"
    )
    revised_id = service.save_draft(1, "Headline", "Meta", "Body", "Approved analysis")
    assert service.get_draft(revised_id)["lens"] == "Approved analysis"


def test_browser_line_endings_do_not_create_false_version_changes(service: DashboardService) -> None:
    original = service.get_draft(1)
    revised_id = service.save_draft(
        1,
        original["headline"],
        original["metadata"],
        original["body"].replace("\n", "\r\n"),
        "",
    )
    revised = service.get_draft(revised_id)
    assert revised["body"] == original["body"]
    assert revised["comparison"]["changes"] == {
        "headline": False,
        "metadata": False,
        "body": False,
        "lens": False,
    }


def test_schedule_actions_persist_without_claiming_installation(service: DashboardService) -> None:
    assert service.schedule_action("run_now") == "queued"
    assert service.schedule_action("run_now") == "already_queued"
    assert service.schedule_status()["queue"][0]["kind"] == "scout_scan"
    assert service.database.one(
        "SELECT COUNT(*) AS count FROM work_item WHERE kind = 'scout_scan'"
    )["count"] == 1

    with pytest.raises(ValueError, match="not installed"):
        service.schedule_action("resume")

    assert service.schedule_action("pause") == "paused"


def test_extended_catchup_requires_a_human_selected_past_interval(service: DashboardService) -> None:
    today = datetime.now(UTC).date()
    start = today - timedelta(days=14)
    end = today - timedelta(days=8)

    assert service.queue_extended_catchup(start.isoformat(), end.isoformat()) == "queued"
    assert service.queue_extended_catchup(start.isoformat(), end.isoformat()) == "already_queued"
    queued = service.database.one("SELECT * FROM work_item WHERE kind = 'catch_up'")
    assert queued is not None
    assert json.loads(queued["payload_json"]) == {
        "end_date": end.isoformat(),
        "start_date": start.isoformat(),
        "trigger": "human",
    }
    scan = service.database.one("SELECT * FROM scan_run WHERE trigger_type = 'extended-catch-up'")
    assert scan is not None
    assert "human-selected interval" in scan["details"]

    with pytest.raises(ValueError, match="valid start and end"):
        service.queue_extended_catchup("not-a-date", end.isoformat())
    with pytest.raises(ValueError, match="must not be after"):
        service.queue_extended_catchup(today.isoformat(), end.isoformat())
    with pytest.raises(ValueError, match="future dates"):
        service.queue_extended_catchup(today.isoformat(), (today + timedelta(days=1)).isoformat())


def test_purge_requires_exact_confirmation_and_preserves_editorial_data(service: DashboardService) -> None:
    with pytest.raises(ValueError, match="Select"):
        service.purge_operations("PURGE OPERATIONS")
    with pytest.raises(ValueError, match="did not match"):
        service.purge_operations("PURGE", category_selected=True)

    assert service.purge_operations("PURGE OPERATIONS", category_selected=True) == 1
    assert service.database.one("SELECT COUNT(*) AS count FROM story_cluster")["count"] == 5
    assert service.database.one("SELECT COUNT(*) AS count FROM draft")["count"] == 1


def test_human_bytes_formats_storage_sizes() -> None:
    assert human_bytes(100) == "100 B"
    assert human_bytes(2048) == "2.0 KB"
    assert human_bytes(5 * 1024**3) == "5.0 GB"


def test_filters_missing_records_and_guarded_actions(service: DashboardService) -> None:
    assert len(service.list_stories(status="candidate")) == 2
    assert len(service.list_stories(lane="AGI Development")) == 2
    assert service.get_story("missing") is None
    assert service.get_draft(999) is None

    with pytest.raises(ValueError, match="Unsupported review"):
        service.review("story-demo-runtime-001", "publish")
    with pytest.raises(LookupError, match="Story not found"):
        service.review("missing", "archive")
    with pytest.raises(LookupError, match="Draft not found"):
        service.save_draft(999, "Headline", "Meta", "Body", "")
    with pytest.raises(ValueError, match="required"):
        service.save_draft(1, "", "Meta", "", "")
    with pytest.raises(LookupError, match="Source not found"):
        service.toggle_source("missing")
    with pytest.raises(ValueError, match="scheduler controls are unavailable"):
        service.schedule_action("install")


def test_inbox_type_filters_include_editorial_and_operational_work(service: DashboardService) -> None:
    assert [story["id"] for story in service.list_stories(kind="catch_up")] == ["story-demo-catchup-005"]
    assert [story["id"] for story in service.list_stories(kind="watch")] == ["story-demo-watch-003"]
    assert [story["id"] for story in service.list_stories(kind="correction")] == ["story-demo-eval-004"]
    assert service.list_stories(kind="health") == []
    assert [notice["kind"] for notice in service.list_inbox_notices("health")] == ["health"]
    assert [notice["kind"] for notice in service.list_inbox_notices("correction")] == ["correction"]
    assert service.list_inbox_notices("candidate") == []


def test_all_non_draft_review_states_are_recorded(service: DashboardService) -> None:
    assert service.review("story-demo-runtime-001", "archive", "No longer timely") == "archived"
    assert service.review("story-demo-watch-003", "withdraw") == "withdrawn"
    with pytest.raises(ValueError, match="Unsupported"):
        service.review("story-demo-watch-003", "research")
    with pytest.raises(ValueError, match="Unsupported"):
        service.review("story-demo-watch-003", "accept")


def test_installed_schedule_can_resume(service: DashboardService) -> None:
    service.database.set_state("schedule_installed", "true", "2026-07-14T00:00:00Z")
    assert service.schedule_action("resume") == "active"
    assert service.schedule_status()["status"] == "active"


def test_source_health_totals_exclude_locally_disabled_sources(service: DashboardService) -> None:
    assert service.toggle_source("hacker-news") is False

    data = service.sources()
    assert data["enabled_count"] == 11
    assert data["disabled_count"] == 1
    assert data["health_counts"] == {"healthy": 9, "degraded": 1, "paused": 1}


def test_settings_alerts_and_evidence_failure_paths(service: DashboardService) -> None:
    settings = service.settings()
    assert settings["counts"]["stories"] == 5
    assert settings["counts"]["registered sources"] == 12
    assert settings["counts"]["source items"] == 9
    assert settings["app_version"] == __version__
    assert settings["purge_preview"]["operations_count"] == 1
    assert settings["demo_mode"] is True
    assert service.mark_alerts_read() == 6
    assert service.mark_alerts_read() == 0

    with pytest.raises(LookupError, match="Story not found"):
        service.evidence_bundle("missing")
    with pytest.raises(LookupError, match="Draft not found"):
        service.draft_markdown(999)


def test_lens_markdown_includes_separate_label(service: DashboardService) -> None:
    service.database.execute("UPDATE draft SET lens = ? WHERE id = 1", ("A bounded lens section.",))
    rendered = service.draft_markdown(1)
    assert "## Open-Source Lens" in rendered
    assert "A bounded lens section." in rendered


def test_production_draft_sources_render_safely_in_markdown_html_and_view_model(service: DashboardService) -> None:
    service.database.execute(
        "UPDATE draft SET sources_json=? WHERE id=1",
        (
            Database.json(
                [
                    {"title": "Primary [announcement]", "url": "https://example.com/news?a=1&b=2", "role": "Event"},
                    {"title": "Unsafe <source>", "url": "javascript:alert(1)", "role": "Reporting"},
                    "Legacy source name",
                ]
            ),
        ),
    )

    draft = service.get_draft(1)
    markdown = service.draft_markdown(1)
    document = service.draft_html(1)

    assert draft["source_rows"][0]["url"] == "https://example.com/news?a=1&b=2"
    assert draft["source_rows"][1]["url"] == ""
    assert "[Primary \\[announcement\\]](<https://example.com/news?a=1&b=2>) — Event" in markdown
    assert "Unsafe &lt;source&gt; — Reporting" in document
    assert "javascript:" not in document
    assert 'rel="noreferrer noopener"' in document
    assert "Legacy source name" in markdown and "Legacy source name" in document
