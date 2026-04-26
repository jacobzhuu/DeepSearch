from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from packages.db.models import ResearchTask
from packages.db.repositories import (
    CandidateUrlRepository,
    ClaimEvidenceRepository,
    ClaimRepository,
    ContentSnapshotRepository,
    FetchAttemptRepository,
    ReportArtifactRepository,
    ResearchTaskRepository,
    SearchQueryRepository,
    SourceChunkRepository,
    SourceDocumentRepository,
    TaskEventRepository,
)
from services.orchestrator.app.indexing import IndexedChunkPage
from services.orchestrator.app.search import SearchProviderError
from services.orchestrator.app.services.acquisition import AcquisitionService
from services.orchestrator.app.services.claims import ClaimDraftingService
from services.orchestrator.app.services.indexing import IndexingService
from services.orchestrator.app.services.parsing import ParsingService, parse_entry_diagnostic
from services.orchestrator.app.services.reporting import ReportSynthesisService
from services.orchestrator.app.services.research_tasks import (
    TaskNotFoundError,
    build_task_event_payload,
)
from services.orchestrator.app.services.search_discovery import SearchDiscoveryService

PIPELINE_EVENT_SOURCE = "pipeline.run"
DEBUG_EVENT_SOURCE = "debug.run-real-pipeline"
PIPELINE_EVENT_PREFIX = "pipeline"
DEBUG_EVENT_PREFIX = "debug.pipeline"

STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"

STAGE_SEARCHING = "SEARCHING"
STAGE_ACQUIRING = "ACQUIRING"
STAGE_PARSING = "PARSING"
STAGE_INDEXING = "INDEXING"
STAGE_DRAFTING_CLAIMS = "DRAFTING_CLAIMS"
STAGE_VERIFYING = "VERIFYING"
STAGE_REPORTING = "REPORTING"

PIPELINE_RUNNABLE_STATUSES = ("PLANNED",)
SEARCH_ALLOWED_STATUSES = ("PLANNED", STAGE_SEARCHING)
ACQUISITION_ALLOWED_STATUSES = ("PLANNED", STAGE_ACQUIRING)
PARSING_ALLOWED_STATUSES = ("PLANNED", STAGE_PARSING)
INDEXING_ALLOWED_STATUSES = ("PLANNED", STAGE_INDEXING)
DRAFT_ALLOWED_STATUSES = ("PLANNED", STAGE_DRAFTING_CLAIMS)
VERIFY_ALLOWED_STATUSES = ("PLANNED", STAGE_VERIFYING)
MIN_SUCCESSFUL_SOURCES_WARNING_THRESHOLD = 2


@dataclass(frozen=True)
class DebugPipelineCounts:
    search_queries: int
    candidate_urls: int
    fetch_attempts: int
    content_snapshots: int
    source_documents: int
    source_chunks: int
    indexed_chunks: int
    claims: int
    claim_evidence: int
    report_artifacts: int


@dataclass(frozen=True)
class DebugPipelineFailure:
    stage: str
    reason: str
    exception: str | None
    message: str
    next_action: str
    counts: DebugPipelineCounts
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class DebugPipelineResult:
    task: ResearchTask
    completed: bool
    stages_completed: list[str]
    counts: DebugPipelineCounts
    report_artifact_id: UUID | None
    report_version: int | None
    report_markdown_preview: str | None
    failure: DebugPipelineFailure | None
    dependencies: dict[str, Any]


class DebugPipelinePreconditionError(Exception):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details


