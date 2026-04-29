from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from packages.db.models import CandidateUrl, ResearchTask, SourceChunk, SourceDocument
from packages.db.repositories import (
    CandidateUrlRepository,
    ClaimEvidenceRepository,
    ClaimRepository,
    ContentSnapshotRepository,
    FetchAttemptRepository,
    FetchJobRepository,
    ReportArtifactRepository,
    ResearchTaskRepository,
    SearchQueryRepository,
    SourceChunkRepository,
    SourceDocumentRepository,
    TaskEventRepository,
)
from services.orchestrator.app.claims import (
    classify_query_intent,
    is_answer_relevant_score,
    iter_supporting_spans,
    score_claim_statement,
)
from services.orchestrator.app.indexing import IndexedChunkPage
from services.orchestrator.app.planning import (
    ResearchPlan,
    ResearchPlannerError,
    ResearchPlannerService,
)
from services.orchestrator.app.research_quality import (
    SourceYieldSummary,
    answer_slot_coverage,
    build_slot_coverage_summary,
    classify_source_intent,
    contribution_level_for_counts,
    normalize_dropped_reasons,
    source_intent_priority,
    summarize_evidence_yield,
)
from services.orchestrator.app.search import SearchProviderError
from services.orchestrator.app.services.acquisition import (
    AcquisitionService,
    fetch_priority_metadata,
)
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
ACQUISITION_ALLOWED_STATUSES = ("PLANNED", STAGE_ACQUIRING, STAGE_DRAFTING_CLAIMS)
PARSING_ALLOWED_STATUSES = ("PLANNED", STAGE_PARSING, STAGE_DRAFTING_CLAIMS)
INDEXING_ALLOWED_STATUSES = ("PLANNED", STAGE_INDEXING, STAGE_DRAFTING_CLAIMS)
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


