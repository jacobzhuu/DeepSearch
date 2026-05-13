"""Task-scoped acquisition funnel diagnostics (DB-backed, read-only)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from packages.db.models import (
    CandidateUrl,
    Claim,
    ContentSnapshot,
    FetchAttempt,
    FetchJob,
    ReportArtifact,
    ResearchTask,
    SearchQuery,
    SourceChunk,
    SourceDocument,
    TaskEvent,
)
from packages.db.repositories import ContentSnapshotRepository, FetchAttemptRepository
from services.orchestrator.app.services.acquisition import (
    ACQUISITION_BROWSER_FALLBACK_EVENT,
    ACQUISITION_FETCH_BATCH_SUMMARY_EVENT,
    FETCH_STATUS_SUCCEEDED,
    _active_triage_decision,
    _is_official_repository_readme_derivative_candidate,
    _sort_candidates_for_fetch,
)
from services.orchestrator.app.services.parsing import _snapshot_eligible_for_evidence_parse

_FETCH_ATTEMPT_POLICY_ERROR_CODES: frozenset[str] = frozenset(
    {
        "unsupported_scheme",
        "invalid_target",
        "target_blocked",
        "dns_resolution_failed",
        "body_too_large",
        "redirect_loop",
        "too_many_redirects",
    }
)


def _attempt_blocked_by_acquisition_policy(attempt: FetchAttempt | None) -> bool:
    if attempt is None:
        return False
    error_code = str(attempt.error_code or "")
    if "policy" in error_code:
        return True
    if error_code in _FETCH_ATTEMPT_POLICY_ERROR_CODES:
        return True
    trace = attempt.trace_json or {}
    decision = trace.get("decision_reason")
    if isinstance(decision, str) and decision in {
        "blocked_hostname",
        "all_resolved_ips_non_global",
    }:
        return True
    return False


def _github_repo_root_url(url: str) -> bool:
    parsed = urlsplit(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    if domain != "github.com":
        return False
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    return len(parts) == 2


def _readme_official_parse_rejection_distribution(
    session: Session,
    task_id: UUID,
    readme_canonical_urls: set[str],
    *,
    event_limit: int = 200,
) -> dict[str, int]:
    if not readme_canonical_urls:
        return {}
    reasons: Counter[str] = Counter()
    events = session.scalars(
        select(TaskEvent)
        .where(TaskEvent.task_id == task_id)
        .order_by(TaskEvent.sequence_no.desc())
        .limit(event_limit)
    ).all()
    for event in events:
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        rows: list[dict[str, Any]] = []
        result = payload.get("result")
        if isinstance(result, dict):
            raw = result.get("parse_decisions")
            if isinstance(raw, list):
                rows.extend(item for item in raw if isinstance(item, dict))
        details = payload.get("details")
        if isinstance(details, dict):
            raw = details.get("parse_decisions")
            if isinstance(raw, list):
                rows.extend(item for item in raw if isinstance(item, dict))
        for row in rows:
            canon = row.get("canonical_url")
            if not isinstance(canon, str) or canon not in readme_canonical_urls:
                continue
            status = row.get("status")
            reason = row.get("reason")
            decision = row.get("decision")
            if isinstance(reason, str) and reason:
                if isinstance(status, str) and status in {"SKIPPED", "FAILED"}:
                    reasons[reason] += 1
                elif isinstance(decision, str) and decision not in {"parsed"}:
                    reasons[reason] += 1
    return dict(reasons)


def _official_repository_readme_funnel_metrics(
    session: Session,
    task_id: UUID,
    *,
    task_query: str | None = None,
    settings_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Ledger-backed counters for official_repository_readme (narrow raw README derivatives)."""
    candidates = list(
        session.scalars(select(CandidateUrl).where(CandidateUrl.task_id == task_id)).all()
    )
    attempt_repo = FetchAttemptRepository(session)
    blocked_repo_policy = 0
    for cand in candidates:
        if not _github_repo_root_url(cand.canonical_url):
            continue
        md = cand.metadata_json or {}
        if md.get("official_repository_readme_derivative"):
            continue
        role = str(md.get("source_role") or md.get("known_source_class") or "").strip()
        if role != "official_repository":
            continue
        fetch_job = session.scalars(
            select(FetchJob)
            .where(FetchJob.task_id == task_id, FetchJob.candidate_url_id == cand.id)
            .where(FetchJob.mode == "HTTP")
            .limit(1)
        ).first()
        if fetch_job is None:
            continue
        if fetch_job.status == FETCH_STATUS_SUCCEEDED:
            continue
        latest = attempt_repo.get_latest_for_job(fetch_job.id)
        if _attempt_blocked_by_acquisition_policy(latest):
            blocked_repo_policy += 1

    readme_labeled = [
        c for c in candidates if _is_official_repository_readme_derivative_candidate(c)
    ]
    readme_ids = {c.id for c in readme_labeled}
    readme_urls = {c.canonical_url for c in readme_labeled}

    loose_derivative_count = sum(
        1
        for c in candidates
        if (c.metadata_json or {}).get("official_repository_readme_derivative") is True
    )

    settings = dict(settings_snapshot or {})
    max_must_fetch = int(settings.get("research_acquisition_max_must_fetch_per_round") or 3)
    resolved_query = task_query
    if resolved_query is None:
        task_row = session.get(ResearchTask, task_id)
        resolved_query = task_row.query if task_row is not None else None

    selected_for_fetch, skipped_by_triage = _sort_candidates_for_fetch(
        candidates,
        query=resolved_query,
        max_must_fetch_per_round=max_must_fetch,
    )
    skipped_ids = {c.id for c in skipped_by_triage}
    selected_ids_in_order = [c.id for c in selected_for_fetch]
    selected_set = set(selected_ids_in_order)

    not_selected_reasons: Counter[str] = Counter()
    for c in readme_labeled:
        if c.id in skipped_ids:
            decision = _active_triage_decision(c) or "triage_unknown"
            not_selected_reasons[f"triage:{decision}"] += 1
        elif c.id not in selected_set:
            not_selected_reasons["not_in_acquisition_order"] += 1

    batch_stop_by_candidate = _batch_stop_reason_by_candidate(session, task_id)
    canonical_to_ids_with_job: dict[str, set[UUID]] = {}
    rows_job = session.execute(
        select(CandidateUrl.canonical_url, CandidateUrl.id)
        .join(FetchJob, FetchJob.candidate_url_id == CandidateUrl.id)
        .where(FetchJob.task_id == task_id)
    ).all()
    for canon, cid in rows_job:
        canonical_to_ids_with_job.setdefault(str(canon), set()).add(cid)
    have_fetch_job_ids = set(
        session.scalars(
            select(FetchJob.candidate_url_id).where(FetchJob.task_id == task_id)
        ).all()
    )

    for c in readme_labeled:
        if c.id in skipped_ids or c.id not in selected_set:
            continue
        if c.id in have_fetch_job_ids:
            continue
        reason = _classify_not_fetched_candidate(
            c,
            batch_stop_reason=batch_stop_by_candidate.get(c.id),
            canonical_to_ids_with_job=canonical_to_ids_with_job,
        )
        not_selected_reasons[f"queued_no_fetch:{reason}"] += 1

    readme_selected_for_fetch_count = sum(1 for cid in selected_ids_in_order if cid in readme_ids)

    fetch_job_readme = 0
    fetch_attempt_readme = 0
    fetch_success_readme = 0
    snapshot_readme = 0
    source_doc_readme = 0
    if readme_ids:
        fetch_job_readme = int(
            session.scalar(
                select(func.count(FetchJob.id))
                .select_from(FetchJob)
                .where(
                    FetchJob.task_id == task_id,
                    FetchJob.mode == "HTTP",
                    FetchJob.candidate_url_id.in_(readme_ids),
                )
            )
            or 0
        )
        fetch_attempt_readme = int(
            session.scalar(
                select(func.count(FetchAttempt.id))
                .select_from(FetchAttempt)
                .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
                .where(
                    FetchJob.task_id == task_id,
                    FetchJob.mode == "HTTP",
                    FetchJob.candidate_url_id.in_(readme_ids),
                )
            )
            or 0
        )
        fetch_success_readme = int(
            session.scalar(
                select(func.count(FetchJob.id))
                .select_from(FetchJob)
                .where(
                    FetchJob.task_id == task_id,
                    FetchJob.mode == "HTTP",
                    FetchJob.status == FETCH_STATUS_SUCCEEDED,
                    FetchJob.candidate_url_id.in_(readme_ids),
                )
            )
            or 0
        )
        snapshot_readme = int(
            session.scalar(
                select(func.count(ContentSnapshot.id))
                .select_from(ContentSnapshot)
                .join(FetchAttempt, FetchAttempt.id == ContentSnapshot.fetch_attempt_id)
                .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
                .where(FetchJob.task_id == task_id, FetchJob.candidate_url_id.in_(readme_ids))
            )
            or 0
        )
        source_doc_readme = int(
            session.scalar(
                select(func.count(func.distinct(SourceDocument.id)))
                .select_from(SourceDocument)
                .join(ContentSnapshot, ContentSnapshot.id == SourceDocument.content_snapshot_id)
                .join(FetchAttempt, FetchAttempt.id == ContentSnapshot.fetch_attempt_id)
                .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
                .join(CandidateUrl, CandidateUrl.id == FetchJob.candidate_url_id)
                .where(SourceDocument.task_id == task_id, CandidateUrl.id.in_(readme_ids))
            )
            or 0
        )

    chunk_seen = 0
    if readme_urls:
        chunk_seen = int(
            session.scalar(
                select(func.count(SourceChunk.id))
                .select_from(SourceChunk)
                .join(SourceDocument, SourceDocument.id == SourceChunk.source_document_id)
                .where(
                    SourceDocument.task_id == task_id,
                    SourceDocument.canonical_url.in_(readme_urls),
                )
            )
            or 0
        )

    parse_rejection = _readme_official_parse_rejection_distribution(
        session, task_id, readme_urls
    )

    return {
        "official_repository_blocked_by_policy_count": blocked_repo_policy,
        "official_repository_readme_candidate_count": len(readme_labeled),
        "official_repository_readme_candidate_derived_count": len(readme_labeled),
        "official_repository_readme_loose_derivative_flag_count": loose_derivative_count,
        "official_repository_readme_selected_for_fetch_count": readme_selected_for_fetch_count,
        "official_repository_readme_not_selected_reason_distribution": dict(
            sorted(not_selected_reasons.items(), key=lambda item: item[0])
        ),
        "official_repository_readme_fetch_job_count": fetch_job_readme,
        "official_repository_readme_fetch_attempt_count": fetch_attempt_readme,
        "official_repository_readme_fetch_success_count": fetch_success_readme,
        "official_repository_readme_snapshot_count": snapshot_readme,
        "official_repository_readme_source_document_count": source_doc_readme,
        "official_repository_readme_parse_success_count": source_doc_readme,
        "official_repository_readme_parse_rejection_reason_distribution": dict(
            sorted(parse_rejection.items(), key=lambda item: item[0])
        ),
        "official_repository_chunks_seen": chunk_seen,
    }