class DebugRealPipelineRunner:
    def __init__(
        self,
        session: Session,
        *,
        search_service: SearchDiscoveryService,
        acquisition_service: AcquisitionService,
        parsing_service: ParsingService,
        indexing_service: IndexingService,
        claims_service: ClaimDraftingService,
        reporting_service: ReportSynthesisService,
        dependencies: dict[str, Any],
        fetch_limit: int = 3,
        parse_limit: int = 3,
        index_limit: int = 10,
        claim_limit: int = 5,
        event_source: str = PIPELINE_EVENT_SOURCE,
        event_prefix: str = PIPELINE_EVENT_PREFIX,
        target_successful_snapshots: int = MIN_SUCCESSFUL_SOURCES_WARNING_THRESHOLD,
    ) -> None:
        self.session = session
        self.search_service = search_service
        self.acquisition_service = acquisition_service
        self.parsing_service = parsing_service
        self.indexing_service = indexing_service
        self.claims_service = claims_service
        self.reporting_service = reporting_service
        self.dependencies = dependencies
        self.fetch_limit = fetch_limit
        self.parse_limit = parse_limit
        self.index_limit = index_limit
        self.claim_limit = claim_limit
        self.event_source = event_source
        self.event_prefix = event_prefix
        self.target_successful_snapshots = target_successful_snapshots
        self.task_repository = ResearchTaskRepository(session)
        self.event_repository = TaskEventRepository(session)

    def run(self, task_id: UUID) -> DebugPipelineResult:
        task = self._get_task(task_id)
        if task.status not in PIPELINE_RUNNABLE_STATUSES:
            raise DebugPipelinePreconditionError(
                f"DeepSearch pipeline can only run from PLANNED; current status is {task.status}"
            )

        stages_completed: list[str] = []
        report_artifact_id: UUID | None = None
        report_version: int | None = None
        report_markdown_preview: str | None = None

        self._mark_started(task)

        for stage, action in (
            (STAGE_SEARCHING, self._run_search),
            (STAGE_ACQUIRING, self._run_fetch),
            (STAGE_PARSING, self._run_parse),
            (STAGE_INDEXING, self._run_index),
            (STAGE_DRAFTING_CLAIMS, self._run_draft_claims),
            (STAGE_VERIFYING, self._run_verify_claims),
            (STAGE_REPORTING, self._run_report),
        ):
            self._record_stage_started(task.id, stage)
            try:
                stage_result = action(task.id)
            except Exception as error:  # noqa: BLE001 - debug endpoint must report exact blocker.
                self.session.rollback()
                counts = self._safe_counts(task.id)
                failure = DebugPipelineFailure(
                    stage=stage,
                    reason=_classify_failure(error),
                    exception=type(error).__name__,
                    message=str(error),
                    next_action=_next_action_for_failure(stage=stage, error=error),
                    counts=counts,
                    details=getattr(error, "details", None),
                )
                self._record_failure(task.id, failure)
                refreshed_task = self._get_task(task.id)
                return DebugPipelineResult(
                    task=refreshed_task,
                    completed=False,
                    stages_completed=stages_completed,
                    counts=counts,
                    report_artifact_id=None,
                    report_version=None,
                    report_markdown_preview=None,
                    failure=failure,
                    dependencies=self.dependencies,
                )

            stages_completed.append(stage)
            self._record_stage_completed(task.id, stage, stage_result)
            if stage == STAGE_REPORTING:
                report_artifact_id = stage_result.get("report_artifact_id")
                report_version = stage_result.get("report_version")
                report_markdown_preview = stage_result.get("report_markdown_preview")

        completed_task = self._mark_completed(task.id)
        return DebugPipelineResult(
            task=completed_task,
            completed=True,
            stages_completed=stages_completed,
            counts=self._safe_counts(task.id),
            report_artifact_id=report_artifact_id,
            report_version=report_version,
            report_markdown_preview=report_markdown_preview,
            failure=None,
            dependencies=self.dependencies,
        )

    def _run_search(self, task_id: UUID) -> dict[str, Any]:
        result = self.search_service.discover_candidates(task_id)
        if not result.candidate_urls:
            raise DebugPipelinePreconditionError("search produced no candidate URLs")
        search_queries = []
        search_result_count = 0
        for item in result.search_queries:
            raw_payload = item.search_query.raw_response_json or {}
            result_count = raw_payload.get("result_count", 0)
            if not isinstance(result_count, int):
                result_count = 0
            search_result_count += result_count
            search_queries.append(
                {
                    "search_query_id": str(item.search_query.id),
                    "query_text": item.search_query.query_text,
                    "provider": item.search_query.provider,
                    "result_count": result_count,
                    "candidates_added": item.candidates_added,
                    "duplicates_skipped": item.duplicates_skipped,
                    "filtered_out": item.filtered_out,
                    "unresponsive_engines": raw_payload.get("response_metadata", {}).get(
                        "unresponsive_engines", []
                    ),
                }
            )
        return {
            "search_queries": search_queries,
            "search_query_count": len(result.search_queries),
            "search_result_count": search_result_count,
            "candidate_urls_added": len(result.candidate_urls),
            "selected_sources": [
                _candidate_url_summary(candidate_url) for candidate_url in result.candidate_urls
            ],
            "duplicates_skipped": result.duplicates_skipped,
            "filtered_out": result.filtered_out,
        }

    def _run_fetch(self, task_id: UUID) -> dict[str, Any]:
        result = self.acquisition_service.acquire_candidates(
            task_id,
            candidate_url_ids=None,
            limit=self.fetch_limit,
            target_successful_snapshots=self.target_successful_snapshots,
        )
        stage_result = _acquisition_stage_result(result)
        if stage_result["fetch_succeeded"] <= 0:
            raise DebugPipelinePreconditionError(
                "fetch produced no successful content snapshots",
                details=stage_result,
            )
        warnings = []
        if stage_result["fetch_succeeded"] < MIN_SUCCESSFUL_SOURCES_WARNING_THRESHOLD:
            warnings.append(
                "fetch succeeded for fewer than 2 sources; the MVP flow continues, but report "
                "coverage may be weak."
            )
        stage_result["warnings"] = warnings
        return stage_result

    def _run_parse(self, task_id: UUID) -> dict[str, Any]:
        result = self.parsing_service.parse_snapshots(
            task_id,
            content_snapshot_ids=None,
            limit=self.parse_limit,
        )
        parse_decisions = [parse_entry_diagnostic(entry) for entry in result.entries]
        stage_result = {
            "created": result.created,
            "updated": result.updated,
            "skipped_existing": result.skipped_existing,
            "skipped_unsupported": result.skipped_unsupported,
            "failed": result.failed,
            "parse_decisions": parse_decisions,
        }
        if result.created + result.updated + result.skipped_existing <= 0:
            raise DebugPipelinePreconditionError(
                _format_parse_no_documents_message(parse_decisions),
                details=stage_result,
            )
        return stage_result

    def _run_index(self, task_id: UUID) -> dict[str, Any]:
        result = self.indexing_service.index_source_chunks(
            task_id,
            source_chunk_ids=None,
            limit=self.index_limit,
        )
        if not result.indexed_chunks:
            raise DebugPipelinePreconditionError("indexing found no source chunks to index")
        indexed_page = self.indexing_service.list_indexed_chunks(task_id, offset=0, limit=100)
        return {
            "indexed_count": len(result.indexed_chunks),
            "indexed_total": indexed_page.total,
        }

    def _run_draft_claims(self, task_id: UUID) -> dict[str, Any]:
        task = self._get_task(task_id)
        source_chunks = SourceChunkRepository(self.session).list_for_task(
            task_id,
            limit=self.claim_limit,
        )
        source_chunk_ids = [source_chunk.id for source_chunk in source_chunks]
        result = self.claims_service.draft_claims(
            task_id,
            query=task.query,
            source_chunk_ids=source_chunk_ids,
            limit=self.claim_limit,
        )
        if not result.entries:
            raise DebugPipelinePreconditionError("claim drafting produced no claims")
        return {
            "created_claims": result.created_claims,
            "reused_claims": result.reused_claims,
            "created_claim_evidence": result.created_claim_evidence,
            "reused_claim_evidence": result.reused_claim_evidence,
        }

    def _run_verify_claims(self, task_id: UUID) -> dict[str, Any]:
        result = self.claims_service.verify_claims(
            task_id,
            claim_ids=None,
            limit=self.claim_limit,
        )
        if not result.entries:
            raise DebugPipelinePreconditionError("claim verification found no draft claims")
        return {
            "verified_claims": result.verified_claims,
            "created_citation_spans": result.created_citation_spans,
            "reused_citation_spans": result.reused_citation_spans,
            "created_claim_evidence": result.created_claim_evidence,
            "reused_claim_evidence": result.reused_claim_evidence,
        }

    def _run_report(self, task_id: UUID) -> dict[str, Any]:
        result = self.reporting_service.generate_markdown_report(task_id)
        if not result.markdown.strip():
            raise DebugPipelinePreconditionError("report generation produced empty markdown")
        return {
            "report_artifact_id": result.artifact.id,
            "report_version": result.artifact.version,
            "supported_claims": result.supported_claims,
            "mixed_claims": result.mixed_claims,
            "unsupported_claims": result.unsupported_claims,
            "draft_claims": result.draft_claims,
            "report_markdown_preview": result.markdown[:500],
        }

    def _mark_started(self, task: ResearchTask) -> None:
        from_status = task.status
        task.started_at = task.started_at or datetime.now(UTC)
        self.task_repository.set_status(task, STATUS_RUNNING, ended_at=None)
        self._record_event(
            task.id,
            self._event_type("started"),
            {
                **self._pipeline_payload(from_status=from_status, to_status=STATUS_RUNNING),
                "stage": STATUS_RUNNING,
                "status_note": "synchronous pipeline run started",
            },
        )
        self.session.commit()

    def _mark_completed(self, task_id: UUID) -> ResearchTask:
        task = self._get_task(task_id)
        from_status = task.status
        self.task_repository.set_status(task, STATUS_COMPLETED, ended_at=datetime.now(UTC))
        self._record_event(
            task.id,
            self._event_type("completed"),
            {
                **self._pipeline_payload(from_status=from_status, to_status=STATUS_COMPLETED),
                "stage": STATUS_COMPLETED,
                "counts": _counts_to_dict(self._safe_counts(task.id)),
            },
        )
        self.session.commit()
        self.session.refresh(task)
        return task

    def _record_stage_started(self, task_id: UUID, stage: str) -> None:
        task = self._get_task(task_id)
        from_status = task.status
        self.task_repository.set_status(task, stage, ended_at=None)
        self._record_event(
            task_id,
            self._event_type("stage_started"),
            {
                **self._pipeline_payload(from_status=from_status, to_status=stage),
                "stage": stage,
            },
        )
        self.session.commit()

    def _record_stage_completed(
        self,
        task_id: UUID,
        stage: str,
        stage_result: dict[str, Any],
    ) -> None:
        task = self._get_task(task_id)
        self._record_event(
            task_id,
            self._event_type("stage_completed"),
            {
                **self._pipeline_payload(from_status=task.status, to_status=task.status),
                "stage": stage,
                "result": _json_safe(stage_result),
                "counts": _counts_to_dict(self._safe_counts(task_id)),
                "warnings": _stage_warnings(stage_result),
            },
        )
        self.session.commit()

    def _record_failure(self, task_id: UUID, failure: DebugPipelineFailure) -> None:
        task = self._get_task(task_id)
        from_status = task.status
        self.task_repository.set_status(task, STATUS_FAILED, ended_at=datetime.now(UTC))
        self._record_event(
            task_id,
            self._event_type("failed"),
            {
                **self._pipeline_payload(from_status=from_status, to_status=STATUS_FAILED),
                "stage": failure.stage,
                "reason": failure.reason,
                "exception": failure.exception,
                "message": failure.message,
                "next_action": failure.next_action,
                "counts": _counts_to_dict(failure.counts),
                "details": _json_safe(failure.details) if failure.details is not None else None,
            },
        )
        self.session.commit()

    def _record_event(
        self,
        task_id: UUID,
        event_type: str,
        payload_json: dict[str, Any],
    ) -> None:
        self.event_repository.record(
            task_id=task_id,
            event_type=event_type,
            payload_json=payload_json,
        )

    def _event_type(self, name: str) -> str:
        return f"{self.event_prefix}.{name}"

    def _pipeline_payload(self, *, from_status: str | None, to_status: str) -> dict[str, Any]:
        payload = build_task_event_payload(from_status=from_status, to_status=to_status)
        payload["source"] = self.event_source
        return payload

    def _safe_counts(self, task_id: UUID) -> DebugPipelineCounts:
        return collect_debug_pipeline_counts(
            self.session,
            task_id,
            indexed_chunk_counter=lambda: self.indexing_service.list_indexed_chunks(
                task_id,
                offset=0,
                limit=100,
            ),
        )

    def _get_task(self, task_id: UUID) -> ResearchTask:
        task = self.task_repository.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task


