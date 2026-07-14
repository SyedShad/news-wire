from __future__ import annotations

import httpx
import pytest

from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.evidence import (
    EvidenceEnricher,
    extract_page,
    normalize_public_https_url,
    publisher_key,
    qualification_state,
    recalculate_claim_statuses,
    recalculate_story_qualification,
)
from open_source_ai_news_wire.network import SafeHttpClient, UnsafeRequest
from open_source_ai_news_wire.services import DashboardService
from open_source_ai_news_wire.storage import Database
from open_source_ai_news_wire.web import create_app


PUBLIC_IP = lambda _host: ["93.184.216.34"]


def _service(tmp_path):
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    return database, DashboardService(database)


def _client_factory(body: bytes):
    def factory(host: str) -> SafeHttpClient:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=body,
                headers={"content-type": "text/html; charset=utf-8"},
                request=request,
            )
        )
        return SafeHttpClient(
            allowed_hosts={host}, resolver=PUBLIC_IP, transport=transport
        )

    return factory


def _inspect(database: Database, service: DashboardService, story_id: str, url: str, method: str = "manual") -> int:
    evidence_id, state = service.queue_evidence_inspection(story_id, url, method)
    assert state == "queued"
    enricher = EvidenceEnricher(
        database,
        client_factory=_client_factory(
            b"<html lang='en'><head><title>Official AI release</title>"
            b"<meta name='description' content='The organization announced an AI release.'>"
            b"</head><body>Public release details</body></html>"
        ),
    )
    assert enricher.process_next() == evidence_id
    assert database.one("SELECT status FROM evidence_source WHERE id = ?", (evidence_id,)) == {
        "status": "fetched"
    }
    return evidence_id


def test_hacker_news_link_can_be_confirmed_then_importance_overridden_and_approved(tmp_path) -> None:
    database, service = _service(tmp_path)
    story_id = "story-demo-watch-003"
    database.execute(
        """
        UPDATE source_item SET source_name = 'Hacker News AI Discovery',
            title = 'Show HN: public AI project release',
            url = 'https://project.example.org/release',
            canonical_url = 'https://project.example.org/release',
            passage = 'Article URL: https://project.example.org/release Comments URL: https://news.ycombinator.com/item?id=123'
        WHERE story_id = ? AND source_role = 'Discovery' AND id = (
            SELECT MIN(id) FROM source_item WHERE story_id = ? AND source_role = 'Discovery'
        )
        """,
        (story_id, story_id),
    )
    database.execute(
        "UPDATE candidate SET importance_gate = 0 WHERE story_id = ?", (story_id,)
    )
    claim_id = int(database.one("SELECT id FROM claim WHERE story_id = ?", (story_id,))["id"])
    evidence_id = _inspect(
        database,
        service,
        story_id,
        "https://project.example.org/release",
        "linked",
    )

    with pytest.raises(ValueError, match="first-party"):
        service.confirm_evidence(
            story_id, evidence_id, "Event", {claim_id: "attributes"}
        )

    state = service.confirm_evidence(
        story_id,
        evidence_id,
        "Event",
        {claim_id: "attributes"},
        first_party=True,
        reason="The inspected page is the organizer's official event page.",
    )
    assert state["evidence_gate"] is True
    assert state["automated_importance"] is False
    assert database.one("SELECT status FROM claim WHERE id = ?", (claim_id,)) == {
        "status": "attributed"
    }
    with pytest.raises(ValueError, match="recorded reason"):
        service.qualify_story(story_id)

    assert service.qualify_story(story_id, "This early release is editorially relevant.") == "candidate"
    candidate = database.one(
        "SELECT evidence_gate, importance_gate, importance_override, importance_override_reason FROM candidate WHERE story_id = ?",
        (story_id,),
    )
    assert candidate == {
        "evidence_gate": 1,
        "importance_gate": 0,
        "importance_override": 1,
        "importance_override_reason": "This early release is editorially relevant.",
    }
    assert database.one(
        "SELECT source_role FROM source_item WHERE story_id = ? AND canonical_url = ?",
        (story_id, "https://project.example.org/release"),
    ) == {"source_role": "Discovery"}
    assert service.review(story_id, "approve_neutral") == "approved"


def test_two_reporting_sources_must_have_independent_publishers(tmp_path) -> None:
    database, service = _service(tmp_path)
    story_id = "story-demo-watch-003"
    claim_id = int(database.one("SELECT id FROM claim WHERE story_id = ?", (story_id,))["id"])

    for url in (
        "https://news.example.com/report",
        "https://analysis.example.com/confirmation",
    ):
        evidence_id = _inspect(database, service, story_id, url)
        service.confirm_evidence(
            story_id, evidence_id, "Reporting", {claim_id: "supports"}
        )
    state = qualification_state(database, story_id)
    assert state["reporting_count"] == 1
    assert state["evidence_gate"] is False

    evidence_id = _inspect(database, service, story_id, "https://independent.example.org/report")
    state = service.confirm_evidence(
        story_id, evidence_id, "Reporting", {claim_id: "supports"}
    )
    assert state["reporting_count"] == 2
    assert state["evidence_gate"] is True
    assert database.one("SELECT status FROM claim WHERE id = ?", (claim_id,)) == {
        "status": "verified"
    }


