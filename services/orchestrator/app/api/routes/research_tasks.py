from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from packages.db.models import ResearchTask
from services.orchestrator.app.api.schemas.research_tasks import (
    CreateResearchTaskRequest,
    PlanResearchTaskRequest,
    ResearchPlanMutationResponse,
    ResearchPlanResponse,
    ResearchTaskDetailResponse,
    ResearchTaskListItemResponse,
    ResearchTaskListResponse,
    ResearchTaskMutationResponse,
    ResearchTaskObservabilityResponse,
    ResearchTaskProgressResponse,
    ReviseResearchTaskRequest,
    TaskEventListResponse,
    TaskEventResponse,
)
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.planning import (
    DISABLED_PLANNER_WARNING,
    LLM_PLANNER_FALLBACK_WARNING,
    LLM_PLANNER_SUCCESS_WARNING,
    build_optional_research_plan,
    build_research_plan_from_payload,
    create_research_planner_service,
)
from services.orchestrator.app.reporting import normalize_report_language
from services.orchestrator.app.research_quality import summarize_evidence_yield
from services.orchestrator.app.services.research_tasks import (
    ResearchTaskService,
    TaskNotFoundError,
    TaskSnapshot,
    TaskStateConflictError,
    create_research_task_service,
)
from services.orchestrator.app.settings import get_settings

router = APIRouter(prefix="/api/v1/research/tasks", tags=["research-tasks"])
SessionDep = Annotated[Session, Depends(get_db_session)]

NON_FATAL_DETERMINISTIC_REJECTION_REASONS = {
    "insufficient_claim_quality",
    "insufficient_answer_score",
    "not_answer_relevant",
}


def get_research_task_service(session: SessionDep) -> ResearchTaskService:
    return create_research_task_service(session)


ServiceDep = Annotated[
    ResearchTaskService,
    Depends(get_research_task_service),
]


