from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from flask import Flask

from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.web import create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    application = create_app(
        data_root=tmp_path / "wire-data",
        auth_required=False,
        test_config={"TESTING": True, "SECRET_KEY": "test-secret"},
    )
    seed_demo_data(application.config["DATABASE"])
    return application


@pytest.fixture
def client(app: Flask):
    return app.test_client()


def csrf(client) -> str:
    client.get("/")
    with client.session_transaction() as current_session:
        return current_session["csrf_token"]


@pytest.mark.parametrize(
    "path, marker",
    [
        ("/", "What changed in AI?"),
        ("/inbox", "Fetched articles"),
        ("/stories/story-demo-runtime-001", "Generate content"),
        ("/drafts", "Content & history"),
        ("/drafts/1", "Edit version 1"),
        ("/sources", "Sources & health"),
        ("/schedule", "Schedule & usage"),
        ("/settings", "Settings & data"),
    ],
)
def test_dashboard_pages_render(client, path: str, marker: str) -> None:
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200
    assert marker.encode() in response.data


def test_overview_exposes_queue_lag_and_scan_bounds(client) -> None:
    rendered = client.get("/")
    assert b"Queue lag" in rendered.data
    assert b"Last scan" in rendered.data
    assert b"Next pass" in rendered.data


def test_story_exposes_research_claims_and_source_provenance(app: Flask, client) -> None:
    rendered = client.get("/stories/story-demo-runtime-001", follow_redirects=True)
    assert rendered.status_code == 200
    assert b"Generate content" in rendered.data and b"Generate graphics" in rendered.data
    assert b"Research history" not in rendered.data and b"Review score" not in rendered.data
    assert b"Visit source" in rendered.data

def test_security_headers_and_loopback_host_enforcement(client) -> None:
    response = client.get("/")
    assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"

    rejected = client.get("/", headers={"Host": "news-wire.example.com"})
    assert rejected.status_code == 400


def test_cross_origin_state_change_is_rejected(app: Flask, client) -> None:
    token = csrf(client)
    rejected = client.post(
        "/schedule/run_now",
        data={"csrf_token": token},
        headers={"Origin": "http://malicious.example"},
    )
    assert rejected.status_code == 400
    assert app.config["DATABASE"].one(
        "SELECT COUNT(*) AS count FROM work_item WHERE kind = 'scout_scan'"
    )["count"] == 0

    accepted = client.post(
        "/schedule/run_now",
        data={"csrf_token": token},
        headers={"Origin": "http://localhost"},
    )
    assert accepted.status_code == 303

    opaque_with_csrf = client.post(
        "/schedule/run_now",
        data={"csrf_token": token},
        headers={"Origin": "null"},
    )
    assert opaque_with_csrf.status_code == 303
    opaque_without_csrf = client.post(
        "/schedule/run_now",
        headers={"Origin": "null"},
    )
    assert opaque_without_csrf.status_code == 400


def test_state_change_requires_csrf(client) -> None:
    response = client.post("/schedule/run_now")
    assert response.status_code == 400


def test_review_action_and_source_toggle_are_functional(app: Flask, client) -> None:
    token = csrf(client)
    response = client.post(
        "/stories/story-demo-runtime-001/content",
        data={"csrf_token": token, "guidance": "Keep the facts tight."},
    )
    assert response.status_code == 410

    source_before = app.config["DATABASE"].one(
        "SELECT enabled FROM source_registry WHERE id = 'hacker-news'"
    )["enabled"]
    response = client.post(
        "/sources/hacker-news/toggle",
        data={"csrf_token": token},
    )
    assert response.status_code == 303
    source_after = app.config["DATABASE"].one(
        "SELECT enabled FROM source_registry WHERE id = 'hacker-news'"
    )["enabled"]
    assert source_after != source_before
    source_page = client.get("/sources")
    assert b"9/11" in source_page.data
    assert b"enabled sources healthy" in source_page.data
    assert b"status-disabled" in source_page.data