def _eligible_snapshots_without_source_document(
    session: Session, task_id: UUID
) -> tuple[int, dict[str, int]]:
    """Snapshots with succeeded fetch, evidence-parse eligible, no ``source_document`` yet."""
    domain_counts: Counter[str] = Counter()
    n = 0
    cs_repo = ContentSnapshotRepository(session)
    for cs in cs_repo.list_for_task(task_id):
        fetch_attempt = cs.fetch_attempt
        fetch_job = fetch_attempt.fetch_job
        if fetch_job.status != FETCH_STATUS_SUCCEEDED or fetch_attempt.error_code:
            continue
        if not _snapshot_eligible_for_evidence_parse(cs):
            continue
        if cs.source_document is not None:
            continue
        n += 1
        cand = fetch_job.candidate_url
        domain_counts[str(cand.domain or "unknown")] += 1
    top = dict(domain_counts.most_common(40))
    return n, top


def _latest_parsing_stage_result(session: Session, task_id: UUID) -> dict[str, Any]:
    """Most recent ``pipeline*.stage_completed`` payload ``result`` for ``PARSING``."""
    events = session.scalars(
        select(TaskEvent)
        .where(
            TaskEvent.task_id == task_id,
            TaskEvent.event_type.like("%.stage_completed"),
        )
        .order_by(TaskEvent.sequence_no.desc())
        .limit(400)
    ).all()
    for event in events:
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("stage") != "PARSING":
            continue
        result = payload.get("result")
        if isinstance(result, dict):
            return result
    return {}


