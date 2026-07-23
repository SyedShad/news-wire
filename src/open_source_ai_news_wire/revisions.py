"""Immutable signatures and typed revision records.

These helpers deliberately exclude engagement and parser metadata.  They are
the contract shared by collection, approval, and drafting when deciding
whether editorial content changed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


OBSERVATION_HASH_VERSION = 2


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_atomic_claim(value: str) -> str:
    """Normalize claim-bearing prose without erasing substantive wording."""
    return re.sub(r"\s+", " ", str(value)).strip()


def observation_hash(title: str, material_summary: str) -> str:
    """Version-2 observation hash, excluding mutable attention metadata."""
    return _digest(
        {
            "version": OBSERVATION_HASH_VERSION,
            "title": normalize_atomic_claim(title),
            "material_summary": normalize_atomic_claim(material_summary),
        }
    )


def atomic_claim_signature(claims: Iterable[str]) -> str:
    normalized = sorted(
        {normalize_atomic_claim(claim) for claim in claims if normalize_atomic_claim(claim)}
    )
    return _digest({"claims": normalized})


def claim_signature_record(claim: Mapping[str, Any]) -> dict[str, object]:
    claim_id = int(claim["id"])
    signature = _digest(
        {
            "id": claim_id,
            "text": normalize_atomic_claim(str(claim.get("text") or "")),
            "status": str(claim.get("status") or ""),
            "volatility": str(claim.get("volatility") or ""),
        }
    )
    return {"id": claim_id, "signature": signature}


def source_signature_record(source: Mapping[str, Any]) -> dict[str, object]:
    """Sign source identity and claim-bearing content, never engagement data."""
    source_id = str(source.get("id") or source.get("evidence_key") or "")
    if not source_id:
        raise ValueError("Source signatures require an immutable source identifier")
    passage = str(source.get("passage") or source.get("summary") or "")
    signature = _digest(
        {
            "id": source_id,
            "role": str(source.get("source_role") or source.get("role") or ""),
            "canonical_url": str(source.get("canonical_url") or source.get("url") or ""),
            "title": normalize_atomic_claim(str(source.get("title") or "")),
            "passage": normalize_atomic_claim(passage),
            "verification_status": str(source.get("verification_status") or ""),
            "citation_allowed": bool(source.get("citation_allowed")),
            "reporting_origin_key": str(source.get("reporting_origin_key") or ""),
            "origin_status": str(source.get("origin_status") or ""),
            "provenance_type": str(source.get("provenance_type") or ""),
            "claim_relationships": sorted(
                (
                    int(item.get("claim_id") or 0),
                    str(item.get("relationship") or ""),
                )
                for item in source.get("claim_relationships", [])
                if isinstance(item, Mapping)
            ),
        }
    )
    return {"id": source_id, "signature": signature}


def approval_signature_sets(
    claims: Iterable[Mapping[str, Any]], sources: Iterable[Mapping[str, Any]]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    claim_records = sorted(
        (claim_signature_record(claim) for claim in claims), key=lambda item: int(item["id"])
    )
    source_records = sorted(
        (source_signature_record(source) for source in sources),
        key=lambda item: str(item["id"]),
    )
    return claim_records, source_records


@dataclass(frozen=True, slots=True)
class StoryRevision:
    story_id: str
    story_revision: int
    material_revision: int
    claim_signature: str


@dataclass(frozen=True, slots=True)
class ApprovalSnapshot:
    story_id: str
    story_revision: int
    claim_signatures: tuple[tuple[int, str], ...]
    source_signatures: tuple[tuple[str, str], ...]