def test_manual_evidence_service_rejects_invalid_transitions_and_reuses_urls(tmp_path) -> None:
    callbacks: list[str] = []
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    service = DashboardService(database, run_callback=lambda: callbacks.append("run"))
    story_id = "story-demo-watch-003"
    claim_id = int(database.one("SELECT id FROM claim WHERE story_id = ?", (story_id,))["id"])

    with pytest.raises(LookupError, match="Story not found"):
        service.queue_evidence_inspection("missing-story", "https://example.com", "manual")
    with pytest.raises(ValueError, match="acquisition method"):
        service.queue_evidence_inspection(story_id, "https://example.com", "automatic")
    with pytest.raises(ValueError, match="stored Discovery"):
        service.queue_evidence_inspection(story_id, "https://example.com", "linked")

    evidence_id, status = service.queue_evidence_inspection(
        story_id, "https://example.com/report", "manual"
    )
    assert status == "queued"
    assert callbacks == ["run"]
    repeated_id, repeated_status = service.queue_evidence_inspection(
        story_id, "https://example.com/report", "manual"
    )
    assert (repeated_id, repeated_status) == (evidence_id, "queued")

    with pytest.raises(ValueError, match="Choose"):
        service.confirm_evidence(story_id, evidence_id, "Primary", {})
    with pytest.raises(LookupError, match="Evidence source not found"):
        service.confirm_evidence(story_id, 999999, "Discovery", {})
    with pytest.raises(ValueError, match="fetched"):
        service.confirm_evidence(story_id, evidence_id, "Discovery", {})
    with pytest.raises(LookupError, match="Evidence source not found"):
        service.exclude_evidence(story_id, 999999)

    EvidenceEnricher(
        database,
        client_factory=_client_factory(b"<html><title>Report</title><p>Details</p></html>"),
    ).process_next()
    with pytest.raises(ValueError, match="Map at least one claim"):
        service.confirm_evidence(story_id, evidence_id, "Reporting", {})
    with pytest.raises(ValueError, match="Unsupported claim relationship"):
        service.confirm_evidence(story_id, evidence_id, "Reporting", {claim_id: "copies"})
    other_claim = int(
        database.one(
            "SELECT id FROM claim WHERE story_id != ? ORDER BY id LIMIT 1", (story_id,)
        )["id"]
    )
    with pytest.raises(ValueError, match="crossed the story boundary"):
        service.confirm_evidence(story_id, evidence_id, "Reporting", {other_claim: "supports"})

    service.confirm_evidence(story_id, evidence_id, "Discovery", {})
    assert service.queue_evidence_inspection(
        story_id, "https://example.com/report", "manual"
    ) == (evidence_id, "confirmed")


def test_excluding_qualifying_evidence_invalidates_pending_draft(tmp_path) -> None:
    database, service = _service(tmp_path)
    story_id = "story-demo-watch-003"
    claim_id = int(database.one("SELECT id FROM claim WHERE story_id = ?", (story_id,))["id"])
    evidence_id = _inspect(database, service, story_id, "https://official.example.org/release")
    service.confirm_evidence(
        story_id, evidence_id, "Event", {claim_id: "supports"}, first_party=True
    )
    service.qualify_story(story_id)
    service.review(story_id, "approve_neutral")

    state = service.exclude_evidence(story_id, evidence_id, "The page changed ownership.")

    assert state["evidence_gate"] is False
    assert database.one("SELECT status FROM story_cluster WHERE id = ?", (story_id,)) == {
        "status": "signal"
    }
    assert database.one(
        "SELECT status, last_error_class FROM work_item WHERE story_id = ? AND kind = 'draft'",
        (story_id,),
    ) == {"status": "needs_reapproval", "last_error_class": "evidence_invalidated"}


def test_evidence_url_and_dns_boundaries() -> None:
    assert normalize_public_https_url("https://example.com/news?utm_source=x") == "https://example.com/news"
    for url in (
        "http://example.com/news",
        "https://user:pass@example.com/news",
        "https://example.com:8443/news",
        "file:///private/etc/passwd",
    ):
        with pytest.raises(ValueError):
            normalize_public_https_url(url)

    answers = iter((["93.184.216.34"], ["93.184.216.35"]))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="ok", request=request))
    with SafeHttpClient(
        allowed_hosts={"example.com"}, resolver=lambda _host: next(answers), transport=transport
    ) as client:
        with pytest.raises(UnsafeRequest, match="DNS resolution changed"):
            client.fetch("https://example.com/news")


