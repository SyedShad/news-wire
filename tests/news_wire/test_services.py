from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.services import DashboardService, human_bytes
from open_source_ai_news_wire.storage import Database


@pytest.fixture
def service(tmp_path: Path) -> DashboardService:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    return DashboardService(database)


def test_overview_separates_candidates_watches_and_drafts(service: DashboardService) -> None:
    data = service.overview()
    assert data["counts"] == {"candidates": 2, "watches": 1, "draft_ready": 1, "urgent": 1}
    assert data["sources"]["degraded"] == 1
    assert data["schedule"]["background_units"] == 3
    assert data["schedule"]["draft_units"] == 2
    assert data["queue_count"] == 0
    assert data["queue_lag"] == "Clear"

    service.schedule_action("run_now")
    queued = service.overview()
    assert queued["queue_count"] == 1
    assert queued["queue_lag"] == "<1 min"


def test_human_approval_queues_draft_with_snapshot(service: DashboardService) -> None:
    status = service.review("story-demo-runtime-001", "approve_neutral", "Keep the scope tight.")
    assert status == "approved"

    work = service.database.one(
        "SELECT * FROM work_item WHERE story_id = ? AND kind = 'draft'",
        ("story-demo-runtime-001",),
    )
    assert work is not None
    snapshot = json.loads(work["payload_json"])
    assert snapshot["mode"] == "Neutral News Brief"
    assert snapshot["approval_snapshot_at"]
    assert snapshot["story"]["id"] == "story-demo-runtime-001"
    assert snapshot["story"]["first_public_at"]
    assert len(snapshot["claims"]) == 2
    assert {claim["status"] for claim in snapshot["claims"]} == {"verified"}
    assert len(snapshot["sources"]) == 2
    assert all(source["url"].startswith("https://") for source in snapshot["sources"])
    assert work["status"] == "queued"
    assert work["idempotency_key"].startswith("draft-approval:")
    assert work["available_at"]
    action = service.database.one(
        "SELECT approval_snapshot_json FROM review_action WHERE id = ?",
        (snapshot["review_action_id"],),
    )
    assert json.loads(action["approval_snapshot_json"])["story"]["id"] == "story-demo-runtime-001"


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
    assert waiting["status"] == "waiting"
    assert waiting["retryable"] is True
    assert service.database.one(
        "SELECT COUNT(*) AS count FROM review_action WHERE story_id = ? AND action = 'approve_neutral'",
        ("story-demo-runtime-001",),
    )["count"] == 1


def test_watch_cannot_be_approved_for_drafting(service: DashboardService) -> None:
    with pytest.raises(ValueError, match="Evidence and importance"):
        service.review("story-demo-watch-003", "approve_neutral")


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
    assert settings["app_version"] == "0.3.3"
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
