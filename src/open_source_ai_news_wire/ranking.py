"""Dynamic freshness, attention, and human-review ranking."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .qualification import freshness_points


def _moment(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _age_hours(moment: datetime | None, now: datetime) -> float:
    if moment is None:
        return float("inf")
    return max(0.0, (now - moment).total_seconds() / 3600)


def _age_label(hours: float) -> str:
    if hours == float("inf"):
        return "age unknown"
    if hours < 1:
        return f"{max(0, int(hours * 60))}m ago"
    if hours < 24:
        return f"{int(hours)}h ago"
    days = int(hours // 24)
    return f"{days}d ago"


def breadth_points(identity_count: int) -> int:
    if identity_count < 2:
        return 0
    if identity_count == 2:
        return 2
    if identity_count <= 4:
        return 3
    if identity_count <= 9:
        return 4
    return 5


def percentile_points(percentile: float) -> int:
    if percentile >= 0.97:
        return 5
    if percentile >= 0.90:
        return 4
    if percentile >= 0.75:
        return 2
    if percentile >= 0.50:
        return 1
    return 0


def rank_story(
    story: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a story view model whose urgency changes as the clock advances."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    first_public = _moment(str(story.get("first_public_at") or ""))
    detected = _moment(str(story.get("detected_at") or ""))
    material_updated = _moment(story.get("material_updated_at"))
    first_age = _age_hours(first_public, current)
    material_age = _age_hours(material_updated, current)
    original_publication_known = bool(story.get("original_publication_known", True))

    if first_age > 24 and material_updated is not None and material_age <= 24:
        freshness = "Updated"
        anchor = material_updated
        anchor_age = material_age
    elif not original_publication_known:
        freshness = "Newly surfaced"
        anchor = detected
        anchor_age = _age_hours(detected, current)
    else:
        anchor = first_public
        anchor_age = first_age
        freshness = "Breaking" if first_age <= 2 else "Fresh" if first_age <= 24 else "Older"

    importance = max(0, min(60, int(story.get("importance_score") or 0)))
    event_count = int(story.get("confirmed_event_count") or 0)
    reporting_origins = int(story.get("reporting_origin_count") or 0)
    evidence = 15 if event_count >= 1 or reporting_origins >= 2 else 7 if reporting_origins == 1 else 0

    identity_count = int(story.get("source_identity_count") or 0)
    breadth = breadth_points(identity_count)
    velocity = max(0, min(5, int(story.get("momentum_velocity_points") or 0)))
    momentum = min(10, breadth + velocity)
    current_freshness = (
        freshness_points(anchor_age)
        if freshness in {"Breaking", "Fresh", "Updated"}
        else 0
    )
    review_score = min(100, importance + evidence + momentum + current_freshness)

    if freshness == "Older":
        priority = "Older context"
    elif freshness == "Newly surfaced":
        priority = "Newly surfaced"
    elif review_score >= 80:
        priority = "Urgent"
    elif review_score >= 65:
        priority = "High"
    else:
        priority = "Standard"

    enriched = dict(story)
    enriched.update(
        {
            "historical_priority": story.get("priority"),
            "historical_priority_score": int(story.get("priority_score") or 0),
            "freshness": freshness,
            "age_hours": first_age,
            "age_label": (
                _age_label(first_age)
                if original_publication_known
                else "original date unknown"
            ),
            "ranking_anchor_at": anchor.isoformat().replace("+00:00", "Z") if anchor else None,
            "ranking_age_hours": anchor_age,
            "ranking_age_label": _age_label(anchor_age),
            "review_score": review_score,
            "priority": priority,
            "importance_score": importance,
            "impact_level": "High impact" if importance >= 40 else "Notable" if importance >= 30 else "Standard impact",
            "evidence_points": evidence,
            "evidence_state": "Qualified" if evidence == 15 else "One reporting origin" if evidence == 7 else "Verification pending",
            "confirmed_origin_count": event_count + reporting_origins,
            "momentum_score": momentum,
            "attention_level": "High attention" if momentum >= 7 else "Building attention" if momentum >= 4 else "Low attention",
            "source_identity_count": identity_count,
            "is_review_current": freshness in {"Breaking", "Fresh", "Updated"},
            "is_material_update_current": freshness == "Updated",
            "is_newly_surfaced": freshness == "Newly surfaced",
            "original_publication_known": original_publication_known,
        }
    )
    return enriched


def ranking_sort_key(story: dict[str, Any], *, newest: bool = False) -> tuple[Any, ...]:
    anchor = _moment(story.get("ranking_anchor_at")) or datetime.min.replace(tzinfo=UTC)
    if newest:
        return (-anchor.timestamp(), str(story.get("id") or ""))
    return (-int(story.get("review_score") or 0), -anchor.timestamp(), str(story.get("id") or ""))