def test_url_normalization_publisher_grouping_and_page_extraction() -> None:
    for value in ("", "https://example.com/" + ("x" * 2049)):
        with pytest.raises(ValueError, match="bounded"):
            normalize_public_https_url(value)
    with pytest.raises(ValueError, match="invalid publisher"):
        publisher_key("https://\ud800.example/news")
    assert publisher_key("https://briefs.news.example.com/item") == "example.com"
    assert publisher_key("https://93.184.216.34/item") == "93.184.216.34"

    plain = extract_page(b"  public   release notes  ", "text/plain")
    assert plain.passage == "public release notes"
    assert plain.language == "und"
    with pytest.raises(ValueError, match="UTF-8"):
        extract_page(b"\xff", "text/html")

    page = extract_page(
        b"<html lang='en-US'><head><title> A &amp; B </title>"
        b"<meta property='article:published_time' content='not-a-date'>"
        b"</head><body>  <script>ignore me</script> Visible details </body></html>",
        "text/html; charset=utf-8",
    )
    assert page.title == "A & B"
    assert page.passage == "A & B Visible details"
    assert page.published_at is None
    assert page.language == "en-US"


def test_enricher_fails_stale_redirected_and_empty_evidence_safely(tmp_path) -> None:
    database, service = _service(tmp_path)
    story_id = "story-demo-watch-003"
    now = "2026-07-14T12:00:00Z"
    database.execute(
        """
        INSERT INTO work_item(kind, story_id, status, priority, payload_json, created_at, updated_at)
        VALUES('evidence_enrichment', ?, 'pending', 1, '{"evidence_source_id": 999999}', ?, ?)
        """,
        (story_id, now, now),
    )
    assert EvidenceEnricher(database).process_next() == 999999
    assert database.one(
        "SELECT status, last_error_class FROM work_item ORDER BY id DESC LIMIT 1"
    ) == {"status": "failed", "last_error_class": "ValueError"}

    class _Fetched:
        def __init__(self, url: str, body: bytes) -> None:
            self.url = url
            self.body = body
            self.headers = {"content-type": "text/html"}

    class _Client:
        def __init__(self, result: _Fetched) -> None:
            self.result = result

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def fetch(self, _url: str):
            return self.result

    evidence_id, _ = service.queue_evidence_inspection(
        story_id, "https://example.com/report", "manual"
    )
    enricher = EvidenceEnricher(
        database,
        client_factory=lambda _host: _Client(
            _Fetched("https://different.org/report", b"<p>Moved</p>")
        ),
    )
    assert enricher.process_next() == evidence_id
    assert database.one(
        "SELECT status, error_class FROM evidence_source WHERE id = ?", (evidence_id,)
    ) == {"status": "failed", "error_class": "UnsafeRequest"}

    empty_id, _ = service.queue_evidence_inspection(
        story_id, "https://empty.example.net/report", "manual"
    )
    empty_enricher = EvidenceEnricher(
        database,
        client_factory=lambda _host: _Client(
            _Fetched("https://empty.example.net/report", b"<html><script>x</script></html>")
        ),
    )
    assert empty_enricher.process_next() == empty_id
    assert database.one(
        "SELECT status, error_class FROM evidence_source WHERE id = ?", (empty_id,)
    ) == {"status": "failed", "error_class": "ValueError"}
    assert empty_enricher.process_next() is None


def test_qualification_creation_claim_states_and_repeat_invalidation(tmp_path) -> None:
    database, service = _service(tmp_path)
    story_id = "story-demo-watch-003"
    claim_id = int(database.one("SELECT id FROM claim WHERE story_id = ?", (story_id,))["id"])
    with pytest.raises(LookupError, match="Story not found"):
        recalculate_story_qualification(database, "missing-story")

    database.execute("DELETE FROM candidate WHERE story_id = ?", (story_id,))
    state = recalculate_story_qualification(database, story_id)
    assert state["evidence_gate"] is False
    assert database.one("SELECT score FROM candidate WHERE story_id = ?", (story_id,)) is not None

    evidence_id = _inspect(database, service, story_id, "https://official.example.org/update")
    service.confirm_evidence(
        story_id, evidence_id, "Event", {claim_id: "supports"}, first_party=True
    )
    service.qualify_story(story_id, "The evidence-backed update is important enough to review.")
    assert database.one("SELECT status FROM claim WHERE id = ?", (claim_id,)) == {
        "status": "attributed"
    }

    contextual_id = _inspect(database, service, story_id, "https://context.example.net/item")
    service.confirm_evidence(
        story_id, contextual_id, "Discovery", {claim_id: "context"}
    )
    contradictory_id = _inspect(database, service, story_id, "https://factcheck.example.net/item")
    service.confirm_evidence(
        story_id, contradictory_id, "Reporting", {claim_id: "contradicts"}
    )
    assert database.one("SELECT status FROM claim WHERE id = ?", (claim_id,)) == {
        "status": "disputed"
    }

    service.exclude_evidence(story_id, contradictory_id, "Contradiction withdrawn")
    service.exclude_evidence(story_id, evidence_id, "Official page withdrawn")
    assert database.one(
        "SELECT COUNT(*) AS count FROM alert WHERE story_id = ? AND kind = 'correction'",
        (story_id,),
    ) == {"count": 1}
    # Recalculation while the unread correction exists must not duplicate it.
    recalculate_story_qualification(database, story_id)
    assert database.one(
        "SELECT COUNT(*) AS count FROM alert WHERE story_id = ? AND kind = 'correction'",
        (story_id,),
    ) == {"count": 1}

    database.execute(
        "DELETE FROM evidence_source_claim WHERE evidence_source_id = ?", (contextual_id,)
    )
    recalculate_claim_statuses(database, story_id)
    assert database.one("SELECT status FROM claim WHERE id = ?", (claim_id,)) == {
        "status": "unverified"
    }


