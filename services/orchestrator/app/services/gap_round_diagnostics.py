"""Structured diagnostics for ``RESEARCHING_MORE`` (gap) pipeline rounds."""

from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.db.models import CandidateUrl, TaskEvent

GAP_ROUND_OUTCOME_DRAFTED = "drafted"
GAP_ROUND_OUTCOME_SKIPPED = "skipped_drafting"
GAP_ROUND_OUTCOME_FAILED = "failed"

# Bounded taxonomy for ``skip_drafting_reason`` (and ``gap_round_outcome`` helpers).
SKIP_COVERAGE_SUFFICIENT = "coverage_sufficient"
SKIP_NO_FOLLOWUP_QUERIES = "no_followup_queries"
SKIP_NO_CANDIDATE_URLS = "no_candidate_urls"
SKIP_NO_SELECTED_CANDIDATES = "no_selected_candidates"
SKIP_NO_FETCH_JOBS = "no_fetch_jobs"
SKIP_NO_SUCCESSFUL_FETCHES = "no_successful_fetches"
SKIP_NO_CONTENT_SNAPSHOTS = "no_content_snapshots"
SKIP_NO_SOURCE_DOCUMENTS = "no_source_documents"
SKIP_NO_SOURCE_CHUNKS = "no_source_chunks"
SKIP_NO_NEW_CHUNKS = "no_new_chunks"
SKIP_CHUNKS_ALREADY_USED = "chunks_already_used"
SKIP_FETCH_BUDGET_EXHAUSTED = "fetch_budget_exhausted"
SKIP_PARSE_LIMIT_EXHAUSTED = "parse_limit_exhausted"
SKIP_STRATEGIST_ERROR = "strategist_error_or_invalid_payload"
SKIP_DISABLED_BY_SETTINGS = "disabled_by_settings"
SKIP_SUPPLEMENTAL_SEARCH_FAILED_CONTINUING_EXISTING_EVIDENCE = (
    "supplemental_search_failed_continuing_existing_evidence"
)
SKIP_UNKNOWN = "unknown"

ALL_SKIP_DRAFTING_REASONS: frozenset[str] = frozenset(
    {
        SKIP_COVERAGE_SUFFICIENT,
        SKIP_NO_FOLLOWUP_QUERIES,
        SKIP_NO_CANDIDATE_URLS,
        SKIP_NO_SELECTED_CANDIDATES,
        SKIP_NO_FETCH_JOBS,
        SKIP_NO_SUCCESSFUL_FETCHES,
        SKIP_NO_CONTENT_SNAPSHOTS,
        SKIP_NO_SOURCE_DOCUMENTS,
        SKIP_NO_SOURCE_CHUNKS,
        SKIP_NO_NEW_CHUNKS,
        SKIP_CHUNKS_ALREADY_USED,
        SKIP_FETCH_BUDGET_EXHAUSTED,
        SKIP_PARSE_LIMIT_EXHAUSTED,
        SKIP_STRATEGIST_ERROR,
        SKIP_DISABLED_BY_SETTINGS,
        SKIP_SUPPLEMENTAL_SEARCH_FAILED_CONTINUING_EXISTING_EVIDENCE,
        SKIP_UNKNOWN,
    }
)


def canonical_urls_for_candidate_ids(
    session: Session,
    task_id: UUID,
    candidate_ids: list[UUID],
    *,
    limit: int = 40,
) -> list[str]:
    if not candidate_ids:
        return []
    stmt = (
        select(CandidateUrl.canonical_url)
        .where(CandidateUrl.task_id == task_id, CandidateUrl.id.in_(candidate_ids[:limit]))
        .order_by(CandidateUrl.rank.asc())
    )
    rows = session.scalars(stmt).all()
    return [str(url) for url in rows if url]


def fetch_budget_hint_from_skipped_sources(skipped_sources: list[dict[str, Any]]) -> bool:
    for item in skipped_sources:
        if not isinstance(item, dict):
            continue
        if item.get("stop_reason") == "fetch_budget_exhausted":
            return True
        if item.get("skip_reason") == "fetch_budget_exhausted":
            return True
    return False


