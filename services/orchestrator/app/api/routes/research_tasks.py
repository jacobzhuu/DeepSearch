from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from packages.db.models import ResearchTask
from services.orchestrator.app.api.schemas.research_tasks import (
    CreateResearchTaskRequest,
    ResearchTaskDetailResponse,
    ResearchTaskMutationResponse,
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
            current_state=snapshot.task.status,
            events_total=len(snapshot.events),
            latest_event_at=latest_event_at,
        ),
    )