def test_every_active_story_offers_content_and_old_approvals_are_gone(app: Flask, client) -> None:
    rendered = client.get("/stories/story-demo-watch-003", follow_redirects=True)
    assert rendered.status_code == 200
    assert b"Generate content" in rendered.data and b"Manually approve" not in rendered.data
    token=csrf(client)
    for suffix in ("review","content","evidence/inspect","qualify"):
        response=client.post("/stories/story-demo-watch-003/"+suffix,data={"csrf_token":token,"action":"manual_approve_neutral"})
        assert response.status_code==410
    assert app.config['DATABASE'].one("SELECT COUNT(*) AS n FROM generation_job")['n']==0


def test_content_requires_csrf_and_has_no_browser_approval_prompt(app: Flask, client) -> None:
    rejected = client.post(
        "/stories/story-demo-watch-003/content",
        data={},
    )
    assert rejected.status_code == 400
    assert app.config["DATABASE"].one(
        "SELECT COUNT(*) AS count FROM work_item WHERE kind = 'content'"
    )["count"] == 0

    javascript = client.get("/static/app.js")
    assert b"Qualification requirements have not passed" not in javascript.data
    assert b"manual_override_v1" not in javascript.data


def test_sources_expose_cursor_failure_and_recovery_state(client) -> None:
    rendered = client.get("/sources")
    assert b"Cursor" in rendered.data
    assert b"Failure streak" in rendered.data
    assert b"Last success" in rendered.data
    assert b"Recovery" in rendered.data
    assert b"Retry pending after 2 failures" in rendered.data
    assert b"No open recovery gap" in rendered.data


def test_draft_edit_creates_new_version(app: Flask, client) -> None:
    token = csrf(client)
    response = client.post(
        "/drafts/1/save",
        data={
            "csrf_token": token,
            "headline": "Edited headline",
            "metadata": "Fresh · verified",
            "body": "Edited factual brief.",
            "lens": "",
        },
    )
    assert response.status_code == 303
    assert response.headers["Location"].endswith("/drafts/2")
    rows = app.config["DATABASE"].query("SELECT version, status FROM draft ORDER BY version")
    assert rows == [{"version": 1, "status": "Superseded"}, {"version": 2, "status": "Current"}]
    comparison = client.get("/drafts/2")
    assert b"Version comparison" in comparison.data
    assert b"v2" in comparison.data and b"v1" in comparison.data
    assert b"Open previous version" in comparison.data


def test_content_action_exposes_live_shell_status(tmp_path: Path) -> None:
    application=create_app(data_root=tmp_path/'wire-data',auth_required=False,test_config={'TESTING':True})
    seed_demo_data(application.config['DATABASE'])
    browser=application.test_client()
    response=browser.post('/stories/story-demo-runtime-001/content',data={'csrf_token':csrf(browser)})
    assert response.status_code==410
    assert application.config['DATABASE'].one("SELECT COUNT(*) AS n FROM work_item WHERE kind='content'")['n']==0


def test_draft_status_and_retry_enforce_story_boundary_and_csrf(app: Flask, client) -> None:
    token = csrf(client)
    assert client.get("/stories/missing/draft-status.json").status_code == 404
    assert client.post("/stories/story-demo-runtime-001/draft/retry").status_code == 400
    assert client.post(
        "/stories/missing/draft/retry", data={"csrf_token": token}
    ).status_code == 410


def test_draft_polling_opens_only_a_continuously_visible_story_tab(client) -> None:
    javascript = client.get("/static/app.js")

    assert javascript.status_code == 200
    assert b"draftTabWasHidden" in javascript.data
    assert b"draftAutoOpen" in javascript.data
    assert b"draftAutoOpen && status.status === 'draft_ready'" in javascript.data
    assert b"!draftTabWasHidden && !document.hidden" in javascript.data


