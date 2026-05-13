"""Consolidated detection of README-normalized claim lineage for diagnostics only."""

from __future__ import annotations

from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from services.orchestrator.app.claims.verification import (
    VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE,
)


class _NotesCarrier(Protocol):
    notes_json: Mapping[str, Any] | None


def truthy_readme_flag(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, int) and value == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
        return True
    return False


def normalized_from_readme_from_notes(notes: Mapping[str, Any] | None) -> bool:
    """True when README lineage is recorded on the claim or nested evidence_candidate."""
    if not isinstance(notes, Mapping):
        return False
    if truthy_readme_flag(notes.get("normalized_from_readme")):
        return True
    candidate = notes.get("evidence_candidate")
    if isinstance(candidate, Mapping):
        if truthy_readme_flag(candidate.get("normalized_from_readme")):
            return True
        meta = candidate.get("metadata")
        if isinstance(meta, Mapping) and truthy_readme_flag(meta.get("normalized_from_readme")):
            return True
    return False


def normalized_from_readme_from_claim(claim: _NotesCarrier) -> bool:
    notes = claim.notes_json
    return normalized_from_readme_from_notes(
        notes if isinstance(notes, Mapping) else None
    )


def readme_composite_support_relation_count_from_notes(
    notes: Mapping[str, Any] | None,
) -> int:
    """Count persisted support relations produced by README composite verification."""
    if not isinstance(notes, Mapping):
        return 0
    verification = notes.get("verification")
    if not isinstance(verification, Mapping):
        return 0
    rows = verification.get("evidence_relations")
    if not isinstance(rows, list):
        return 0
    count = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if row.get("relation_type") not in {"support", "weak_support"}:
            continue
        if _relation_row_is_readme_composite(row):
            count += 1
    return count


def readme_composite_support_relation_count_for_claims(claims: list[Any]) -> int:
    total = 0
    for claim in claims:
        notes = getattr(claim, "notes_json", None)
        total += readme_composite_support_relation_count_from_notes(
            notes if isinstance(notes, Mapping) else None
        )
    return total


def _relation_row_is_readme_composite(row: Mapping[str, Any]) -> bool:
    if row.get("verifier_method") == VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE:
        return True
    if row.get("repository_normalized_support_method") == (
        VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE
    ):
        return True
    detail = row.get("relation_detail")
    if isinstance(detail, str) and "readme_composite" in detail.lower():
        return True
    reasons = row.get("reasons")
    if isinstance(reasons, list):
        for item in reasons:
            if not isinstance(item, str):
                continue
            lower = item.lower()
            if "readme_repository_normalized" in lower or "readme_composite" in lower:
                return True
    return False


def is_raw_github_readme_url(canonical_url: str | None) -> bool:
    if not isinstance(canonical_url, str) or not canonical_url.strip():
        return False
    parsed = urlparse(canonical_url.strip())
    host = (parsed.netloc or "").lower()
    if host != "raw.githubusercontent.com":
        return False
    path = (parsed.path or "").lower()
    return path.endswith("/readme.md") or path.endswith("/readme.markdown")


def report_evidence_is_readme_composite(ev: Any) -> bool:
    """Report bundle evidence row: composite README support."""
    method = getattr(ev, "verifier_method", None)
    if method == VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE:
        return True
    detail = getattr(ev, "relation_detail", None)
    if isinstance(detail, str) and "readme_composite" in detail.lower():
        return True
    for item in getattr(ev, "reasons", ()) or ():
        if isinstance(item, str) and (
            "readme_repository_normalized" in item.lower() or "readme_composite" in item.lower()
        ):
            return True
    return False


def readme_composite_support_relation_count_from_report_claims(claims: list[Any]) -> int:
    count = 0
    for claim in claims:
        for ev in getattr(claim, "support_evidence", []) or []:
            if report_evidence_is_readme_composite(ev):
                count += 1
    return count