def collect_debug_pipeline_counts(
    session: Session,
    task_id: UUID,
    *,
    indexed_chunk_counter: Callable[[], IndexedChunkPage] | None = None,
) -> DebugPipelineCounts:
    indexed_chunks = 0
    if indexed_chunk_counter is not None:
        try:
            indexed_chunks = indexed_chunk_counter().total
        except Exception:  # noqa: BLE001 - counts must not mask the real pipeline failure.
            indexed_chunks = 0

    return DebugPipelineCounts(
        search_queries=len(SearchQueryRepository(session).list_for_task(task_id)),
        candidate_urls=len(CandidateUrlRepository(session).list_for_task(task_id)),
        fetch_attempts=len(FetchAttemptRepository(session).list_for_task(task_id)),
        content_snapshots=len(ContentSnapshotRepository(session).list_for_task(task_id)),
        source_documents=len(SourceDocumentRepository(session).list_for_task(task_id)),
        source_chunks=len(SourceChunkRepository(session).list_for_task(task_id)),
        indexed_chunks=indexed_chunks,
        claims=len(ClaimRepository(session).list_for_task(task_id)),
        claim_evidence=len(ClaimEvidenceRepository(session).list_for_task(task_id)),
        report_artifacts=len(ReportArtifactRepository(session).list_for_task(task_id)),
    )


