from __future__ import annotations

from sqlalchemy.orm import Session

from packages.db.repositories import ResearchTaskRepository, TaskEventRepository
from services.orchestrator.app.services.research_tasks import (
    FUTURE_RUNTIME_STATUS_VALUES,
    PHASE2_ACTIVE_STATUS,
    PHASE2_CANCELLED_STATUS,
    PHASE2_EXECUTABLE_CANDIDATE_STATUS,
    PHASE2_PAUSED_STATUS,
    TASK_CANCELLED_EVENT,
    TASK_CREATED_EVENT,
    TASK_PAUSED_EVENT,
    TASK_RESUMED_EVENT,
    TASK_REVISED_EVENT,
    ResearchTaskService,
    TaskStateConflictError,
    create_research_task_service,
)


def _create_service(db_session: Session) -> ResearchTaskService:
    return create_research_task_service(db_session)


def test_create_task_persists_created_event_and_initial_status(db_session: Session) -> None:
    service = _create_service(db_session)

    task = service.create_task(
        query="Track NVIDIA open model releases",
        constraints={"language": "zh-CN"},
    )
    events = TaskEventRepository(db_session).list_for_task(task.id)

    assert task.status == PHASE2_ACTIVE_STATUS
    assert task.revision_no == 1
    assert len(events) == 1
    assert events[0].event_type == TASK_CREATED_EVENT
    assert events[0].sequence_no == 1
    assert events[0].payload_json["to_status"] == PHASE2_ACTIVE_STATUS
    assert events[0].payload_json["changes"]["revision_no"] == 1


def test_pause_resume_and_cancel_transitions_record_events(db_session: Session) -> None:
    service = _create_service(db_session)
    task = service.create_task(query="State transition task", constraints={})

    paused_status = service.pause_task(task.id).status
    resumed_status = service.resume_task(task.id).status
    cancelled = service.cancel_task(task.id)
    events = TaskEventRepository(db_session).list_for_task(task.id)

    assert paused_status == PHASE2_PAUSED_STATUS
    assert resumed_status == PHASE2_ACTIVE_STATUS
    assert cancelled.status == PHASE2_CANCELLED_STATUS
    assert cancelled.ended_at is not None
    assert [event.sequence_no for event in events] == [1, 2, 3, 4]
    assert [event.event_type for event in events] == [
        TASK_CREATED_EVENT,
        TASK_PAUSED_EVENT,
        TASK_RESUMED_EVENT,
        TASK_CANCELLED_EVENT,
    ]


def test_revise_updates_task_fields_and_returns_to_planned(db_session: Session) -> None:
    service = _create_service(db_session)
    task = service.create_task(query="Original task query", constraints={"language": "en"})
    service.pause_task(task.id)

    revised = service.revise_task(
        task.id,
        query="Revised task query",
        constraints={"max_rounds": 2},
    )
    events = TaskEventRepository(db_session).list_for_task(task.id)

    assert revised.status == PHASE2_ACTIVE_STATUS
    assert revised.revision_no == 2
    assert revised.query == "Revised task query"
    assert revised.constraints_json == {"language": "en", "max_rounds": 2}
    assert events[-1].event_type == TASK_REVISED_EVENT
    assert events[-1].sequence_no == 3
    assert events[-1].payload_json["from_status"] == PHASE2_PAUSED_STATUS
    assert events[-1].payload_json["to_status"] == PHASE2_ACTIVE_STATUS
    assert events[-1].payload_json["changes"]["revision_no"] == 2


def test_get_events_supports_after_sequence_no_and_limit(db_session: Session) -> None:
    service = _create_service(db_session)
    task = service.create_task(query="Filtered event task", constraints={})
    service.pause_task(task.id)
    service.resume_task(task.id)
    service.cancel_task(task.id)

    filtered_events = service.get_events(task.id, after_sequence_no=2, limit=2)

    assert [event.sequence_no for event in filtered_events] == [3, 4]
    assert [event.event_type for event in filtered_events] == [
        TASK_RESUMED_EVENT,
        TASK_CANCELLED_EVENT,
    ]


def test_runtime_status_placeholders_do_not_change_current_resume_target() -> None:
    assert FUTURE_RUNTIME_STATUS_VALUES == (
        "QUEUED",
        "RUNNING",
        "FAILED",
        "COMPLETED",
        "NEEDS_REVISION",
    )
    assert PHASE2_EXECUTABLE_CANDIDATE_STATUS == "PLANNED"


def test_invalid_transition_raises_conflict_error(db_session: Session) -> None:
    service = _create_service(db_session)
    task = service.create_task(query="Invalid transition task", constraints={})

    try:
        service.resume_task(task.id)
    except TaskStateConflictError as error:
        assert error.current_status == PHASE2_ACTIVE_STATUS
        assert error.action == "resume"
    else:
        raise AssertionError("resume should fail when the task is not paused")

    stored_task = ResearchTaskRepository(db_session).get(task.id)
    assert stored_task is not None
    assert stored_task.status == PHASE2_ACTIVE_STATUS