@router.post(
    "",
    response_model=ResearchTaskMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_research_task(
    request: CreateResearchTaskRequest,
    service: ServiceDep,
) -> ResearchTaskMutationResponse:
    task = service.create_task(
        query=request.query,
        constraints=_constraints_with_report_language(
            request.constraints,
            report_language=request.report_language,
            include_language_default=False,
        )
        or {},
    )
    return ResearchTaskMutationResponse(
        task_id=task.id,
        status=task.status,
        revision_no=task.revision_no,
        updated_at=task.updated_at,
    )


@router.get("", response_model=ResearchTaskListResponse)
def list_research_tasks(
    service: ServiceDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ResearchTaskListResponse:
    normalized_status = status_filter.strip().upper() if status_filter else None
    snapshots = service.list_task_snapshots(status=normalized_status, limit=limit)
    tasks = [_serialize_task_list_item(snapshot) for snapshot in snapshots]
    return ResearchTaskListResponse(tasks=tasks, count=len(tasks))


@router.get("/{task_id}", response_model=ResearchTaskDetailResponse)
def get_research_task(task_id: UUID, service: ServiceDep) -> ResearchTaskDetailResponse:
    snapshot = _get_task_snapshot_or_404(service, task_id)
    return _serialize_task_snapshot(snapshot)


@router.get("/{task_id}/events", response_model=TaskEventListResponse)
def get_research_task_events(
    task_id: UUID,
    service: ServiceDep,
    after_sequence_no: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> TaskEventListResponse:
    try:
        events = service.get_events(
            task_id,
            after_sequence_no=after_sequence_no,
            limit=limit,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return TaskEventListResponse(
        task_id=task_id,
        events=[
            TaskEventResponse(
                event_id=event.id,
                run_id=event.run_id,
                event_type=event.event_type,
                sequence_no=event.sequence_no,
                payload=event.payload_json,
                created_at=event.created_at,
            )
            for event in events
        ],
    )


@router.post("/{task_id}/plan", response_model=ResearchPlanMutationResponse)
def plan_research_task(
    task_id: UUID,
    service: ServiceDep,
    request: PlanResearchTaskRequest | None = None,
) -> ResearchPlanMutationResponse:
    request = request or PlanResearchTaskRequest()
    snapshot = _get_task_snapshot_or_404(service, task_id)
    settings = get_settings()
    dependencies = _dependency_summary(settings)

    planner_status = "created"
    planner_failure: dict[str, Any] | None = None
    if request.research_plan is not None:
        research_plan_payload = dict(request.research_plan)
        if not isinstance(research_plan_payload.get("planner_diagnostics"), dict):
            previous_diagnostics = _latest_planner_diagnostics(snapshot)
            if previous_diagnostics:
                previous_diagnostics = dict(previous_diagnostics)
                previous_diagnostics["preserved_after_operator_edit"] = True
                research_plan_payload["planner_diagnostics"] = previous_diagnostics
        plan = build_research_plan_from_payload(
            research_plan_payload,
            query=snapshot.task.query,
            planner_mode="operator_edited",
            max_subquestions=settings.research_planner_max_subquestions,
            max_search_queries=settings.research_planner_max_search_queries,
        )
        plan_source = "operator_edited"
    else:
        planner_result = build_optional_research_plan(
            planner_service=create_research_planner_service(settings),
            task_id=task_id,
            query=snapshot.task.query,
            constraints=dict(snapshot.task.constraints_json),
            max_subquestions=settings.research_planner_max_subquestions,
            max_search_queries=settings.research_planner_max_search_queries,
        )
        plan = planner_result.plan
        plan_source = planner_result.plan_source
        planner_status = planner_result.planner_status
        planner_failure = planner_result.failure

    try:
        running_mode = _running_mode(dependencies)
        summary = plan.summary_payload()
        if planner_failure is not None:
            summary["planner_failure"] = planner_failure
        response_warnings = list(
            dict.fromkeys(_runtime_warnings(dependencies) + list(plan.warnings))
        )
        task = service.record_research_plan_created(
            task_id,
            research_plan=plan.to_payload(),
            planner_mode=plan.planner_mode,
            plan_source=plan_source,
            planner_status=planner_status,
            summary=summary,
            warnings=response_warnings,
            dependencies=dependencies,
            running_mode=running_mode,
        )
    except TaskStateConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    return ResearchPlanMutationResponse(
        task_id=task.id,
        status=task.status,
        revision_no=task.revision_no,
        updated_at=task.updated_at,
        planner_status=planner_status,
        planner_mode=plan.planner_mode,
        plan_source=plan_source,
        research_plan=plan.to_payload(),
        running_mode=running_mode,
        dependencies=dependencies,
        warnings=response_warnings,
    )


@router.get("/{task_id}/plan", response_model=ResearchPlanResponse)
def get_research_plan(task_id: UUID, service: ServiceDep) -> ResearchPlanResponse:
    snapshot = _get_task_snapshot_or_404(service, task_id)
    for event in reversed(snapshot.events):
        if event.event_type != "research_plan.created":
            continue
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        result = payload.get("result")
        if not isinstance(result, dict):
            continue
        plan = result.get("research_plan")
        if not isinstance(plan, dict):
            continue
        return ResearchPlanResponse(
            task_id=snapshot.task.id,
            status=snapshot.task.status,
            revision_no=snapshot.task.revision_no,
            research_plan=plan,
            planner_status=_string_or_none(payload.get("planner_status")),
            planner_mode=_string_or_none(payload.get("planner_mode")),
            plan_source=_string_or_none(payload.get("plan_source")),
            created_at=event.created_at,
            warnings=_string_list(payload.get("warnings")),
        )
    return ResearchPlanResponse(
        task_id=snapshot.task.id,
        status=snapshot.task.status,
        revision_no=snapshot.task.revision_no,
        research_plan=None,
    )


@router.post("/{task_id}/pause", response_model=ResearchTaskMutationResponse)
def pause_research_task(task_id: UUID, service: ServiceDep) -> ResearchTaskMutationResponse:
    return _run_task_mutation(service.pause_task, task_id)


@router.post("/{task_id}/resume", response_model=ResearchTaskMutationResponse)
def resume_research_task(task_id: UUID, service: ServiceDep) -> ResearchTaskMutationResponse:
    return _run_task_mutation(service.resume_task, task_id)


@router.post("/{task_id}/cancel", response_model=ResearchTaskMutationResponse)
def cancel_research_task(task_id: UUID, service: ServiceDep) -> ResearchTaskMutationResponse:
    return _run_task_mutation(service.cancel_task, task_id)


@router.post("/{task_id}/revise", response_model=ResearchTaskMutationResponse)
def revise_research_task(
    task_id: UUID,
    request: ReviseResearchTaskRequest,
    service: ServiceDep,
) -> ResearchTaskMutationResponse:
    try:
        task = service.revise_task(
            task_id,
            query=request.query,
            constraints=_constraints_with_report_language(
                request.constraints,
                report_language=request.report_language,
                include_language_default=False,
            ),
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except TaskStateConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    return ResearchTaskMutationResponse(
        task_id=task.id,
        status=task.status,
        revision_no=task.revision_no,
        updated_at=task.updated_at,
    )


def _constraints_with_report_language(
    constraints: dict[str, Any] | None,
    *,
    report_language: str | None,
    include_language_default: bool,
) -> dict[str, Any] | None:
    if report_language is None:
        return constraints
    normalized_constraints = dict(constraints or {})
    normalized_report_language = normalize_report_language(report_language)
    normalized_constraints["report_language"] = normalized_report_language
    if include_language_default:
        normalized_constraints.setdefault("language", normalized_report_language)
    return normalized_constraints


def _run_task_mutation(
    mutation: Callable[[UUID], ResearchTask],
    task_id: UUID,
) -> ResearchTaskMutationResponse:
    try:
        task = mutation(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except TaskStateConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    return ResearchTaskMutationResponse(
        task_id=task.id,
        status=task.status,
        revision_no=task.revision_no,
        updated_at=task.updated_at,
    )


def _get_task_snapshot_or_404(service: ResearchTaskService, task_id: UUID) -> TaskSnapshot:
    try:
        return service.get_task_snapshot(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


def _latest_planner_diagnostics(snapshot: TaskSnapshot) -> dict[str, Any]:
    for event in reversed(snapshot.events):
        if event.event_type != "research_plan.created":
            continue
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        result = payload.get("result")
        if not isinstance(result, dict):
            continue
        plan = result.get("research_plan")
        if not isinstance(plan, dict):
            continue
        diagnostics = plan.get("planner_diagnostics")
        if isinstance(diagnostics, dict) and diagnostics:
            return dict(diagnostics)
    return {}


def _serialize_task_snapshot(snapshot: TaskSnapshot) -> ResearchTaskDetailResponse:
    latest_event_at = snapshot.events[-1].created_at if snapshot.events else None
    current_state = snapshot.task.status
    if snapshot.events and snapshot.task.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
        latest_stage = snapshot.events[-1].payload_json.get("stage")
        if isinstance(latest_stage, str) and latest_stage.strip():
            current_state = latest_stage.strip()
    return ResearchTaskDetailResponse(
        task_id=snapshot.task.id,
        query=snapshot.task.query,
        status=snapshot.task.status,
        constraints=snapshot.task.constraints_json,
        revision_no=snapshot.task.revision_no,
        created_at=snapshot.task.created_at,
        updated_at=snapshot.task.updated_at,
        started_at=snapshot.task.started_at,
        ended_at=snapshot.task.ended_at,
        progress=ResearchTaskProgressResponse(
            current_state=current_state,
            events_total=len(snapshot.events),
            latest_event_at=latest_event_at,
            observability=_derive_observability(snapshot),
        ),
    )


def _serialize_task_list_item(snapshot: TaskSnapshot) -> ResearchTaskListItemResponse:
    latest_event = snapshot.events[-1] if snapshot.events else None
    return ResearchTaskListItemResponse(
        task_id=snapshot.task.id,
        query=snapshot.task.query,
        status=snapshot.task.status,
        revision_no=snapshot.task.revision_no,
        created_at=snapshot.task.created_at,
        updated_at=snapshot.task.updated_at,
        started_at=snapshot.task.started_at,
        ended_at=snapshot.task.ended_at,
        events_total=len(snapshot.events),
        latest_event_at=latest_event.created_at if latest_event is not None else None,
    )


def _dependency_summary(settings: Any) -> dict[str, Any]:
    search_mode = settings.search_provider.strip().lower()
    index_mode = settings.index_backend.strip().lower()
    return {
        "search_provider": search_mode,
        "search_mode": "smoke-search" if search_mode == "smoke" else "real-search",
        "searxng_base_url": settings.searxng_base_url,
        "yacy_base_url": settings.yacy_base_url,
        "snapshot_storage_backend": settings.snapshot_storage_backend,
        "snapshot_storage_root": settings.snapshot_storage_root,
        "snapshot_storage_bucket": settings.snapshot_storage_bucket,
        "report_storage_bucket": settings.report_storage_bucket,
        "index_backend": index_mode,
        "index_mode": "deterministic-local" if index_mode in {"local", "memory"} else index_mode,
        "opensearch_base_url": settings.opensearch_base_url,
        "opensearch_index_name": settings.opensearch_index_name,
        "uses_llm_api": _uses_llm_api(settings),
        "llm_mode": _llm_mode(settings),
        "llm_provider": settings.llm_provider.strip().lower() or "noop",
        "llm_model": settings.llm_model.strip(),
        "llm_base_url_configured": bool(settings.llm_base_url.strip()),
        "research_planner_enabled": bool(
            settings.research_planner_enabled and settings.llm_enabled
        ),
        "llm_report_writer_enabled": _llm_report_writer_configured(settings),
        "llm_source_judge_enabled": _llm_source_judge_configured(settings),
        "llm_source_judge_active_rerank": bool(
            settings.llm_source_judge_active_rerank and _llm_source_judge_configured(settings)
        ),
        "llm_query_rewriter_enabled": _llm_query_rewriter_configured(settings),
        "llm_evidence_reranker_enabled": _llm_evidence_reranker_configured(settings),
        "llm_claim_reviewer_enabled": _llm_claim_reviewer_configured(settings),
        "report_writer_mode": (
            "llm-grounded" if _llm_report_writer_configured(settings) else "deterministic"
        ),
        "uses_worker_or_queue": True,
    }


def _llm_mode(settings: Any) -> str:
    planner_configured = bool(settings.research_planner_enabled and settings.llm_enabled)
    report_configured = _llm_report_writer_configured(settings)
    assistance = [
        name
        for name, configured in (
            ("rewrite", _llm_query_rewriter_configured(settings)),
            ("judge", _llm_source_judge_configured(settings)),
            ("rerank", _llm_evidence_reranker_configured(settings)),
            ("review", _llm_claim_reviewer_configured(settings)),
        )
        if configured
    ]
    if report_configured and planner_configured:
        base = "planner+report-LLM"
    elif report_configured:
        base = "report-LLM"
    elif not planner_configured:
        base = "no-LLM"
    else:
        normalized_provider = settings.llm_provider.strip().lower() or "noop"
        base = "planner-noop" if normalized_provider == "noop" else "planner-LLM"
    if assistance:
        return f"{base}+assist-{'-'.join(assistance)}"
    return base


def _uses_llm_api(settings: Any) -> bool:
    return bool(
        settings.llm_enabled
        and settings.llm_provider.strip().lower() not in {"", "noop"}
        and (
            settings.research_planner_enabled
            or settings.llm_report_writer_enabled
            or settings.llm_source_judge_enabled
            or settings.llm_query_rewriter_enabled
            or settings.llm_evidence_reranker_enabled
            or settings.llm_claim_reviewer_enabled
        )
    )


def _llm_report_writer_configured(settings: Any) -> bool:
    return bool(
        settings.llm_enabled
        and settings.llm_report_writer_enabled
        and settings.llm_provider.strip().lower() not in {"", "noop"}
    )


def _llm_source_judge_configured(settings: Any) -> bool:
    return bool(settings.llm_enabled and settings.llm_source_judge_enabled)


def _llm_query_rewriter_configured(settings: Any) -> bool:
    return bool(settings.llm_enabled and settings.llm_query_rewriter_enabled)


def _llm_evidence_reranker_configured(settings: Any) -> bool:
    return bool(settings.llm_enabled and settings.llm_evidence_reranker_enabled)


def _llm_claim_reviewer_configured(settings: Any) -> bool:
    return bool(settings.llm_enabled and settings.llm_claim_reviewer_enabled)


def _running_mode(dependencies: dict[str, Any]) -> str:
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


def _runtime_warnings(dependencies: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if dependencies.get("search_provider") == "smoke":
        warnings.append(
            "Development smoke mode is active; sources are synthetic fixtures, "
            "not real web evidence."
        )
    if dependencies.get("index_mode") == "deterministic-local":
        warnings.append(
            "Local deterministic index backend is active; retrieval is for smoke validation, "
            "not durable research."
        )
    if dependencies.get("llm_mode") == "no-LLM":
        warnings.append(DISABLED_PLANNER_WARNING)
    elif not dependencies.get("research_planner_enabled"):
        warnings.append(DISABLED_PLANNER_WARNING)
    return warnings


def _derive_observability(snapshot: TaskSnapshot) -> ResearchTaskObservabilityResponse | None:
    running_mode: str | None = None
    dependencies: dict[str, Any] | None = None
    planner_enabled: bool | None = None
    planner_mode: str | None = None
    planner_status: str | None = None
    plan_source: str | None = None
    subquestion_count: int | None = None
    planner_search_query_count: int | None = None
    research_plan: dict[str, Any] | None = None
    raw_planner_queries: list[dict[str, Any]] = []
    final_search_queries: list[dict[str, Any]] = []
    dropped_or_downweighted_planner_queries: list[dict[str, Any]] = []
    planner_guardrail_warnings: list[str] = []
    intent_classification: str | None = None
    extracted_entity: str | None = None
    search_result_count: int | None = None
    search_queries: list[dict[str, Any]] = []
    known_path_fallback: dict[str, Any] | None = None
    selected_sources_from_search: list[dict[str, Any]] = []
    selected_sources: list[dict[str, Any]] = []
    source_judgments: list[dict[str, Any]] = []
    llm_assistance: dict[str, Any] = {}
    fetch_succeeded: int | None = None
    fetch_failed: int | None = None
    fetch_succeeded_total = 0
    fetch_failed_total = 0
    saw_fetch_counters = False
    attempted_sources: list[dict[str, Any]] = []
    unattempted_sources: list[dict[str, Any]] = []
    failed_sources: list[dict[str, Any]] = []
    parse_decisions: list[dict[str, Any]] = []
    source_quality_summary: dict[str, Any] | None = None
    source_yield_summary: list[dict[str, Any]] = []
    dropped_sources: list[dict[str, Any]] = []
    answer_coverage: dict[str, bool] | None = None
    answer_slots: list[dict[str, Any]] = []
    report_slot_coverage: list[dict[str, Any]] = []
    slot_coverage_summary: list[dict[str, Any]] = []
    answer_yield: list[dict[str, Any]] = []
    evidence_yield_summary: dict[str, Any] | None = None
    verification_summary: dict[str, Any] | None = None
    supplemental_acquisition: dict[str, Any] | None = None
    research_strategy: dict[str, Any] | None = None
    gap_analysis: dict[str, Any] | None = None
    gap_rounds: list[dict[str, Any]] = []
    failure_diagnostics: dict[str, Any] | None = None
    pipeline_counts: dict[str, int] = {}
    warnings: list[str] = []
    runtime_warnings: list[str] = []
    latest_planner_diagnostics: dict[str, Any] = {}

    for event in snapshot.events:
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        result = payload.get("result")
        if not isinstance(result, dict):
            result = {}

        stage = payload.get("stage")
        counts = payload.get("counts")
        if isinstance(counts, dict):
            pipeline_counts = {
                key: value
                for key, value in counts.items()
                if isinstance(key, str) and isinstance(value, int)
            }
        dependency_payload = payload.get("dependencies")
        if isinstance(dependency_payload, dict):
            dependencies = dependency_payload
            running_mode = _string_or_none(payload.get("running_mode")) or _running_mode(
                dependency_payload
            )
            runtime_warnings.extend(_runtime_warnings(dependency_payload))
        if event.event_type == "research_plan.created":
            planner_enabled = True
            planner_status = _string_or_none(payload.get("planner_status")) or "created"
            plan_source = _string_or_none(payload.get("plan_source")) or plan_source
            changes = payload.get("changes")
            if isinstance(changes, dict):
                plan_source = _string_or_none(changes.get("research_plan_source")) or plan_source
            result_plan = result.get("research_plan")
            if isinstance(result_plan, dict):
                current_plan = dict(result_plan)
                diagnostics = current_plan.get("planner_diagnostics")
                if isinstance(diagnostics, dict) and diagnostics:
                    latest_planner_diagnostics = dict(diagnostics)
                elif latest_planner_diagnostics:
                    current_plan["planner_diagnostics"] = dict(latest_planner_diagnostics)
                research_plan = current_plan
                planner_mode = _string_or_none(result_plan.get("planner_mode")) or planner_mode
                subquestions = result_plan.get("subquestions")
                if isinstance(subquestions, list):
                    subquestion_count = len(subquestions)
                plan_search_queries = result_plan.get("search_queries")
                if isinstance(plan_search_queries, list):
                    planner_search_query_count = len(plan_search_queries)
                raw_planner_queries = (
                    _object_list(result_plan.get("raw_planner_queries")) or raw_planner_queries
                )
                final_search_queries = (
                    _object_list(result_plan.get("final_search_queries")) or final_search_queries
                )
                dropped_or_downweighted_planner_queries = (
                    _object_list(result_plan.get("dropped_or_downweighted_planner_queries"))
                    or dropped_or_downweighted_planner_queries
                )
                planner_guardrail_warnings = (
                    _string_list(result_plan.get("planner_guardrail_warnings"))
                    or planner_guardrail_warnings
                )
                intent_classification = (
                    _string_or_none(result_plan.get("intent_classification"))
                    or intent_classification
                )
                extracted_entity = (
                    _string_or_none(result_plan.get("extracted_entity")) or extracted_entity
                )
            planner_mode = _string_or_none(payload.get("planner_mode")) or planner_mode
            value = result.get("subquestion_count")
            if isinstance(value, int):
                subquestion_count = value
            value = result.get("search_query_count")
            if isinstance(value, int):
                planner_search_query_count = value
            raw_planner_queries = (
                _object_list(result.get("raw_planner_queries")) or raw_planner_queries
            )
            final_search_queries = (
                _object_list(result.get("final_search_queries")) or final_search_queries
            )
            dropped_or_downweighted_planner_queries = (
                _object_list(result.get("dropped_or_downweighted_planner_queries"))
                or dropped_or_downweighted_planner_queries
            )
            planner_guardrail_warnings = (
                _string_list(result.get("planner_guardrail_warnings")) or planner_guardrail_warnings
            )
            intent_classification = (
                _string_or_none(result.get("intent_classification")) or intent_classification
            )
            extracted_entity = _string_or_none(result.get("extracted_entity")) or extracted_entity
        elif event.event_type == "research_plan.failed":
            planner_enabled = True
            planner_status = "failed"
        elif stage == "SEARCHING":
            value = result.get("search_result_count")
            if isinstance(value, int):
                search_result_count = value
            search_queries = _object_list(result.get("search_queries")) or search_queries
            fallback = result.get("known_path_fallback")
            if isinstance(fallback, dict):
                known_path_fallback = fallback
            selected_sources = _object_list(result.get("selected_sources"))
            source_judgments = _object_list(result.get("source_judgments")) or source_judgments
            assistance = result.get("llm_assistance")
            if isinstance(assistance, dict):
                llm_assistance.update(assistance)
            raw_planner_queries = (
                _object_list(result.get("raw_planner_queries")) or raw_planner_queries
            )
            final_search_queries = (
                _object_list(result.get("final_search_queries")) or final_search_queries
            )
            dropped_or_downweighted_planner_queries = (
                _object_list(result.get("dropped_or_downweighted_planner_queries"))
                or dropped_or_downweighted_planner_queries
            )
            planner_guardrail_warnings = (
                _string_list(result.get("planner_guardrail_warnings")) or planner_guardrail_warnings
            )
            intent_classification = (
                _string_or_none(result.get("intent_classification")) or intent_classification
            )
            extracted_entity = _string_or_none(result.get("extracted_entity")) or extracted_entity
        elif stage == "ACQUIRING":
            details = payload.get("details")
            acquisition_payload = result
            if not acquisition_payload and isinstance(details, dict):
                acquisition_payload = details
            succeeded = acquisition_payload.get(
                "fetch_succeeded",
                acquisition_payload.get("succeeded"),
            )
            failed = acquisition_payload.get(
                "fetch_failed",
                acquisition_payload.get("failed"),
            )
            if isinstance(succeeded, int):
                fetch_succeeded_total += succeeded
                saw_fetch_counters = True
            if isinstance(failed, int):
                fetch_failed_total += failed
                saw_fetch_counters = True
            selected_sources_from_search = (
                _object_list(acquisition_payload.get("selected_sources_from_search"))
                or selected_sources_from_search
            )
            selected_sources = (
                _object_list(acquisition_payload.get("selected_sources")) or selected_sources
            )
            attempted_sources = (
                _object_list(acquisition_payload.get("attempted_sources")) or attempted_sources
            )
            unattempted_sources = (
                _object_list(acquisition_payload.get("unattempted_sources")) or unattempted_sources
            )
            dropped_sources = (
                _object_list(acquisition_payload.get("dropped_sources")) or dropped_sources
            )
            failed_sources = _object_list(acquisition_payload.get("failed_sources"))
        elif stage == "PARSING":
            parse_decisions = _object_list(result.get("parse_decisions")) or parse_decisions
        elif stage == "DRAFTING_CLAIMS":
            answer_yield = _object_list(result.get("answer_yield")) or answer_yield
            answer_slots = _object_list(result.get("answer_slots")) or answer_slots
            report_slot_coverage = (
                _object_list(result.get("report_slot_coverage")) or report_slot_coverage
            )
            slot_coverage_summary = (
                _object_list(result.get("slot_coverage_summary")) or slot_coverage_summary
            )
            source_yield_summary = (
                _object_list(result.get("source_yield_summary")) or source_yield_summary
            )
            dropped_sources = _object_list(result.get("dropped_sources")) or dropped_sources
            evidence_summary = _evidence_yield_summary_from_diagnostics(result) or result.get(
                "evidence_yield_summary"
            )
            if isinstance(evidence_summary, dict):
                evidence_yield_summary = evidence_summary
            coverage = result.get("answer_coverage")
            if isinstance(coverage, dict):
                answer_coverage = {
                    key: bool(value) for key, value in coverage.items() if isinstance(key, str)
                }
            supplemental = result.get("supplemental_acquisition")
            if isinstance(supplemental, dict):
                supplemental_acquisition = supplemental
            assistance = result.get("llm_assistance")
            if isinstance(assistance, dict):
                llm_assistance.update(assistance)
        elif stage == "VERIFYING":
            verification = result.get("verification_summary")
            if isinstance(verification, dict):
                verification_summary = verification
            assistance = result.get("llm_assistance")
            if isinstance(assistance, dict):
                llm_assistance.update(assistance)
            assistance = result.get("llm_assistance")
            if isinstance(assistance, dict):
                llm_assistance.update(assistance)
            slot_coverage_summary = (
                _object_list(result.get("slot_coverage_summary")) or slot_coverage_summary
            )
        elif stage == "RESEARCHING_MORE":
            if result:
                gap_rounds.append(result)
            gap = result.get("gap_analysis")
            if isinstance(gap, dict):
                gap_analysis = gap
            search = result.get("search")
            if isinstance(search, dict):
                value = search.get("search_result_count")
                if isinstance(value, int):
                    search_result_count = value
                search_queries = _object_list(search.get("search_queries")) or search_queries
                fallback = search.get("known_path_fallback")
                if isinstance(fallback, dict):
                    known_path_fallback = fallback
                selected_sources = _object_list(search.get("selected_sources")) or selected_sources
                source_judgments = _object_list(search.get("source_judgments")) or source_judgments
                assistance = search.get("llm_assistance")
                if isinstance(assistance, dict):
                    llm_assistance.update(assistance)
            acquisition = result.get("acquisition")
            if isinstance(acquisition, dict):
                succeeded = acquisition.get(
                    "fetch_succeeded",
                    acquisition.get("succeeded"),
                )
                failed = acquisition.get("fetch_failed", acquisition.get("failed"))
                if isinstance(succeeded, int):
                    fetch_succeeded_total += succeeded
                    saw_fetch_counters = True
                if isinstance(failed, int):
                    fetch_failed_total += failed
                    saw_fetch_counters = True
                attempted_sources = (
                    _object_list(acquisition.get("attempted_sources")) or attempted_sources
                )
                unattempted_sources = (
                    _object_list(acquisition.get("unattempted_sources")) or unattempted_sources
                )
                failed_sources = _object_list(acquisition.get("failed_sources"))
            parsing = result.get("parsing")
            if isinstance(parsing, dict):
                parse_decisions = _object_list(parsing.get("parse_decisions")) or parse_decisions
            drafting = result.get("drafting")
            if isinstance(drafting, dict):
                source_yield_summary = (
                    _object_list(drafting.get("source_yield_summary")) or source_yield_summary
                )
                evidence_summary = _evidence_yield_summary_from_diagnostics(
                    drafting
                ) or drafting.get("evidence_yield_summary")
                if isinstance(evidence_summary, dict):
                    evidence_yield_summary = evidence_summary
            verification = result.get("verification")
            if isinstance(verification, dict):
                verification_payload = verification.get("verification_summary")
                if isinstance(verification_payload, dict):
                    verification_summary = verification_payload
                assistance = verification.get("llm_assistance")
                if isinstance(assistance, dict):
                    llm_assistance.update(assistance)
            slot_coverage_summary = (
                _object_list(result.get("slot_coverage_summary")) or slot_coverage_summary
            )
        elif stage == "REPORTING":
            quality_summary = result.get("source_quality_summary")
            if isinstance(quality_summary, dict):
                source_quality_summary = quality_summary
            verification = result.get("verification_summary")
            if isinstance(verification, dict):
                verification_summary = verification

        details = payload.get("details")
        if isinstance(details, dict):
            parse_decisions = _object_list(details.get("parse_decisions")) or parse_decisions
            if payload.get("stage") == "DRAFTING_CLAIMS":
                failure_diagnostics = details
            answer_yield = (
                _object_list(details.get("answer_yield"))
                or _object_list(details.get("per_source_answer_yield"))
                or answer_yield
            )
            source_yield_summary = (
                _object_list(details.get("source_yield_summary")) or source_yield_summary
            )
            dropped_sources = _object_list(details.get("dropped_sources")) or dropped_sources
            slot_coverage_summary = (
                _object_list(details.get("slot_coverage_summary")) or slot_coverage_summary
            )
            evidence_summary = _evidence_yield_summary_from_diagnostics(details) or details.get(
                "evidence_yield_summary"
            )
            if isinstance(evidence_summary, dict):
                evidence_yield_summary = evidence_summary
            verification = details.get("verification_summary")
            if isinstance(verification, dict):
                verification_summary = verification
            answer_slots = _object_list(details.get("answer_slots")) or answer_slots
            report_slot_coverage = (
                _object_list(details.get("report_slot_coverage")) or report_slot_coverage
            )
            supplemental = details.get("supplemental_acquisition")
            if isinstance(supplemental, dict):
                supplemental_acquisition = supplemental

        warnings.extend(_string_list(payload.get("warnings")))
        warnings.extend(_string_list(result.get("warnings")))
        if event.event_type.endswith(".gap_analysis") and isinstance(result, dict):
            gap_analysis = result
        if event.event_type.endswith(".research_strategy") and isinstance(result, dict):
            research_strategy = result

    if research_plan is not None:
        diagnostics = research_plan.get("planner_diagnostics")
        if isinstance(diagnostics, dict) and diagnostics:
            latest_planner_diagnostics = dict(diagnostics)
    planner_fell_back = (
        planner_status in {"fallback", "failed"}
        or (plan_source is not None and "after_llm_failure" in plan_source)
        or bool(latest_planner_diagnostics.get("planner_fallback"))
    )
    planner_used_llm = (
        planner_mode == "llm" and planner_status in {"success", "created"} and not planner_fell_back
    )
    if planner_enabled is True or research_plan is not None:
        runtime_warnings = [
            item
            for item in runtime_warnings
            if item
            not in {
                DISABLED_PLANNER_WARNING,
                "No LLM planner is active; generated plans use deterministic fallback only.",
            }
        ]
    provenance_warnings: list[str] = []
    if planner_fell_back:
        provenance_warnings.append(LLM_PLANNER_FALLBACK_WARNING)
    elif planner_used_llm:
        provenance_warnings.append(LLM_PLANNER_SUCCESS_WARNING)
    deduped_warnings = list(dict.fromkeys(provenance_warnings + warnings + runtime_warnings))
    if saw_fetch_counters:
        fetch_succeeded = fetch_succeeded_total
        fetch_failed = fetch_failed_total
    source_yield_rows = source_yield_summary or answer_yield
    selected_sources = _sources_with_yield(selected_sources, source_yield_rows)
    attempted_sources = _sources_with_yield(attempted_sources, source_yield_rows)
    unattempted_sources = _sources_with_yield(unattempted_sources, source_yield_rows)
    dropped_sources = _sources_with_yield(dropped_sources, source_yield_rows)
    if (
        planner_enabled is None
        and research_plan is None
        and not raw_planner_queries
        and not final_search_queries
        and search_result_count is None
        and not search_queries
        and known_path_fallback is None
        and not selected_sources_from_search
        and not selected_sources
        and not source_judgments
        and not llm_assistance
        and fetch_succeeded is None
        and fetch_failed is None
        and not attempted_sources
        and not unattempted_sources
        and not failed_sources
        and not parse_decisions
        and source_quality_summary is None
        and not source_yield_summary
        and not dropped_sources
        and not answer_slots
        and not report_slot_coverage
        and not slot_coverage_summary
        and not answer_yield
        and evidence_yield_summary is None
        and verification_summary is None
        and supplemental_acquisition is None
        and research_strategy is None
        and failure_diagnostics is None
        and not pipeline_counts
        and not deduped_warnings
        and running_mode is None
        and dependencies is None
        and plan_source is None
    ):
        return None

    return ResearchTaskObservabilityResponse(
        running_mode=running_mode,
        dependencies=dependencies,
        planner_enabled=planner_enabled,
        planner_mode=planner_mode,
        planner_status=planner_status,
        plan_source=plan_source,
        subquestion_count=subquestion_count,
        search_query_count=planner_search_query_count,
        research_plan=research_plan,
        raw_planner_queries=raw_planner_queries,
        final_search_queries=final_search_queries,
        dropped_or_downweighted_planner_queries=dropped_or_downweighted_planner_queries,
        planner_guardrail_warnings=planner_guardrail_warnings,
        intent_classification=intent_classification,
        extracted_entity=extracted_entity,
        search_result_count=search_result_count,
        search_queries=search_queries,
        known_path_fallback=known_path_fallback,
        selected_sources_from_search=selected_sources_from_search,
        selected_sources=selected_sources,
        source_judgments=source_judgments,
        llm_assistance=llm_assistance,
        fetch_succeeded=fetch_succeeded,
        fetch_failed=fetch_failed,
        attempted_sources=attempted_sources,
        unattempted_sources=unattempted_sources,
        failed_sources=failed_sources,
        parse_decisions=parse_decisions,
        source_quality_summary=source_quality_summary,
        source_yield_summary=source_yield_summary,
        dropped_sources=dropped_sources,
        answer_coverage=answer_coverage,
        answer_slots=answer_slots,
        report_slot_coverage=report_slot_coverage,
        slot_coverage_summary=slot_coverage_summary,
        answer_yield=answer_yield,
        evidence_yield_summary=evidence_yield_summary or {},
        verification_summary=verification_summary or {},
        supplemental_acquisition=supplemental_acquisition,
        research_strategy=research_strategy,
        gap_analysis=gap_analysis,
        gap_rounds=gap_rounds,
        failure_diagnostics=failure_diagnostics,
        pipeline_counts=pipeline_counts,
        warnings=deduped_warnings,
    )


def _object_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _evidence_yield_summary_from_diagnostics(payload: dict[str, Any]) -> dict[str, Any] | None:
    diagnostics_value = payload.get("diagnostics")
    diagnostics = diagnostics_value if isinstance(diagnostics_value, dict) else payload
    evidence_candidates = _object_list(diagnostics.get("evidence_candidates"))
    if not evidence_candidates:
        return None
    normalized_candidates: list[dict[str, Any]] = []
    for candidate in evidence_candidates:
        rejection_reasons = [
            reason
            for reason in _string_list(candidate.get("rejection_reasons"))
            if reason not in NON_FATAL_DETERMINISTIC_REJECTION_REASONS
        ]
        normalized_candidates.append({**candidate, "rejection_reasons": rejection_reasons})
    accepted_candidate_ids = {
        item for item in _string_list(diagnostics.get("accepted_evidence_candidate_ids")) if item
    }
    query = _string_or_none(diagnostics.get("query") or payload.get("query"))
    return summarize_evidence_yield(
        normalized_candidates,
        accepted_candidate_ids=accepted_candidate_ids,
        query=query,
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _sources_with_yield(
    sources: list[dict[str, Any]],
    answer_yield: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not sources or not answer_yield:
        return sources
    by_url = {
        item.get("canonical_url"): item
        for item in answer_yield
        if isinstance(item.get("canonical_url"), str)
    }
    enriched: list[dict[str, Any]] = []
    for source in sources:
        source_yield = by_url.get(source.get("canonical_url"))
        if source_yield is None:
            enriched.append(source)
        else:
            enriched.append({**source, "source_yield": source_yield})
    return enriched