def _counts_to_dict(counts: DebugPipelineCounts) -> dict[str, int]:
    return {
        "search_queries": counts.search_queries,
        "candidate_urls": counts.candidate_urls,
        "fetch_attempts": counts.fetch_attempts,
        "content_snapshots": counts.content_snapshots,
        "source_documents": counts.source_documents,
        "source_chunks": counts.source_chunks,
        "indexed_chunks": counts.indexed_chunks,
        "claims": counts.claims,
        "claim_evidence": counts.claim_evidence,
        "report_artifacts": counts.report_artifacts,
    }


def _candidate_url_summary(candidate_url: Any) -> dict[str, Any]:
    return {
        "candidate_url_id": str(candidate_url.id),
        "canonical_url": candidate_url.canonical_url,
        "domain": candidate_url.domain,
        "title": candidate_url.title,
        "rank": candidate_url.rank,
    }


def _fetch_entry_summary(entry: Any) -> dict[str, Any]:
    attempt = entry.fetch_attempt
    trace = attempt.trace_json if attempt is not None else {}
    if not isinstance(trace, dict):
        trace = {}
    trace_summary = _fetch_trace_summary(trace)
    return {
        **_candidate_url_summary(entry.candidate_url),
        "fetch_job_id": str(entry.fetch_job.id),
        "fetch_attempt_id": str(attempt.id) if attempt is not None else None,
        "attempted": True,
        "fetch_attempted": True,
        "status": entry.fetch_job.status,
        "fetch_status": entry.fetch_job.status,
        "http_status": attempt.http_status if attempt is not None else None,
        "error_code": attempt.error_code if attempt is not None else None,
        "error_reason": _fetch_error_reason(trace),
        "final_url": trace_summary.get("final_url"),
        "trace": trace_summary,
        "snapshot_id": (
            str(entry.content_snapshot.id) if entry.content_snapshot is not None else None
        ),
        "skipped_existing": entry.skipped_existing,
    }


