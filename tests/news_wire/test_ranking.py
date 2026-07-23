from __future__ import annotations

from datetime import UTC, datetime, timedelta

from open_source_ai_news_wire.ranking import breadth_points, percentile_points, rank_story


NOW = datetime(2026, 7, 23, 12, tzinfo=UTC)


def _story(age: timedelta, **overrides):
    row = {
        "id": "story-one",
        "first_public_at": (NOW - age).isoformat().replace("+00:00", "Z"),
        "material_updated_at": None,
        "priority": "Urgent",
        "priority_score": 99,
        "importance_score": 40,
        "source_count": 1,
        "discovery_identity_count": 0,
        "confirmed_event_count": 0,
        "reporting_origin_count": 0,
        "momentum_velocity_points": 0,
    }
    row.update(overrides)
    return row


def test_freshness_boundaries_are_dynamic() -> None:
    assert rank_story(_story(timedelta(hours=2)), now=NOW)["freshness"] == "Breaking"
    assert rank_story(_story(timedelta(hours=2, seconds=1)), now=NOW)["freshness"] == "Fresh"
    assert rank_story(_story(timedelta(hours=24)), now=NOW)["freshness"] == "Fresh"
    older = rank_story(_story(timedelta(hours=24, seconds=1)), now=NOW)
    assert older["freshness"] == "Older"
    assert older["priority"] == "Older context"
    assert older["review_score"] < 99


def test_material_update_reenters_without_rewriting_first_public_time() -> None:
    first_public_at = (NOW - timedelta(days=10)).isoformat().replace("+00:00", "Z")
    updated_at = (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    story = rank_story(
        _story(
            timedelta(days=10),
            first_public_at=first_public_at,
            material_updated_at=updated_at,
        ),
        now=NOW,
    )
    assert story["freshness"] == "Updated"
    assert story["ranking_anchor_at"] == updated_at
    assert story["first_public_at"] == first_public_at
    assert story["is_review_current"] is True


def test_momentum_raises_attention_but_not_evidence() -> None:
    story = rank_story(
        _story(
            timedelta(hours=3),
            discovery_identity_count=12,
            momentum_velocity_points=5,
        ),
        now=NOW,
    )
    assert story["momentum_score"] == 10
    assert story["attention_level"] == "High attention"
    assert story["evidence_points"] == 0
    assert story["evidence_state"] == "Verification pending"


def test_breadth_and_velocity_bands_are_bounded() -> None:
    assert [breadth_points(value) for value in (0, 1, 2, 3, 4, 5, 9, 10)] == [
        0, 0, 2, 3, 3, 4, 4, 5
    ]
    assert [percentile_points(value) for value in (0.49, 0.5, 0.75, 0.9, 0.97)] == [
        0, 1, 2, 4, 5
    ]


def test_age_labels_and_invalid_timestamps_fail_to_older_context() -> None:
    minutes = rank_story(_story(timedelta(minutes=35)), now=NOW)
    hours = rank_story(_story(timedelta(hours=8)), now=NOW)
    days = rank_story(_story(timedelta(days=4)), now=NOW)
    invalid = rank_story(
        _story(timedelta(), first_public_at="not-a-time", material_updated_at="also-invalid"),
        now=NOW,
    )
    naive = rank_story(
        _story(timedelta(), first_public_at="2026-07-23T11:00:00"),
        now=NOW,
    )

    assert minutes["age_label"] == "35m ago"
    assert hours["age_label"] == "8h ago"
    assert days["age_label"] == "4d ago"
    assert invalid["age_label"] == "age unknown"
    assert invalid["freshness"] == "Older"
    assert invalid["ranking_anchor_at"] is None
    assert naive["freshness"] == "Breaking"


def test_review_priority_and_evidence_bands_are_exact() -> None:
    one_report = rank_story(
        _story(timedelta(hours=3), importance_score=45, reporting_origin_count=1),
        now=NOW,
    )
    two_reports = rank_story(
        _story(timedelta(hours=3), importance_score=45, reporting_origin_count=2),
        now=NOW,
    )
    capped = rank_story(
        _story(
            timedelta(minutes=5),
            importance_score=999,
            confirmed_event_count=1,
            discovery_identity_count=99,
            momentum_velocity_points=99,
        ),
        now=NOW,
    )

    assert one_report["evidence_points"] == 7
    assert one_report["evidence_state"] == "One reporting origin"
    assert one_report["priority"] == "Standard"
    assert two_reports["evidence_points"] == 15
    assert two_reports["priority"] == "High"
    assert capped["review_score"] == 100
    assert capped["priority"] == "Urgent"
