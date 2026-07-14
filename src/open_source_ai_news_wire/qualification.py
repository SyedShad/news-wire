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
    "regulation", "regulator", "policy", "law", "court", "copyright", "safety",
    "security", "incident", "misuse", "acquisition", "funding", "compute", "chip",
    "export control", "partnership", "launch", "release", "evaluation", "research",
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
    priority: str
    importance_gate: bool
    opportunity_strength: str | None
    relevance_bridge: str
    mechanism: str
    counterargument: str
    reasons: tuple[str, ...]


def qualify(observation: Observation, source: dict[str, object], *, observed_at: str) -> Qualification:
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
    freshness = "Breaking" if age_hours <= 2 else "Fresh" if age_hours <= 6 else "Catch-Up"

    impact_matches = _contains(text, IMPACT_TERMS)
    impact_score = min(40, 12 + len(impact_matches) * 7) if relevant else 0
    time_score = 20 if freshness == "Breaking" else 12 if freshness == "Fresh" else 4
    freshness_score = 15 if freshness == "Breaking" else 10 if freshness == "Fresh" else 3
    novelty_score = 10
    role = str(source.get("monitoring_role", "Discovery"))
    source_score = 10 if role == "Event" else 7 if role == "Reporting" else 3
    score = min(100, impact_score + time_score + freshness_score + novelty_score + source_score)
    importance_gate = relevant and impact_score >= 26 and score >= 60
    priority = (
        "Urgent"
        if score >= 80 and impact_score >= 33
        else "High"
        if score >= 60 and impact_score >= 26
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
        priority=priority,
        importance_gate=importance_gate,
        opportunity_strength=opportunity_strength,
        relevance_bridge=bridge,
        mechanism=mechanism,
        counterargument=counterargument,
        reasons=reasons,
    )