def test_fast_completed_content_returns_the_immediate_shell(tmp_path: Path) -> None:
    application=create_app(data_root=tmp_path/'wire-data',auth_required=False,test_config={'TESTING':True})
    seed_demo_data(application.config['DATABASE'])
    browser=application.test_client()
    before=application.config['DATABASE'].one('SELECT body FROM draft WHERE id=1')['body']
    browser.post('/stories/story-demo-runtime-001/content',data={'csrf_token':csrf(browser)})
    assert application.config['DATABASE'].one('SELECT body FROM draft WHERE id=1')['body']==before
    assert browser.get('/drafts/1').status_code==200


def test_drafts_history_shows_failed_request_without_draft(app: Flask, client) -> None:
    before=app.config['DATABASE'].one('SELECT body FROM draft WHERE id=1')['body']
    rendered=client.get('/drafts')
    assert rendered.status_code==200 and b'Content & history' in rendered.data
    assert app.config['DATABASE'].one('SELECT body FROM draft WHERE id=1')['body']==before


def test_draft_exposes_copy_to_clipboard_output(client) -> None:
    rendered = client.get("/drafts/1")
    assert b"Copy post" in rendered.data
    assert b"data-copy-text=" in rendered.data
    assert b"## Sources" in rendered.data
    assert b"Correction status" in rendered.data
    assert b"Draft evidence needs review" in rendered.data


def test_exports_are_sanitized_and_self_contained(client) -> None:
    evidence = client.get("/stories/story-demo-runtime-001/evidence.json")
    assert evidence.status_code == 200
    assert evidence.json["schema_version"] == 2
    assert "attachment" in evidence.headers["Content-Disposition"]

    markdown = client.get("/drafts/1/export.md")
    assert markdown.status_code == 200
    assert markdown.mimetype == "text/markdown"
    assert b"## Sources" in markdown.data

    document = client.get("/drafts/1/export.html")
    assert document.status_code == 200
    assert b"<script" not in document.data.lower()
    assert b"http://" not in document.data.lower()


def test_confirmed_markdown_links_render_safely_in_exports_and_revisions(app: Flask, client) -> None:
    source_url = "https://news.yahoo.example/report"
    body = (
        f"According to [Axios, via Yahoo](<{source_url}>), the policy proposal is under discussion.\n\n"
        "No final order has been issued."
    )
    app.config["DATABASE"].execute(
        "UPDATE draft SET body = ?, sources_json = ? WHERE id = 1",
        (
            body,
            json.dumps(
                [
                    {
                        "display_label": "Axios, via Yahoo",
                        "title": "Hosted Axios report",
                        "url": source_url,
                        "role": "Reporting",
                    }
                ]
            ),
        ),
    )

    markdown = client.get("/drafts/1/export.md")
    assert b"[Axios, via Yahoo](<https://news.yahoo.example/report>)" in markdown.data
    document = client.get("/drafts/1/export.html")
    assert b'<a href="https://news.yahoo.example/report"' in document.data
    assert b"[Axios, via Yahoo]" not in document.data

    token = csrf(client)
    rejected = client.post(
        "/drafts/1/save",
        data={
            "csrf_token": token,
            "headline": "Safe headline",
            "metadata": "Fresh",
            "body": "According to [Unknown](<https://evil.example/report>), this happened.",
            "lens": "",
        },
    )
    assert rejected.status_code == 303
    assert app.config["DATABASE"].one("SELECT COUNT(*) AS count FROM draft") == {"count": 1}


def test_purge_confirmation_is_deliberate(app: Flask, client) -> None:
    token = csrf(client)
    rejected = client.post(
        "/settings/purge-operations",
        data={"csrf_token": token, "confirmation": "PURGE OPERATIONS"},
    )
    assert rejected.status_code == 303
    assert app.config["DATABASE"].one("SELECT COUNT(*) AS count FROM diagnostic_event")["count"] == 1

    accepted = client.post(
        "/settings/purge-operations",
        data={"csrf_token": token, "category": "operations", "confirmation": "PURGE OPERATIONS"},
    )
    assert accepted.status_code == 303
    assert app.config["DATABASE"].one("SELECT COUNT(*) AS count FROM diagnostic_event")["count"] == 0


