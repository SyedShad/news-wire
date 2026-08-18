"""Deterministic AI relevance, editorial classification, and priority scoring."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from .adapters import Observation


AI_TERMS = {
    "ai", "agi", "llm", "llms", "model", "models", "inference", "agent", "agents",
    "multimodal", "neural", "robotics", "benchmark", "transformer", "transformers",
    "machine learning", "artificial intelligence", "language model", "deep learning",
}
OPEN_TERMS = {
    "open source", "open-source", "open weight", "open-weight", "source available",
    "source-available", "github", "repository", "dataset", "license", "transformers",
    "pytorch", "vllm", "llama.cpp", "hugging face",
}
AGI_TERMS = {
    "agi", "artificial general intelligence", "general intelligence", "superintelligence",
    "frontier model", "long horizon", "long-horizon", "autonomous agent", "reasoning",
    "capability evaluation", "general-purpose ai",
}
BROADER_TERMS = {
    "privacy", "personal data", "personal information", "data protection",
    "surveillance", "biometric", "cybersecurity", "cyber security", "security",
    "vulnerability", "vulnerabilities", "breach", "breaches", "attack", "attacks",
    "misuse", "security incident", "security incidents", "regulation", "regulations",
    "regulator", "regulators", "legislation", "law", "laws", "court", "courts",
    "compliance", "copyright", "policy enforcement", "enforcement", "export control",
}
IMPACT_TERMS = {
    "launch", "release", "regulation", "law", "ban", "acquisition", "funding",
    "security", "vulnerability", "incident", "benchmark", "breakthrough", "open source",
    "model", "compute", "chip", "copyright", "court", "safety", "agi",
}


def _normalized_text(observation: Observation) -> str:
    return f"{observation.title} {observation.summary}".lower()


def _contains(text: str, terms: set[str]) -> set[str]:
    words = set(re.findall(r"[a-z0-9.]+", text))
    found: set[str] = set()
    for term in terms:
        if " " in term or "-" in term or "." in term:
            if term in text:
                found.add(term)
        elif term in words:
            found.add(term)
    return found


@dataclass(frozen=True, slots=True)
class Qualification:
    relevant: bool
    lane: str
    openness_class: str
    freshness: str
    score: int
    impact_score: int
    novelty_score: int
    source_significance: int
    importance_score: int
    priority: str
    importance_gate: bool
    opportunity_strength: str | None
    relevance_bridge: str
    mechanism: str
    counterargument: str
    reasons: tuple[str, ...]


def importance_components(
    title: str,
    summary: str,
    source_roles: list[str] | tuple[str, ...],
    *,
    novelty: int,
    assume_relevant: bool = False,
) -> dict[str, int]:
    """Return durable importance components without any age contribution."""
    text = f"{title} {summary}".lower()
    relevant = assume_relevant or bool(_contains(text, AI_TERMS))
    impact_matches = _contains(text, IMPACT_TERMS)
    material = min(40, 12 + len(impact_matches) * 7) if relevant else 0
    significance = max(
        (10 if role == "Event" else 7 if role == "Reporting" else 3 for role in source_roles),
        default=0,
    )
    novelty_score = max(0, min(10, int(novelty)))
    return {
        "material_importance": material,
        "novelty": novelty_score,
        "source_significance": significance,
        "total": min(60, material + novelty_score + significance),
    }


def freshness_points(age_hours: float) -> int:
    if age_hours <= 2:
        return 15
    if age_hours <= 6:
        return 12
    if age_hours <= 12:
        return 9
    if age_hours <= 24:
        return 5
    return 0


def qualify(
    observation: Observation,
    source: dict[str, object],
    *,
    observed_at: str,
    novelty: int = 10,
) -> Qualification:
    text = _normalized_text(observation)
    ai_matches = _contains(text, AI_TERMS)
    family = str(source.get("family", ""))
    source_implies_ai = family in {
        "Official AI organizations", "Open development ecosystems", "Research",
        "Safety and security", "Aggregation",
    }
    relevant = bool(ai_matches or source_implies_ai)
    open_matches = _contains(text, OPEN_TERMS)
    agi_matches = _contains(text, AGI_TERMS)
    broader_matches = _contains(text, BROADER_TERMS)
    if open_matches or family == "Open development ecosystems":
        lane = "Open Ecosystem News"
    elif agi_matches:
        lane = "AGI Development"
    else:
        lane = "Broader AI News"
        # Broad coverage is intentionally narrow: a generic AI launch, funding
        # round, chip, acquisition, research result, or safety story is outside
        # this lane unless the item directly names privacy, security, or a
        # regulatory/legal enforcement issue.
        relevant = relevant and bool(broader_matches)

    if "open source" in text or "open-source" in text:
        openness = "open source"
    elif "open weight" in text or "open-weight" in text:
        openness = "open-weight"
    elif "source available" in text or "source-available" in text:
        openness = "source-available"
    elif any(term in text for term in ("closed", "proprietary")):
        openness = "closed/proprietary"
    else:
        openness = "not stated"

    published = datetime.fromisoformat(observation.published_at.replace("Z", "+00:00"))
    detected = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    age_hours = max(0.0, (detected.astimezone(UTC) - published.astimezone(UTC)).total_seconds() / 3600)
    freshness = "Breaking" if age_hours <= 2 else "Fresh" if age_hours <= 24 else "Older"

    role = str(source.get("monitoring_role", "Discovery"))
    components = importance_components(
        observation.title,
        observation.summary,
        [role],
        novelty=novelty,
        assume_relevant=relevant,
    )
    impact_matches = _contains(text, IMPACT_TERMS)
    impact_score = components["material_importance"]
    evidence_score = 15 if role == "Event" else 0
    score = min(100, components["total"] + evidence_score + freshness_points(age_hours))
    importance_gate = relevant and impact_score >= 26 and components["total"] >= 40
    priority = (
        "Urgent"
        if score >= 80
        else "High"
        if score >= 65
        else "Standard"
    )

    opportunity_strength: str | None = None
    bridge = ""
    mechanism = ""
    counterargument = ""
    opportunity_terms = _contains(
        text,
        {"regulation", "policy", "safety", "security", "transparency", "audit", "access", "governance", "monopoly", "copyright", "incident"},
    )
    if lane == "Broader AI News" and opportunity_terms:
        if opportunity_terms & {"transparency", "audit", "safety", "security", "incident"}:
            mechanism = "public auditability and transparent evidence"
        elif opportunity_terms & {"regulation", "policy", "governance", "copyright"}:
            mechanism = "open governance and inspectable compliance"
        else:
            mechanism = "distributed access and competitive implementation"
        opportunity_strength = "Moderate" if role in {"Event", "Reporting"} and len(opportunity_terms) >= 2 else "Weak"
        bridge = f"The development creates a concrete question about whether {mechanism} could change its outcome."
        counterargument = "Greater openness can also increase misuse, compliance, privacy, or security risks and is not automatically the better outcome."

    reasons = tuple(sorted(ai_matches | impact_matches | open_matches | agi_matches | broader_matches))
    return Qualification(
        relevant=relevant,
        lane=lane,
        openness_class=openness,
        freshness=freshness,
        score=score,
        impact_score=impact_score,
        novelty_score=components["novelty"],
        source_significance=components["source_significance"],
        importance_score=components["total"],
        priority=priority,
        importance_gate=importance_gate,
        opportunity_strength=opportunity_strength,
        relevance_bridge=bridge,
        mechanism=mechanism,
        counterargument=counterargument,
        reasons=reasons,
    )