def _acquisition_stage_result(result: Any) -> dict[str, Any]:
    attempted_sources = [_fetch_entry_summary(entry) for entry in result.entries]
    unattempted_sources = [
        _unattempted_candidate_summary(candidate_url)
        for candidate_url in result.unattempted_candidates
    ]
    failed_sources = [
        source for source in attempted_sources if source.get("fetch_status") == "FAILED"
    ]
    successful_sources = [
        source
        for source in attempted_sources
        if source.get("fetch_status") == "SUCCEEDED" and source.get("snapshot_id") is not None
    ]
    selected_sources = [*attempted_sources, *unattempted_sources]
    return {
        "created": result.created,
        "skipped_existing": result.skipped_existing,
        "succeeded": len(successful_sources),
        "failed": len(failed_sources),
        "fetch_succeeded": len(successful_sources),
        "fetch_failed": len(failed_sources),
        "content_snapshots": len(successful_sources),
        "selected_sources_from_search": [
            _candidate_url_summary(candidate_url)
            for candidate_url in result.selected_candidates_from_search
        ],
        "selected_sources": selected_sources,
        "attempted_sources": attempted_sources,
        "unattempted_sources": unattempted_sources,
        "failed_sources": failed_sources,
        "fetch_attempts_summary": selected_sources,
        "warnings": [],
    }


def _unattempted_candidate_summary(candidate_url: Any) -> dict[str, Any]:
    return {
        **_candidate_url_summary(candidate_url),
        "fetch_job_id": None,
        "fetch_attempt_id": None,
        "attempted": False,
        "fetch_attempted": False,
        "status": "UNATTEMPTED",
        "fetch_status": "UNATTEMPTED",
        "http_status": None,
        "error_code": None,
        "error_reason": None,
        "final_url": None,
        "trace": {},
        "snapshot_id": None,
        "skipped_existing": False,
    }


