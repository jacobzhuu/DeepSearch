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
    search_result_count: int | None = None
    selected_sources_from_search: list[dict[str, Any]] = []
    selected_sources: list[dict[str, Any]] = []
    fetch_succeeded: int | None = None
    fetch_failed: int | None = None
    attempted_sources: list[dict[str, Any]] = []
    unattempted_sources: list[dict[str, Any]] = []
    failed_sources: list[dict[str, Any]] = []
    parse_decisions: list[dict[str, Any]] = []
    warnings: list[str] = []

    for event in snapshot.events:
        payload = event.payload_json or {}
        if not isinstance(payload, dict):
            continue
        result = payload.get("result")
        if not isinstance(result, dict):
            result = {}

        stage = payload.get("stage")
        if stage == "SEARCHING":
            value = result.get("search_result_count")
            if isinstance(value, int):
                search_result_count = value
            selected_sources = _object_list(result.get("selected_sources"))
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
            failed_sources = _object_list(acquisition_payload.get("failed_sources"))
        elif stage == "PARSING":
            parse_decisions = _object_list(result.get("parse_decisions")) or parse_decisions

        details = payload.get("details")
        if isinstance(details, dict):
            parse_decisions = _object_list(details.get("parse_decisions")) or parse_decisions

        warnings.extend(_string_list(payload.get("warnings")))
        warnings.extend(_string_list(result.get("warnings")))

    deduped_warnings = list(dict.fromkeys(warnings))
    if (
        search_result_count is None
        and not selected_sources_from_search
        and not selected_sources
        and fetch_succeeded is None
        and fetch_failed is None
        and not attempted_sources
        and not unattempted_sources
        and not failed_sources
        and not parse_decisions
        and not deduped_warnings
    ):
        return None

    return ResearchTaskObservabilityResponse(
        search_result_count=search_result_count,
        selected_sources_from_search=selected_sources_from_search,
        selected_sources=selected_sources,
        fetch_succeeded=fetch_succeeded,
        fetch_failed=fetch_failed,
        attempted_sources=attempted_sources,
        unattempted_sources=unattempted_sources,
        failed_sources=failed_sources,
        parse_decisions=parse_decisions,
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