def build_gap_round_diagnostics(
    *,
    gap_round_outcome: str,
    skip_drafting_reason: str | None,
    gap_round_index: int | None,
    strategy_decision: Any,
    gap_triggered: bool | None,
    search_attempted: bool,
    search_skipped_reason: str | None,
    search_queries_count: int,
    search_result_count: int,
    candidate_urls_added: int,
    selected_candidate_ids: list[UUID],
    selected_candidate_urls: list[str],
    fetch_jobs_created: int | None,
    fetch_attempts_created: int | None,
    content_snapshots_created: int | None,
    source_documents_created: int | None,
    source_chunks_created: int | None,
    parse_attempted: bool,
    index_attempted: bool,
    drafting_attempted: bool,
    drafting_created_claims: int | None,
    drafting_reused_claims: int | None,
    verification_attempted: bool,
    verification_supported_claims: int | None,
    coverage_before: list[dict[str, Any]] | None,
    coverage_after: list[dict[str, Any]] | None,
    loop_stop_reason: str | None = None,
    supplemental_search_failed: bool = False,
    continuing_with_existing_evidence: bool = False,
) -> dict[str, Any]:
    if skip_drafting_reason is not None and skip_drafting_reason not in ALL_SKIP_DRAFTING_REASONS:
        skip_drafting_reason = SKIP_UNKNOWN
    return {
        "gap_round_outcome": gap_round_outcome,
        "skip_drafting_reason": skip_drafting_reason,
        "gap_round_index": gap_round_index,
        "strategy_decision": strategy_decision,
        "gap_triggered": gap_triggered,
        "search_attempted": search_attempted,
        "search_skipped_reason": search_skipped_reason,
        "search_queries_count": search_queries_count,
        "search_result_count": search_result_count,
        "candidate_urls_added": candidate_urls_added,
        "selected_candidate_ids_count": len(selected_candidate_ids),
        "selected_candidate_urls": selected_candidate_urls,
        "fetch_jobs_created": fetch_jobs_created,
        "fetch_attempts_created": fetch_attempts_created,
        "content_snapshots_created": content_snapshots_created,
        "source_documents_created": source_documents_created,
        "source_chunks_created": source_chunks_created,
        "parse_attempted": parse_attempted,
        "index_attempted": index_attempted,
        "drafting_attempted": drafting_attempted,
        "drafting_created_claims": drafting_created_claims,
        "drafting_reused_claims": drafting_reused_claims,
        "verification_attempted": verification_attempted,
        "verification_supported_claims": verification_supported_claims,
        "coverage_before": coverage_before,
        "coverage_after": coverage_after,
        "loop_stop_reason": loop_stop_reason,
        "supplemental_search_failed": supplemental_search_failed,
        "continuing_with_existing_evidence": continuing_with_existing_evidence,
    }


def attach_gap_round_to_stage_result(
    result: dict[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """Merge diagnostics into a ``RESEARCHING_MORE`` stage result (mutates a shallow copy)."""
    merged = {**result, "gap_round_diagnostics": diagnostics}
    merged["gap_round_outcome"] = diagnostics.get("gap_round_outcome")
    merged["skip_drafting_reason"] = diagnostics.get("skip_drafting_reason")
    return merged


def summarize_gap_round_diagnostics_for_task(session: Session, task_id: UUID) -> dict[str, Any]:
    """
    Aggregate ``gap_round_diagnostics`` from ``pipeline*.stage_completed`` / ``RESEARCHING_MORE``.

    Read-only; relies on payloads written by ``DebugRealPipelineRunner``.
    """
    events = session.scalars(
        select(TaskEvent)
        .where(
            TaskEvent.task_id == task_id,
            TaskEvent.event_type.like("%.stage_completed"),
        )
        .order_by(TaskEvent.sequence_no.asc())
    ).all()
    total = 0
    with_drafting = 0
    skipped = 0
    nested_created_total = 0
    nested_supported_total = 0
    skip_reasons: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()

    for event in events:
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("stage") != "RESEARCHING_MORE":
            continue
        result = payload.get("result")
        if not isinstance(result, dict):
            continue
        total += 1
        diag = result.get("gap_round_diagnostics")
        if isinstance(diag, dict):
            outcome = str(diag.get("gap_round_outcome") or "")
            if outcome:
                outcomes[outcome] += 1
            reason = diag.get("skip_drafting_reason")
            if isinstance(reason, str) and reason.strip():
                skip_reasons[reason.strip()] += 1
            if outcome == GAP_ROUND_OUTCOME_DRAFTED:
                with_drafting += 1
            elif outcome == GAP_ROUND_OUTCOME_SKIPPED:
                skipped += 1
            nested_created_total += int(diag.get("drafting_created_claims") or 0)
            nested_supported_total += int(diag.get("verification_supported_claims") or 0)
        elif result.get("drafting") is not None:
            # Legacy payloads without ``gap_round_diagnostics``: infer drafting occurred.
            with_drafting += 1
            dr = result.get("drafting")
            if isinstance(dr, dict):
                nested_created_total += int(dr.get("created_claims") or 0)

    return {
        "gap_rounds_total": total,
        "gap_rounds_with_drafting": with_drafting,
        "gap_rounds_skipped_drafting": skipped,
        "skip_drafting_reason_distribution": dict(sorted(skip_reasons.items())),
        "gap_round_outcome_distribution": dict(sorted(outcomes.items())),
        "nested_drafting_created_claims_total": nested_created_total,
        "nested_verification_supported_claims_total": nested_supported_total,
    }


def verification_supported_count_from_summary(verification_summary: dict[str, Any]) -> int:
    counts = verification_summary.get("claim_status_counts")
    if not isinstance(counts, dict):
        return 0
    raw = counts.get("supported")
    return int(raw) if isinstance(raw, int) else 0
