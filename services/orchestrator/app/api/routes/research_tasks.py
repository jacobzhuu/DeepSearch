from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from packages.db.models import ResearchTask
from services.orchestrator.app.api.schemas.research_tasks import (
    CreateResearchTaskRequest,
    ResearchTaskDetailResponse,
    ResearchTaskMutationResponse,
    ResearchTaskObservabilityResponse,
    ResearchTaskProgressResponse,
    ReviseResearchTaskRequest,
    TaskEventListResponse,
    TaskEventResponse,
)
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.services.research_tasks import (
    ResearchTaskService,
    TaskNotFoundError,
    TaskSnapshot,
    TaskStateConflictError,
    create_research_task_service,
)

router = APIRouter(prefix="/api/v1/research/tasks", tags=["research-tasks"])
SessionDep = Annotated[Session, Depends(get_db_session)]


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
    task = service.create_task(query=request.query, constraints=request.constraints)
    return ResearchTaskMutationResponse(
        task_id=task.id,
        status=task.status,
        revision_no=task.revision_no,
        updated_at=task.updated_at,
    )


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
        task = service.revise_task(task_id, query=request.query, constraints=request.constraints)
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


def _derive_observability(snapshot: TaskSnapshot) -> ResearchTaskObservabilityResponse | None:
    planner_enabled: bool | None = None
    planner_mode: str | None = None
    planner_status: str | None = None
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
    selected_sources_from_search: list[dict[str, Any]] = []
    selected_sources: list[dict[str, Any]] = []
    fetch_succeeded: int | None = None
    fetch_failed: int | None = None
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
    failure_diagnostics: dict[str, Any] | None = None
    warnings: list[str] = []

    for event in snapshot.events:
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        result = payload.get("result")
        if not isinstance(result, dict):
            result = {}

        stage = payload.get("stage")
        if event.event_type == "research_plan.created":
            planner_enabled = True
            planner_status = "created"
            result_plan = result.get("research_plan")
            if isinstance(result_plan, dict):
                research_plan = result_plan
                planner_mode = _string_or_none(result_plan.get("planner_mode")) or planner_mode
                subquestions = result_plan.get("subquestions")
                if isinstance(subquestions, list):
                    subquestion_count = len(subquestions)
                search_queries = result_plan.get("search_queries")
                if isinstance(search_queries, list):
                    planner_search_query_count = len(search_queries)
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
            selected_sources = _object_list(result.get("selected_sources"))
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
                fetch_succeeded = succeeded
            if isinstance(failed, int):
                fetch_failed = failed
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
            evidence_summary = result.get("evidence_yield_summary")
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
        elif stage == "VERIFYING":
            verification = result.get("verification_summary")
            if isinstance(verification, dict):
                verification_summary = verification
            slot_coverage_summary = (
                _object_list(result.get("slot_coverage_summary")) or slot_coverage_summary
            )
        elif stage == "REPORTING":
            quality_summary = result.get("source_quality_summary")
            if isinstance(quality_summary, dict):
                source_quality_summary = quality_summary
            source_yield_summary = (
                _object_list(result.get("source_yield_summary")) or source_yield_summary
            )
            dropped_sources = _object_list(result.get("dropped_sources")) or dropped_sources
            slot_coverage_summary = (
                _object_list(result.get("slot_coverage_summary")) or slot_coverage_summary
            )
            evidence_summary = result.get("evidence_yield_summary")
            if isinstance(evidence_summary, dict):
                evidence_yield_summary = evidence_summary
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
            evidence_summary = details.get("evidence_yield_summary")
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

    deduped_warnings = list(dict.fromkeys(warnings))
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
        and not selected_sources_from_search
        and not selected_sources
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
        and failure_diagnostics is None
        and not deduped_warnings
    ):
        return None

    return ResearchTaskObservabilityResponse(
        planner_enabled=planner_enabled,
        planner_mode=planner_mode,
        planner_status=planner_status,
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
        selected_sources_from_search=selected_sources_from_search,
        selected_sources=selected_sources,
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
        failure_diagnostics=failure_diagnostics,
        warnings=deduped_warnings,
    )


def _object_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


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