def _fetch_trace_summary(trace: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in (
        "requested_url",
        "final_url",
        "exception_type",
        "message",
        "resolved_ips",
        "proxy_enabled",
        "proxy_source",
        "proxy_url_masked",
        "decision_reason",
        "safety_warning",
    ):
        value = trace.get(key)
        if value is not None:
            summary[key] = value
    return summary


def _fetch_error_reason(trace: dict[str, Any]) -> str | None:
    for key in ("message", "reason"):
        value = trace.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    storage_error = trace.get("storage_error")
    if isinstance(storage_error, dict):
        value = storage_error.get("message")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _format_parse_no_documents_message(parse_decisions: list[dict[str, Any]]) -> str:
    if not parse_decisions:
        return "parse produced no source documents; no content snapshots were selected."

    formatted_decisions = []
    for decision in parse_decisions:
        formatted_decisions.append(
            "snapshot_id={snapshot_id} canonical_url={canonical_url} "
            "mime_type={mime_type} storage_bucket={storage_bucket} "
            "storage_key={storage_key} snapshot_bytes={snapshot_bytes} "
            "body_length={body_length} decision={decision} parser_error={parser_error}".format(
                snapshot_id=decision.get("snapshot_id"),
                canonical_url=decision.get("canonical_url"),
                mime_type=decision.get("mime_type"),
                storage_bucket=decision.get("storage_bucket"),
                storage_key=decision.get("storage_key"),
                snapshot_bytes=decision.get("snapshot_bytes"),
                body_length=decision.get("body_length"),
                decision=decision.get("decision"),
                parser_error=decision.get("parser_error") or "n/a",
            )
        )

    return "parse produced no source documents; parse decisions: " + " | ".join(formatted_decisions)


def _stage_warnings(stage_result: dict[str, Any]) -> list[str]:
    warnings = stage_result.get("warnings", [])
    if not isinstance(warnings, list):
        return []
    return [item for item in warnings if isinstance(item, str) and item.strip()]


def _classify_failure(error: Exception) -> str:
    if isinstance(error, DebugPipelinePreconditionError):
        return "pipeline_precondition_failed"
    if isinstance(error, SearchProviderError):
        return error.reason
    if isinstance(error, TaskNotFoundError):
        return "task_not_found"
    return "stage_exception"


def _next_action_for_failure(*, stage: str, error: Exception) -> str:
    if isinstance(error, DebugPipelinePreconditionError):
        if stage == STAGE_SEARCHING:
            return (
                "Check SEARCH_PROVIDER and SEARXNG_BASE_URL. For development smoke only, "
                "set SEARCH_PROVIDER=smoke; for real search, point SEARXNG_BASE_URL at a "
                "SearXNG endpoint that returns JSON from /search?format=json."
            )
        if stage == STAGE_ACQUIRING:
            return (
                "Check that candidate URLs are public HTTP/HTTPS pages and pass the acquisition "
                "SSRF policy. Inspect fetch attempts for per-URL error_code values."
            )
        if stage == STAGE_PARSING:
            return (
                "Check that fetched snapshots are text/html or text/plain and still exist "
                "in storage."
            )
        if stage == STAGE_INDEXING:
            return (
                "Check INDEX_BACKEND and OpenSearch reachability. For development smoke only, "
                "set INDEX_BACKEND=local."
            )
        if stage == STAGE_DRAFTING_CLAIMS:
            return "Check that source_chunks are non-empty before claim drafting."
        if stage == STAGE_VERIFYING:
            return "Check that draft claims exist and retrieval can read indexed chunks."
        if stage == STAGE_REPORTING:
            return "Check report object storage configuration and claim/evidence ledger rows."
    if stage == STAGE_SEARCHING:
        return (
            "Verify SEARCH_PROVIDER and SEARXNG_BASE_URL. The configured endpoint must be a "
            "SearXNG-compatible API, not the frontend server."
        )
    if stage == STAGE_INDEXING or stage == STAGE_VERIFYING:
        return (
            "Verify INDEX_BACKEND. Use OpenSearch for real retrieval or INDEX_BACKEND=local "
            "for explicitly marked development smoke runs."
        )
    return "Inspect the task events, server logs, and intermediate ledger endpoints for this task."


def _json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    safe_payload: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, UUID):
            safe_payload[key] = str(value)
        else:
            safe_payload[key] = value
    return safe_payload