def compute_acquisition_funnel_diagnostics(
    session: Session,
    task_id: UUID,
    *,
    task_query: str | None = None,
    settings_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Aggregate ledger counts for the search → fetch → parse → chunk funnel.

    Parser rejection distribution is merged from recent ``task_event`` payloads when present.

    When ``task_query`` is omitted, the task row is loaded to mirror triage ordering used during
    acquisition. ``settings_snapshot`` should mirror the orchestrator Settings fields used for
    acquisition caps (see ``GET .../funnel-metrics``).
    """
    search_query_count = int(
        session.scalar(
            select(func.count()).select_from(SearchQuery).where(SearchQuery.task_id == task_id)
        )
        or 0
    )
    candidate_url_count = int(
        session.scalar(
            select(func.count()).select_from(CandidateUrl).where(CandidateUrl.task_id == task_id)
        )
        or 0
    )
    fetch_job_count = int(
        session.scalar(
            select(func.count()).select_from(FetchJob).where(FetchJob.task_id == task_id)
        )
        or 0
    )
    snapshot_count = int(
        session.scalar(
            select(func.count(ContentSnapshot.id))
            .select_from(ContentSnapshot)
            .join(FetchAttempt, FetchAttempt.id == ContentSnapshot.fetch_attempt_id)
            .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
            .where(FetchJob.task_id == task_id)
        )
        or 0
    )
    successful_fetch_job_count = int(
        session.scalar(
            select(func.count(FetchJob.id))
            .select_from(FetchJob)
            .where(FetchJob.task_id == task_id, FetchJob.status == "SUCCEEDED")
        )
        or 0
    )
    source_document_count = int(
        session.scalar(
            select(func.count(SourceDocument.id)).where(SourceDocument.task_id == task_id)
        )
        or 0
    )
    source_chunk_count = int(
        session.scalar(
            select(func.count(SourceChunk.id))
            .select_from(SourceChunk)
            .join(SourceDocument, SourceDocument.id == SourceChunk.source_document_id)
            .where(SourceDocument.task_id == task_id)
        )
        or 0
    )

    candidates_with_snapshot = int(
        session.scalar(
            select(func.count(func.distinct(FetchJob.candidate_url_id)))
            .select_from(ContentSnapshot)
            .join(FetchAttempt, FetchAttempt.id == ContentSnapshot.fetch_attempt_id)
            .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
            .where(FetchJob.task_id == task_id)
        )
        or 0
    )

    snapshots_with_document = int(
        session.scalar(
            select(func.count(ContentSnapshot.id))
            .select_from(ContentSnapshot)
            .join(FetchAttempt, FetchAttempt.id == ContentSnapshot.fetch_attempt_id)
            .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
            .join(SourceDocument, SourceDocument.content_snapshot_id == ContentSnapshot.id)
            .where(FetchJob.task_id == task_id)
        )
        or 0
    )

    documents_with_chunks = int(
        session.scalar(
            select(func.count(func.distinct(SourceDocument.id)))
            .select_from(SourceDocument)
            .join(SourceChunk, SourceChunk.source_document_id == SourceDocument.id)
            .where(SourceDocument.task_id == task_id)
        )
        or 0
    )

    fetch_status_rows = session.execute(
        select(FetchJob.status, func.count(FetchJob.id))
        .where(FetchJob.task_id == task_id)
        .group_by(FetchJob.status)
    ).all()
    fetch_status_distribution = {str(row[0]): int(row[1]) for row in fetch_status_rows}

    fetch_error_rows = session.execute(
        select(FetchAttempt.error_code, func.count(FetchAttempt.id))
        .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
        .where(FetchJob.task_id == task_id)
        .group_by(FetchAttempt.error_code)
    ).all()
    fetch_error_code_distribution: dict[str, int] = {}
    for code, count in fetch_error_rows:
        key = "ok" if code is None else str(code)
        fetch_error_code_distribution[key] = int(count)

    domain_failure_rows = session.execute(
        select(CandidateUrl.domain, func.count(FetchAttempt.id))
        .join(FetchJob, FetchJob.candidate_url_id == CandidateUrl.id)
        .join(FetchAttempt, FetchAttempt.fetch_job_id == FetchJob.id)
        .where(FetchJob.task_id == task_id, FetchAttempt.error_code.isnot(None))
        .group_by(CandidateUrl.domain)
        .order_by(func.count(FetchAttempt.id).desc())
        .limit(40)
    ).all()
    domain_failure_distribution = {str(row[0]): int(row[1]) for row in domain_failure_rows}

    parser_decision_distribution, parser_reason_distribution = _parse_distributions_from_events(
        session, task_id
    )

    settings_snapshot = dict(settings_snapshot or {})
    resolved_query = task_query
    if resolved_query is None:
        task_row = session.get(ResearchTask, task_id)
        resolved_query = task_row.query if task_row is not None else None

    max_must_fetch = int(
        settings_snapshot.get("research_acquisition_max_must_fetch_per_round") or 3
    )

    all_candidates = list(
        session.scalars(select(CandidateUrl).where(CandidateUrl.task_id == task_id)).all()
    )
    selected_for_fetch, skipped_by_triage = _sort_candidates_for_fetch(
        all_candidates,
        query=resolved_query,
        max_must_fetch_per_round=max_must_fetch,
    )

    distinct_candidates_with_fetch_job = int(
        session.scalar(
            select(func.count(func.distinct(FetchJob.candidate_url_id))).where(
                FetchJob.task_id == task_id
            )
        )
        or 0
    )
    candidates_without_fetch_job = max(0, candidate_url_count - distinct_candidates_with_fetch_job)

    have_fetch_job_ids = set(
        session.scalars(
            select(FetchJob.candidate_url_id).where(FetchJob.task_id == task_id)
        ).all()
    )

    canonical_to_ids_with_job: dict[str, set[UUID]] = {}
    rows_job = session.execute(
        select(CandidateUrl.canonical_url, CandidateUrl.id)
        .join(FetchJob, FetchJob.candidate_url_id == CandidateUrl.id)
        .where(FetchJob.task_id == task_id)
    ).all()
    for canon, cid in rows_job:
        canonical_to_ids_with_job.setdefault(str(canon), set()).add(cid)

    batch_stop_by_candidate = _batch_stop_reason_by_candidate(session, task_id)
    fetch_batch_stop_reason_distribution = _fetch_batch_stop_reason_distribution(session, task_id)
    fetch_budget_limit_applied = (
        fetch_batch_stop_reason_distribution.get("fetch_budget_exhausted", 0) > 0
    )

    not_fetched_reason_counter: Counter[str] = Counter()
    for candidate in all_candidates:
        if candidate.id in have_fetch_job_ids:
            continue
        reason = _classify_not_fetched_candidate(
            candidate,
            batch_stop_reason=batch_stop_by_candidate.get(candidate.id),
            canonical_to_ids_with_job=canonical_to_ids_with_job,
        )
        not_fetched_reason_counter[reason] += 1

    fetch_job_mode_rows = session.execute(
        select(FetchJob.mode, func.count(FetchJob.id))
        .where(FetchJob.task_id == task_id)
        .group_by(FetchJob.mode)
    ).all()
    fetch_jobs_by_mode = {str(row[0]): int(row[1]) for row in fetch_job_mode_rows}

    http_job_rows = session.execute(
        select(FetchJob.status, func.count(FetchJob.id))
        .where(FetchJob.task_id == task_id, FetchJob.mode == "HTTP")
        .group_by(FetchJob.status)
    ).all()
    http_fetch_job_status_distribution = {str(row[0]): int(row[1]) for row in http_job_rows}

    claim_count = int(
        session.scalar(select(func.count(Claim.id)).where(Claim.task_id == task_id)) or 0
    )
    supported_claim_count = int(
        session.scalar(
            select(func.count(Claim.id)).where(
                Claim.task_id == task_id, Claim.verification_status == "supported"
            )
        )
        or 0
    )
    report_artifact_count = int(
        session.scalar(
            select(func.count(ReportArtifact.id)).where(ReportArtifact.task_id == task_id)
        )
        or 0
    )

    body_too_large_total = int(
        session.scalar(
            select(func.count(FetchAttempt.id))
            .select_from(FetchAttempt)
            .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
            .where(FetchJob.task_id == task_id, FetchAttempt.error_code == "body_too_large")
        )
        or 0
    )
    mime_coalesce = func.coalesce(ContentSnapshot.mime_type, literal("unknown"))
    body_too_large_rows = session.execute(
        select(CandidateUrl.domain, mime_coalesce, func.count(FetchAttempt.id))
        .select_from(FetchAttempt)
        .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
        .join(CandidateUrl, CandidateUrl.id == FetchJob.candidate_url_id)
        .outerjoin(ContentSnapshot, ContentSnapshot.fetch_attempt_id == FetchAttempt.id)
        .where(FetchJob.task_id == task_id, FetchAttempt.error_code == "body_too_large")
        .group_by(CandidateUrl.domain, mime_coalesce)
        .order_by(func.count(FetchAttempt.id).desc())
        .limit(60)
    ).all()
    body_too_large_by_domain_mime = [
        {"domain": str(r[0]), "mime_type": str(r[1]), "attempts": int(r[2])}
        for r in body_too_large_rows
    ]

    response_cap_scan = _scan_response_cap_in_fetch_attempts(session, task_id)
    success_target_batch = _fetch_batch_success_target_metrics(session, task_id)

    browser_fallback_task_metrics = _browser_fallback_metrics_from_events(session, task_id)

    eligible_no_doc, unparsed_eligible_snapshot_domains = (
        _eligible_snapshots_without_source_document(session, task_id)
    )
    latest_parsing = _latest_parsing_stage_result(session, task_id)
    parse_drain_batches_run = int(latest_parsing.get("parse_drain_batches_run") or 0)
    if latest_parsing.get("parse_drain_created_documents") is not None:
        parse_drain_created_documents = int(
            latest_parsing.get("parse_drain_created_documents") or 0
        )
    else:
        parse_drain_created_documents = int(latest_parsing.get("created") or 0) + int(
            latest_parsing.get("updated") or 0
        )
    rlimit = max(1, int(settings_snapshot.get("research_parse_limit") or 8))
    drain_enabled = bool(settings_snapshot.get("research_parse_drain_enabled"))
    stop_reason = str(latest_parsing.get("parse_drain_stop_reason") or "")
    parse_limit_exhausted = False
    if eligible_no_doc > 0:
        if not drain_enabled and eligible_no_doc > rlimit:
            parse_limit_exhausted = True
        elif drain_enabled and stop_reason in (
            "max_batches",
            "max_seconds",
            "target_documents",
            "no_progress",
        ):
            parse_limit_exhausted = True

    parse_not_attempted_reason_distribution: dict[str, int] = {}
    if eligible_no_doc > 0:
        if not drain_enabled:
            if eligible_no_doc > rlimit:
                parse_not_attempted_reason_distribution["parse_limit_exhausted"] = eligible_no_doc
            else:
                parse_not_attempted_reason_distribution[
                    "eligible_unparsed_within_parse_limit"
                ] = eligible_no_doc
        elif stop_reason:
            parse_not_attempted_reason_distribution[
                f"after_parse_drain:{stop_reason}"
            ] = eligible_no_doc
        else:
            parse_not_attempted_reason_distribution["eligible_unparsed_no_parsing_event"] = (
                eligible_no_doc
            )

    def _rate(numerator: int, denominator: int) -> float | None:
        if denominator <= 0:
            return None
        return round(float(numerator) / float(denominator), 6)

    readme_repo_metrics = _official_repository_readme_funnel_metrics(
        session,
        task_id,
        task_query=resolved_query,
        settings_snapshot=settings_snapshot,
    )

    return {
        "task_id": str(task_id),
        "counts": {
            "search_query": search_query_count,
            "candidate_url": candidate_url_count,
            "fetch_job": fetch_job_count,
            "successful_fetch_job": successful_fetch_job_count,
            "content_snapshot": snapshot_count,
            "source_document": source_document_count,
            "source_chunk": source_chunk_count,
            "candidates_with_snapshot": candidates_with_snapshot,
            "snapshots_with_source_document": snapshots_with_document,
            "documents_with_chunks": documents_with_chunks,
            "claim": claim_count,
            "supported_claim": supported_claim_count,
            "report_artifact": report_artifact_count,
            "candidates_with_any_fetch_job": distinct_candidates_with_fetch_job,
            "candidates_without_fetch_job": candidates_without_fetch_job,
            "selected_candidate_count": len(selected_for_fetch),
            "skipped_candidate_count": len(skipped_by_triage),
        },
        "rates": {
            "candidate_url_to_snapshot_rate": _rate(candidates_with_snapshot, candidate_url_count),
            "snapshot_to_document_rate": _rate(snapshots_with_document, snapshot_count),
            "document_to_chunk_rate": _rate(documents_with_chunks, source_document_count),
        },
        "fetch_status_distribution": fetch_status_distribution,
        "fetch_error_code_distribution": dict(
            sorted(fetch_error_code_distribution.items(), key=lambda item: item[0])
        ),
        "domain_failure_distribution": domain_failure_distribution,
        "parser_decision_distribution": parser_decision_distribution,
        "parser_rejection_reason_distribution": parser_reason_distribution,
        "candidate_not_fetched_reason_distribution": dict(
            sorted(not_fetched_reason_counter.items(), key=lambda item: item[0])
        ),
        "fetch_batch_stop_reason_distribution": dict(
            sorted(fetch_batch_stop_reason_distribution.items(), key=lambda item: item[0])
        ),
        "fetch_jobs_by_mode": dict(sorted(fetch_jobs_by_mode.items(), key=lambda item: item[0])),
        "http_fetch_job_status_distribution": dict(
            sorted(http_fetch_job_status_distribution.items(), key=lambda item: item[0])
        ),
        "fetch_budget_limit_applied": fetch_budget_limit_applied,
        "eligible_snapshots_without_source_document": eligible_no_doc,
        "parse_not_attempted_snapshot_count": eligible_no_doc,
        "parse_limit_exhausted": parse_limit_exhausted,
        "parse_drain_batches_run": parse_drain_batches_run,
        "parse_drain_created_documents": parse_drain_created_documents,
        "parse_drain_stop_reason": stop_reason or None,
        "unparsed_eligible_snapshot_domains": unparsed_eligible_snapshot_domains,
        "parse_not_attempted_reason_distribution": dict(
            sorted(parse_not_attempted_reason_distribution.items(), key=lambda item: item[0])
        ),
        "acquisition_limits": {
            "max_candidates_per_request": settings_snapshot.get(
                "acquisition_max_candidates_per_request"
            ),
            "max_must_fetch_per_round": settings_snapshot.get(
                "research_acquisition_max_must_fetch_per_round"
            ),
            "target_successful_snapshots": settings_snapshot.get(
                "acquisition_target_successful_snapshots"
            ),
            "max_response_bytes": settings_snapshot.get("acquisition_max_response_bytes"),
            "trusted_docs_domains": settings_snapshot.get("acquisition_trusted_docs_domains"),
            "trusted_docs_max_response_bytes": settings_snapshot.get(
                "acquisition_trusted_docs_max_response_bytes"
            ),
            "min_successful_authoritative_snapshots": settings_snapshot.get(
                "acquisition_min_successful_authoritative_snapshots"
            ),
            "defer_success_target_for_high_priority": settings_snapshot.get(
                "acquisition_defer_success_target_for_high_priority"
            ),
            "research_parse_limit": settings_snapshot.get("research_parse_limit"),
            "research_parse_drain_enabled": settings_snapshot.get("research_parse_drain_enabled"),
            "research_parse_max_batches": settings_snapshot.get("research_parse_max_batches"),
            "research_parse_target_documents": settings_snapshot.get(
                "research_parse_target_documents"
            ),
            "research_parse_drain_max_seconds": settings_snapshot.get(
                "research_parse_drain_max_seconds"
            ),
        },
        "browser_fallback_task_metrics": browser_fallback_task_metrics,
        **readme_repo_metrics,
        "response_cap_policy_distribution": dict(
            sorted(response_cap_scan["cap_sources"].items(), key=lambda item: item[0])
        ),
        "response_cap_decision_distribution": dict(
            sorted(response_cap_scan["cap_decisions"].items(), key=lambda item: item[0])
        ),
        "trusted_docs_elevated_cap_fetch_attempts": response_cap_scan["trusted_elevated"],
        "fetch_batch_success_target_continue_total": success_target_batch["continue_total"],
        "body_too_large": {
            "total_attempts": body_too_large_total,
            "by_domain_and_mime": body_too_large_by_domain_mime,
            "configured_max_response_bytes": settings_snapshot.get(
                "acquisition_max_response_bytes"
            ),
        },
        "diagnostics_meta": {
            "ledger_backed": True,
            "rates_denominators": (
                "candidate_url_to_snapshot_rate uses distinct candidates_with_snapshot / "
                "candidate_url rows; snapshot_to_document_rate uses "
                "snapshots_with_source_document / "
                "content_snapshot rows; document_to_chunk_rate uses documents_with_chunks / "
                "source_document rows."
            ),
            "parser_distributions": {
                "source": "task_event_payloads",
                "event_scan_limit": 200,
                "limitation": (
                    "Parse decision and rejection histograms are reconstructed only from the "
                    "most recent task_event rows that embed parse_decisions; they are not a "
                    "complete ledger of every parse attempt and can miss older rounds."
                ),
                "future_ledger_model": (
                    "Introduce durable parse outcome rows keyed by content_snapshot_id "
                    "(decision, reason, timestamps) written whenever ParsingService runs, so "
                    "funnel metrics and audits do not depend on task_event retention."
                ),
            },
            "candidate_not_fetched": {
                "source": "ledger_plus_task_event_heuristic",
                "event_scan_limit": 200,
                "limitation": (
                    "Reasons merge durable acquisition.fetch_batch_summary stop_reason for "
                    "unattempted candidate ids (per batch) with triage metadata and "
                    "duplicate-canonical "
                    "heuristics. Candidates never present in a batch summary payload fall back to "
                    "heuristics or unknown."
                ),
            },
        },
    }


def _scan_response_cap_in_fetch_attempts(
    session: Session,
    task_id: UUID,
    *,
    limit: int = 512,
) -> dict[str, Any]:
    cap_sources: Counter[str] = Counter()
    cap_decisions: Counter[str] = Counter()
    trusted_elevated = 0
    traces = session.scalars(
        select(FetchAttempt.trace_json)
        .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
        .where(FetchJob.task_id == task_id)
        .order_by(FetchAttempt.started_at.desc())
        .limit(limit)
    ).all()
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        src = trace.get("response_cap_source")
        if isinstance(src, str) and src.strip():
            cap_sources[src.strip()] += 1
        dec = trace.get("cap_decision")
        if isinstance(dec, str) and dec.strip():
            cap_decisions[dec.strip()] += 1
        if trace.get("cap_decision") == "trusted_docs_elevated_cap":
            trusted_elevated += 1
    return {
        "cap_sources": cap_sources,
        "cap_decisions": cap_decisions,
        "trusted_elevated": trusted_elevated,
    }


def _fetch_batch_success_target_metrics(
    session: Session,
    task_id: UUID,
    *,
    limit: int = 200,
) -> dict[str, int]:
    continue_total = 0
    events = session.scalars(
        select(TaskEvent)
        .where(
            TaskEvent.task_id == task_id,
            TaskEvent.event_type == ACQUISITION_FETCH_BATCH_SUMMARY_EVENT,
        )
        .order_by(TaskEvent.sequence_no.desc())
        .limit(limit)
    ).all()
    for event in events:
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        raw = payload.get("success_target_continue_count")
        if isinstance(raw, int) and raw > 0:
            continue_total += raw
    return {"continue_total": continue_total}


def _batch_stop_reason_by_candidate(
    session: Session,
    task_id: UUID,
    *,
    limit: int = 200,
) -> dict[UUID, str]:
    """Latest batch event wins per candidate id (later sequence overwrites)."""
    out: dict[UUID, str] = {}
    events = session.scalars(
        select(TaskEvent)
        .where(
            TaskEvent.task_id == task_id,
            TaskEvent.event_type == ACQUISITION_FETCH_BATCH_SUMMARY_EVENT,
        )
        .order_by(TaskEvent.sequence_no.asc())
        .limit(limit)
    ).all()
    for event in events:
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        stop_reason = payload.get("stop_reason")
        if not isinstance(stop_reason, str):
            continue
        raw_ids = payload.get("unattempted_candidate_ids")
        if not isinstance(raw_ids, list):
            continue
        for raw in raw_ids:
            try:
                cid = UUID(str(raw))
            except (ValueError, TypeError):
                continue
            out[cid] = stop_reason
    return out


def _fetch_batch_stop_reason_distribution(
    session: Session,
    task_id: UUID,
    *,
    limit: int = 200,
) -> dict[str, int]:
    counter: Counter[str] = Counter()
    events = session.scalars(
        select(TaskEvent)
        .where(
            TaskEvent.task_id == task_id,
            TaskEvent.event_type == ACQUISITION_FETCH_BATCH_SUMMARY_EVENT,
        )
        .order_by(TaskEvent.sequence_no.desc())
        .limit(limit)
    ).all()
    for event in events:
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        stop_reason = payload.get("stop_reason")
        if isinstance(stop_reason, str) and stop_reason:
            counter[stop_reason] += 1
    return dict(counter)


def _browser_fallback_metrics_from_events(
    session: Session,
    task_id: UUID,
    *,
    limit: int = 500,
) -> dict[str, int]:
    events = session.scalars(
        select(TaskEvent)
        .where(
            TaskEvent.task_id == task_id,
            TaskEvent.event_type == ACQUISITION_BROWSER_FALLBACK_EVENT,
        )
        .order_by(TaskEvent.sequence_no.desc())
        .limit(limit)
    ).all()
    considered = 0
    attempted = 0
    succeeded = 0
    failed = 0
    skipped = 0
    for event in events:
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("browser_fallback_considered") is True:
            considered += 1
        if payload.get("browser_fallback_attempted") is True:
            attempted += 1
        if payload.get("browser_fallback_result") == "succeeded":
            succeeded += 1
        elif payload.get("browser_fallback_result") == "failed":
            failed += 1
        if payload.get("browser_fallback_considered") is True and payload.get(
            "browser_fallback_attempted"
        ) is not True:
            skipped += 1
    return {
        "browser_fallback_events": len(events),
        "browser_fallback_considered": considered,
        "browser_fallback_attempted": attempted,
        "browser_fallback_succeeded": succeeded,
        "browser_fallback_failed": failed,
        "browser_fallback_skipped_after_consideration": skipped,
    }


def _classify_not_fetched_candidate(
    candidate: CandidateUrl,
    *,
    batch_stop_reason: str | None,
    canonical_to_ids_with_job: dict[str, set[UUID]],
) -> str:
    if batch_stop_reason == "fetch_budget_exhausted":
        return "fetch_budget_exhausted"
    if batch_stop_reason == "success_target_met":
        return "success_target_met"
    if batch_stop_reason == "unknown_early_exit":
        return "unknown"

    decision = _active_triage_decision(candidate)
    if decision == "skip_duplicate":
        return "duplicate_url"
    if decision == "skip_low_value":
        return "not_selected_by_triage"
    if decision == "skip_unsafe_or_invalid":
        return "blocked_by_policy"
    if decision == "defer":
        return "low_priority"

    canon = str(candidate.canonical_url)
    job_ids = canonical_to_ids_with_job.get(canon, set())
    if candidate.id not in job_ids and job_ids:
        return "duplicate_url"
    return "unknown"


def _parse_distributions_from_events(
    session: Session,
    task_id: UUID,
    *,
    event_limit: int = 200,
) -> tuple[dict[str, int], dict[str, int]]:
    decisions: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    events = session.scalars(
        select(TaskEvent)
        .where(TaskEvent.task_id == task_id)
        .order_by(TaskEvent.sequence_no.desc())
        .limit(event_limit)
    ).all()
    for event in events:
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        candidates: list[dict[str, Any]] = []
        result = payload.get("result")
        if isinstance(result, dict):
            raw = result.get("parse_decisions")
            if isinstance(raw, list):
                candidates.extend(item for item in raw if isinstance(item, dict))
        details = payload.get("details")
        if isinstance(details, dict):
            raw = details.get("parse_decisions")
            if isinstance(raw, list):
                candidates.extend(item for item in raw if isinstance(item, dict))
        for row in candidates:
            decision = row.get("decision")
            if isinstance(decision, str) and decision:
                decisions[decision] += 1
            status = row.get("status")
            reason = row.get("reason")
            if isinstance(reason, str) and reason:
                if isinstance(status, str) and status in {"SKIPPED", "FAILED"}:
                    reasons[reason] += 1
                elif isinstance(decision, str) and decision not in {"parsed"}:
                    reasons[reason] += 1
    return dict(decisions), dict(reasons)
