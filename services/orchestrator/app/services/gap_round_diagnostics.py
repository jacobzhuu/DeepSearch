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


def research_round_entry_from_gap_stage_result(
    stage: dict[str, Any],
    *,
    sequence_index: int,
) -> dict[str, Any]:
    """
    One RESEARCHING_MORE stage_completed ``result`` object -> compact per-round observability.

    ``sequence_index`` is 0-based order within the task when multiple gap rounds exist.
    """
    search = stage.get("search")
    search = search if isinstance(search, dict) else {}
    acquisition = stage.get("acquisition")
    acquisition = acquisition if isinstance(acquisition, dict) else {}
    parsing = stage.get("parsing")
    parsing = parsing if isinstance(parsing, dict) else {}
    drafting = stage.get("drafting")
    drafting = drafting if isinstance(drafting, dict) else {}
    verification = stage.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    gap_diag = stage.get("gap_round_diagnostics")
    gap_diag = gap_diag if isinstance(gap_diag, dict) else {}
    gap_analysis = stage.get("gap_analysis")
    gap_analysis = gap_analysis if isinstance(gap_analysis, dict) else {}

    queries: list[str] = []
    for row in search.get("search_queries", []):
        if not isinstance(row, dict):
            continue
        qt = row.get("query_text")
        if isinstance(qt, str) and qt.strip():
            queries.append(qt.strip())

    candidate_urls: list[str] = []
    for row in search.get("selected_sources", []):
        if not isinstance(row, dict):
            continue
        url = row.get("canonical_url")
        if isinstance(url, str) and url.strip():
            candidate_urls.append(url.strip())
    candidate_urls = list(dict.fromkeys(candidate_urls))[:80]

    new_urls_raw = gap_diag.get("selected_candidate_urls")
    new_candidate_urls: list[str] = []
    if isinstance(new_urls_raw, list):
        new_candidate_urls = [
            str(u).strip() for u in new_urls_raw if isinstance(u, str) and str(u).strip()
        ][:80]

    fetch_attempts = gap_diag.get("fetch_attempts_created")
    if not isinstance(fetch_attempts, int):
        fetch_attempts = int(acquisition.get("fetch_succeeded") or 0) + int(
            acquisition.get("fetch_failed") or 0
        )
    fetch_succeeded = gap_diag.get("content_snapshots_created")
    if not isinstance(fetch_succeeded, int):
        fetch_succeeded = int(acquisition.get("fetch_succeeded") or acquisition.get("succeeded") or 0)

    source_documents = gap_diag.get("source_documents_created")
    if not isinstance(source_documents, int):
        source_documents = int(parsing.get("created") or 0)

    source_chunks = gap_diag.get("source_chunks_created")
    if not isinstance(source_chunks, int):
        source_chunks = 0

    claims_created = gap_diag.get("drafting_created_claims")
    claims_reused = gap_diag.get("drafting_reused_claims")
    if not isinstance(claims_created, int):
        claims_created = int(drafting.get("created_claims") or 0)
    if not isinstance(claims_reused, int):
        claims_reused = int(drafting.get("reused_claims") or 0)
    claims_total = claims_created + claims_reused

    supported = gap_diag.get("verification_supported_claims")
    if not isinstance(supported, int):
        vs = verification.get("verification_summary")
        supported = (
            verification_supported_count_from_summary(vs)
            if isinstance(vs, dict)
            else 0
        )

    round_no = gap_diag.get("gap_round_index")
    if not isinstance(round_no, int):
        raw_rn = gap_analysis.get("round_no")
        if isinstance(raw_rn, int):
            round_no = raw_rn
        else:
            try:
                round_no = int(raw_rn) if raw_rn is not None else sequence_index + 1
            except (TypeError, ValueError):
                round_no = sequence_index + 1

    status, reason = _research_round_status_reason(stage, gap_diag=gap_diag)

    return {
        "round": round_no,
        "sequence_index": sequence_index,
        "queries": queries,
        "candidate_urls": candidate_urls,
        "new_candidate_urls": new_candidate_urls,
        "fetch_attempts": int(fetch_attempts),
        "fetch_succeeded": int(fetch_succeeded),
        "source_documents": int(source_documents),
        "source_chunks": int(source_chunks),
        "claims": int(claims_total),
        "supported_claims": int(supported),
        "status": status,
        "reason": reason,
    }


def _research_round_status_reason(
    stage: dict[str, Any],
    *,
    gap_diag: dict[str, Any],
) -> tuple[str, str | None]:
    outcome = str(gap_diag.get("gap_round_outcome") or "")
    skip = gap_diag.get("skip_drafting_reason")
    skip_s = str(skip).strip() if isinstance(skip, str) and skip.strip() else None
    search = stage.get("search")
    search = search if isinstance(search, dict) else {}

    if gap_diag.get("supplemental_search_failed"):
        if search.get("failed") is True:
            sr = search.get("reason")
            msg = str(sr) if isinstance(sr, str) and sr.strip() else "supplemental_search_failed"
            return "search_timeout", skip_s or msg
        return "search_timeout", skip_s or "supplemental_search_failed"

    if skip_s in {
        SKIP_NO_CANDIDATE_URLS,
        SKIP_NO_SELECTED_CANDIDATES,
        SKIP_NO_FOLLOWUP_QUERIES,
    }:
        return "no_new_urls", skip_s

    if outcome == GAP_ROUND_OUTCOME_DRAFTED:
        supported = int(gap_diag.get("verification_supported_claims") or 0)
        created = int(gap_diag.get("drafting_created_claims") or 0)
        if supported > 0 or created > 0:
            return "produced", None
        return "drafted", skip_s

    if outcome == GAP_ROUND_OUTCOME_SKIPPED:
        return "skipped", skip_s

    if outcome == GAP_ROUND_OUTCOME_FAILED:
        return "failed", skip_s

    return "unknown", skip_s


def research_rounds_from_gap_stage_results(gap_rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        research_round_entry_from_gap_stage_result(item, sequence_index=idx)
        for idx, item in enumerate(gap_rounds)
        if isinstance(item, dict)
    ]
