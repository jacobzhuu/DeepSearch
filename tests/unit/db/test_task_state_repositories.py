from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from packages.db.models import ResearchTask
from packages.db.repositories import ResearchTaskRepository, TaskEventRepository


def _create_task(db_session: Session) -> ResearchTask:
    task = ResearchTask(
        query="Phase 2 repository task",
        status="PLANNED",
        constraints_json={"language": "en"},
    )
    ResearchTaskRepository(db_session).add(task)
    db_session.commit()
    return task


def test_research_task_repository_can_apply_status_and_revision_updates(
    db_session: Session,
) -> None:
    repository = ResearchTaskRepository(db_session)
    task = _create_task(db_session)

    paused_task = repository.set_status(task, "PAUSED", ended_at=None)
    db_session.commit()

    assert paused_task.status == "PAUSED"
    assert paused_task.ended_at is None

    revised_task = repository.apply_revision(
        task,
        query="Phase 2 revised task",
        constraints_patch={"max_rounds": 2},
        status="PLANNED",
    )
    db_session.commit()

    assert revised_task.query == "Phase 2 revised task"
    assert revised_task.status == "PLANNED"
    assert revised_task.constraints_json == {"language": "en", "max_rounds": 2}


def test_task_event_repository_records_stable_payloads_in_order(db_session: Session) -> None:
    event_repository = TaskEventRepository(db_session)
    task = _create_task(db_session)

    first_event = event_repository.record(
        task_id=task.id,
        event_type="task.created",
        payload_json={
            "event_version": 1,
            "source": "api",
            "from_status": None,
            "to_status": "PLANNED",
            "changes": {"query": task.query},
        },
    )
    second_event = event_repository.record(
        task_id=task.id,
        event_type="task.paused",
        payload_json={
            "event_version": 1,
            "source": "api",
            "from_status": "PLANNED",
            "to_status": "PAUSED",
            "changes": {},
        },
    )
    db_session.commit()

    ordered_events = event_repository.list_for_task(task.id)

    assert [event.id for event in ordered_events] == [first_event.id, second_event.id]
    assert ordered_events[0].payload_json["event_version"] == 1
    assert ordered_events[1].payload_json["to_status"] == "PAUSED"

    cancelled_task = ResearchTaskRepository(db_session).set_status(
        task,
        "CANCELLED",
        ended_at=datetime(2026, 4, 22, tzinfo=UTC),
    )
    db_session.commit()

    assert cancelled_task.ended_at is not None
