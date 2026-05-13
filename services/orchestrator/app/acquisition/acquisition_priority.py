"""Heuristic acquisition priority for triage ordering and success-target holds."""

from __future__ import annotations

from typing import Any

from packages.db.models import CandidateUrl

_OFFICIAL_CATEGORY_PREFIXES: tuple[str, ...] = (
    "official_",
    "official_docs",
    "official_repository",
    "official_home",
    "official_about",
)


def _metadata_str(metadata: dict[str, Any], key: str) -> str | None:
    raw = metadata.get(key)
    return raw.strip().lower() if isinstance(raw, str) and raw.strip() else None


def candidate_authoritative_heuristic(candidate: CandidateUrl) -> bool:
    """Ledger-only signal that this URL is likely primary / official / docs evidence."""
    metadata = candidate.metadata_json or {}
    if metadata.get("known_path_candidate") is True:
        return True
    cat = _metadata_str(metadata, "candidate_source_category")
    if cat and any(cat.startswith(p) for p in _OFFICIAL_CATEGORY_PREFIXES):
        return True
    if cat in {"official_docs_reference", "official_or_reference"}:
        return True
    domain = (candidate.domain or "").lower().rstrip(".")
    if domain.startswith("docs.") or domain.endswith(".readthedocs.io"):
        return True
    if domain in {"react.dev", "developer.mozilla.org", "developer.chrome.com"}:
        return True
    if "docs." in domain and domain.endswith(
        (
            ".openai.com",
            ".langchain.com",
            ".python.org",
        )
    ):
        return True
    return False


def _read_triage_decision(candidate: CandidateUrl) -> str | None:
    """Mirror acquisition triage helpers without importing ``acquisition``."""
    metadata = candidate.metadata_json or {}
    if metadata.get("llm_source_triage_active") is not True:
        return None
    source_judge = metadata.get("llm_source_judge")
    if not isinstance(source_judge, dict):
        return None
    output = source_judge.get("output_judgment")
    if not isinstance(output, dict):
        return None
    decision = output.get("triage_decision")
    if not isinstance(decision, str):
        return None
    stripped = decision.strip()
    return stripped or None


def _documentation_lane_priority_bonus(candidate: CandidateUrl) -> int:
    """
    Return negative score adjustment (lower fetch rank = earlier) for doc-heavy hosts.

    Applied only outside explicit triage must_fetch / skip branches in ``_fetch_priority_score``.
    """
    domain = (candidate.domain or "").lower().rstrip(".")
    url = (candidate.canonical_url or "").lower()
    if domain == "react.dev" or "react.dev/" in url:
        return -30
    if domain in {"developer.mozilla.org", "developer.chrome.com"}:
        return -28
    if domain.startswith("docs.") or domain.endswith("readthedocs.io"):
        return -22
    return 0


def _github_or_docs_lane_for_success_hold(candidate: CandidateUrl) -> bool:
    """Broad net for success-target deferral (includes GitHub URLs)."""
    domain = (candidate.domain or "").lower().rstrip(".")
    url = (candidate.canonical_url or "").lower()
    if domain.startswith("docs.") or domain.endswith("readthedocs.io"):
        return True
    if "github.com" in url:
        return True
    if domain == "raw.githubusercontent.com" and url.endswith("/readme.md"):
        return True
    return False


def official_repository_readme_acquire_hold(candidate: CandidateUrl) -> bool:
    """Defer success_target_met while official-repository README derivatives remain unattempted."""
    md = candidate.metadata_json or {}
    if md.get("official_repository_readme_derivative") is not True:
        return False
    if str(md.get("source_intent") or "").strip() != "official_repository_readme":
        return False
    return (candidate.domain or "").lower().rstrip(".") == "raw.githubusercontent.com"


def documentation_lane_fetch_score_delta(candidate: CandidateUrl) -> int:
    """Negative values mean earlier fetch (lower ``_fetch_priority_score`` sort key)."""
    return _documentation_lane_priority_bonus(candidate)


def candidate_high_priority_for_success_hold(candidate: CandidateUrl) -> bool:
    """High-priority candidates should defer success_target_met when still unattempted."""
    if _read_triage_decision(candidate) == "must_fetch":
        return True
    if official_repository_readme_acquire_hold(candidate):
        return True
    if candidate_authoritative_heuristic(candidate):
        return True
    if documentation_lane_fetch_score_delta(candidate) != 0:
        return True
    return _github_or_docs_lane_for_success_hold(candidate)


def any_authoritative_candidates(candidates: list[CandidateUrl]) -> bool:
    return any(candidate_authoritative_heuristic(c) for c in candidates)
