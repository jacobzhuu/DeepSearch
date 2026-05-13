from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from packages.db.models import CandidateUrl, ResearchRun, ResearchTask, SourceChunk, SourceDocument
from packages.db.repositories import (
    CandidateUrlRepository,
    ClaimEvidenceRepository,
    ClaimRepository,
    ContentSnapshotRepository,
    FetchAttemptRepository,
    FetchJobRepository,
    ReportArtifactRepository,
    ResearchRunRepository,
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
    PlannedSearchQuery,
    ResearchPlan,
    ResearchPlannerService,
    build_default_research_plan,
    build_optional_research_plan,
    research_plan_from_serialized_payload,
)
from services.orchestrator.app.research_quality import (
    LLMClaimReviewService,
    LLMEvidenceRerankerService,
    LLMQueryRewriterService,
    LLMResearchStrategistService,
    SourceJudgeService,
    SourceYieldSummary,
    analyze_required_slot_gaps,
    answer_slot_coverage,
    answer_slots_for_query,
    build_slot_coverage_summary,
    classify_source_intent,
    contribution_level_for_counts,
    evaluate_research_coverage,
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
from services.orchestrator.app.services.gap_round_diagnostics import (
    GAP_ROUND_OUTCOME_DRAFTED,
    GAP_ROUND_OUTCOME_SKIPPED,
    SKIP_FETCH_BUDGET_EXHAUSTED,
    SKIP_NO_CANDIDATE_URLS,
    SKIP_NO_CONTENT_SNAPSHOTS,
    SKIP_NO_FOLLOWUP_QUERIES,
    SKIP_NO_NEW_CHUNKS,
    SKIP_NO_SELECTED_CANDIDATES,
    SKIP_NO_SOURCE_CHUNKS,
    SKIP_NO_SOURCE_DOCUMENTS,
    SKIP_NO_SUCCESSFUL_FETCHES,
    SKIP_SUPPLEMENTAL_SEARCH_FAILED_CONTINUING_EXISTING_EVIDENCE,
    attach_gap_round_to_stage_result,
    build_gap_round_diagnostics,
    canonical_urls_for_candidate_ids,
    fetch_budget_hint_from_skipped_sources,
    verification_supported_count_from_summary,
)
from services.orchestrator.app.services.indexing import IndexingService
from services.orchestrator.app.services.parsing import ParsingService, parse_entry_diagnostic
from services.orchestrator.app.services.reporting import (
    ReportSynthesisService,
    _claim_focus_matches_query,
    _claim_focus_required_for_report,
    _report_claim_answer_relevant,
    _report_claim_score,
)
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
STATUS_QUEUED = "QUEUED"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_PAUSED = "PAUSED"
STATUS_CANCELLED = "CANCELLED"

STAGE_SEARCHING = "SEARCHING"
STAGE_ACQUIRING = "ACQUIRING"
STAGE_PARSING = "PARSING"
STAGE_INDEXING = "INDEXING"
STAGE_DRAFTING_CLAIMS = "DRAFTING_CLAIMS"
STAGE_VERIFYING = "VERIFYING"
STAGE_RESEARCHING_MORE = "RESEARCHING_MORE"
STAGE_REPORTING = "REPORTING"

PIPELINE_RUNNABLE_STATUSES = ("PLANNED", STATUS_QUEUED)
SEARCH_ALLOWED_STATUSES = ("PLANNED", STATUS_QUEUED, STAGE_SEARCHING, STAGE_RESEARCHING_MORE)
ACQUISITION_ALLOWED_STATUSES = (
    "PLANNED",
    STATUS_QUEUED,
    STAGE_ACQUIRING,
    STAGE_DRAFTING_CLAIMS,
    STAGE_RESEARCHING_MORE,
)
PARSING_ALLOWED_STATUSES = (
    "PLANNED",
    STATUS_QUEUED,
    STAGE_PARSING,
    STAGE_DRAFTING_CLAIMS,
    STAGE_RESEARCHING_MORE,
)
INDEXING_ALLOWED_STATUSES = (
    "PLANNED",
    STATUS_QUEUED,
    STAGE_INDEXING,
    STAGE_DRAFTING_CLAIMS,
    STAGE_RESEARCHING_MORE,
)
DRAFT_ALLOWED_STATUSES = ("PLANNED", STATUS_QUEUED, STAGE_DRAFTING_CLAIMS, STAGE_RESEARCHING_MORE)
VERIFY_ALLOWED_STATUSES = ("PLANNED", STATUS_QUEUED, STAGE_VERIFYING, STAGE_RESEARCHING_MORE)
MIN_SUCCESSFUL_SOURCES_WARNING_THRESHOLD = 2
GAP_SEARCH_UNAVAILABLE_WARNING = "gap_search_unavailable"

# Gap search candidate selection skip reasons (pipeline.gap_analysis / acquisition diagnostics).
GAP_SEARCH_SKIP_CATEGORY_NOT_ALLOWED = "gap_category_not_allowed"
GAP_SEARCH_SKIP_PRIORITY_TOO_LOW = "gap_priority_too_low"
GAP_SEARCH_SKIP_MISSING_CANDIDATE_ID = "gap_missing_candidate_id"
GAP_SEARCH_SKIP_INVALID_CANDIDATE_ID = "gap_invalid_candidate_id"
GAP_SEARCH_SKIP_DUPLICATE_IN_ROUND = "gap_duplicate_in_round"

# When overall coverage is sufficient and only optional slots remain weak, suppress
# strategist-driven ``continue_search`` gap rounds by default (see
# ``_maybe_suppress_strategist_gap_continue_for_coverage_alignment``).
COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY_STOP = "coverage_sufficient_optional_weak_only"
SUPPLEMENTAL_SEARCH_FAILED_WARNING = "supplemental_search_failed"
MIN_REPORT_MAIN_CLAIMS = 12
MIN_REPORT_SUPPORT_EVIDENCE = 12
MIN_REPORT_SOURCE_DOCUMENTS = 4


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


class DebugPipelineInterrupted(Exception):
    def __init__(self, task_id: UUID, status: str) -> None:
        super().__init__(f"pipeline interrupted for task {task_id}; current status is {status}")
        self.task_id = task_id
        self.status = status


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
        query_rewriter_service: LLMQueryRewriterService | None = None,
        source_judge_service: SourceJudgeService | None = None,
        research_strategist_service: LLMResearchStrategistService | None = None,
        evidence_reranker_service: LLMEvidenceRerankerService | None = None,
        claim_reviewer_service: LLMClaimReviewService | None = None,
        dependencies: dict[str, Any],
        fetch_limit: int = 3,
        parse_limit: int = 3,
        index_limit: int = 10,
        claim_limit: int = 5,
        parse_drain_enabled: bool = False,
        parse_drain_max_batches: int = 3,
        parse_drain_target_documents: int = 20,
        parse_drain_max_seconds: float = 0.0,
        event_source: str = PIPELINE_EVENT_SOURCE,
        event_prefix: str = PIPELINE_EVENT_PREFIX,
        target_successful_snapshots: int = MIN_SUCCESSFUL_SOURCES_WARNING_THRESHOLD,
        min_answer_sources: int = 3,
        max_supplemental_sources: int = 3,
        max_gap_rounds: int = 2,
        gap_max_queries_per_round: int = 4,
        research_loop_enabled: bool = False,
        research_loop_strategist_shadow_mode: bool = True,
        research_loop_max_total_queries: int = 16,
        research_loop_max_total_fetch_attempts: int = 20,
        research_loop_max_strategy_calls: int = 4,
        research_loop_fetch_more_candidates_per_round: int = 3,
        research_loop_min_distinct_domains: int = 3,
        research_loop_min_authoritative_sources: int = 1,
        research_loop_required_slot_min_status: str = "moderate",
        research_loop_allow_low_coverage_report: bool = True,
    ) -> None:
        self.session = session
        self.search_service = search_service
        self.acquisition_service = acquisition_service
        self.parsing_service = parsing_service
        self.indexing_service = indexing_service
        self.claims_service = claims_service
        self.reporting_service = reporting_service
        self.planner_service = planner_service
        self.query_rewriter_service = query_rewriter_service
        self.source_judge_service = source_judge_service
        self.research_strategist_service = research_strategist_service
        self.evidence_reranker_service = evidence_reranker_service
        self.claim_reviewer_service = claim_reviewer_service
        self.research_plan: ResearchPlan | None = None
        self.dependencies = dependencies
        self.fetch_limit = fetch_limit
        self.parse_limit = parse_limit
        self.index_limit = index_limit
        self.claim_limit = claim_limit
        self.parse_drain_enabled = bool(parse_drain_enabled)
        self.parse_drain_max_batches = max(1, int(parse_drain_max_batches))
        self.parse_drain_target_documents = max(1, int(parse_drain_target_documents))
        self.parse_drain_max_seconds = max(0.0, float(parse_drain_max_seconds))
        self.event_source = event_source
        self.event_prefix = event_prefix
        self.target_successful_snapshots = target_successful_snapshots
        self.min_answer_sources = max(1, min_answer_sources)
        self.max_supplemental_sources = max(0, max_supplemental_sources)
        self.max_gap_rounds = max(0, max_gap_rounds)
        self.gap_max_queries_per_round = max(1, gap_max_queries_per_round)
        self.research_loop_enabled = research_loop_enabled
        self.research_loop_strategist_shadow_mode = research_loop_strategist_shadow_mode
        self.research_loop_max_total_queries = max(1, research_loop_max_total_queries)
        self.research_loop_max_total_fetch_attempts = max(1, research_loop_max_total_fetch_attempts)
        self.research_loop_max_strategy_calls = max(0, research_loop_max_strategy_calls)
        self.research_loop_fetch_more_candidates_per_round = max(
            0,
            research_loop_fetch_more_candidates_per_round,
        )
        self.research_loop_min_distinct_domains = max(0, research_loop_min_distinct_domains)
        self.research_loop_min_authoritative_sources = max(
            0,
            research_loop_min_authoritative_sources,
        )
        self.research_loop_required_slot_min_status = research_loop_required_slot_min_status
        self.research_loop_allow_low_coverage_report = research_loop_allow_low_coverage_report
        self.research_strategy_calls = 0
        self.supplemental_acquisition_ran = False
        self.llm_assistance: dict[str, Any] = {}
        self.task_repository = ResearchTaskRepository(session)
        self.run_repository = ResearchRunRepository(session)
        self.event_repository = TaskEventRepository(session)

    def run(self, task_id: UUID) -> DebugPipelineResult:
        task = self._get_task(task_id)
        if task.status not in PIPELINE_RUNNABLE_STATUSES:
            raise DebugPipelinePreconditionError(
                "DeepSearch pipeline can only run from PLANNED or QUEUED; "
                f"current status is {task.status}"
            )

        stages_completed: list[str] = []
        report_artifact_id: UUID | None = None
        report_version: int | None = None
        report_markdown_preview: str | None = None

        checkpoint_stages = self._checkpoint_completed_stages(task)
        try:
            self._mark_started(task)
            self._run_planner_if_configured(task.id)

            for stage, action in (
                (STAGE_SEARCHING, self._run_search),
                (STAGE_ACQUIRING, self._run_fetch),
                (STAGE_PARSING, self._run_parse),
                (STAGE_INDEXING, self._run_index),
                (STAGE_DRAFTING_CLAIMS, self._run_draft_claims),
                (STAGE_VERIFYING, self._run_verify_claims),
            ):
                if stage in checkpoint_stages:
                    stages_completed.append(stage)
                    continue
                failure = self._execute_stage(task.id, stage, action, stages_completed)
                if failure is not None:
                    return failure

            gap_failure = self._run_gap_rounds(task.id, stages_completed)
            if gap_failure is not None:
                return gap_failure

            if STAGE_REPORTING not in checkpoint_stages:
                report_failure = self._execute_stage(
                    task.id,
                    STAGE_REPORTING,
                    self._run_report,
                    stages_completed,
                )
                if report_failure is not None:
                    return report_failure
                report_stage_result = self._latest_stage_result(task.id, STAGE_REPORTING)
                if report_stage_result is not None:
                    report_artifact_id = report_stage_result.get("report_artifact_id")
                    report_version = report_stage_result.get("report_version")
                    report_markdown_preview = report_stage_result.get("report_markdown_preview")
            else:
                stages_completed.append(STAGE_REPORTING)
        except DebugPipelineInterrupted:
            refreshed_task = self._get_task(task.id)
            return DebugPipelineResult(
                task=refreshed_task,
                completed=False,
                stages_completed=stages_completed,
                counts=self._safe_counts(task.id),
                report_artifact_id=report_artifact_id,
                report_version=report_version,
                report_markdown_preview=report_markdown_preview,
                failure=None,
                dependencies=self.dependencies,
            )

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

    def _execute_stage(
        self,
        task_id: UUID,
        stage: str,
        action: Callable[[UUID], dict[str, Any]],
        stages_completed: list[str],
    ) -> DebugPipelineResult | None:
        self._ensure_task_can_continue(task_id)
        self._record_stage_started(task_id, stage)
        try:
            stage_result = action(task_id)
        except DebugPipelineInterrupted:
            raise
        except Exception as error:  # noqa: BLE001 - pipeline must report exact blocker.
            self.session.rollback()
            counts = self._safe_counts(task_id)
            failure = DebugPipelineFailure(
                stage=stage,
                reason=_classify_failure(error),
                exception=type(error).__name__,
                message=str(error),
                next_action=_next_action_for_failure(stage=stage, error=error),
                counts=counts,
                details=getattr(error, "details", None),
            )
            self._record_failure(task_id, failure)
            refreshed_task = self._get_task(task_id)
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
        self._record_stage_completed(
            task_id,
            stage,
            stage_result,
            stages_completed=stages_completed,
        )
        self._ensure_task_can_continue(task_id)
        return None

    def _run_gap_rounds(
        self,
        task_id: UUID,
        stages_completed: list[str],
    ) -> DebugPipelineResult | None:
        task = self._get_task(task_id)
        max_rounds = self._resolve_max_gap_rounds(task)
        round_no = 1
        while True:
            self._ensure_task_can_continue(task_id)
            slot_coverage_summary = self._current_slot_coverage_summary(task_id)
            coverage_evaluation = self._current_coverage_evaluation(
                task_id,
                slot_coverage_summary=slot_coverage_summary,
                round_no=round_no,
                max_rounds=max_rounds,
            )
            strategy_payload = self._run_research_strategy_if_configured(
                task_id,
                round_no=round_no,
                max_rounds=max_rounds,
                slot_coverage_summary=slot_coverage_summary,
                coverage_evaluation=coverage_evaluation,
            )
            if strategy_payload is not None:
                self._record_research_strategy(task_id, strategy_payload)

            # Active loop break conditions
            if self.research_loop_enabled:
                if (
                    not self.research_loop_strategist_shadow_mode
                    and strategy_payload is not None
                    and strategy_payload.get("status") == "used"
                ):
                    decision = strategy_payload.get("decision")
                    if decision in {"stop_budget_exhausted", "stop_unanswerable"} and not (
                        self._has_followup_budget(task_id, round_no=round_no, max_rounds=max_rounds)
                    ):
                        break

            analysis = analyze_required_slot_gaps(
                task.query,
                slot_coverage_summary=slot_coverage_summary,
                round_no=round_no,
                max_rounds=max_rounds,
                max_queries_per_round=self.gap_max_queries_per_round,
                existing_query_texts=self._existing_search_query_texts(task_id),
            )
            analysis_payload = analysis.to_payload()
            if strategy_payload is not None and self._strategy_should_drive_followup(
                strategy_payload
            ):
                analysis_payload = _gap_analysis_payload_from_strategy(
                    analysis_payload,
                    strategy_payload,
                    round_no=round_no,
                    max_rounds=max_rounds,
                )
            analysis_payload = _maybe_suppress_strategist_gap_continue_for_coverage_alignment(
                analysis_payload,
                coverage_evaluation=coverage_evaluation,
                slot_coverage_summary=slot_coverage_summary,
                strategy_payload=strategy_payload,
                research_loop_enabled=self.research_loop_enabled,
                research_loop_strategist_shadow_mode=self.research_loop_strategist_shadow_mode,
            )
            if not analysis_payload.get("triggered"):
                quality_gate = self._current_report_quality_gate(
                    task_id,
                    slot_coverage_summary=slot_coverage_summary,
                    coverage_evaluation=coverage_evaluation,
                    round_no=round_no,
                    max_rounds=max_rounds,
                )
                if quality_gate.get("triggered"):
                    analysis_payload = _gap_analysis_payload_from_quality_gate(
                        analysis_payload,
                        quality_gate,
                        query=task.query,
                        round_no=round_no,
                        max_rounds=max_rounds,
                        max_queries=self.gap_max_queries_per_round,
                        existing_query_texts=self._existing_search_query_texts(task_id),
                    )
                else:
                    analysis_payload["report_quality_gate"] = quality_gate
            self._record_gap_analysis(task_id, analysis_payload)
            if not analysis_payload.get("triggered"):
                break

            def run_gap_stage(
                current_task_id: UUID,
                *,
                payload: dict[str, Any] = analysis_payload,
            ) -> dict[str, Any]:
                return self._run_research_more_round(current_task_id, payload)

            failure = self._execute_stage(
                task_id,
                STAGE_RESEARCHING_MORE,
                run_gap_stage,
                stages_completed,
            )
            if failure is not None:
                return failure
            round_no += 1
        return None

    def _attach_gap_round_diagnostics(
        self,
        task_id: UUID,
        gap_analysis: dict[str, Any],
        result: dict[str, Any],
        *,
        gap_round_outcome: str,
        skip_drafting_reason: str | None,
        coverage_before: list[dict[str, Any]] | None,
        coverage_after: list[dict[str, Any]] | None,
        search_attempted: bool,
        search_skipped_reason: str | None,
        search_result: dict[str, Any],
        selected_candidate_ids: list[UUID],
        skipped_gap_search_sources: list[dict[str, Any]],
        skipped_fallback_sources: list[dict[str, Any]],
        acquisition_batch: Any | None,
        acquisition_result: dict[str, Any] | None,
        snapshot_ids: list[UUID] | None,
        parse_batch: Any | None,
        parsing_result: dict[str, Any] | None,
        source_chunk_ids: list[UUID] | None,
        index_batch: Any | None,
        indexing_result: dict[str, Any] | None,
        drafting_result: dict[str, Any] | None,
        drafting_attempted: bool,
        verification_result: dict[str, Any] | None,
        verification_attempted: bool,
        supplemental_search_failed: bool,
        continuing_with_existing_evidence: bool,
        loop_stop_reason: str | None = None,
    ) -> dict[str, Any]:
        gap_round_raw = gap_analysis.get("round_no")
        gap_round_index: int | None
        if isinstance(gap_round_raw, int):
            gap_round_index = gap_round_raw
        else:
            gap_round_index = None
            if gap_round_raw is not None:
                try:
                    gap_round_index = int(gap_round_raw)
                except (TypeError, ValueError):
                    gap_round_index = None
        gap_triggered_raw = gap_analysis.get("triggered")
        gap_triggered: bool | None
        if isinstance(gap_triggered_raw, bool):
            gap_triggered = gap_triggered_raw
        elif gap_triggered_raw is not None:
            gap_triggered = bool(gap_triggered_raw)
        else:
            gap_triggered = None

        sqc = int(search_result.get("search_query_count") or 0)
        src = int(search_result.get("search_result_count") or 0)
        cadded = int(search_result.get("candidate_urls_added") or 0)

        fetch_jobs_created = None
        fetch_attempts_created = None
        content_snapshots_created = None
        if acquisition_batch is not None:
            fetch_jobs_created = int(acquisition_batch.created)
            fetch_attempts_created = sum(
                1
                for e in acquisition_batch.entries
                if getattr(e, "fetch_attempt", None) is not None
            )
            content_snapshots_created = sum(
                1
                for e in acquisition_batch.entries
                if getattr(e, "content_snapshot", None) is not None
            )
        elif acquisition_result is not None:
            fetch_jobs_created = int(acquisition_result.get("created") or 0)
            fetch_attempts_created = int(len(acquisition_result.get("attempted_sources") or []))
            content_snapshots_created = int(acquisition_result.get("content_snapshots") or 0)

        source_documents_created = None
        source_chunks_created = None
        if parse_batch is not None:
            source_documents_created = int(parse_batch.created)
            source_chunks_created = sum(
                len(e.source_document.chunks)
                for e in parse_batch.entries
                if e.source_document is not None
            )
        elif parsing_result is not None and source_documents_created is None:
            source_documents_created = int(parsing_result.get("created") or 0)

        parse_attempted = bool(snapshot_ids)
        index_attempted = bool(source_chunk_ids)

        d_created = None
        d_reused = None
        if isinstance(drafting_result, dict):
            d_created = int(drafting_result.get("created_claims") or 0)
            d_reused = int(drafting_result.get("reused_claims") or 0)

        ver_supported = None
        if isinstance(verification_result, dict):
            vs = verification_result.get("verification_summary")
            if isinstance(vs, dict):
                ver_supported = verification_supported_count_from_summary(vs)

        selected_urls = canonical_urls_for_candidate_ids(
            self.session,
            task_id,
            list(selected_candidate_ids),
        )

        diagnostics = build_gap_round_diagnostics(
            gap_round_outcome=gap_round_outcome,
            skip_drafting_reason=skip_drafting_reason,
            gap_round_index=gap_round_index,
            strategy_decision=gap_analysis.get("strategy_decision"),
            gap_triggered=gap_triggered,
            search_attempted=search_attempted,
            search_skipped_reason=search_skipped_reason,
            search_queries_count=sqc,
            search_result_count=src,
            candidate_urls_added=cadded,
            selected_candidate_ids=list(selected_candidate_ids),
            selected_candidate_urls=selected_urls,
            fetch_jobs_created=fetch_jobs_created,
            fetch_attempts_created=fetch_attempts_created,
            content_snapshots_created=content_snapshots_created,
            source_documents_created=source_documents_created,
            source_chunks_created=source_chunks_created,
            parse_attempted=parse_attempted,
            index_attempted=index_attempted,
            drafting_attempted=drafting_attempted,
            drafting_created_claims=d_created,
            drafting_reused_claims=d_reused,
            verification_attempted=verification_attempted,
            verification_supported_claims=ver_supported,
            coverage_before=coverage_before,
            coverage_after=coverage_after,
            loop_stop_reason=loop_stop_reason,
            supplemental_search_failed=supplemental_search_failed,
            continuing_with_existing_evidence=continuing_with_existing_evidence,
        )
        return attach_gap_round_to_stage_result(result, diagnostics)

    def _run_research_more_round(
        self,
        task_id: UUID,
        gap_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        task = self._get_task(task_id)
        planned_queries = _planned_queries_from_gap_analysis(gap_analysis)
        strategy_decision = gap_analysis.get("strategy_decision")
        coverage_before = self._current_slot_coverage_summary(task_id)

        search_result: dict[str, Any] = {
            "search_queries": [],
            "search_query_count": 0,
            "search_result_count": 0,
            "candidate_urls_added": 0,
            "candidate_urls_available": 0,
            "selected_sources": [],
            "source_judgments": [],
        }

        search_attempted = strategy_decision != "fetch_more_existing_candidates"
        search_skipped_reason: str | None = None

        if strategy_decision == "fetch_more_existing_candidates":
            search_result["skipped"] = True
            search_result["reason"] = "fetch_more_existing_candidates_decision"
            search_skipped_reason = str(search_result.get("reason"))
        else:
            try:
                search_result = self._run_search(
                    task_id,
                    planned_search_queries=planned_queries,
                    include_default_expansions=False,
                    require_candidates=False,
                )
            except SearchProviderError as error:
                existing_evidence = self._existing_evidence_status_for_gap_fallback(task_id)
                if not existing_evidence["usable_evidence"]:
                    raise
                warnings = [
                    GAP_SEARCH_UNAVAILABLE_WARNING,
                    (
                        f"{SUPPLEMENTAL_SEARCH_FAILED_WARNING}: {error.reason}; "
                        "continuing to reporting with existing evidence."
                    ),
                ]
                cov_after = self._current_slot_coverage_summary(task_id)
                base = {
                    "gap_analysis": gap_analysis,
                    "search": {
                        "supplemental_search": True,
                        "failed": True,
                        "reason": error.reason,
                        "error": error.to_payload(),
                        "planned_queries": [query.to_payload() for query in planned_queries],
                        "search_query_count": 0,
                        "search_result_count": 0,
                    },
                    "warnings": warnings,
                    "existing_evidence": existing_evidence,
                    "slot_coverage_summary": cov_after,
                    "continuing_with_existing_evidence": True,
                }
                return self._attach_gap_round_diagnostics(
                    task_id,
                    gap_analysis,
                    base,
                    gap_round_outcome=GAP_ROUND_OUTCOME_SKIPPED,
                    skip_drafting_reason=SKIP_SUPPLEMENTAL_SEARCH_FAILED_CONTINUING_EXISTING_EVIDENCE,
                    coverage_before=coverage_before,
                    coverage_after=cov_after,
                    search_attempted=True,
                    search_skipped_reason=None,
                    search_result=base["search"],
                    selected_candidate_ids=[],
                    skipped_gap_search_sources=[],
                    skipped_fallback_sources=[],
                    acquisition_batch=None,
                    acquisition_result=None,
                    snapshot_ids=None,
                    parse_batch=None,
                    parsing_result=None,
                    source_chunk_ids=None,
                    index_batch=None,
                    indexing_result=None,
                    drafting_result=None,
                    drafting_attempted=False,
                    verification_result=None,
                    verification_attempted=False,
                    supplemental_search_failed=True,
                    continuing_with_existing_evidence=True,
                )
        tolerated_failure = _supplemental_search_provider_failure(search_result)
        if tolerated_failure is not None:
            existing_evidence = self._existing_evidence_status_for_gap_fallback(task_id)
            if existing_evidence["usable_evidence"]:
                reason = str(
                    tolerated_failure.get("reason")
                    or tolerated_failure.get("provider_failure_reason")
                    or "search_provider_unavailable"
                )
                warnings = [
                    GAP_SEARCH_UNAVAILABLE_WARNING,
                    (
                        f"{SUPPLEMENTAL_SEARCH_FAILED_WARNING}: {reason}; "
                        "continuing to reporting with existing evidence."
                    ),
                ]
                cov_after = self._current_slot_coverage_summary(task_id)
                base = {
                    "gap_analysis": gap_analysis,
                    "search": {
                        **search_result,
                        "failed": True,
                        "reason": reason,
                        "error": tolerated_failure.get("error"),
                    },
                    "warnings": warnings,
                    "existing_evidence": existing_evidence,
                    "slot_coverage_summary": cov_after,
                    "continuing_with_existing_evidence": True,
                }
                return self._attach_gap_round_diagnostics(
                    task_id,
                    gap_analysis,
                    base,
                    gap_round_outcome=GAP_ROUND_OUTCOME_SKIPPED,
                    skip_drafting_reason=SKIP_SUPPLEMENTAL_SEARCH_FAILED_CONTINUING_EXISTING_EVIDENCE,
                    coverage_before=coverage_before,
                    coverage_after=cov_after,
                    search_attempted=True,
                    search_skipped_reason=None,
                    search_result=search_result,
                    selected_candidate_ids=[],
                    skipped_gap_search_sources=[],
                    skipped_fallback_sources=[],
                    acquisition_batch=None,
                    acquisition_result=None,
                    snapshot_ids=None,
                    parse_batch=None,
                    parsing_result=None,
                    source_chunk_ids=None,
                    index_batch=None,
                    indexing_result=None,
                    drafting_result=None,
                    drafting_attempted=False,
                    verification_result=None,
                    verification_attempted=False,
                    supplemental_search_failed=True,
                    continuing_with_existing_evidence=True,
                )
        selected_candidate_ids, skipped_gap_search_sources = _select_gap_search_candidate_ids(
            search_result,
            limit=self.max_supplemental_sources,
        )
        round_warnings: list[str] = []
        fallback_sources: list[dict[str, Any]] = []
        skipped_fallback_sources: list[dict[str, Any]] = []
        attempted_search_fallback = False
        if not selected_candidate_ids:
            fetch_more_limit = self.max_supplemental_sources
            if strategy_decision == "fetch_more_existing_candidates":
                fetch_more_limit = self.research_loop_fetch_more_candidates_per_round

            fallback_candidates, skipped_fallback_sources = _select_supplemental_candidates(
                self.session,
                task_id,
                query=task.query,
                limit=fetch_more_limit,
                high_value_only=True,
            )
            attempted_search_fallback = True
            fallback_nonempty = bool(fallback_candidates)
            selected_candidate_ids = [candidate.id for candidate in fallback_candidates]
            fallback_sources = [
                {
                    **_candidate_url_summary(candidate, query=task.query),
                    "selected_by": "gap_round_unattempted_candidate",
                }
                for candidate in fallback_candidates
            ]
            if not selected_candidate_ids:
                round_warnings.append("Gap round produced no new or unattempted candidate URLs.")
                combined_skipped = [*skipped_gap_search_sources, *skipped_fallback_sources]
                if fetch_budget_hint_from_skipped_sources(combined_skipped):
                    no_sel_skip = SKIP_FETCH_BUDGET_EXHAUSTED
                elif not planned_queries and strategy_decision != "fetch_more_existing_candidates":
                    no_sel_skip = SKIP_NO_FOLLOWUP_QUERIES
                elif (
                    int(search_result.get("candidate_urls_added") or 0) == 0
                    and not fallback_nonempty
                ):
                    no_sel_skip = SKIP_NO_CANDIDATE_URLS
                else:
                    no_sel_skip = SKIP_NO_SELECTED_CANDIDATES
                cov_after = self._current_slot_coverage_summary(task_id)
                base = {
                    "gap_analysis": gap_analysis,
                    "search": search_result,
                    "fallback_sources": fallback_sources,
                    "skipped_gap_search_sources": skipped_gap_search_sources,
                    "skipped_fallback_sources": skipped_fallback_sources,
                    "warnings": round_warnings,
                    "slot_coverage_summary": cov_after,
                }
                return self._attach_gap_round_diagnostics(
                    task_id,
                    gap_analysis,
                    base,
                    gap_round_outcome=GAP_ROUND_OUTCOME_SKIPPED,
                    skip_drafting_reason=no_sel_skip,
                    coverage_before=coverage_before,
                    coverage_after=cov_after,
                    search_attempted=search_attempted,
                    search_skipped_reason=search_skipped_reason,
                    search_result=search_result,
                    selected_candidate_ids=[],
                    skipped_gap_search_sources=skipped_gap_search_sources,
                    skipped_fallback_sources=skipped_fallback_sources,
                    acquisition_batch=None,
                    acquisition_result=None,
                    snapshot_ids=None,
                    parse_batch=None,
                    parsing_result=None,
                    source_chunk_ids=None,
                    index_batch=None,
                    indexing_result=None,
                    drafting_result=None,
                    drafting_attempted=False,
                    verification_result=None,
                    verification_attempted=False,
                    supplemental_search_failed=False,
                    continuing_with_existing_evidence=False,
                )
        if (
            attempted_search_fallback
            and selected_candidate_ids
            and strategy_decision != "fetch_more_existing_candidates"
        ):
            round_warnings.append(
                "Gap round search added no high-value URLs; attempting existing "
                "unattempted high-value candidates."
            )

        acquisition_batch = self.acquisition_service.acquire_candidates(
            task_id,
            candidate_url_ids=selected_candidate_ids,
            limit=len(selected_candidate_ids),
            target_successful_snapshots=None,
        )
        acquisition_result = _acquisition_stage_result(acquisition_batch)
        snapshot_ids = [
            entry.content_snapshot.id
            for entry in acquisition_batch.entries
            if entry.content_snapshot
        ]
        if not snapshot_ids:
            round_warnings.append("Gap round fetched no successful content snapshots.")
            cov_after = self._current_slot_coverage_summary(task_id)
            if int(acquisition_result.get("fetch_succeeded") or 0) == 0:
                snap_skip = SKIP_NO_SUCCESSFUL_FETCHES
            else:
                snap_skip = SKIP_NO_CONTENT_SNAPSHOTS
            base = {
                "gap_analysis": gap_analysis,
                "search": search_result,
                "acquisition": acquisition_result,
                "fallback_sources": fallback_sources,
                "skipped_gap_search_sources": skipped_gap_search_sources,
                "skipped_fallback_sources": skipped_fallback_sources,
                "warnings": round_warnings,
                "slot_coverage_summary": cov_after,
            }
            return self._attach_gap_round_diagnostics(
                task_id,
                gap_analysis,
                base,
                gap_round_outcome=GAP_ROUND_OUTCOME_SKIPPED,
                skip_drafting_reason=snap_skip,
                coverage_before=coverage_before,
                coverage_after=cov_after,
                search_attempted=search_attempted,
                search_skipped_reason=search_skipped_reason,
                search_result=search_result,
                selected_candidate_ids=selected_candidate_ids,
                skipped_gap_search_sources=skipped_gap_search_sources,
                skipped_fallback_sources=skipped_fallback_sources,
                acquisition_batch=acquisition_batch,
                acquisition_result=acquisition_result,
                snapshot_ids=[],
                parse_batch=None,
                parsing_result=None,
                source_chunk_ids=None,
                index_batch=None,
                indexing_result=None,
                drafting_result=None,
                drafting_attempted=False,
                verification_result=None,
                verification_attempted=False,
                supplemental_search_failed=False,
                continuing_with_existing_evidence=False,
            )

        parse_result = self.parsing_service.parse_snapshots(
            task_id,
            content_snapshot_ids=snapshot_ids,
            limit=len(snapshot_ids),
        )
        parse_decisions = [parse_entry_diagnostic(entry) for entry in parse_result.entries]
        source_chunk_ids: list[UUID] = []
        for entry in parse_result.entries:
            if entry.source_document is None:
                continue
            source_chunk_ids.extend(chunk.id for chunk in entry.source_document.chunks)
        parsing_result = {
            "created": parse_result.created,
            "updated": parse_result.updated,
            "skipped_existing": parse_result.skipped_existing,
            "skipped_unsupported": parse_result.skipped_unsupported,
            "skipped_static_html_hold": parse_result.skipped_static_html_hold,
            "skipped_no_valid_chunks": parse_result.skipped_no_valid_chunks,
            "failed": parse_result.failed,
            "invalid_chunk_rejection_count": parse_result.invalid_chunk_rejection_count,
            "invalid_chunk_rejection_reason_distribution": (
                parse_result.invalid_chunk_rejection_reason_distribution or {}
            ),
            "snapshots_with_no_valid_chunks": parse_result.snapshots_with_no_valid_chunks,
            "parser_invalid_output_count": parse_result.snapshots_with_no_valid_chunks,
            "parse_decisions": parse_decisions,
        }
        if not source_chunk_ids:
            round_warnings.append("Gap round parsed no source chunks.")
            has_any_doc = any(e.source_document is not None for e in parse_result.entries)
            chunk_skip = (
                SKIP_NO_SOURCE_DOCUMENTS if not has_any_doc else SKIP_NO_SOURCE_CHUNKS
            )
            cov_after = self._current_slot_coverage_summary(task_id)
            base = {
                "gap_analysis": gap_analysis,
                "search": search_result,
                "acquisition": acquisition_result,
                "parsing": parsing_result,
                "fallback_sources": fallback_sources,
                "skipped_gap_search_sources": skipped_gap_search_sources,
                "skipped_fallback_sources": skipped_fallback_sources,
                "warnings": round_warnings,
                "slot_coverage_summary": cov_after,
                "candidate_counts": _aggregate_candidate_counts(
                    search_result=search_result,
                    fallback_sources=fallback_sources,
                    selected_candidate_ids=selected_candidate_ids,
                    skipped_gap_search_sources=skipped_gap_search_sources,
                    skipped_fallback_sources=skipped_fallback_sources,
                ),
            }
            return self._attach_gap_round_diagnostics(
                task_id,
                gap_analysis,
                base,
                gap_round_outcome=GAP_ROUND_OUTCOME_SKIPPED,
                skip_drafting_reason=chunk_skip,
                coverage_before=coverage_before,
                coverage_after=cov_after,
                search_attempted=search_attempted,
                search_skipped_reason=search_skipped_reason,
                search_result=search_result,
                selected_candidate_ids=selected_candidate_ids,
                skipped_gap_search_sources=skipped_gap_search_sources,
                skipped_fallback_sources=skipped_fallback_sources,
                acquisition_batch=acquisition_batch,
                acquisition_result=acquisition_result,
                snapshot_ids=snapshot_ids,
                parse_batch=parse_result,
                parsing_result=parsing_result,
                source_chunk_ids=[],
                index_batch=None,
                indexing_result=None,
                drafting_result=None,
                drafting_attempted=False,
                verification_result=None,
                verification_attempted=False,
                supplemental_search_failed=False,
                continuing_with_existing_evidence=False,
            )

        index_result = self.indexing_service.index_source_chunks(
            task_id,
            source_chunk_ids=source_chunk_ids,
            limit=len(source_chunk_ids),
        )
        indexing_result = {"indexed_count": len(index_result.indexed_chunks)}
        drafting_precondition_failed = False
        try:
            drafting_result = self._run_draft_claims(task_id)
        except DebugPipelinePreconditionError as error:
            drafting_precondition_failed = True
            round_warnings.append(f"Gap round produced no new draft claims: {error}")
            drafting_result = {
                "created_claims": 0,
                "reused_claims": 0,
                "diagnostics": _json_safe(error.details or {}),
            }
        verification_attempted = False
        try:
            verification_result = self._run_verify_claims(task_id)
            verification_attempted = True
        except DebugPipelinePreconditionError as error:
            round_warnings.append(f"Gap round found no draft claims to verify: {error}")
            verification_result = {
                "verified_claims": 0,
                "slot_coverage_summary": self._current_slot_coverage_summary(task_id),
            }

        slot_coverage_summary = self._current_slot_coverage_summary(task_id)
        remaining_required_gaps = [
            slot
            for slot in slot_coverage_summary
            if slot.get("required") is True and slot.get("status") in {"missing", "weak"}
        ]
        if remaining_required_gaps:
            round_warnings.append(
                "Required answer slots remain missing or weak after this gap round."
            )
        if drafting_precondition_failed:
            gap_outcome = GAP_ROUND_OUTCOME_SKIPPED
            gap_skip_reason = SKIP_NO_NEW_CHUNKS
        else:
            gap_outcome = GAP_ROUND_OUTCOME_DRAFTED
            gap_skip_reason = None
        base = {
            "gap_analysis": gap_analysis,
            "search": search_result,
            "acquisition": acquisition_result,
            "parsing": parsing_result,
            "indexing": indexing_result,
            "drafting": drafting_result,
            "verification": verification_result,
            "slot_coverage_summary": slot_coverage_summary,
            "remaining_required_gaps": remaining_required_gaps,
            "fallback_sources": fallback_sources,
            "skipped_gap_search_sources": skipped_gap_search_sources,
            "skipped_fallback_sources": skipped_fallback_sources,
            "candidate_counts": _aggregate_candidate_counts(
                search_result=search_result,
                fallback_sources=fallback_sources,
                selected_candidate_ids=selected_candidate_ids,
                skipped_gap_search_sources=skipped_gap_search_sources,
                skipped_fallback_sources=skipped_fallback_sources,
            ),
            "warnings": round_warnings,
            "query": task.query,
        }
        return self._attach_gap_round_diagnostics(
            task_id,
            gap_analysis,
            base,
            gap_round_outcome=gap_outcome,
            skip_drafting_reason=gap_skip_reason,
            coverage_before=coverage_before,
            coverage_after=slot_coverage_summary,
            search_attempted=search_attempted,
            search_skipped_reason=search_skipped_reason,
            search_result=search_result,
            selected_candidate_ids=selected_candidate_ids,
            skipped_gap_search_sources=skipped_gap_search_sources,
            skipped_fallback_sources=skipped_fallback_sources,
            acquisition_batch=acquisition_batch,
            acquisition_result=acquisition_result,
            snapshot_ids=snapshot_ids,
            parse_batch=parse_result,
            parsing_result=parsing_result,
            source_chunk_ids=source_chunk_ids,
            index_batch=index_result,
            indexing_result=indexing_result,
            drafting_result=drafting_result,
            drafting_attempted=True,
            verification_result=verification_result,
            verification_attempted=verification_attempted,
            supplemental_search_failed=False,
            continuing_with_existing_evidence=False,
        )

    def _run_search(
        self,
        task_id: UUID,
        *,
        planned_search_queries: list[PlannedSearchQuery] | None = None,
        include_default_expansions: bool = True,
        require_candidates: bool = True,
    ) -> dict[str, Any]:
        effective_planned_queries = planned_search_queries
        if effective_planned_queries is None and self.research_plan is not None:
            effective_planned_queries = self.research_plan.search_queries
        result = self.search_service.discover_candidates(
            task_id,
            planned_search_queries=effective_planned_queries,
            include_default_expansions=include_default_expansions,
            include_authoritative_source_resolver=False,
        )
        if require_candidates and not result.candidate_urls:
            raise DebugPipelinePreconditionError("search produced no candidate URLs")
        search_queries = []
        search_result_count = 0
        candidate_urls_added = 0
        known_path_fallbacks: list[dict[str, Any]] = []
        for item in result.search_queries:
            raw_payload = item.search_query.raw_response_json or {}
            result_count = raw_payload.get("result_count", 0)
            if not isinstance(result_count, int):
                result_count = 0
            search_result_count += result_count
            candidate_urls_added += item.candidates_added
            search_query_payload = {
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
            search_provider_failure = raw_payload.get("search_provider_failure")
            if isinstance(search_provider_failure, dict):
                search_query_payload["search_provider_failure"] = _json_safe(
                    search_provider_failure
                )
            provider_result_diagnostics = raw_payload.get("provider_result_diagnostics")
            if isinstance(provider_result_diagnostics, dict):
                search_query_payload["provider_result_diagnostics"] = _json_safe(
                    provider_result_diagnostics
                )
                search_query_payload["selected_count"] = provider_result_diagnostics.get(
                    "selected_count"
                )
                search_query_payload["rejected_noisy_count"] = provider_result_diagnostics.get(
                    "rejected_noisy_count"
                )
                search_query_payload["fallback_used"] = provider_result_diagnostics.get(
                    "fallback_used"
                )
            known_source_resolver = raw_payload.get("known_source_resolver")
            if isinstance(known_source_resolver, dict):
                search_query_payload["known_source_resolver"] = _json_safe(known_source_resolver)
            known_path_fallback = raw_payload.get("known_path_fallback")
            if isinstance(known_path_fallback, dict):
                safe_fallback = _json_safe(known_path_fallback)
                search_query_payload["known_path_fallback"] = safe_fallback
                known_path_fallbacks.append(safe_fallback)
            search_queries.append(search_query_payload)
        selected_sources = [
            _candidate_url_summary(candidate_url, query=result.task.query)
            for candidate_url in result.candidate_urls
        ]
        source_judgments = []
        if self.source_judge_service is not None:
            source_judgments = [
                item.to_payload()
                for item in self.source_judge_service.judge_candidates(
                    result.candidate_urls,
                    query=result.task.query,
                )
            ]
            self._persist_source_judgments(result.candidate_urls, source_judgments)
            judgments_by_url = {item["canonical_url"]: item for item in source_judgments}
            for selected_source in selected_sources:
                canonical_url = selected_source.get("canonical_url")
                if isinstance(canonical_url, str) and canonical_url in judgments_by_url:
                    selected_source["source_judge"] = judgments_by_url[canonical_url]
            self.llm_assistance["source_judge"] = _source_judge_assistance_summary(
                source_judgments,
                enabled=self.source_judge_service.enabled,
                active=(
                    self.source_judge_service.active_rerank
                    or getattr(self.source_judge_service, "active_triage", False)
                ),
            )
        return {
            "search_queries": search_queries,
            "search_query_count": len(result.search_queries),
            "search_result_count": search_result_count,
            "research_plan_used": self.research_plan is not None and planned_search_queries is None,
            "supplemental_search": planned_search_queries is not None,
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
            "candidate_urls_added": candidate_urls_added,
            "candidate_urls_available": len(result.candidate_urls),
            "selected_sources": selected_sources,
            "source_judgments": source_judgments,
            "llm_assistance": dict(self.llm_assistance),
            "duplicates_skipped": result.duplicates_skipped,
            "filtered_out": result.filtered_out,
            "known_path_fallback": _known_path_fallback_summary(known_path_fallbacks),
        }

    def _persist_source_judgments(
        self,
        candidates: list[CandidateUrl],
        judgments: list[dict[str, Any]],
    ) -> None:
        if self.source_judge_service is None or not judgments:
            return
        candidates_by_url = {candidate.canonical_url: candidate for candidate in candidates}
        for judgment in judgments:
            canonical_url = judgment.get("canonical_url")
            if not isinstance(canonical_url, str):
                continue
            candidate = candidates_by_url.get(canonical_url)
            if candidate is None:
                continue
            metadata = dict(candidate.metadata_json or {})
            output = judgment.get("output_judgment")
            metadata["llm_source_judge"] = _json_safe(judgment)
            if (
                self.source_judge_service.active_rerank
                and isinstance(output, dict)
                and judgment.get("used_in_final_ranking") is True
            ):
                metadata["llm_source_judge_priority_delta"] = _source_judge_priority_delta(output)
            if (
                getattr(self.source_judge_service, "active_triage", False)
                and isinstance(output, dict)
                and judgment.get("used_in_final_ranking") is True
            ):
                metadata["llm_source_triage_active"] = True
            candidate.metadata_json = metadata
        self.session.flush()

    def _run_planner_if_configured(self, task_id: UUID) -> None:
        task = self._get_task(task_id)
        existing_plan = self._latest_existing_research_plan(task)
        if existing_plan is not None:
            self.research_plan = existing_plan
            self._apply_query_rewriter_if_configured(task)
            return
        if self.planner_service is None:
            if self.query_rewriter_service is not None and self.query_rewriter_service.enabled:
                self.research_plan = build_default_research_plan(
                    task.query,
                    max_subquestions=5,
                    max_search_queries=8,
                    planner_mode="deterministic",
                )
                self._apply_query_rewriter_if_configured(task)
            return
        planner_result = build_optional_research_plan(
            planner_service=self.planner_service,
            task_id=task_id,
            query=task.query,
            constraints=dict(task.constraints_json),
            max_subquestions=self.planner_service.max_subquestions,
            max_search_queries=self.planner_service.max_search_queries,
            llm_plan_source="llm_planner",
            failure_plan_source="pipeline_deterministic_fallback_after_llm_failure",
        )
        if planner_result.failure is not None:
            self._record_event(
                task_id,
                "research_plan.failed",
                {
                    **self._pipeline_payload(from_status=task.status, to_status=task.status),
                    "changes": {
                        "revision_no": task.revision_no,
                        "research_plan_source": "pipeline_failed",
                    },
                    "stage": "PLANNING",
                    "planner_enabled": True,
                    "planner_status": "failed",
                    "fallback": "deterministic_plan",
                    "reason": planner_result.failure.get("reason"),
                    "details": _json_safe(planner_result.failure),
                    "warnings": planner_result.warnings,
                },
            )

        plan = planner_result.plan
        self.research_plan = plan
        self._apply_query_rewriter_if_configured(task)
        plan = self.research_plan or plan
        self._record_event(
            task_id,
            "research_plan.created",
            {
                **self._pipeline_payload(from_status=task.status, to_status=task.status),
                "changes": {
                    "revision_no": task.revision_no,
                    "research_plan_source": planner_result.plan_source,
                },
                "stage": "PLANNING",
                "planner_enabled": True,
                "planner_status": planner_result.planner_status,
                "planner_mode": plan.planner_mode,
                "plan_source": planner_result.plan_source,
                "result": {
                    "research_plan": plan.to_payload(),
                    **plan.summary_payload(),
                },
                "warnings": planner_result.warnings,
            },
        )
        self.session.commit()

    def _apply_query_rewriter_if_configured(self, task: ResearchTask) -> None:
        if self.query_rewriter_service is None or self.research_plan is None:
            return
        result = self.query_rewriter_service.rewrite(
            query=task.query,
            plan=self.research_plan,
            constraints=dict(task.constraints_json),
        )
        self.llm_assistance[result.stage] = {
            "enabled": self.query_rewriter_service.enabled,
            "used": result.used,
            "status": result.status,
            "fallback_reason": result.fallback_reason,
            **_json_safe(result.diagnostics),
        }
        if not result.search_queries:
            return
        existing_query_texts = {query.query_text for query in self.research_plan.search_queries}
        merged_queries = list(self.research_plan.search_queries)
        for rewritten_query in sorted(result.search_queries, key=lambda item: item.priority):
            if rewritten_query.query_text in existing_query_texts:
                continue
            merged_queries.append(rewritten_query)
            existing_query_texts.add(rewritten_query.query_text)
        self.research_plan = ResearchPlan(
            intent=self.research_plan.intent,
            normalized_question=self.research_plan.normalized_question,
            subquestions=list(self.research_plan.subquestions),
            search_queries=merged_queries,
            source_preferences=dict(self.research_plan.source_preferences),
            answer_outline=list(self.research_plan.answer_outline),
            risk_notes=list(self.research_plan.risk_notes),
            planner_mode=self.research_plan.planner_mode,
            warnings=list(self.research_plan.warnings),
            answer_slots=list(self.research_plan.answer_slots),
            raw_planner_queries=list(self.research_plan.raw_planner_queries),
            final_search_queries=[
                *list(self.research_plan.final_search_queries),
                *[query.to_payload() for query in result.search_queries],
            ],
            dropped_or_downweighted_planner_queries=list(
                self.research_plan.dropped_or_downweighted_planner_queries
            ),
            planner_guardrail_warnings=list(self.research_plan.planner_guardrail_warnings),
            intent_classification=self.research_plan.intent_classification,
            extracted_entity=self.research_plan.extracted_entity,
            planner_diagnostics={
                **dict(self.research_plan.planner_diagnostics),
                "llm_query_rewriter": self.llm_assistance[result.stage],
            },
        )

    def _latest_existing_research_plan(self, task: ResearchTask) -> ResearchPlan | None:
        for event in reversed(self.event_repository.list_for_task(task.id)):
            if event.event_type != "research_plan.created":
                continue
            payload = event.payload_json or {}
            if not isinstance(payload, dict):
                continue
            changes = payload.get("changes")
            if isinstance(changes, dict):
                revision_no = changes.get("revision_no")
                if isinstance(revision_no, int) and revision_no != task.revision_no:
                    continue
            result = payload.get("result")
            if not isinstance(result, dict):
                continue
            plan_payload = result.get("research_plan")
            if not isinstance(plan_payload, dict):
                continue
            return research_plan_from_serialized_payload(plan_payload)
        return None

    def _run_fetch(self, task_id: UUID) -> dict[str, Any]:
        target_successful_snapshots = self.target_successful_snapshots
        fetch_limit = self.fetch_limit
        if self._is_planner_overview_run():
            target_successful_snapshots = max(
                target_successful_snapshots,
                self.min_answer_sources,
                6,
            )
            fetch_limit = max(fetch_limit, 6)
        elif self._is_recent_official_source_run():
            target_successful_snapshots = max(
                target_successful_snapshots,
                self.min_answer_sources,
                4,
            )
            fetch_limit = max(fetch_limit, 5)
        elif self._is_comparison_run(task_id):
            target_successful_snapshots = max(
                target_successful_snapshots,
                self.min_answer_sources,
                4,
            )
            fetch_limit = max(fetch_limit, 6)
        elif self._is_deployment_run(task_id):
            target_successful_snapshots = max(target_successful_snapshots, 4)
            fetch_limit = max(fetch_limit, 5)
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
        start = time.monotonic()
        batches_run = 0
        agg_created = 0
        agg_updated = 0
        agg_skipped_existing = 0
        agg_skipped_unsupported = 0
        agg_skipped_static_html_hold = 0
        agg_skipped_no_valid_chunks = 0
        agg_failed = 0
        agg_invalid_chunk = 0
        agg_invalid_reasons: Counter[str] = Counter()
        all_parse_decisions: list[dict[str, Any]] = []
        first_created_plus_updated = 0
        last_result = None
        drain_stop_reason = "unknown"

        while True:
            batches_run += 1
            last_result = self.parsing_service.parse_snapshots(
                task_id,
                content_snapshot_ids=None,
                limit=parse_limit,
            )
            agg_created += last_result.created
            agg_updated += last_result.updated
            agg_skipped_existing += last_result.skipped_existing
            agg_skipped_unsupported += last_result.skipped_unsupported
            agg_skipped_static_html_hold += last_result.skipped_static_html_hold
            agg_skipped_no_valid_chunks += last_result.skipped_no_valid_chunks
            agg_failed += last_result.failed
            agg_invalid_chunk += last_result.invalid_chunk_rejection_count
            dist = last_result.invalid_chunk_rejection_reason_distribution or {}
            for k, v in dist.items():
                if isinstance(v, int):
                    agg_invalid_reasons[str(k)] += v
            all_parse_decisions.extend(
                parse_entry_diagnostic(entry) for entry in last_result.entries
            )
            if batches_run == 1:
                first_created_plus_updated = last_result.created + last_result.updated

            if not self.parse_drain_enabled:
                drain_stop_reason = "disabled"
                break
            if batches_run >= self.parse_drain_max_batches:
                drain_stop_reason = "max_batches"
                break
            if self.parse_drain_max_seconds > 0 and (
                time.monotonic() - start >= self.parse_drain_max_seconds
            ):
                drain_stop_reason = "max_seconds"
                break
            if self.parsing_service.count_source_documents_for_task(task_id) >= (
                self.parse_drain_target_documents
            ):
                drain_stop_reason = "target_documents"
                break
            if last_result.created + last_result.updated == 0:
                drain_stop_reason = "no_progress"
                break
            if self.parsing_service.count_eligible_snapshots_without_source_document(task_id) == 0:
                drain_stop_reason = "fully_drained"
                break

        extra_batches = max(0, batches_run - 1)
        extra_documents = max(0, agg_created + agg_updated - first_created_plus_updated)
        unparsed_eligible_after = (
            self.parsing_service.count_eligible_snapshots_without_source_document(task_id)
        )
        if self.parse_drain_enabled and unparsed_eligible_after == 0:
            drain_stop_reason = "fully_drained"

        rlimit = max(1, int(self.parse_limit))
        parse_limit_exhausted = unparsed_eligible_after > 0 and (
            (not self.parse_drain_enabled and unparsed_eligible_after > rlimit)
            or (
                self.parse_drain_enabled
                and drain_stop_reason
                in ("max_batches", "max_seconds", "target_documents", "no_progress")
            )
        )

        stage_result = {
            "created": agg_created,
            "updated": agg_updated,
            "skipped_existing": agg_skipped_existing,
            "skipped_unsupported": agg_skipped_unsupported,
            "skipped_static_html_hold": agg_skipped_static_html_hold,
            "skipped_no_valid_chunks": agg_skipped_no_valid_chunks,
            "failed": agg_failed,
            "invalid_chunk_rejection_count": agg_invalid_chunk,
            "invalid_chunk_rejection_reason_distribution": dict(
                sorted(agg_invalid_reasons.items())
            ),
            "snapshots_with_no_valid_chunks": agg_skipped_no_valid_chunks,
            "parser_invalid_output_count": agg_skipped_no_valid_chunks,
            "rejection_reason_distribution": _aggregate_parse_rejection_reasons(
                all_parse_decisions
            ),
            "parse_decisions": all_parse_decisions,
            "parse_drain_enabled": self.parse_drain_enabled,
            "parse_drain_batches_run": batches_run,
            "parse_drain_extra_batches": extra_batches,
            "parse_drain_extra_documents": extra_documents,
            "parse_drain_created_documents": agg_created + agg_updated,
            "parse_drain_stop_reason": drain_stop_reason,
            "eligible_snapshots_without_source_document": unparsed_eligible_after,
            "parse_limit_exhausted": parse_limit_exhausted,
        }
        if agg_created + agg_updated + agg_skipped_existing <= 0:
            raise DebugPipelinePreconditionError(
                _format_parse_no_documents_message(all_parse_decisions),
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
        claim_limit = _claim_limit_for_query(task.query, self.claim_limit)
        source_chunk_ids = _select_claim_drafting_chunk_ids(
            self.session,
            task_id,
            query=task.query,
            limit=max(claim_limit * 2, 8),
        )
        source_chunk_ids, evidence_rerank_diagnostics = self._rerank_claim_chunks_if_configured(
            task,
            source_chunk_ids,
        )
        result: Any = self.claims_service.draft_claims(
            task_id,
            query=task.query,
            source_chunk_ids=source_chunk_ids,
            limit=claim_limit,
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
                    limit=max(claim_limit * 2, 8),
                )
                refreshed_chunk_ids, refreshed_rerank = self._rerank_claim_chunks_if_configured(
                    task,
                    refreshed_chunk_ids,
                )
                if refreshed_rerank:
                    evidence_rerank_diagnostics = refreshed_rerank
                second_result = self.claims_service.draft_claims(
                    task_id,
                    query=task.query,
                    source_chunk_ids=refreshed_chunk_ids,
                    limit=claim_limit,
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
            "llm_assistance": {
                **dict(self.llm_assistance),
                **(
                    {"evidence_reranker": evidence_rerank_diagnostics}
                    if evidence_rerank_diagnostics
                    else {}
                ),
            },
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
            "llm_assistance": {
                **dict(self.llm_assistance),
                **(
                    {"evidence_reranker": evidence_rerank_diagnostics}
                    if evidence_rerank_diagnostics
                    else {}
                ),
            },
        }

    def _rerank_claim_chunks_if_configured(
        self,
        task: ResearchTask,
        source_chunk_ids: list[UUID],
    ) -> tuple[list[UUID], dict[str, Any] | None]:
        if self.evidence_reranker_service is None or not source_chunk_ids:
            return source_chunk_ids, None
        chunks = SourceChunkRepository(self.session).list_by_ids_for_task(task.id, source_chunk_ids)
        chunks_by_id = {chunk.id: chunk for chunk in chunks}
        ordered_chunks = [
            chunks_by_id[chunk_id] for chunk_id in source_chunk_ids if chunk_id in chunks_by_id
        ]
        result = self.evidence_reranker_service.rerank(
            query=task.query,
            chunks=ordered_chunks,
            answer_slots=[slot.to_payload() for slot in answer_slots_for_query(task.query)],
        )
        summary = {
            "enabled": self.evidence_reranker_service.enabled,
            "used": result.used,
            "status": result.status,
            "fallback_reason": result.fallback_reason,
            **_json_safe(result.diagnostics),
        }
        self.llm_assistance[result.stage] = summary
        return result.source_chunk_ids, summary

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
        task = self._get_task(task_id)
        claim_limit = _claim_limit_for_query(task.query, self.claim_limit)
        claim_review_summary = self._review_claims_if_configured(task, claim_limit=claim_limit)
        result = self.claims_service.verify_claims(
            task_id,
            claim_ids=None,
            limit=claim_limit,
        )
        if not result.entries:
            raise DebugPipelinePreconditionError("claim verification found no draft claims")
        verification_summary = _build_verification_summary(result.entries)
        slot_coverage_summary = build_slot_coverage_summary(
            task.query,
            evidence_candidates=_evidence_candidates_from_claims(self.session, task_id),
            claim_rows=_claim_rows_for_slot_summary_from_claims(
                self.session,
                task_id,
                query=task.query,
            ),
        )
        return {
            "verified_claims": result.verified_claims,
            "created_citation_spans": result.created_citation_spans,
            "reused_citation_spans": result.reused_citation_spans,
            "created_claim_evidence": result.created_claim_evidence,
            "reused_claim_evidence": result.reused_claim_evidence,
            "verification_summary": verification_summary,
            "slot_coverage_summary": slot_coverage_summary,
            "llm_assistance": {
                **dict(self.llm_assistance),
                **({"claim_reviewer": claim_review_summary} if claim_review_summary else {}),
            },
        }

    def _review_claims_if_configured(
        self,
        task: ResearchTask,
        *,
        claim_limit: int,
    ) -> dict[str, Any] | None:
        if self.claim_reviewer_service is None:
            return None
        claims = ClaimRepository(self.session).list_for_task(
            task.id,
            verification_status="draft",
            limit=claim_limit,
        )
        result = self.claim_reviewer_service.review(query=task.query, claims=claims)
        decisions_by_claim_id = {
            decision["claim_id"]: decision
            for decision in result.decisions
            if isinstance(decision.get("claim_id"), str)
            and result.used
            and result.status == "used"
            and not decision.get("quality_flags")
        }
        for claim in claims:
            decision = decisions_by_claim_id.get(str(claim.id))
            if decision is None:
                continue
            claim.notes_json = {
                **(claim.notes_json or {}),
                "llm_claim_review": _json_safe(decision),
            }
        self.session.flush()
        summary = {
            "enabled": self.claim_reviewer_service.enabled,
            "used": result.used,
            "status": result.status,
            "fallback_reason": result.fallback_reason,
            **_json_safe(result.diagnostics),
        }
        self.llm_assistance[result.stage] = summary
        return summary

    def _run_report(self, task_id: UUID) -> dict[str, Any]:
        result = self.reporting_service.generate_markdown_report(task_id)
        if not result.markdown.strip():
            raise DebugPipelinePreconditionError("report generation produced empty markdown")
        source_quality_summary = _build_source_quality_summary(self.session, task_id)
        manifest = result.artifact.manifest_json or {}
        return {
            "report_artifact_id": result.artifact.id,
            "report_version": result.artifact.version,
            "report_language": result.report_language,
            "report_writer_mode": result.writer_mode,
            "llm_writer_status": result.llm_writer_status,
            "supported_claims": result.supported_claims,
            "mixed_claims": result.mixed_claims,
            "contradicted_claims": result.contradicted_claims,
            "unsupported_claims": result.unsupported_claims,
            "draft_claims": result.draft_claims,
            "report_markdown_preview": result.markdown[:500],
            "source_quality_summary": source_quality_summary,
            "slot_coverage_summary": manifest.get("slot_coverage_summary", []),
            "source_yield_summary": manifest.get("source_yield_summary", []),
            "evidence_yield_summary": manifest.get("evidence_yield_summary", {}),
            "verification_summary": manifest.get("verification_summary", {}),
            "dropped_sources": manifest.get("dropped_sources", []),
            "report_writer": manifest.get("report_writer", {}),
            "llm_assistance": dict(self.llm_assistance),
            "warnings": source_quality_summary["warnings"],
        }

    def _mark_started(self, task: ResearchTask) -> None:
        from_status = task.status
        run = self._get_or_create_current_run(task)
        task.started_at = task.started_at or datetime.now(UTC)
        self.task_repository.set_status(task, STATUS_RUNNING, ended_at=None)
        self._update_run_checkpoint(
            run,
            current_state=STATUS_RUNNING,
            checkpoint_patch={
                "phase": "pipeline",
                "current_stage": STATUS_RUNNING,
                "stages_completed": sorted(self._checkpoint_completed_stages(task)),
            },
        )
        self._record_event(
            task.id,
            self._event_type("started"),
            {
                **self._pipeline_payload(from_status=from_status, to_status=STATUS_RUNNING),
                "stage": STATUS_RUNNING,
                "status_note": "pipeline worker run started",
                "running_mode": _running_mode_from_dependencies(self.dependencies),
                "dependencies": _json_safe(self.dependencies),
            },
            run_id=run.id,
        )
        self.session.commit()

    def _mark_completed(self, task_id: UUID) -> ResearchTask:
        task = self._get_task(task_id)
        from_status = task.status
        run = self._get_or_create_current_run(task)
        self.task_repository.set_status(task, STATUS_COMPLETED, ended_at=datetime.now(UTC))
        self._update_run_checkpoint(
            run,
            current_state=STATUS_COMPLETED,
            ended_at=datetime.now(UTC),
            checkpoint_patch={"current_stage": STATUS_COMPLETED},
        )
        self._record_event(
            task.id,
            self._event_type("completed"),
            {
                **self._pipeline_payload(from_status=from_status, to_status=STATUS_COMPLETED),
                "stage": STATUS_COMPLETED,
                "counts": _counts_to_dict(self._safe_counts(task.id)),
            },
            run_id=run.id,
        )
        self.session.commit()
        self.session.refresh(task)
        return task

    def _record_stage_started(self, task_id: UUID, stage: str) -> None:
        task = self._get_task(task_id)
        from_status = task.status
        run = self._get_or_create_current_run(task)
        self.task_repository.set_status(task, stage, ended_at=None)
        self._update_run_checkpoint(
            run,
            current_state=stage,
            checkpoint_patch={"current_stage": stage},
        )
        self._record_event(
            task_id,
            self._event_type("stage_started"),
            {
                **self._pipeline_payload(from_status=from_status, to_status=stage),
                "stage": stage,
            },
            run_id=run.id,
        )
        self.session.commit()

    def _record_stage_completed(
        self,
        task_id: UUID,
        stage: str,
        stage_result: dict[str, Any],
        *,
        stages_completed: list[str],
    ) -> None:
        task = self._get_task(task_id)
        run = self._get_or_create_current_run(task)
        self._update_run_checkpoint(
            run,
            current_state=task.status,
            checkpoint_patch={
                "current_stage": task.status,
                "last_completed_stage": stage,
                "stages_completed": list(stages_completed),
            },
        )
        payload: dict[str, Any] = {
            **self._pipeline_payload(from_status=task.status, to_status=task.status),
            "stage": stage,
            "result": _json_safe(stage_result),
            "counts": _counts_to_dict(self._safe_counts(task_id)),
            "warnings": _stage_warnings(stage_result),
        }
        if stage == STAGE_RESEARCHING_MORE and isinstance(stage_result, dict):
            gap_diag = stage_result.get("gap_round_diagnostics")
            if isinstance(gap_diag, dict):
                payload["gap_round_outcome"] = gap_diag.get("gap_round_outcome")
                payload["skip_drafting_reason"] = gap_diag.get("skip_drafting_reason")
                payload["gap_round_index"] = gap_diag.get("gap_round_index")
        self._record_event(
            task_id,
            self._event_type("stage_completed"),
            payload,
            run_id=run.id,
        )
        self.session.commit()

    def _record_failure(self, task_id: UUID, failure: DebugPipelineFailure) -> None:
        task = self._get_task(task_id)
        from_status = task.status
        run = self._get_or_create_current_run(task)
        self.task_repository.set_status(task, STATUS_FAILED, ended_at=datetime.now(UTC))
        self._update_run_checkpoint(
            run,
            current_state=STATUS_FAILED,
            ended_at=datetime.now(UTC),
            checkpoint_patch={
                "current_stage": STATUS_FAILED,
                "failed_stage": failure.stage,
                "failure_reason": failure.reason,
            },
        )
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
            run_id=run.id,
        )
        self.session.commit()

    def _record_gap_analysis(self, task_id: UUID, gap_analysis: dict[str, Any]) -> None:
        task = self._get_task(task_id)
        run = self._get_or_create_current_run(task)
        self._update_run_checkpoint(
            run,
            current_state=task.status,
            checkpoint_patch={"last_gap_analysis": _json_safe(gap_analysis)},
        )
        self._record_event(
            task_id,
            self._event_type("gap_analysis"),
            {
                **self._pipeline_payload(from_status=task.status, to_status=task.status),
                "stage": STAGE_RESEARCHING_MORE,
                "result": _json_safe(gap_analysis),
                "warnings": _stage_warnings(gap_analysis),
            },
            run_id=run.id,
        )
        self.session.commit()

    def _record_research_strategy(self, task_id: UUID, strategy: dict[str, Any]) -> None:
        task = self._get_task(task_id)
        run = self._get_or_create_current_run(task)
        self._update_run_checkpoint(
            run,
            current_state=task.status,
            checkpoint_patch={"last_research_strategy": _json_safe(strategy)},
        )
        self._record_event(
            task_id,
            self._event_type("research_strategy"),
            {
                **self._pipeline_payload(from_status=task.status, to_status=task.status),
                "stage": STAGE_RESEARCHING_MORE,
                "result": _json_safe(strategy),
                "warnings": _stage_warnings(strategy),
            },
            run_id=run.id,
        )
        self.session.commit()

    def _record_event(
        self,
        task_id: UUID,
        event_type: str,
        payload_json: dict[str, Any],
        *,
        run_id: UUID | None = None,
    ) -> None:
        self.event_repository.record(
            task_id=task_id,
            event_type=event_type,
            payload_json=payload_json,
            run_id=run_id,
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

    def _ensure_task_can_continue(self, task_id: UUID) -> None:
        task = self._get_task(task_id)
        self.session.refresh(task)
        if task.status in {STATUS_PAUSED, STATUS_CANCELLED}:
            raise DebugPipelineInterrupted(task_id, task.status)

    def _get_or_create_current_run(self, task: ResearchTask) -> ResearchRun:
        latest_run = self.run_repository.get_latest_for_task(task.id)
        if latest_run is not None and _run_revision_no(latest_run) == task.revision_no:
            return latest_run
        next_round_no = 1 if latest_run is None else latest_run.round_no + 1
        return self.run_repository.add(
            ResearchRun(
                task_id=task.id,
                round_no=next_round_no,
                current_state=task.status,
                checkpoint_json={
                    "task_revision_no": task.revision_no,
                    "phase": "pipeline",
                    "stages_completed": [],
                },
            )
        )

    def _update_run_checkpoint(
        self,
        run: ResearchRun,
        *,
        current_state: str,
        checkpoint_patch: dict[str, Any],
        ended_at: datetime | None = None,
    ) -> None:
        checkpoint = dict(run.checkpoint_json or {})
        checkpoint.update(
            {
                **checkpoint_patch,
                "task_revision_no": checkpoint.get("task_revision_no")
                or self._get_task(run.task_id).revision_no,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        run.current_state = current_state
        run.checkpoint_json = _json_safe(checkpoint)
        if ended_at is not None:
            run.ended_at = ended_at
        self.session.flush()

    def _checkpoint_completed_stages(self, task: ResearchTask) -> set[str]:
        latest_run = self.run_repository.get_latest_for_task(task.id)
        if latest_run is None or _run_revision_no(latest_run) != task.revision_no:
            return set()
        value = (latest_run.checkpoint_json or {}).get("stages_completed")
        if not isinstance(value, list):
            return set()
        return {stage for stage in value if isinstance(stage, str)}

    def _latest_stage_result(self, task_id: UUID, stage: str) -> dict[str, Any] | None:
        for event in reversed(self.event_repository.list_for_task(task_id)):
            payload = event.payload_json or {}
            if not isinstance(payload, dict):
                continue
            if payload.get("stage") != stage:
                continue
            result = payload.get("result")
            if isinstance(result, dict):
                return result
        return None

    def _resolve_max_gap_rounds(self, task: ResearchTask) -> int:
        constraints = task.constraints_json or {}
        raw_value = constraints.get("max_gap_rounds", constraints.get("max_rounds"))
        if isinstance(raw_value, int) and raw_value >= 0:
            return raw_value
        return self.max_gap_rounds

    def _current_slot_coverage_summary(self, task_id: UUID) -> list[dict[str, Any]]:
        task = self._get_task(task_id)
        return build_slot_coverage_summary(
            task.query,
            evidence_candidates=_evidence_candidates_from_claims(self.session, task_id),
            claim_rows=_claim_rows_for_slot_summary_from_claims(
                self.session,
                task_id,
                query=task.query,
            ),
        )

    def _current_coverage_evaluation(
        self,
        task_id: UUID,
        *,
        slot_coverage_summary: list[dict[str, Any]],
        round_no: int,
        max_rounds: int,
    ) -> dict[str, Any]:
        budget_exhausted = (
            round_no > max_rounds
            or self._safe_counts(task_id).fetch_attempts
            >= self.research_loop_max_total_fetch_attempts
        )
        source_yield_summary = _build_source_yield_summary(
            self.session,
            task_id,
            query=self._get_task(task_id).query,
            evidence_candidates=_evidence_candidates_from_claims(self.session, task_id),
            accepted_candidate_ids=set(),
        )
        return evaluate_research_coverage(
            slot_coverage_summary=slot_coverage_summary,
            source_yield_summary=source_yield_summary,
            required_slot_min_status=self.research_loop_required_slot_min_status,
            min_distinct_domains=self.research_loop_min_distinct_domains,
            min_authoritative_sources=self.research_loop_min_authoritative_sources,
            min_source_roles=2,  # Default for now, could be made a setting
            allow_low_coverage_report=self.research_loop_allow_low_coverage_report,
            budget_exhausted=budget_exhausted,
        ).to_payload() | {"source_yield_summary": source_yield_summary}

    def _current_report_quality_gate(
        self,
        task_id: UUID,
        *,
        slot_coverage_summary: list[dict[str, Any]],
        coverage_evaluation: dict[str, Any],
        round_no: int,
        max_rounds: int,
    ) -> dict[str, Any]:
        task = self._get_task(task_id)
        counts = self._safe_counts(task_id)
        return _evaluate_report_quality_gate(
            self.session,
            task_id,
            query=task.query,
            counts=counts,
            slot_coverage_summary=slot_coverage_summary,
            coverage_evaluation=coverage_evaluation,
            min_main_claims=_minimum_report_main_claims(task.query),
            min_support_evidence=_minimum_report_support_evidence(task.query),
            min_source_documents=MIN_REPORT_SOURCE_DOCUMENTS,
            round_no=round_no,
            max_rounds=max_rounds,
            max_total_queries=self.research_loop_max_total_queries,
            max_total_fetch_attempts=self.research_loop_max_total_fetch_attempts,
        )

    def _has_followup_budget(self, task_id: UUID, *, round_no: int, max_rounds: int) -> bool:
        counts = self._safe_counts(task_id)
        return (
            round_no <= max_rounds
            and counts.search_queries < self.research_loop_max_total_queries
            and counts.fetch_attempts < self.research_loop_max_total_fetch_attempts
        )

    def _run_research_strategy_if_configured(
        self,
        task_id: UUID,
        *,
        round_no: int,
        max_rounds: int,
        slot_coverage_summary: list[dict[str, Any]],
        coverage_evaluation: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self.research_strategist_service is None:
            return None
        if not self.research_strategist_service.enabled:
            return None
        if self.research_strategy_calls >= self.research_loop_max_strategy_calls:
            return {
                "status": "skipped",
                "used": False,
                "fallback_reason": "strategy_call_budget_exhausted",
                "decision": None,
                "planned_queries": [],
                "diagnostics": {
                    "strategy_calls": self.research_strategy_calls,
                    "max_strategy_calls": self.research_loop_max_strategy_calls,
                },
                "coverage_evaluation": coverage_evaluation,
                "round_no": round_no,
                "max_rounds": max_rounds,
            }
        research_state = self._research_strategy_state(
            task_id,
            round_no=round_no,
            max_rounds=max_rounds,
            slot_coverage_summary=slot_coverage_summary,
            coverage_evaluation=coverage_evaluation,
        )
        self.research_strategy_calls += 1
        result = self.research_strategist_service.decide(
            research_state,
            existing_query_texts=self._existing_search_query_texts(task_id),
        )
        payload = result.to_payload()
        payload["round_no"] = round_no
        payload["max_rounds"] = max_rounds
        payload["shadow_mode"] = self.research_loop_strategist_shadow_mode
        payload["active_loop_enabled"] = self.research_loop_enabled
        payload["coverage_evaluation"] = coverage_evaluation
        payload["budget_remaining"] = research_state["budget_remaining"]
        self.llm_assistance["research_strategist"] = {
            "enabled": self.research_strategist_service.enabled,
            "used": result.used,
            "status": result.status,
            "fallback_reason": result.fallback_reason,
            "decision": result.decision,
            "planned_query_count": len(result.planned_queries),
        }
        return payload

    def _research_strategy_state(
        self,
        task_id: UUID,
        *,
        round_no: int,
        max_rounds: int,
        slot_coverage_summary: list[dict[str, Any]],
        coverage_evaluation: dict[str, Any],
    ) -> dict[str, Any]:
        task = self._get_task(task_id)
        counts = self._safe_counts(task_id)
        previous_queries = [
            {
                "query_text": item.query_text,
                "round": item.round_no,
                "provider": item.provider,
                "result_count": _safe_int((item.raw_response_json or {}).get("result_count")),
            }
            for item in SearchQueryRepository(self.session).list_for_task(task_id)
        ][-20:]
        fetch_jobs = FetchJobRepository(self.session).list_for_task(task_id)
        attempted_candidate_ids = {fetch_job.candidate_url_id for fetch_job in fetch_jobs}
        candidate_summary = []
        for candidate in CandidateUrlRepository(self.session).list_for_task(task_id)[:30]:
            candidate_summary.append(
                {
                    **_candidate_url_summary(candidate, query=task.query),
                    "attempt_status": (
                        "ATTEMPTED" if candidate.id in attempted_candidate_ids else "UNATTEMPTED"
                    ),
                }
            )
        verified_claim_summary = []
        for claim in ClaimRepository(self.session).list_for_task(task_id)[:30]:
            if claim.verification_status == "draft":
                continue
            notes = claim.notes_json if isinstance(claim.notes_json, dict) else {}
            verified_claim_summary.append(
                {
                    "claim": claim.statement,
                    "verification_status": claim.verification_status,
                    "covered_slots": [
                        item for item in notes.get("slot_ids", []) if isinstance(item, str)
                    ],
                    "support_level": _claim_support_level_from_notes(notes),
                }
            )
        return {
            "question": task.query,
            "normalized_question": (
                self.research_plan.normalized_question
                if self.research_plan is not None
                else task.query
            ),
            "round_index": round_no,
            "budget_remaining": {
                "max_rounds_remaining": max(0, max_rounds - round_no + 1),
                "search_queries_remaining": max(
                    0,
                    self.research_loop_max_total_queries - counts.search_queries,
                ),
                "fetch_attempts_remaining": max(
                    0,
                    self.research_loop_max_total_fetch_attempts - counts.fetch_attempts,
                ),
                "llm_calls_remaining": max(
                    0,
                    self.research_loop_max_strategy_calls - self.research_strategy_calls,
                ),
            },
            "answer_slots": slot_coverage_summary,
            "coverage_evaluation": coverage_evaluation,
            "previous_queries": previous_queries,
            "candidate_summary": candidate_summary,
            "verified_claim_summary": verified_claim_summary,
            "constraints": {
                "max_queries_per_round": self.gap_max_queries_per_round,
                "required_slot_min_status": self.research_loop_required_slot_min_status,
                "min_distinct_domains": self.research_loop_min_distinct_domains,
                "min_authoritative_sources": self.research_loop_min_authoritative_sources,
            },
        }

    def _strategy_should_drive_followup(self, strategy_payload: dict[str, Any] | None) -> bool:
        if strategy_payload is None:
            return False
        if not self.research_loop_enabled or self.research_loop_strategist_shadow_mode:
            return False
        if strategy_payload.get("status") not in {"used", "fallback"}:
            return False
        decision = strategy_payload.get("decision")
        if decision == "continue_search":
            return bool(strategy_payload.get("planned_queries"))
        if decision == "fetch_more_existing_candidates":
            return True
        return False

    def _existing_evidence_status_for_gap_fallback(self, task_id: UUID) -> dict[str, Any]:
        counts = self._safe_counts(task_id)
        slot_coverage_summary = self._current_slot_coverage_summary(task_id)
        covered_required_slots = [
            slot.get("slot_id")
            for slot in slot_coverage_summary
            if slot.get("required") is True and slot.get("status") in {"covered", "weak"}
        ]
        partial_report_possible = (
            counts.source_documents > 0
            and counts.source_chunks > 0
            and (counts.claims > 0 or counts.claim_evidence > 0)
        )
        usable_evidence = partial_report_possible or bool(covered_required_slots)
        return {
            "usable_evidence": usable_evidence,
            "partial_report_possible": partial_report_possible,
            "covered_required_slots": covered_required_slots,
            "counts": _counts_to_dict(counts),
        }

    def _existing_search_query_texts(self, task_id: UUID) -> set[str]:
        return {
            item.query_text
            for item in SearchQueryRepository(self.session).list_for_task(task_id)
            if item.query_text.strip()
        }

    def _is_planner_overview_run(self) -> bool:
        return bool(
            self.research_plan is not None
            and self.research_plan.intent_classification == "overview_definition_intent"
        )

    def _is_recent_official_source_run(self) -> bool:
        if self.research_plan is None:
            return False
        warnings = set(self.research_plan.planner_guardrail_warnings) | set(
            self.research_plan.warnings
        )
        return "planner_queries_supplemented_for_recent_nvidia_official_sources" in warnings

    def _is_deployment_run(self, task_id: UUID) -> bool:
        if self.research_plan is not None:
            if self.research_plan.intent == "deployment":
                return True
            if self.research_plan.intent_classification == "deployment_intent":
                return True
        task = self._get_task(task_id)
        return classify_query_intent(task.query).intent_name == "deployment"

    def _is_comparison_run(self, task_id: UUID) -> bool:
        task = self._get_task(task_id)
        lower = f" {task.query.lower()} "
        return " compare " in lower or " vs " in lower or " versus " in lower


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


def _counts_to_dict(counts: DebugPipelineCounts) -> dict[str, Any]:
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
        "yield_breakdown": (
            f"fetch_succeeded({counts.content_snapshots}) -> "
            f"source_documents({counts.source_documents}) -> "
            f"source_chunks({counts.source_chunks}) -> "
            f"claims({counts.claims}) -> "
            f"evidence({counts.claim_evidence})"
        ),
    }


def _run_revision_no(run: ResearchRun) -> int | None:
    value = (run.checkpoint_json or {}).get("task_revision_no")
    return value if isinstance(value, int) else None


def _candidate_url_summary(candidate_url: Any, *, query: str | None = None) -> dict[str, Any]:
    metadata = candidate_url.metadata_json or {}
    if not isinstance(metadata, dict):
        metadata = {}
    summary = {
        "candidate_url_id": str(candidate_url.id),
        "canonical_url": candidate_url.canonical_url,
        "domain": candidate_url.domain,
        "title": candidate_url.title,
        "rank": candidate_url.rank,
        **fetch_priority_metadata(candidate_url, query=query),
    }
    for key in (
        "candidate_source",
        "fallback_reason",
        "original_search_provider",
        "known_path_candidate",
        "source_selection_reason",
        "llm_source_judge_priority_delta",
        "llm_source_judge",
    ):
        value = metadata.get(key)
        if value is None:
            continue
        summary[key] = value
    return summary


def _known_path_fallback_summary(fallbacks: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_count = 0
    duplicates_skipped = 0
    filtered_out = 0
    for fallback in fallbacks:
        candidate_count += _safe_int(fallback.get("known_path_fallback_candidate_count"))
        duplicates_skipped += _safe_int(fallback.get("known_path_fallback_duplicates_skipped"))
        filtered_out += _safe_int(fallback.get("known_path_fallback_filtered_out"))
    return {
        "applied": bool(fallbacks),
        "candidate_count": candidate_count,
        "duplicates_skipped": duplicates_skipped,
        "filtered_out": filtered_out,
        "fallbacks": fallbacks,
    }


def _supplemental_search_provider_failure(
    search_result: dict[str, Any],
) -> dict[str, Any] | None:
    if search_result.get("supplemental_search") is not True:
        return None
    for search_query in search_result.get("search_queries", []):
        if not isinstance(search_query, dict):
            continue
        failure = search_query.get("search_provider_failure")
        if not isinstance(failure, dict):
            continue
        error = failure.get("error")
        if not isinstance(error, dict):
            error = failure.get("provider_error")
        reason = None
        if isinstance(error, dict):
            reason = error.get("reason")
        return {
            "reason": reason
            or failure.get("provider_error_reason")
            or failure.get("provider_failure_reason"),
            "error": error,
            **failure,
        }
    return None


def _safe_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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
    skipped_by_triage_sources = [
        {
            **_unattempted_candidate_summary(candidate_url, query=task_query),
            "skip_reason": "llm_source_triage_skip",
        }
        for candidate_url in getattr(result, "skipped_by_triage_candidates", [])
    ]
    failed_sources = [
        source for source in attempted_sources if source.get("fetch_status") == "FAILED"
    ]
    successful_sources = [
        source
        for source in attempted_sources
        if source.get("fetch_status") == "SUCCEEDED" and source.get("snapshot_id") is not None
    ]
    selected_sources = [*attempted_sources, *unattempted_sources, *skipped_by_triage_sources]
    dropped_sources = [
        {**source, "dropped_reasons": [_fetch_dropped_reason(source)]}
        for source in [*failed_sources, *unattempted_sources, *skipped_by_triage_sources]
    ]
    per_domain_attempt_distribution: dict[str, int] = {}
    for source in attempted_sources:
        domain = source.get("domain")
        if isinstance(domain, str) and domain:
            per_domain_attempt_distribution[domain] = (
                per_domain_attempt_distribution.get(domain, 0) + 1
            )
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
        "skipped_by_triage_sources": skipped_by_triage_sources,
        "selected_but_unattempted_count": len(unattempted_sources),
        "skipped_by_budget_count": len(unattempted_sources),
        "skipped_by_triage_count": len(skipped_by_triage_sources),
        "per_domain_attempt_distribution": dict(sorted(per_domain_attempt_distribution.items())),
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
    scored_sources = [
        source_document.final_source_score
        for source_document in source_documents
        if source_document.final_source_score is not None
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
        "average_source_quality": (
            round(sum(scored_sources) / len(scored_sources), 4) if scored_sources else None
        ),
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


def _planned_queries_from_gap_analysis(gap_analysis: dict[str, Any]) -> list[PlannedSearchQuery]:
    raw_queries = gap_analysis.get("supplemental_queries")
    if not isinstance(raw_queries, list):
        return []
    planned: list[PlannedSearchQuery] = []
    for index, item in enumerate(raw_queries, start=1):
        if not isinstance(item, dict):
            continue
        query_text = item.get("query_text")
        if not isinstance(query_text, str) or not query_text.strip():
            continue
        rationale = item.get("rationale")
        expected_source_type = item.get("expected_source_type")
        priority = item.get("priority")
        planned.append(
            PlannedSearchQuery(
                query_text=query_text.strip(),
                rationale=(
                    rationale.strip()
                    if isinstance(rationale, str) and rationale.strip()
                    else "Fill missing or weak required answer slots."
                ),
                expected_source_type=(
                    expected_source_type.strip()
                    if isinstance(expected_source_type, str) and expected_source_type.strip()
                    else "official_or_reference"
                ),
                priority=priority if isinstance(priority, int) and priority > 0 else index,
                query_source=(
                    item.get("query_source")
                    if isinstance(item.get("query_source"), str)
                    else "gap_analyzer"
                ),
                metadata={
                    "gap_round_no": item.get("round_no"),
                    "slot_ids": (
                        item.get("slot_ids") if isinstance(item.get("slot_ids"), list) else []
                    ),
                },
            )
        )
    return planned


def _gap_analysis_payload_from_strategy(
    fallback_gap_analysis: dict[str, Any],
    strategy_payload: dict[str, Any],
    *,
    round_no: int,
    max_rounds: int,
) -> dict[str, Any]:
    decision = strategy_payload.get("decision")
    planned_queries = [
        query
        for query in strategy_payload.get("planned_queries", [])
        if isinstance(query, dict) and isinstance(query.get("query_text"), str)
    ]
    if decision == "continue_search" and not planned_queries:
        return fallback_gap_analysis

    triggered = False
    reason = None
    if decision == "continue_search":
        triggered = True
        reason = "llm_research_strategy_continue_search"
    elif decision == "fetch_more_existing_candidates":
        triggered = True
        reason = "llm_research_strategy_fetch_more_existing_candidates"

    if not triggered:
        return fallback_gap_analysis

    return {
        **fallback_gap_analysis,
        "round_no": round_no,
        "max_rounds": max_rounds,
        "triggered": True,
        "reason": reason,
        "strategy_status": strategy_payload.get("status"),
        "strategy_decision": decision,
        "coverage_evaluation": strategy_payload.get("coverage_evaluation"),
        "supplemental_queries": [
            {
                **query,
                "query_source": "llm_research_strategist",
                "round_no": round_no,
                "slot_ids": (
                    query.get("metadata", {}).get("target_slots")
                    if isinstance(query.get("metadata"), dict)
                    else []
                ),
            }
            for query in planned_queries
        ],
        "fallback_gap_analysis": fallback_gap_analysis,
    }


def _maybe_suppress_strategist_gap_continue_for_coverage_alignment(
    analysis_payload: dict[str, Any],
    *,
    coverage_evaluation: dict[str, Any],
    slot_coverage_summary: list[dict[str, Any]],
    strategy_payload: dict[str, Any] | None,
    research_loop_enabled: bool,
    research_loop_strategist_shadow_mode: bool,
) -> dict[str, Any]:
    """
    If the LLM strategist requests ``continue_search`` but required coverage is already
    sufficient (overall ``sufficient``) and only optional weak slots remain, skip the
    supplemental gap search round by default.

    Quality-gate-driven follow-ups (``report_quality_gate_insufficient``) still run because
    this runs before the quality gate merge and only mutates strategist-triggered payloads.
    """
    if not research_loop_enabled or research_loop_strategist_shadow_mode:
        return analysis_payload
    if strategy_payload is None:
        return analysis_payload
    if strategy_payload.get("status") not in {"used", "fallback"}:
        return analysis_payload
    if strategy_payload.get("decision") != "continue_search":
        return analysis_payload
    planned = strategy_payload.get("planned_queries") or []
    if not planned:
        return analysis_payload
    if not analysis_payload.get("triggered"):
        return analysis_payload
    if coverage_evaluation.get("overall_status") != "sufficient":
        return analysis_payload
    miss_eval = coverage_evaluation.get("required_slots_missing") or []
    weak_eval = coverage_evaluation.get("required_slots_weak") or []
    if miss_eval or weak_eval:
        return analysis_payload
    for slot in slot_coverage_summary:
        if slot.get("required") is True and slot.get("status") in {"missing", "weak"}:
            return analysis_payload
    fallback = analysis_payload.get("fallback_gap_analysis")
    base = fallback if isinstance(fallback, dict) else analysis_payload
    if base.get("required_slots_missing") or base.get("required_slots_weak"):
        return analysis_payload

    out = dict(analysis_payload)
    out["triggered"] = False
    out["reason"] = COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY_STOP
    out["supplemental_queries"] = []
    out["loop_stop_reason"] = COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY_STOP
    out["coverage_alignment"] = {
        "suppressed_strategist_decision": "continue_search",
        "stop_reason": COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY_STOP,
        "prior_gap_reason": analysis_payload.get("reason"),
        "prior_strategy_status": strategy_payload.get("status"),
    }
    warns = list(out.get("warnings") or [])
    warns.append(
        "Strategist requested continue_search, but coverage is sufficient with only optional "
        "weak slots remaining; skipping supplemental gap search round."
    )
    out["warnings"] = warns
    return out


def _gap_analysis_payload_from_quality_gate(
    fallback_gap_analysis: dict[str, Any],
    quality_gate: dict[str, Any],
    *,
    query: str,
    round_no: int,
    max_rounds: int,
    max_queries: int,
    existing_query_texts: set[str],
) -> dict[str, Any]:
    queries = _quality_gate_supplemental_queries(
        query,
        quality_gate=quality_gate,
        round_no=round_no,
        max_queries=max_queries,
        existing_query_texts=existing_query_texts,
    )
    if not queries:
        return {
            **fallback_gap_analysis,
            "report_quality_gate": quality_gate,
            "warnings": [
                *list(fallback_gap_analysis.get("warnings", [])),
                "Report quality gate failed, but no non-duplicate follow-up query was generated.",
            ],
        }
    return {
        **fallback_gap_analysis,
        "round_no": round_no,
        "max_rounds": max_rounds,
        "triggered": True,
        "reason": "report_quality_gate_insufficient",
        "report_quality_gate": quality_gate,
        "supplemental_queries": queries,
    }


def _quality_gate_supplemental_queries(
    query: str,
    *,
    quality_gate: dict[str, Any],
    round_no: int,
    max_queries: int,
    existing_query_texts: set[str],
) -> list[dict[str, Any]]:
    existing = {" ".join(item.lower().split()) for item in existing_query_texts}
    metrics = quality_gate.get("metrics") if isinstance(quality_gate.get("metrics"), dict) else {}
    missing_slots = _string_list(metrics.get("missing_required_slots"))
    weak_slots = _string_list(metrics.get("weak_required_slots"))
    target_slots = list(dict.fromkeys([*missing_slots, *weak_slots]))
    query_variants: list[tuple[str, str, list[str]]] = []
    for slot_id in target_slots:
        query_variants.extend(
            [
                (
                    f"{query} {slot_id} authoritative evidence",
                    "official_or_reference",
                    [slot_id],
                ),
                (f"{query} {slot_id} 深度 证据", "official_or_reference", [slot_id]),
            ]
        )
    if not target_slots:
        query_variants.extend(
            [
                (f"{query} official documentation evidence", "official_docs", []),
                (f"{query} authoritative analysis reference", "official_or_reference", []),
                (f"{query} 机制 影响 权威资料", "official_or_reference", []),
                (f"{query} limitations evidence source", "reference", []),
            ]
        )

    planned: list[dict[str, Any]] = []
    for query_text, source_type, slot_ids in query_variants:
        normalized = " ".join(query_text.lower().split())
        if not normalized or normalized in existing:
            continue
        existing.add(normalized)
        planned.append(
            {
                "query_text": query_text,
                "rationale": (
                    "Report quality gate found thin claims, evidence, source diversity, "
                    "or unresolved coverage; collect additional grounded evidence before "
                    "final reporting."
                ),
                "expected_source_type": source_type,
                "priority": len(planned) + 1,
                "slot_ids": slot_ids,
                "round_no": round_no,
                "query_source": "report_quality_gate",
            }
        )
        if len(planned) >= max(1, max_queries):
            break
    return planned


def _evaluate_report_quality_gate(
    session: Session,
    task_id: UUID,
    *,
    query: str,
    counts: DebugPipelineCounts,
    slot_coverage_summary: list[dict[str, Any]],
    coverage_evaluation: dict[str, Any],
    min_main_claims: int,
    min_support_evidence: int,
    min_source_documents: int,
    round_no: int,
    max_rounds: int,
    max_total_queries: int,
    max_total_fetch_attempts: int,
) -> dict[str, Any]:
    claims = ClaimRepository(session).list_for_task(task_id)
    evidence_rows = ClaimEvidenceRepository(session).list_for_task(task_id)
    support_evidence_by_claim_id: dict[UUID, int] = {}
    support_domains: set[str] = set()
    for evidence in evidence_rows:
        if evidence.relation_type != "support":
            continue
        support_evidence_by_claim_id[evidence.claim_id] = (
            support_evidence_by_claim_id.get(evidence.claim_id, 0) + 1
        )
        support_domains.add(evidence.citation_span.source_chunk.source_document.domain)

    reportable_supported_claims = [
        claim
        for claim in claims
        if claim.verification_status == "supported"
        and support_evidence_by_claim_id.get(claim.id, 0) > 0
        and _claim_counts_toward_gap_slot_coverage(
            claim,
            query=query,
            slot_ids=[
                item
                for item in (claim.notes_json or {}).get("slot_ids", [])
                if isinstance(item, str)
            ],
        )
    ]
    unsupported_statuses = {"unsupported", "mixed", "contradicted", "draft"}
    unresolved_claim_count = sum(
        1 for claim in claims if claim.verification_status in unsupported_statuses
    )
    missing_required_slots = [
        str(slot.get("slot_id") or "unknown")
        for slot in slot_coverage_summary
        if slot.get("required") is True and slot.get("status") == "missing"
    ]
    weak_required_slots = [
        str(slot.get("slot_id") or "unknown")
        for slot in slot_coverage_summary
        if slot.get("required") is True and slot.get("status") == "weak"
    ]

    source_documents_min = max(
        min_source_documents,
        int(coverage_evaluation.get("min_distinct_domains") or 0),
    )
    issues: list[str] = []
    if len(reportable_supported_claims) < min_main_claims:
        issues.append("reportable_supported_claims_below_threshold")
    if sum(support_evidence_by_claim_id.values()) < min_support_evidence:
        issues.append("support_evidence_below_threshold")
    if counts.source_documents < source_documents_min:
        issues.append("source_document_count_below_threshold")
    if coverage_evaluation.get("can_stop") is not True:
        issues.append("coverage_evaluation_not_sufficient")
    if missing_required_slots or weak_required_slots:
        issues.append("required_slots_unresolved")

    can_continue = (
        bool(issues)
        and round_no <= max_rounds
        and counts.search_queries < max_total_queries
        and counts.fetch_attempts < max_total_fetch_attempts
    )
    return {
        "status": "sufficient" if not issues else "insufficient",
        "triggered": can_continue,
        "can_report": not issues or not can_continue,
        "can_continue": can_continue,
        "reason": None if not issues else "report_quality_below_threshold",
        "issues": issues,
        "metrics": {
            "min_main_claims": min_main_claims,
            "reportable_supported_claims": len(reportable_supported_claims),
            "min_support_evidence": min_support_evidence,
            "support_evidence_count": sum(support_evidence_by_claim_id.values()),
            "min_source_documents": source_documents_min,
            "source_documents": counts.source_documents,
            "distinct_support_domains": len(support_domains),
            "missing_required_slots": missing_required_slots,
            "weak_required_slots": weak_required_slots,
            "unresolved_claim_count": unresolved_claim_count,
            "search_queries": counts.search_queries,
            "fetch_attempts": counts.fetch_attempts,
            "max_total_queries": max_total_queries,
            "max_total_fetch_attempts": max_total_fetch_attempts,
            "round_no": round_no,
            "max_rounds": max_rounds,
        },
        "coverage_evaluation": coverage_evaluation,
    }


def _minimum_report_main_claims(query: str) -> int:
    if classify_query_intent(query).intent_name == "deployment":
        return 8
    lower = query.lower()
    if any(term in lower for term in ("compare", "comparison", " versus ", " vs ")):
        return 16
    if any(term in lower for term in ("recent", "latest", "近", "最新", "影响", "趋势")):
        return 14
    return MIN_REPORT_MAIN_CLAIMS


def _minimum_report_support_evidence(query: str) -> int:
    return max(_minimum_report_main_claims(query), MIN_REPORT_SUPPORT_EVIDENCE)


def _claim_support_level_from_notes(notes: dict[str, Any]) -> str:
    verification = notes.get("verification")
    if not isinstance(verification, dict):
        return "unknown"
    strong = _safe_int(verification.get("strong_support_evidence_count"))
    weak = _safe_int(verification.get("weak_support_evidence_count"))
    contradict = _safe_int(verification.get("contradict_evidence_count"))
    if strong and contradict:
        return "mixed"
    if strong:
        return "strong"
    if weak:
        return "weak"
    if contradict:
        return "contradicted"
    return "unsupported"


def _select_claim_drafting_chunk_ids(
    session: Session,
    task_id: UUID,
    *,
    query: str,
    limit: int,
) -> list[UUID]:
    chunks = SourceChunkRepository(session).list_for_task(task_id)
    deployment_query = classify_query_intent(query).intent_name == "deployment"
    ordered_chunks = sorted(
        chunks,
        key=lambda chunk: (
            _source_document_category_priority(chunk.source_document, query=query),
            -_deployment_chunk_signal_score(chunk.text) if deployment_query else 0,
            0 if _chunk_metadata_eligible_for_claims(chunk.metadata_json or {}) else 1,
            -_numeric_metadata_score(chunk.metadata_json or {}, "content_quality_score"),
            _datetime_sort_key(chunk.source_document.fetched_at),
            str(chunk.source_document_id),
            chunk.chunk_no,
        ),
    )
    selected: list[SourceChunk] = []
    selected_ids: set[UUID] = set()
    per_source_counts: dict[UUID, int] = {}

    def add_chunk(chunk: SourceChunk) -> bool:
        if len(selected) >= limit or chunk.id in selected_ids:
            return False
        selected.append(chunk)
        selected_ids.add(chunk.id)
        per_source_counts[chunk.source_document_id] = (
            per_source_counts.get(chunk.source_document_id, 0) + 1
        )
        return True

    eligible_chunks = [
        chunk
        for chunk in ordered_chunks
        if _chunk_metadata_eligible_for_claims(chunk.metadata_json or {})
    ]
    for chunk in eligible_chunks:
        if chunk.source_document_id not in per_source_counts:
            add_chunk(chunk)
        if len(selected) >= limit:
            break

    per_source_target = 2 if _query_needs_balanced_source_claims(query) else 1
    if per_source_target > 1 and len(selected) < limit:
        for chunk in eligible_chunks:
            if per_source_counts.get(chunk.source_document_id, 0) >= per_source_target:
                continue
            add_chunk(chunk)
            if len(selected) >= limit:
                break

    for chunk in ordered_chunks:
        add_chunk(chunk)
        if len(selected) >= limit:
            break

    return [chunk.id for chunk in selected]


def _claim_limit_for_query(query: str, default_limit: int) -> int:
    if classify_query_intent(query).intent_name != "deployment":
        answer_slots = answer_slots_for_query(query)
        required_slot_count = sum(1 for slot in answer_slots if slot.required)
        if required_slot_count >= 4:
            return max(default_limit, min(10, required_slot_count + 3))
        return default_limit
    deployment_slot_count = sum(
        1 for slot in answer_slots_for_query(query) if slot.slot_id.startswith("deployment_")
    )
    return max(default_limit, deployment_slot_count + 8)


def _query_needs_balanced_source_claims(query: str) -> bool:
    lower = query.lower()
    if any(term in lower for term in ("compare", "comparison", " versus ", " vs ")):
        return True
    if "how does" in lower or "how do" in lower:
        return True
    return False


def _deployment_chunk_signal_score(text: str) -> int:
    lower = text.lower()
    markers = (
        "docker or podman",
        "usermod",
        "docker compose pull",
        "docker run",
        "docker compose",
        "docker-compose",
        "settings.yml",
        ".env.example",
        "searxng_",
        "reverse proxy",
        "limiter",
        "bot protection",
        "certificate",
        "certificates",
        "update-ca-certificates",
        "logs",
        "exec ",
        "ports:",
        "volumes:",
        "/etc/searxng",
        "/var/cache/searxng",
    )
    return sum(1 for marker in markers if marker in lower)


def _source_document_category_priority(source_document: SourceDocument, *, query: str) -> int:
    category = _source_document_category(source_document, query=query)
    return source_intent_priority(category, query=query)


def _source_document_category(source_document: SourceDocument, *, query: str | None = None) -> str:
    return classify_source_intent(
        canonical_url=source_document.canonical_url,
        domain=source_document.domain,
        title=source_document.title,
        query=query,
    ).source_category


def _numeric_metadata_score(metadata: dict[str, Any], key: str) -> float:
    value = metadata.get(key)
    return float(value) if isinstance(value, int | float) else 0.0


def _datetime_sort_key(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


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
    high_value_only: bool = False,
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
        "official_repository",
        "github_readme_or_repo",
        "official_docs_reference",
    }
    if not high_value_only:
        preferred_categories.update({"generic_article", "secondary_reference"})
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


def _select_gap_search_candidate_ids(
    search_result: dict[str, Any],
    *,
    limit: int,
) -> tuple[list[UUID], list[dict[str, Any]]]:
    if limit <= 0:
        return [], []
    high_value_categories = {
        "official_about",
        "official_home",
        "official_docs_reference",
        "official_repository",
        "wikipedia_reference",
        "github_readme_or_repo",
    }
    selected: list[UUID] = []
    skipped: list[dict[str, Any]] = []
    seen_ids: set[UUID] = set()
    for source in search_result.get("selected_sources", []):
        if not isinstance(source, dict):
            continue
        raw_id = source.get("candidate_url_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            skipped.append({**source, "skip_reason": GAP_SEARCH_SKIP_MISSING_CANDIDATE_ID})
            continue
        try:
            cand_uuid = UUID(raw_id.strip())
        except (ValueError, TypeError):
            skipped.append({**source, "skip_reason": GAP_SEARCH_SKIP_INVALID_CANDIDATE_ID})
            continue
        if cand_uuid in seen_ids:
            skipped.append({**source, "skip_reason": GAP_SEARCH_SKIP_DUPLICATE_IN_ROUND})
            continue
        category = str(source.get("source_category") or "")
        raw_priority = source.get("fetch_priority_score")
        priority = raw_priority if isinstance(raw_priority, int) else 50
        if category not in high_value_categories:
            skipped.append({**source, "skip_reason": GAP_SEARCH_SKIP_CATEGORY_NOT_ALLOWED})
            continue
        if priority >= 35:
            skipped.append({**source, "skip_reason": GAP_SEARCH_SKIP_PRIORITY_TOO_LOW})
            continue
        if len(selected) >= limit:
            skipped.append({**source, "skip_reason": "gap_search_limit_reached"})
            continue
        selected.append(cand_uuid)
        seen_ids.add(cand_uuid)
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
                "source_category": _source_document_category(source_document, query=query),
                "source_intent": _source_document_category(source_document, query=query),
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
    search_queries = SearchQueryRepository(session).list_for_task(task_id)

    fetch_job_by_candidate_id = {fetch_job.candidate_url_id: fetch_job for fetch_job in fetch_jobs}
    latest_attempt_by_fetch_job_id = {attempt.fetch_job_id: attempt for attempt in fetch_attempts}
    snapshot_by_fetch_attempt_id = {
        snapshot.fetch_attempt_id: snapshot for snapshot in content_snapshots
    }
    source_document_by_url = {item.canonical_url: item for item in source_documents}
    search_queries_by_id = {item.id: item for item in search_queries}
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
        search_query = search_queries_by_id.get(candidate.search_query_id)
        target_slot_ids = _target_slot_ids_from_candidate_or_search_query(
            candidate.metadata_json,
            search_query,
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
                "target_slot_ids": target_slot_ids,
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
        source_intent = _source_document_category(source_document, query=query)
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


def _target_slot_ids_from_candidate_or_search_query(
    candidate_metadata: dict[str, Any] | None,
    search_query: Any | None,
) -> list[str]:
    candidate_slots = _normalize_slot_id_list(
        (candidate_metadata or {}).get("target_slots") or (candidate_metadata or {}).get("slot_ids")
    )
    if candidate_slots:
        return candidate_slots

    raw_response = getattr(search_query, "raw_response_json", None) if search_query else None
    if not isinstance(raw_response, dict):
        return []

    search_slots = _normalize_slot_id_list(
        raw_response.get("target_slots") or raw_response.get("slot_ids")
    )
    if search_slots:
        return search_slots

    expansion_metadata = raw_response.get("expansion_metadata")
    if not isinstance(expansion_metadata, dict):
        return []
    return _normalize_slot_id_list(
        expansion_metadata.get("target_slots") or expansion_metadata.get("slot_ids")
    )


def _normalize_slot_id_list(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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
    raw_trace = source.get("trace")
    trace: dict[str, Any] = raw_trace if isinstance(raw_trace, dict) else {}
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
        if claim is None:
            continue
        row = _claim_slot_summary_row(claim)
        if row is not None:
            rows.append(row)
    return rows


def _claim_rows_for_slot_summary_from_claims(
    session: Session,
    task_id: UUID,
    *,
    query: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for claim in ClaimRepository(session).list_for_task(task_id):
        row = _claim_slot_summary_row(claim, query=query)
        if row is not None:
            rows.append(row)
    return rows


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


def _claim_slot_summary_row(claim: Any, *, query: str | None = None) -> dict[str, Any] | None:
    notes = claim.notes_json if isinstance(claim.notes_json, dict) else {}
    slot_ids = [item for item in notes.get("slot_ids", []) if isinstance(item, str) and item]
    if query is not None and not _claim_counts_toward_gap_slot_coverage(
        claim,
        query=query,
        slot_ids=slot_ids,
    ):
        return None
    verification = notes.get("verification") if isinstance(notes, dict) else {}
    if not isinstance(verification, dict):
        verification = {}
    weak_support_count = verification.get("weak_support_evidence_count")
    strong_support_count = verification.get("strong_support_evidence_count")
    support_level = "weak" if weak_support_count and not strong_support_count else "strong"
    return {
        "claim_id": str(claim.id),
        "verification_status": claim.verification_status,
        "slot_ids": slot_ids,
        "source_document_id": (
            notes.get("source_document_id")
            if isinstance(notes.get("source_document_id"), str)
            else None
        ),
        "support_level": support_level,
    }


def _claim_counts_toward_gap_slot_coverage(
    claim: Any,
    *,
    query: str,
    slot_ids: list[str],
) -> bool:
    notes = claim.notes_json if isinstance(claim.notes_json, dict) else {}
    report_eligible = notes.get("report_eligible")
    if report_eligible is False:
        return False
    if report_eligible is True:
        return True
    claim_score = _report_claim_score(claim, query=query)
    if not _report_claim_answer_relevant(claim_score, query=query):
        return False
    if _claim_focus_required_for_report(query=query, slot_ids=slot_ids) and not (
        _claim_focus_matches_query(claim.statement, query=query)
    ):
        return False
    return True


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


def _aggregate_parse_rejection_reasons(parse_decisions: list[dict[str, Any]]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for decision in parse_decisions:
        reason = str(decision.get("reason") or "unknown")
        if decision.get("decision") == "created":
            continue
        distribution[reason] = distribution.get(reason, 0) + 1
    return distribution


def _source_judge_priority_delta(output: dict[str, Any]) -> int:
    label = str(output.get("label") or "uncertain")
    confidence = output.get("confidence")
    confidence_value = float(confidence) if isinstance(confidence, int | float) else 0.0
    adjustment = output.get("priority_adjustment")
    numeric_adjustment = float(adjustment) if isinstance(adjustment, int | float) else 0.0
    label_delta = {
        "accept": -4,
        "authoritative": -4,
        "relevant": -2,
        "uncertain": 0,
        "stale": 8,
        "marketing": 10,
        "downrank": 16,
        "low_quality": 24,
        "reject": 80,
        "unsafe": 80,
    }.get(label, 0)
    combined = label_delta + numeric_adjustment
    if confidence_value < 0.55 and combined < 0:
        combined = 0
    return max(-6, min(90, int(round(combined))))


def _source_judge_assistance_summary(
    judgments: list[dict[str, Any]],
    *,
    enabled: bool,
    active: bool,
) -> dict[str, Any]:
    label_counts: dict[str, int] = {}
    fallback_counts: dict[str, int] = {}
    used_in_final_ranking = 0
    for judgment in judgments:
        output = judgment.get("output_judgment")
        label = "unknown"
        if isinstance(output, dict):
            label = str(output.get("label") or "unknown")
        label_counts[label] = label_counts.get(label, 0) + 1
        fallback_status = str(judgment.get("fallback_status") or "none")
        fallback_counts[fallback_status] = fallback_counts.get(fallback_status, 0) + 1
        if judgment.get("used_in_final_ranking") is True:
            used_in_final_ranking += 1
    active_rerank_reason: str | None = None
    if not active:
        active_rerank_reason = "active_rerank_disabled"
    elif not judgments:
        active_rerank_reason = "no_candidates_reviewed"
    elif fallback_counts and fallback_counts.get("none", 0) == 0:
        active_rerank_reason = "all_judgments_fell_back"
    elif used_in_final_ranking == 0:
        active_rerank_reason = "no_judgment_passed_active_rerank_guardrails"
    return {
        "enabled": enabled,
        "used": enabled and bool(judgments),
        "status": "used" if enabled and judgments else "disabled",
        "active_rerank": active,
        "active_rerank_reason": active_rerank_reason,
        "judged_candidate_count": len(judgments),
        "used_in_final_ranking_count": used_in_final_ranking,
        "label_counts": dict(sorted(label_counts.items())),
        "fallback_counts": dict(sorted(fallback_counts.items())),
    }


def _stage_warnings(stage_result: dict[str, Any]) -> list[str]:
    warnings = stage_result.get("warnings", [])
    if not isinstance(warnings, list):
        return []
    return [item for item in warnings if isinstance(item, str) and item.strip()]


def _running_mode_from_dependencies(dependencies: dict[str, Any]) -> str:
    return "+".join(
        [
            str(
                dependencies.get("search_mode")
                or dependencies.get("search_provider")
                or "unknown-search"
            ),
            str(
                dependencies.get("index_mode")
                or dependencies.get("index_backend")
                or "unknown-index"
            ),
            str(dependencies.get("llm_mode") or "unknown-llm"),
        ]
    )


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


def _aggregate_candidate_counts(
    *,
    search_result: dict[str, Any],
    fallback_sources: list[dict[str, Any]],
    selected_candidate_ids: list[UUID],
    skipped_gap_search_sources: list[dict[str, Any]],
    skipped_fallback_sources: list[dict[str, Any]],
) -> dict[str, int]:
    all_skipped = skipped_gap_search_sources + skipped_fallback_sources
    rejected_by_triage_count = sum(
        1
        for s in all_skipped
        if s.get("skip_reason")
        in {
            "low_value_gap_search_result",
            GAP_SEARCH_SKIP_CATEGORY_NOT_ALLOWED,
            GAP_SEARCH_SKIP_PRIORITY_TOO_LOW,
            GAP_SEARCH_SKIP_MISSING_CANDIDATE_ID,
            GAP_SEARCH_SKIP_INVALID_CANDIDATE_ID,
            GAP_SEARCH_SKIP_DUPLICATE_IN_ROUND,
            "low_priority_for_overview_supplemental_acquisition",
            "not_a_high_value_supplemental_source",
        }
    )
    skipped_budget = sum(
        1
        for s in all_skipped
        if s.get("skip_reason")
        in {"acquisition_limit_reached", "supplemental_acquisition_limit_reached"}
    )
    return {
        "newly_discovered": search_result.get("candidate_urls_added", 0),
        "existing_unattempted_reused": len(fallback_sources),
        "skipped_budget_limit": skipped_budget,
        "rejected_by_triage": rejected_by_triage_count,
        "attempted_in_round": len(selected_candidate_ids),
    }