def test_one_time_auth_token_is_process_local(tmp_path: Path) -> None:
    root = tmp_path / "wire-data"
    first = create_app(
        data_root=root,
        auth_token="first-token",
        test_config={"TESTING": True, "SECRET_KEY": "one"},
    )
    first_client = first.test_client()
    assert first_client.get("/").status_code == 302
    assert first_client.get("/auth/first-token").status_code == 303
    assert first_client.get("/auth/first-token").status_code == 403

    second = create_app(
        data_root=root,
        auth_token="second-token",
        test_config={"TESTING": True, "SECRET_KEY": "two"},
    )
    assert second.test_client().get("/auth/second-token").status_code == 303


def test_rendered_dashboard_contains_no_destination_specific_language(client) -> None:
    rendered = b"\n".join(client.get(path).data.lower() for path in ("/", "/inbox", "/drafts", "/sources", "/schedule", "/settings"))
    assert b"subreddit" not in rendered
    assert b"auto-post" not in rendered
    assert b"sentient" not in rendered


def test_health_favicon_filters_and_read_alerts(app: Flask, client) -> None:
    assert client.get("/health").json == {"status": "ok", "scope": "loopback", "version": 1}
    assert client.get("/status.json").json == {
        "schema_version": 1,
        "unread_alerts": 3,
        "queued_work": 0,
        "schedule_status": "paused",
    }
    favicon = client.get("/favicon.ico")
    assert favicon.status_code == 200
    assert favicon.mimetype == "image/svg+xml"
    filtered = client.get("/inbox?status=candidate&lane=Open+Ecosystem+News")
    expected=app.config['DASHBOARD_SERVICE'].list_articles(lane='Open Ecosystem News')['articles']
    assert expected and expected[0]['canonical_url'].encode() in filtered.data
    assert b"model transparency records" not in filtered.data

    token = csrf(client)
    response = client.post("/inbox/read-alerts", data={"csrf_token": token})
    assert response.status_code == 303
    assert app.config["DASHBOARD_SERVICE"].operational_unread_count() == 0
    assert app.config["DATABASE"].one("SELECT COUNT(*) AS count FROM alert WHERE read_at IS NULL")["count"] == 3


def test_inbox_filters_health_catchup_and_correction_work(client) -> None:
    operations=client.get('/operations')
    assert b'One source is degraded' in operations.data
    feed=client.get('/inbox')
    assert b'One source is degraded' not in feed.data
    assert b'Draft evidence needs review' not in feed.data


def test_extended_catchup_form_queues_and_coalesces_selected_range(app: Flask, client) -> None:
    page = client.get("/schedule")
    assert b"Extended catch-up" in page.data
    assert b"Queue extended catch-up" in page.data
    assert b"Scheduler controls unavailable" in page.data
    assert b">Resume<" not in page.data

    token = csrf(client)
    today = datetime.now(UTC).date()
    start = today - timedelta(days=10)
    end = today - timedelta(days=5)
    selected = {"csrf_token": token, "start_date": start.isoformat(), "end_date": end.isoformat()}
    assert client.post("/schedule/extended-catchup", data=selected).status_code == 303
    assert client.post("/schedule/extended-catchup", data=selected).status_code == 303
    assert app.config["DATABASE"].one(
        "SELECT COUNT(*) AS count FROM work_item WHERE kind = 'catch_up'"
    )["count"] == 1

    invalid = client.post(
        "/schedule/extended-catchup",
        data={"csrf_token": token, "start_date": today.isoformat(), "end_date": end.isoformat()},
    )
    assert invalid.status_code == 303
    assert app.config["DATABASE"].one(
        "SELECT COUNT(*) AS count FROM work_item WHERE kind = 'catch_up'"
    )["count"] == 1