def test_web_routes_fail_closed_and_show_gate_reasons(tmp_path) -> None:
    app = create_app(
        data_root=tmp_path / "wire-data",
        auth_required=False,
        test_config={"TESTING": True, "SECRET_KEY": "test-secret"},
    )
    seed_demo_data(app.config["DATABASE"])
    client = app.test_client()
    client.get("/")
    with client.session_transaction() as session:
        token = session["csrf_token"]
    story_id = "story-demo-watch-003"

    rendered = client.get(f"/stories/{story_id}")
    assert b"Evidence and qualification" in rendered.data
    assert b"Qualification remains locked until the evidence gate passes" in rendered.data
    assert b"Investigate evidence" in rendered.data

    response = client.post(
        f"/stories/{story_id}/qualify",
        data={"csrf_token": token, "reason": "Bypass"},
    )
    assert response.status_code == 303
    assert app.config["DATABASE"].one(
        "SELECT status FROM story_cluster WHERE id = ?", (story_id,)
    ) == {"status": "watch"}

    response = client.post(
        f"/stories/{story_id}/evidence/inspect",
        data={"csrf_token": token, "url": "http://127.0.0.1/private", "acquisition_method": "manual"},
    )
    assert response.status_code == 303
    assert app.config["DATABASE"].one(
        "SELECT COUNT(*) AS count FROM evidence_source WHERE story_id = ?", (story_id,)
    ) == {"count": 0}


def test_web_evidence_routes_complete_qualification_lifecycle(tmp_path) -> None:
    app = create_app(
        data_root=tmp_path / "wire-data",
        auth_required=False,
        test_config={"TESTING": True, "SECRET_KEY": "test-secret"},
    )
    database = app.config["DATABASE"]
    seed_demo_data(database)
    client = app.test_client()
    client.get("/")
    with client.session_transaction() as session:
        token = session["csrf_token"]
    story_id = "story-demo-watch-003"
    claim_id = int(database.one("SELECT id FROM claim WHERE story_id = ?", (story_id,))["id"])

    response = client.post(
        f"/stories/{story_id}/evidence/inspect",
        data={
            "csrf_token": token,
            "url": "https://official.example.org/release",
            "acquisition_method": "manual",
        },
    )
    assert response.status_code == 303
    evidence_id = int(database.one("SELECT id FROM evidence_source")["id"])
    EvidenceEnricher(
        database,
        client_factory=_client_factory(b"<html><title>Official release</title><p>Details</p></html>"),
    ).process_next()

    response = client.post(
        f"/stories/{story_id}/evidence/{evidence_id}/confirm",
        data={
            "csrf_token": token,
            "role": "Event",
            "first_party": "yes",
            "claim_not-a-number": "supports",
            f"claim_{claim_id}": "attributes",
            "reason": "Official publisher page.",
        },
    )
    assert response.status_code == 303
    assert qualification_state(database, story_id)["evidence_gate"] is True

    response = client.post(
        f"/stories/{story_id}/qualify",
        data={"csrf_token": token, "reason": "Evidence-backed manual decision."},
    )
    assert response.status_code == 303
    assert database.one("SELECT status FROM story_cluster WHERE id = ?", (story_id,)) == {
        "status": "candidate"
    }

    response = client.post(
        f"/stories/{story_id}/evidence/{evidence_id}/exclude",
        data={"csrf_token": token, "reason": "Material page change."},
    )
    assert response.status_code == 303
    assert database.one("SELECT status FROM evidence_source WHERE id = ?", (evidence_id,)) == {
        "status": "excluded"
    }