@dataclass(frozen=True)
class _MergedDraftResult:
    created_claims: int
    reused_claims: int
    created_citation_spans: int
    reused_citation_spans: int
    created_claim_evidence: int
    reused_claim_evidence: int
    entries: list[Any]
    diagnostics: dict[str, object]


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
        planner_service: ResearchPlannerService | None = None,
        dependencies: dict[str, Any],
        fetch_limit: int = 3,
        parse_limit: int = 3,
        index_limit: int = 10,
        claim_limit: int = 5,
        event_source: str = PIPELINE_EVENT_SOURCE,
        event_prefix: str = PIPELINE_EVENT_PREFIX,
        target_successful_snapshots: int = MIN_SUCCESSFUL_SOURCES_WARNING_THRESHOLD,
        min_answer_sources: int = 3,
        max_supplemental_sources: int = 3,
    ) -> None:
        self.session = session
        self.search_service = search_service
        self.acquisition_service = acquisition_service
        self.parsing_service = parsing_service
        self.indexing_service = indexing_service
        self.claims_service = claims_service
        self.reporting_service = reporting_service
        self.planner_service = planner_service
        self.research_plan: ResearchPlan | None = None
        self.dependencies = dependencies
        self.fetch_limit = fetch_limit
        self.parse_limit = parse_limit
        self.index_limit = index_limit
        self.claim_limit = claim_limit
        self.event_source = event_source
        self.event_prefix = event_prefix
        self.target_successful_snapshots = target_successful_snapshots
        self.min_answer_sources = max(1, min_answer_sources)
        self.max_supplemental_sources = max(0, max_supplemental_sources)
        self.supplemental_acquisition_ran = False
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
        self._run_planner_if_configured(task.id)

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
        result = self.search_service.discover_candidates(
            task_id,
            planned_search_queries=(
                self.research_plan.search_queries if self.research_plan is not None else None
            ),
        )
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
            "research_plan_used": self.research_plan is not None,
            "raw_planner_queries": (
                list(self.research_plan.raw_planner_queries)
                if self.research_plan is not None
                else []
            ),
            "final_search_queries": (
                list(self.research_plan.final_search_queries)
                if self.research_plan is not None
                else []
            ),
            "dropped_or_downweighted_planner_queries": (
                list(self.research_plan.dropped_or_downweighted_planner_queries)
                if self.research_plan is not None
                else []
            ),
            "planner_guardrail_warnings": (
                list(self.research_plan.planner_guardrail_warnings)
                if self.research_plan is not None
                else []
            ),
            "intent_classification": (
                self.research_plan.intent_classification if self.research_plan is not None else None
            ),
            "extracted_entity": (
                self.research_plan.extracted_entity if self.research_plan is not None else None
            ),
            "candidate_urls_added": len(result.candidate_urls),
            "selected_sources": [
                _candidate_url_summary(candidate_url, query=result.task.query)
                for candidate_url in result.candidate_urls
            ],
            "duplicates_skipped": result.duplicates_skipped,
            "filtered_out": result.filtered_out,
        }

    def _run_planner_if_configured(self, task_id: UUID) -> None:
        if self.planner_service is None:
            return
        task = self._get_task(task_id)
        try:
            plan = self.planner_service.plan(
                task_id=task_id,
                query=task.query,
                constraints=dict(task.constraints_json),
            )
        except ResearchPlannerError as error:
            self._record_event(
                task_id,
                "research_plan.failed",
                {
                    **self._pipeline_payload(from_status=task.status, to_status=task.status),
                    "planner_enabled": True,
                    "planner_status": "failed",
                    "fallback": "original_query",
                    "reason": error.reason,
                    "details": _json_safe(error.to_payload()),
                    "warnings": ["research planner failed; continuing with the original query."],
                },
            )
            self.session.commit()
            return

        self.research_plan = plan
        self._record_event(
            task_id,
            "research_plan.created",
            {
                **self._pipeline_payload(from_status=task.status, to_status=task.status),
                "planner_enabled": True,
                "planner_status": "created",
                "planner_mode": plan.planner_mode,
                "result": {
                    "research_plan": plan.to_payload(),
                    **plan.summary_payload(),
                },
                "warnings": plan.warnings,
            },
        )
        self.session.commit()

    def _run_fetch(self, task_id: UUID) -> dict[str, Any]:
        target_successful_snapshots = self.target_successful_snapshots
        fetch_limit = self.fetch_limit
        if self._is_planner_overview_run():
            target_successful_snapshots = max(
                target_successful_snapshots,
                self.min_answer_sources,
            )
            fetch_limit = max(fetch_limit, 4)
        result = self.acquisition_service.acquire_candidates(
            task_id,
            candidate_url_ids=None,
            limit=fetch_limit,
            target_successful_snapshots=target_successful_snapshots,
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
        parse_limit = (
            max(self.parse_limit, 4) if self._is_planner_overview_run() else self.parse_limit
        )
        result = self.parsing_service.parse_snapshots(
            task_id,
            content_snapshot_ids=None,
            limit=parse_limit,
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
        source_chunk_ids = _select_claim_drafting_chunk_ids(
            self.session,
            task_id,
            query=task.query,
            limit=max(self.claim_limit, 8),
        )
        result = self.claims_service.draft_claims(
            task_id,
            query=task.query,
            source_chunk_ids=source_chunk_ids,
            limit=self.claim_limit,
        )
        supplemental_result: dict[str, Any] = {
            "triggered": False,
            "reason": None,
            "attempted_sources": [],
            "skipped_sources": [],
        }
        trigger_reason = _supplemental_trigger_reason(result, query=task.query)
        if trigger_reason is not None and not self.supplemental_acquisition_ran:
            supplemental_result = self._run_supplemental_acquisition(
                task_id,
                query=task.query,
                reason=trigger_reason,
            )
            if supplemental_result["attempted_sources"]:
                refreshed_chunk_ids = _select_claim_drafting_chunk_ids(
                    self.session,
                    task_id,
                    query=task.query,
                    limit=max(self.claim_limit, 8),
                )
                second_result = self.claims_service.draft_claims(
                    task_id,
                    query=task.query,
                    source_chunk_ids=refreshed_chunk_ids,
                    limit=self.claim_limit,
                )
                result = _merge_draft_results(result, second_result)

        evidence_candidates = _evidence_candidates_from_diagnostics(result.diagnostics)
        accepted_candidate_ids = _accepted_evidence_candidate_ids_from_entries(result.entries)
        evidence_yield_summary = summarize_evidence_yield(
            evidence_candidates,
            accepted_candidate_ids=accepted_candidate_ids,
            query=task.query,
        )
        source_yield_summary = _build_source_yield_summary(
            self.session,
            task_id,
            query=task.query,
            evidence_candidates=evidence_candidates,
            accepted_candidate_ids=accepted_candidate_ids,
        )
        answer_yield = _build_answer_yield_metrics(
            self.session,
            task_id,
            query=task.query,
            evidence_candidates=evidence_candidates,
            accepted_candidate_ids=accepted_candidate_ids,
        )
        category_coverage = _claim_category_coverage_from_entries(result.entries)
        slot_coverage_summary = build_slot_coverage_summary(
            task.query,
            evidence_candidates=evidence_candidates,
            claim_rows=_claim_rows_for_slot_summary_from_entries(result.entries),
        )
        diagnostics = {
            **result.diagnostics,
            "answer_yield": answer_yield,
            "answer_slots": answer_slot_coverage(task.query, category_coverage),
            "report_slot_coverage": answer_slot_coverage(task.query, category_coverage),
            "evidence_yield_summary": evidence_yield_summary,
            "source_yield_summary": source_yield_summary,
            "slot_coverage_summary": slot_coverage_summary,
            "dropped_sources": _dropped_sources_from_yield(source_yield_summary),
            "accepted_claims_by_category": _accepted_claims_by_category(result.entries),
            "rejected_claims_by_rule": result.diagnostics.get(
                "rejection_reason_distribution",
                {},
            ),
            "category_coverage_missing": _missing_expected_categories(
                task.query,
                category_coverage,
            ),
            "supplemental_acquisition": supplemental_result,
        }
        if not result.entries:
            failure_details = _build_claim_failure_details(
                self.session,
                task_id,
                query=task.query,
                diagnostics=diagnostics,
                supplemental_result=supplemental_result,
                trigger_reason=trigger_reason,
            )
            raise DebugPipelinePreconditionError(
                "claim drafting produced no claims",
                details=failure_details,
            )
        return {
            "created_claims": result.created_claims,
            "reused_claims": result.reused_claims,
            "created_claim_evidence": result.created_claim_evidence,
            "reused_claim_evidence": result.reused_claim_evidence,
            "diagnostics": diagnostics,
            "answer_yield": answer_yield,
            "answer_coverage": {
                category: category in category_coverage
                for category in ("definition", "mechanism", "privacy", "feature")
            },
            "answer_slots": answer_slot_coverage(task.query, category_coverage),
            "report_slot_coverage": answer_slot_coverage(task.query, category_coverage),
            "slot_coverage_summary": slot_coverage_summary,
            "evidence_yield_summary": evidence_yield_summary,
            "source_yield_summary": source_yield_summary,
            "dropped_sources": _dropped_sources_from_yield(source_yield_summary),
            "accepted_claims_by_category": _accepted_claims_by_category(result.entries),
            "category_coverage_missing": _missing_expected_categories(
                task.query,
                category_coverage,
            ),
            "supplemental_acquisition": supplemental_result,
        }

    def _run_supplemental_acquisition(
        self,
        task_id: UUID,
        *,
        query: str,
        reason: str,
    ) -> dict[str, Any]:
        self.supplemental_acquisition_ran = True
        selected_candidates, skipped_sources = _select_supplemental_candidates(
            self.session,
            task_id,
            query=query,
            limit=self.max_supplemental_sources,
        )
        supplemental_result: dict[str, Any] = {
            "triggered": True,
            "reason": reason,
            "attempted_sources": [],
            "skipped_sources": skipped_sources,
            "parse_decisions": [],
            "indexed_count": 0,
        }
        if not selected_candidates:
            return supplemental_result

        acquisition = self.acquisition_service.acquire_candidates(
            task_id,
            candidate_url_ids=[candidate.id for candidate in selected_candidates],
            limit=len(selected_candidates),
            target_successful_snapshots=None,
        )
        acquisition_summary = _acquisition_stage_result(acquisition)
        attempted_sources = []
        for source in acquisition_summary["attempted_sources"]:
            source = dict(source)
            source["selected_by"] = "supplemental_acquisition"
            attempted_sources.append(source)
        supplemental_result["attempted_sources"] = attempted_sources

        snapshot_ids = [
            entry.content_snapshot.id for entry in acquisition.entries if entry.content_snapshot
        ]
        if not snapshot_ids:
            return supplemental_result

        parse_result = self.parsing_service.parse_snapshots(
            task_id,
            content_snapshot_ids=snapshot_ids,
            limit=len(snapshot_ids),
        )
        parse_decisions = [parse_entry_diagnostic(entry) for entry in parse_result.entries]
        supplemental_result["parse_decisions"] = parse_decisions
        source_chunk_ids: list[UUID] = []
        for entry in parse_result.entries:
            if entry.source_document is None:
                continue
            for source_chunk in entry.source_document.chunks:
                source_chunk_ids.append(source_chunk.id)
        if not source_chunk_ids:
            return supplemental_result

        index_result = self.indexing_service.index_source_chunks(
            task_id,
            source_chunk_ids=source_chunk_ids,
            limit=len(source_chunk_ids),
        )
        supplemental_result["indexed_count"] = len(index_result.indexed_chunks)
        return supplemental_result

    def _run_verify_claims(self, task_id: UUID) -> dict[str, Any]:
        result = self.claims_service.verify_claims(
            task_id,
            claim_ids=None,
            limit=self.claim_limit,
        )
        if not result.entries:
            raise DebugPipelinePreconditionError("claim verification found no draft claims")
        verification_summary = _build_verification_summary(result.entries)
        task = self._get_task(task_id)
        slot_coverage_summary = build_slot_coverage_summary(
            task.query,
            evidence_candidates=_evidence_candidates_from_claims(self.session, task_id),
            claim_rows=_claim_rows_for_slot_summary_from_claims(self.session, task_id),
        )
        return {
            "verified_claims": result.verified_claims,
            "created_citation_spans": result.created_citation_spans,
            "reused_citation_spans": result.reused_citation_spans,
            "created_claim_evidence": result.created_claim_evidence,
            "reused_claim_evidence": result.reused_claim_evidence,
            "verification_summary": verification_summary,
            "slot_coverage_summary": slot_coverage_summary,
        }

    def _run_report(self, task_id: UUID) -> dict[str, Any]:
        result = self.reporting_service.generate_markdown_report(task_id)
        if not result.markdown.strip():
            raise DebugPipelinePreconditionError("report generation produced empty markdown")
        source_quality_summary = _build_source_quality_summary(self.session, task_id)
        manifest = result.artifact.manifest_json or {}
        return {
            "report_artifact_id": result.artifact.id,
            "report_version": result.artifact.version,
            "supported_claims": result.supported_claims,
            "mixed_claims": result.mixed_claims,
            "unsupported_claims": result.unsupported_claims,
            "draft_claims": result.draft_claims,
            "report_markdown_preview": result.markdown[:500],
            "source_quality_summary": source_quality_summary,
            "slot_coverage_summary": manifest.get("slot_coverage_summary", []),
            "source_yield_summary": manifest.get("source_yield_summary", []),
            "evidence_yield_summary": manifest.get("evidence_yield_summary", {}),
            "verification_summary": manifest.get("verification_summary", {}),
            "dropped_sources": manifest.get("dropped_sources", []),
            "warnings": source_quality_summary["warnings"],
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

    def _is_planner_overview_run(self) -> bool:
        return bool(
            self.research_plan is not None
            and self.research_plan.intent_classification == "overview_definition_intent"
        )


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


def _candidate_url_summary(candidate_url: Any, *, query: str | None = None) -> dict[str, Any]:
    return {
        "candidate_url_id": str(candidate_url.id),
        "canonical_url": candidate_url.canonical_url,
        "domain": candidate_url.domain,
        "title": candidate_url.title,
        "rank": candidate_url.rank,
        **fetch_priority_metadata(candidate_url, query=query),
    }


def _fetch_entry_summary(entry: Any, *, query: str | None = None) -> dict[str, Any]:
    attempt = entry.fetch_attempt
    trace = attempt.trace_json if attempt is not None else {}
    if not isinstance(trace, dict):
        trace = {}
    trace_summary = _fetch_trace_summary(trace)
    return {
        **_candidate_url_summary(entry.candidate_url, query=query),
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
    task_query = result.task.query
    attempted_sources = [_fetch_entry_summary(entry, query=task_query) for entry in result.entries]
    unattempted_sources = [
        _unattempted_candidate_summary(candidate_url, query=task_query)
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
    dropped_sources = [
        {**source, "dropped_reasons": [_fetch_dropped_reason(source)]}
        for source in [*failed_sources, *unattempted_sources]
    ]
    return {
        "created": result.created,
        "skipped_existing": result.skipped_existing,
        "succeeded": len(successful_sources),
        "failed": len(failed_sources),
        "fetch_succeeded": len(successful_sources),
        "fetch_failed": len(failed_sources),
        "content_snapshots": len(successful_sources),
        "selected_sources_from_search": [
            _candidate_url_summary(candidate_url, query=task_query)
            for candidate_url in result.selected_candidates_from_search
        ],
        "selected_sources": selected_sources,
        "attempted_sources": attempted_sources,
        "unattempted_sources": unattempted_sources,
        "dropped_sources": dropped_sources,
        "failed_sources": failed_sources,
        "fetch_attempts_summary": selected_sources,
        "source_selection_guardrail_applied": any(
            source.get("source_selection_guardrail_applied") for source in selected_sources
        ),
        "warnings": [],
    }


def _unattempted_candidate_summary(
    candidate_url: Any,
    *,
    query: str | None = None,
) -> dict[str, Any]:
    return {
        **_candidate_url_summary(candidate_url, query=query),
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


def _build_source_quality_summary(session: Session, task_id: UUID) -> dict[str, Any]:
    source_documents = SourceDocumentRepository(session).list_for_task(task_id)
    source_chunks = SourceChunkRepository(session).list_for_task(task_id)
    claim_evidence = ClaimEvidenceRepository(session).list_for_task(task_id)

    high_quality_source_ids = {
        source_document.id
        for source_document in source_documents
        if (source_document.final_source_score or 0.0) >= 0.65
    }
    evidence_domains: set[str] = set()
    high_quality_evidence_domains: set[str] = set()
    for evidence in claim_evidence:
        source_document = evidence.citation_span.source_chunk.source_document
        evidence_domains.add(source_document.domain)
        if source_document.id in high_quality_source_ids:
            high_quality_evidence_domains.add(source_document.domain)

    low_quality_sources = [
        source_document
        for source_document in source_documents
        if (source_document.final_source_score or 0.0) < 0.35
    ]
    redirect_stub_sources = [
        source_chunk.source_document_id
        for source_chunk in source_chunks
        if (source_chunk.metadata_json or {}).get("reason") == "redirect_stub"
    ]
    excluded_chunks = [
        source_chunk
        for source_chunk in source_chunks
        if not _chunk_metadata_eligible_for_claims(source_chunk.metadata_json or {})
    ]

    warnings: list[str] = []
    if len(high_quality_evidence_domains) == 1:
        warnings.append("Only one high-quality evidence domain was used")
    elif not high_quality_evidence_domains and evidence_domains:
        warnings.append("No high-quality evidence domain was used")
    if redirect_stub_sources:
        warnings.append("Some fetched pages were redirect stubs")
    if excluded_chunks:
        warnings.append("Some chunks were excluded as boilerplate/reference content")

    return {
        "source_count": len(source_documents),
        "high_quality_source_count": len(high_quality_source_ids),
        "evidence_domain_count": len(evidence_domains),
        "high_quality_evidence_domain_count": len(high_quality_evidence_domains),
        "low_quality_sources_skipped_count": len(low_quality_sources),
        "excluded_chunk_count": len(excluded_chunks),
        "redirect_stub_source_count": len(set(redirect_stub_sources)),
        "warnings": warnings,
    }


def _chunk_metadata_eligible_for_claims(metadata: dict[str, Any]) -> bool:
    if metadata.get("eligible_for_claims") is False:
        return False
    if metadata.get("should_generate_claims") is False:
        return False
    if metadata.get("is_reference_section") is True:
        return False
    if metadata.get("is_navigation_noise") is True:
        return False
    if metadata.get("reason") == "redirect_stub":
        return False
    quality_score = metadata.get("content_quality_score")
    return not isinstance(quality_score, int | float) or quality_score >= 0.3


def _merge_draft_results(first: Any, second: Any) -> _MergedDraftResult:
    return _MergedDraftResult(
        created_claims=int(first.created_claims) + int(second.created_claims),
        reused_claims=int(first.reused_claims) + int(second.reused_claims),
        created_citation_spans=int(first.created_citation_spans)
        + int(second.created_citation_spans),
        reused_citation_spans=int(first.reused_citation_spans) + int(second.reused_citation_spans),
        created_claim_evidence=int(first.created_claim_evidence)
        + int(second.created_claim_evidence),
        reused_claim_evidence=int(first.reused_claim_evidence) + int(second.reused_claim_evidence),
        entries=[*first.entries, *second.entries],
        diagnostics={
            **dict(second.diagnostics),
            "initial_diagnostics": dict(first.diagnostics),
            "post_supplemental_diagnostics": dict(second.diagnostics),
        },
    )


def _select_claim_drafting_chunk_ids(
    session: Session,
    task_id: UUID,
    *,
    query: str,
    limit: int,
) -> list[UUID]:
    chunks = SourceChunkRepository(session).list_for_task(task_id)
    ordered_chunks = sorted(
        chunks,
        key=lambda chunk: (
            _source_document_category_priority(chunk.source_document, query=query),
            0 if _chunk_metadata_eligible_for_claims(chunk.metadata_json or {}) else 1,
            -_numeric_metadata_score(chunk.metadata_json or {}, "content_quality_score"),
            chunk.source_document.fetched_at,
            str(chunk.source_document_id),
            chunk.chunk_no,
        ),
    )
    return [chunk.id for chunk in ordered_chunks[:limit]]


def _source_document_category_priority(source_document: SourceDocument, *, query: str) -> int:
    category = _source_document_category(source_document)
    return source_intent_priority(category, query=query)


def _source_document_category(source_document: SourceDocument) -> str:
    return classify_source_intent(
        canonical_url=source_document.canonical_url,
        domain=source_document.domain,
        title=source_document.title,
    ).source_category


def _numeric_metadata_score(metadata: dict[str, Any], key: str) -> float:
    value = metadata.get(key)
    return float(value) if isinstance(value, int | float) else 0.0


def _supplemental_trigger_reason(result: Any, *, query: str) -> str | None:
    coverage = _claim_category_coverage_from_entries(result.entries)
    if not result.entries:
        return "no_claims_generated"
    if len(result.entries) <= 1 and _missing_expected_categories(query, coverage):
        return "insufficient_answer_category_coverage"
    lower = query.lower()
    if any(term in lower for term in ("privacy", "private", "tracking", "no tracking")):
        if "privacy" not in coverage:
            return "missing_privacy_coverage"
    return None


def _claim_category_coverage_from_entries(entries: list[Any]) -> set[str]:
    coverage: set[str] = set()
    for entry in entries:
        notes = entry.claim.notes_json if entry.claim is not None else {}
        category = notes.get("claim_category") if isinstance(notes, dict) else None
        if isinstance(category, str) and category.strip():
            coverage.add(category.strip())
    return coverage


def _accepted_claims_by_category(entries: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for category in _claim_category_coverage_from_entries(entries):
        counts[category] = sum(
            1
            for entry in entries
            if isinstance(entry.claim.notes_json, dict)
            and entry.claim.notes_json.get("claim_category") == category
        )
    return dict(sorted(counts.items()))


def _missing_expected_categories(query: str, coverage: set[str]) -> list[str]:
    intent = classify_query_intent(query)
    expected = list(intent.expected_claim_types)
    if intent.intent_name == "definition_mechanism":
        expected = ["definition", "mechanism"]
    if any(term in query.lower() for term in ("privacy", "private", "tracking", "no tracking")):
        expected.append("privacy")
    return [category for category in dict.fromkeys(expected) if category not in coverage]


def _select_supplemental_candidates(
    session: Session,
    task_id: UUID,
    *,
    query: str,
    limit: int,
) -> tuple[list[CandidateUrl], list[dict[str, Any]]]:
    if limit <= 0:
        return [], []
    all_candidates = CandidateUrlRepository(session).list_for_task(task_id)
    attempted_candidate_ids = {
        fetch_job.candidate_url_id
        for fetch_job in FetchJobRepository(session).list_for_task(task_id)
    }
    preferred_categories = {
        "official_about",
        "wikipedia_reference",
        "official_home",
        "github_readme_or_repo",
        "official_docs_reference",
        "generic_article",
    }
    if _query_asks_deployment(query):
        preferred_categories.add("official_installation_admin")
    blocked_for_overview = {"forum_social_video", "low_quality_or_blocked"}
    if _is_overview_query(query) and not _query_asks_deployment(query):
        blocked_for_overview.update(
            {
                "official_architecture_admin",
                "official_installation_admin",
                "official_api_dev",
            }
        )
    selected: list[CandidateUrl] = []
    skipped: list[dict[str, Any]] = []
    eligible: list[tuple[int, int, CandidateUrl]] = []
    for candidate in all_candidates:
        metadata = fetch_priority_metadata(candidate, query=query)
        category = str(metadata.get("source_category") or "")
        if candidate.id in attempted_candidate_ids:
            skipped.append(
                {
                    **_candidate_url_summary(candidate, query=query),
                    "skip_reason": "already_attempted",
                }
            )
            continue
        if category in blocked_for_overview:
            skipped.append(
                {
                    **_candidate_url_summary(candidate, query=query),
                    "skip_reason": "low_priority_for_overview_supplemental_acquisition",
                }
            )
            continue
        if category not in preferred_categories:
            skipped.append(
                {
                    **_candidate_url_summary(candidate, query=query),
                    "skip_reason": "not_a_high_value_supplemental_source",
                }
            )
            continue
        score = metadata.get("fetch_priority_score")
        priority_score = int(score) if isinstance(score, int) else 50
        eligible.append((priority_score, candidate.rank, candidate))
    for _, _, candidate in sorted(eligible, key=lambda item: (item[0], item[1], str(item[2].id))):
        if len(selected) >= limit:
            skipped.append(
                {
                    **_candidate_url_summary(candidate, query=query),
                    "skip_reason": "supplemental_acquisition_limit_reached",
                }
            )
            continue
        selected.append(candidate)
    return selected, skipped


def _build_answer_yield_metrics(
    session: Session,
    task_id: UUID,
    *,
    query: str,
    evidence_candidates: list[dict[str, Any]] | None = None,
    accepted_candidate_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    source_documents = SourceDocumentRepository(session).list_for_task(task_id)
    source_chunks = SourceChunkRepository(session).list_for_task(task_id)
    chunks_by_document: dict[UUID, list[SourceChunk]] = {}
    for source_chunk in source_chunks:
        chunks_by_document.setdefault(source_chunk.source_document_id, []).append(source_chunk)

    claims = ClaimRepository(session).list_for_task(task_id)
    accepted_by_document: dict[str, list[str]] = {}
    for claim in claims:
        notes = claim.notes_json or {}
        source_document_id = notes.get("source_document_id")
        category = notes.get("claim_category")
        if isinstance(source_document_id, str) and isinstance(category, str):
            accepted_by_document.setdefault(source_document_id, []).append(category)

    metrics: list[dict[str, Any]] = []
    for source_document in source_documents:
        chunks = chunks_by_document.get(source_document.id, [])
        extracted_text_length = _source_extracted_text_length(chunks)
        eligible_chunks = [
            chunk
            for chunk in chunks
            if _chunk_metadata_eligible_for_claims(chunk.metadata_json or {})
        ]
        candidate_sentence_count = 0
        answer_relevant_candidate_count = 0
        rejected_count = 0
        answer_categories: set[str] = set(accepted_by_document.get(str(source_document.id), []))
        for chunk in chunks:
            for span in iter_supporting_spans(chunk.text):
                candidate_sentence_count += 1
                score = score_claim_statement(
                    statement=span.excerpt,
                    query=query,
                    content_quality_score=_numeric_metadata_score(
                        chunk.metadata_json or {},
                        "content_quality_score",
                    ),
                    source_quality_score=source_document.final_source_score,
                )
                if is_answer_relevant_score(score, query=query):
                    answer_relevant_candidate_count += 1
                    answer_categories.add(score.claim_category)
        if evidence_candidates is not None:
            source_candidates = [
                item
                for item in evidence_candidates
                if item.get("source_document_id") == str(source_document.id)
            ]
            if source_candidates:
                candidate_sentence_count = len(source_candidates)
                answer_relevant_candidate_count = sum(
                    1
                    for item in source_candidates
                    if item.get("metadata", {}).get("answer_relevant") is True
                )
                rejected_count = sum(
                    1 for item in source_candidates if item.get("rejection_reasons")
                )
        accepted_categories = accepted_by_document.get(str(source_document.id), [])
        accepted_evidence_count = len(accepted_categories)
        if accepted_candidate_ids:
            accepted_evidence_count = sum(
                1
                for item in evidence_candidates or []
                if item.get("source_document_id") == str(source_document.id)
                and item.get("evidence_candidate_id") in accepted_candidate_ids
            )
        low_yield_reason = _low_yield_reason(
            extracted_text_length=extracted_text_length,
            chunk_count=len(chunks),
            eligible_chunk_count=len(eligible_chunks),
            candidate_sentence_count=candidate_sentence_count,
            answer_relevant_candidate_count=answer_relevant_candidate_count,
            answer_category_coverage=answer_categories,
        )
        dropped_reasons = _dropped_reasons_for_parsed_source(
            low_yield_reason=low_yield_reason,
            candidate_count=candidate_sentence_count,
            answer_relevant_candidate_count=answer_relevant_candidate_count,
            accepted_evidence_count=accepted_evidence_count,
            rejected_count=rejected_count,
        )
        metrics.append(
            {
                "source_document_id": str(source_document.id),
                "canonical_url": source_document.canonical_url,
                "domain": source_document.domain,
                "title": source_document.title,
                "source_category": _source_document_category(source_document),
                "source_intent": _source_document_category(source_document),
                "source_quality_score": source_document.final_source_score,
                "extracted_text_length": extracted_text_length,
                "chunk_count": len(chunks),
                "eligible_chunk_count": len(eligible_chunks),
                "candidate_sentence_count": candidate_sentence_count,
                "candidate_count": candidate_sentence_count,
                "answer_relevant_candidate_count": answer_relevant_candidate_count,
                "accepted_claim_candidate_count": len(accepted_categories),
                "accepted_evidence_count": accepted_evidence_count,
                "claim_count": len(accepted_categories),
                "rejected_count": rejected_count,
                "answer_category_coverage": sorted(answer_categories),
                "answer_slot_coverage": answer_slot_coverage(query, answer_categories),
                "low_yield_reason": low_yield_reason,
                "dropped_reasons": list(dropped_reasons),
                "contribution_level": contribution_level_for_counts(
                    accepted_evidence_count=accepted_evidence_count,
                    claim_count=len(accepted_categories),
                    candidate_count=candidate_sentence_count,
                ),
            }
        )
    return metrics


def _build_source_yield_summary(
    session: Session,
    task_id: UUID,
    *,
    query: str,
    evidence_candidates: list[dict[str, Any]],
    accepted_candidate_ids: set[str],
) -> list[dict[str, Any]]:
    candidate_urls = CandidateUrlRepository(session).list_for_task(task_id)
    fetch_jobs = FetchJobRepository(session).list_for_task(task_id)
    fetch_attempts = FetchAttemptRepository(session).list_for_task(task_id)
    content_snapshots = ContentSnapshotRepository(session).list_for_task(task_id)
    source_documents = SourceDocumentRepository(session).list_for_task(task_id)
    source_chunks = SourceChunkRepository(session).list_for_task(task_id)
    claims = ClaimRepository(session).list_for_task(task_id)

    fetch_job_by_candidate_id = {fetch_job.candidate_url_id: fetch_job for fetch_job in fetch_jobs}
    latest_attempt_by_fetch_job_id = {attempt.fetch_job_id: attempt for attempt in fetch_attempts}
    snapshot_by_fetch_attempt_id = {
        snapshot.fetch_attempt_id: snapshot for snapshot in content_snapshots
    }
    source_document_by_url = {item.canonical_url: item for item in source_documents}
    chunks_by_source_document_id: dict[UUID, list[SourceChunk]] = {}
    for chunk in source_chunks:
        chunks_by_source_document_id.setdefault(chunk.source_document_id, []).append(chunk)

    claims_by_source_document_id: dict[str, int] = {}
    for claim in claims:
        notes = claim.notes_json or {}
        source_document_id = notes.get("source_document_id")
        if isinstance(source_document_id, str):
            claims_by_source_document_id[source_document_id] = (
                claims_by_source_document_id.get(source_document_id, 0) + 1
            )

    rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for candidate in candidate_urls:
        source_document = source_document_by_url.get(candidate.canonical_url)
        chunks = (
            chunks_by_source_document_id.get(source_document.id, [])
            if source_document is not None
            else []
        )
        source_candidates = [
            item
            for item in evidence_candidates
            if source_document is not None
            and item.get("source_document_id") == str(source_document.id)
        ]
        accepted_evidence_count = sum(
            1
            for item in source_candidates
            if item.get("evidence_candidate_id") in accepted_candidate_ids
        )
        rejected_count = sum(1 for item in source_candidates if item.get("rejection_reasons"))
        claim_count = (
            claims_by_source_document_id.get(str(source_document.id), 0)
            if source_document is not None
            else 0
        )
        attempted = candidate.id in fetch_job_by_candidate_id
        fetch_job = fetch_job_by_candidate_id.get(candidate.id)
        fetched = fetch_job is not None and fetch_job.status == "SUCCEEDED"
        latest_attempt = (
            latest_attempt_by_fetch_job_id.get(fetch_job.id) if fetch_job is not None else None
        )
        content_snapshot = (
            snapshot_by_fetch_attempt_id.get(latest_attempt.id)
            if latest_attempt is not None
            else None
        )
        unsupported_content_type = bool(
            content_snapshot is not None
            and content_snapshot.mime_type not in {"text/html", "text/plain"}
        )
        parsed = source_document is not None
        indexed = bool(chunks)
        source_intent = str(fetch_priority_metadata(candidate, query=query).get("source_intent"))
        dropped_reasons = _source_yield_dropped_reasons(
            attempted=attempted,
            fetched=fetched,
            parsed=parsed,
            indexed=indexed,
            source_chunks=chunks,
            candidate_count=len(source_candidates),
            accepted_evidence_count=accepted_evidence_count,
            claim_count=claim_count,
            rejected_count=rejected_count,
            unsupported_content_type=unsupported_content_type,
            blocked_by_policy=(
                _fetch_job_blocked_by_policy(session, task_id, fetch_job)
                if fetch_job is not None
                else False
            ),
        )
        rows.append(
            SourceYieldSummary(
                source_document_id=str(source_document.id) if source_document is not None else None,
                url=candidate.canonical_url,
                source_intent=source_intent,
                attempted=attempted,
                fetched=fetched,
                parsed=parsed,
                indexed=indexed,
                candidate_count=len(source_candidates),
                accepted_evidence_count=accepted_evidence_count,
                claim_count=claim_count,
                rejected_count=rejected_count,
                dropped_reasons=dropped_reasons,
                contribution_level=contribution_level_for_counts(
                    accepted_evidence_count=accepted_evidence_count,
                    claim_count=claim_count,
                    candidate_count=len(source_candidates),
                ),
            ).to_payload()
            | {
                "candidate_url_id": str(candidate.id),
                "domain": candidate.domain,
                "title": candidate.title,
                "rank": candidate.rank,
            }
        )
        seen_urls.add(candidate.canonical_url)

    for source_document in source_documents:
        if source_document.canonical_url in seen_urls:
            continue
        source_candidates = [
            item
            for item in evidence_candidates
            if item.get("source_document_id") == str(source_document.id)
        ]
        accepted_evidence_count = sum(
            1
            for item in source_candidates
            if item.get("evidence_candidate_id") in accepted_candidate_ids
        )
        claim_count = claims_by_source_document_id.get(str(source_document.id), 0)
        rejected_count = sum(1 for item in source_candidates if item.get("rejection_reasons"))
        chunks = chunks_by_source_document_id.get(source_document.id, [])
        source_intent = _source_document_category(source_document)
        rows.append(
            SourceYieldSummary(
                source_document_id=str(source_document.id),
                url=source_document.canonical_url,
                source_intent=source_intent,
                attempted=True,
                fetched=True,
                parsed=True,
                indexed=bool(chunks),
                candidate_count=len(source_candidates),
                accepted_evidence_count=accepted_evidence_count,
                claim_count=claim_count,
                rejected_count=rejected_count,
                dropped_reasons=_source_yield_dropped_reasons(
                    attempted=True,
                    fetched=True,
                    parsed=True,
                    indexed=bool(chunks),
                    source_chunks=chunks,
                    candidate_count=len(source_candidates),
                    accepted_evidence_count=accepted_evidence_count,
                    claim_count=claim_count,
                    rejected_count=rejected_count,
                    unsupported_content_type=False,
                    blocked_by_policy=False,
                ),
                contribution_level=contribution_level_for_counts(
                    accepted_evidence_count=accepted_evidence_count,
                    claim_count=claim_count,
                    candidate_count=len(source_candidates),
                ),
            ).to_payload()
            | {
                "candidate_url_id": None,
                "domain": source_document.domain,
                "title": source_document.title,
                "rank": None,
            }
        )
    return rows


def _source_yield_dropped_reasons(
    *,
    attempted: bool,
    fetched: bool,
    parsed: bool,
    indexed: bool,
    source_chunks: list[SourceChunk],
    candidate_count: int,
    accepted_evidence_count: int,
    claim_count: int,
    rejected_count: int,
    unsupported_content_type: bool,
    blocked_by_policy: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not attempted:
        reasons.append("not_selected_low_priority")
    elif not fetched:
        if blocked_by_policy:
            reasons.append("blocked_by_policy")
        else:
            reasons.append("fetch_failed")
    elif not parsed and unsupported_content_type:
        reasons.append("unsupported_content_type")
    elif not parsed:
        reasons.append("parse_failed")
    elif not indexed:
        reasons.append("parse_failed")
    elif source_chunks and not any(
        _chunk_metadata_eligible_for_claims(chunk.metadata_json or {}) for chunk in source_chunks
    ):
        reasons.append("low_chunk_quality")
    elif candidate_count <= 0:
        reasons.append("no_evidence_candidates")
    elif accepted_evidence_count <= 0 and claim_count <= 0 and rejected_count > 0:
        reasons.append("evidence_rejected")
    elif accepted_evidence_count <= 0 and claim_count <= 0:
        reasons.append("off_intent")
    return normalize_dropped_reasons(reasons)


def _fetch_job_blocked_by_policy(
    session: Session,
    task_id: UUID,
    fetch_job: Any,
) -> bool:
    for attempt in FetchAttemptRepository(session).list_for_task(
        task_id,
        fetch_job_id=fetch_job.id,
        limit=1,
    ):
        error_code = attempt.error_code or ""
        trace = attempt.trace_json or {}
        if "policy" in error_code or trace.get("decision_reason") is not None:
            return True
    return False


def _source_extracted_text_length(chunks: list[SourceChunk]) -> int:
    for chunk in chunks:
        metadata = chunk.metadata_json or {}
        value = metadata.get("extracted_text_length")
        if isinstance(value, int):
            return value
    return sum(len(chunk.text) for chunk in chunks)


def _low_yield_reason(
    *,
    extracted_text_length: int,
    chunk_count: int,
    eligible_chunk_count: int,
    candidate_sentence_count: int,
    answer_relevant_candidate_count: int,
    answer_category_coverage: set[str],
) -> str | None:
    if chunk_count <= 0:
        return "no_chunks"
    if extracted_text_length < 80:
        return "very_short_extracted_text"
    if eligible_chunk_count <= 0:
        return "no_eligible_chunks"
    if candidate_sentence_count <= 0:
        return "no_candidate_sentences"
    if answer_relevant_candidate_count <= 0:
        return "no_answer_relevant_candidates"
    if not answer_category_coverage:
        return "no_answer_category_coverage"
    return None


def _dropped_reasons_for_parsed_source(
    *,
    low_yield_reason: str | None,
    candidate_count: int,
    answer_relevant_candidate_count: int,
    accepted_evidence_count: int,
    rejected_count: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if low_yield_reason == "no_chunks":
        reasons.append("parse_failed")
    elif low_yield_reason in {"very_short_extracted_text", "no_eligible_chunks"}:
        reasons.append("low_chunk_quality")
    elif candidate_count <= 0:
        reasons.append("no_evidence_candidates")
    elif answer_relevant_candidate_count <= 0:
        reasons.append("off_intent")
    elif accepted_evidence_count <= 0 and rejected_count > 0:
        reasons.append("evidence_rejected")
    return normalize_dropped_reasons(reasons)


def _fetch_dropped_reason(source: dict[str, Any]) -> str:
    if source.get("fetch_status") == "UNATTEMPTED":
        return "not_selected_low_priority"
    error_code = str(source.get("error_code") or "")
    trace = source.get("trace") if isinstance(source.get("trace"), dict) else {}
    if "policy" in error_code or trace.get("decision_reason") is not None:
        return "blocked_by_policy"
    if source.get("fetch_status") == "FAILED":
        return "fetch_failed"
    return "unknown"


def _dropped_sources_from_yield(source_yield_summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in source_yield_summary
        if row.get("contribution_level") == "none" and row.get("dropped_reasons")
    ]


def _evidence_candidates_from_diagnostics(diagnostics: dict[str, object]) -> list[dict[str, Any]]:
    value = diagnostics.get("evidence_candidates")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    post_supplemental = diagnostics.get("post_supplemental_diagnostics")
    initial = diagnostics.get("initial_diagnostics")
    rows: list[dict[str, Any]] = []
    for nested in (initial, post_supplemental):
        if isinstance(nested, dict):
            rows.extend(_evidence_candidates_from_diagnostics(nested))
    return rows


def _accepted_evidence_candidate_ids_from_entries(entries: list[Any]) -> set[str]:
    ids: set[str] = set()
    for entry in entries:
        notes = entry.claim.notes_json if entry.claim is not None else {}
        candidate_id = notes.get("evidence_candidate_id") if isinstance(notes, dict) else None
        if isinstance(candidate_id, str) and candidate_id.strip():
            ids.add(candidate_id)
    return ids


def _claim_rows_for_slot_summary_from_entries(entries: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in entries:
        claim = entry.claim
        notes = claim.notes_json if claim is not None else {}
        if not isinstance(notes, dict):
            continue
        rows.append(_claim_slot_summary_row(claim))
    return rows


def _claim_rows_for_slot_summary_from_claims(
    session: Session,
    task_id: UUID,
) -> list[dict[str, Any]]:
    return [
        _claim_slot_summary_row(claim) for claim in ClaimRepository(session).list_for_task(task_id)
    ]


def _evidence_candidates_from_claims(
    session: Session,
    task_id: UUID,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for claim in ClaimRepository(session).list_for_task(task_id):
        notes = claim.notes_json or {}
        candidate = notes.get("evidence_candidate")
        if isinstance(candidate, dict):
            rows.append(candidate)
    return rows


def _claim_slot_summary_row(claim: Any) -> dict[str, Any]:
    notes = claim.notes_json or {}
    verification = notes.get("verification") if isinstance(notes, dict) else {}
    if not isinstance(verification, dict):
        verification = {}
    weak_support_count = verification.get("weak_support_evidence_count")
    strong_support_count = verification.get("strong_support_evidence_count")
    support_level = "weak" if weak_support_count and not strong_support_count else "strong"
    return {
        "claim_id": str(claim.id),
        "verification_status": claim.verification_status,
        "slot_ids": notes.get("slot_ids", []) if isinstance(notes, dict) else [],
        "source_document_id": (
            notes.get("source_document_id")
            if isinstance(notes.get("source_document_id"), str)
            else None
        ),
        "support_level": support_level,
    }


def _build_verification_summary(entries: list[Any]) -> dict[str, Any]:
    strong_support = 0
    weak_support = 0
    contradiction = 0
    unsupported = 0
    methods: set[str] = set()
    statuses: dict[str, int] = {}
    for entry in entries:
        notes = entry.claim.notes_json or {}
        verification = notes.get("verification") if isinstance(notes, dict) else {}
        if not isinstance(verification, dict):
            verification = {}
        strong_support += int(verification.get("strong_support_evidence_count") or 0)
        weak_support += int(verification.get("weak_support_evidence_count") or 0)
        contradiction += int(verification.get("contradict_evidence_count") or 0)
        unsupported += int(verification.get("insufficient_evidence_count") or 0)
        method = verification.get("verifier_method") or verification.get("method")
        if isinstance(method, str) and method.strip():
            methods.add(method)
        statuses[entry.claim.verification_status] = (
            statuses.get(entry.claim.verification_status, 0) + 1
        )
    return {
        "verifier_methods": sorted(methods),
        "strong_support_evidence_count": strong_support,
        "weak_support_evidence_count": weak_support,
        "contradict_evidence_count": contradiction,
        "insufficient_evidence_count": unsupported,
        "claim_status_counts": dict(sorted(statuses.items())),
        "limitations": [
            "deterministic lexical verification only",
            "weak support is not treated as full entailment",
        ],
    }


def _build_claim_failure_details(
    session: Session,
    task_id: UUID,
    *,
    query: str,
    diagnostics: dict[str, Any],
    supplemental_result: dict[str, Any],
    trigger_reason: str | None,
) -> dict[str, Any]:
    unattempted_high_quality, skipped = _select_supplemental_candidates(
        session,
        task_id,
        query=query,
        limit=10,
    )
    return {
        **diagnostics,
        "why_supplemental_acquisition_triggered": trigger_reason,
        "supplemental_sources_attempted": supplemental_result.get("attempted_sources", []),
        "supplemental_sources_skipped": [
            *supplemental_result.get("skipped_sources", []),
            *skipped,
        ],
        "unattempted_high_quality_sources": [
            _candidate_url_summary(candidate, query=query) for candidate in unattempted_high_quality
        ],
        "why_wikipedia_or_about_not_attempted": _why_wikipedia_or_about_not_attempted(
            session,
            task_id,
            query=query,
        ),
        "why_required_source_intents_missing": _why_required_source_intents_missing(
            session,
            task_id,
            query=query,
        ),
        "per_source_answer_yield": diagnostics.get("answer_yield", []),
        "top_rejected_candidates": diagnostics.get("top_rejected_candidates", []),
        "next_action": (
            "Review source-intent selection, required source intents, and answer-slot coverage "
            "before adding query-specific rules."
        ),
    }


def _why_wikipedia_or_about_not_attempted(
    session: Session,
    task_id: UUID,
    *,
    query: str,
) -> dict[str, str]:
    fetch_jobs = FetchJobRepository(session).list_for_task(task_id)
    attempted_candidate_ids = {fetch_job.candidate_url_id for fetch_job in fetch_jobs}
    candidates = CandidateUrlRepository(session).list_for_task(task_id)
    result = {
        "official_about": "no official_about candidate was present",
        "wikipedia_reference": "no wikipedia_reference candidate was present",
    }
    for candidate in candidates:
        category = str(fetch_priority_metadata(candidate, query=query).get("source_category"))
        if category not in result:
            continue
        if candidate.id in attempted_candidate_ids:
            result[category] = "candidate was attempted"
        else:
            result[category] = "candidate was present but remained unattempted"
    return result


def _why_required_source_intents_missing(
    session: Session,
    task_id: UUID,
    *,
    query: str,
) -> dict[str, str]:
    fetch_jobs = FetchJobRepository(session).list_for_task(task_id)
    attempted_candidate_ids = {fetch_job.candidate_url_id for fetch_job in fetch_jobs}
    candidates = CandidateUrlRepository(session).list_for_task(task_id)
    required_intents = _required_source_intents_for_query(query)
    result = {intent: f"no {intent} candidate was present" for intent in required_intents}
    for candidate in candidates:
        source_intent = str(fetch_priority_metadata(candidate, query=query).get("source_intent"))
        if source_intent not in result:
            continue
        if candidate.id in attempted_candidate_ids:
            result[source_intent] = "candidate was attempted"
        else:
            result[source_intent] = "candidate was present but remained unattempted"
    return result


def _required_source_intents_for_query(query: str) -> list[str]:
    lower = query.lower()
    if any(term in lower for term in ("docker", "deploy", "deployment", "install")):
        return ["official_installation_admin", "official_docs_reference"]
    if _is_overview_query(query):
        return ["official_about", "wikipedia_reference"]
    return ["official_docs_reference"]


def _is_overview_query(query: str) -> bool:
    intent = classify_query_intent(query)
    lower = query.lower()
    return intent.intent_name in {"definition", "definition_mechanism"} or "overview" in lower


def _query_asks_deployment(query: str) -> bool:
    lower = query.lower()
    return any(term in lower for term in ("docker", "deploy", "deployment", "install", "setup"))


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
    details = getattr(error, "details", None)
    if isinstance(details, dict):
        next_action = details.get("next_action")
        if isinstance(next_action, str) and next_action.strip():
            return next_action.strip()
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