def test_missing_resources_and_invalid_actions_fail_safely(app: Flask, client) -> None:
    token = csrf(client)
    for path in (
        "/stories/missing",
        "/stories/missing/evidence.json",
        "/drafts/999",
        "/drafts/999/export.md",
        "/drafts/999/export.html",
    ):
        assert client.get(path).status_code == 404
    assert client.post("/sources/missing/toggle", data={"csrf_token": token}).status_code == 404

    invalid_review = client.post(
        "/stories/story-demo-watch-003/review",
        data={"csrf_token": token, "action": "approve_neutral"},
    )
    assert invalid_review.status_code == 410
    assert app.config["DATABASE"].one(
        "SELECT status FROM story_cluster WHERE id = 'story-demo-watch-003'"
    )["status"] == "watch"

    invalid_save = client.post(
        "/drafts/1/save",
        data={"csrf_token": token, "headline": "", "metadata": "", "body": "", "lens": ""},
    )
    assert invalid_save.status_code == 303


def test_schedule_error_success_diagnostics_and_stop_callback(app: Flask, client) -> None:
    token = csrf(client)
    assert client.post("/schedule/resume", data={"csrf_token": token}).status_code == 303
    assert client.post("/schedule/run_now", data={"csrf_token": token}).status_code == 303
    diagnostics = client.get("/settings/diagnostics.json")
    assert diagnostics.status_code == 200
    assert diagnostics.json["schema_version"] == 1

    stopped: list[bool] = []
    app.config["STOP_CALLBACK"] = lambda: stopped.append(True)
    stop = client.post("/stop", data={"csrf_token": token})
    assert stop.status_code == 200
    assert stopped == [True]


def test_lens_html_export_escapes_content(app: Flask, client) -> None:
    app.config["DATABASE"].execute(
        "UPDATE draft SET headline = ?, lens = ? WHERE id = 1",
        ("Safe <headline>", "Public auditability <script>alert(1)</script>"),
    )
    document = client.get("/drafts/1/export.html")
    assert b"Open-Source Lens" in document.data
    assert b"&lt;script&gt;" in document.data
    assert b"<script>" not in document.data


def test_external_story_links_reject_active_schemes(app: Flask, client) -> None:
    article=app.config['DASHBOARD_SERVICE'].list_articles()['articles'][0]
    app.config['DATABASE'].execute("UPDATE fetched_article SET canonical_url='javascript:alert(1)' WHERE id=?",(article['id'],))
    rendered=client.get('/articles/'+article['id'])
    assert b'href="javascript:' not in rendered.data and b'href="#"' in rendered.data


def test_neutral_editor_does_not_expose_lens_field(client) -> None:
    rendered = client.get("/drafts/1")
    assert b"Open-source lens" not in rendered.data
    assert b'name="lens" value=""' in rendered.data


def test_auth_rejects_bad_token_and_touch_callback_runs(tmp_path: Path) -> None:
    touched: list[bool] = []
    app = create_app(
        data_root=tmp_path / "wire-data",
        auth_token="right-token",
        test_config={"TESTING": True, "SECRET_KEY": "touch", "TOUCH_CALLBACK": lambda: touched.append(True)},
    )
    client = app.test_client()
    assert client.get("/locked").status_code == 401
    assert client.get("/auth/wrong-token").status_code == 403
    assert client.get("/health").status_code == 200
    assert touched == []
    assert client.get("/auth/right-token").status_code == 303
    assert touched == [True]
    assert client.get("/status.json").status_code == 200
    assert touched == [True]


def test_settings_exposes_mobile_reachable_stop_control(client) -> None:
    rendered = client.get("/settings")
    assert b"Review session" in rendered.data
    assert b"Stop this dashboard" in rendered.data
