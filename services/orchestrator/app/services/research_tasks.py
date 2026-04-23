from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from packages.db.models import ResearchTask, TaskEvent
from packages.db.models.constants import CURRENT_TASK_STATUS_VALUES
from packages.db.models.constants import (
    FUTURE_RUNTIME_STATUS_VALUES as MODEL_FUTURE_RUNTIME_STATUS_VALUES,
)
from packages.db.repositories import ResearchTaskRepository, TaskEventRepository

TASK_EVENT_VERSION = 1
TASK_CREATED_EVENT = "task.created"
TASK_PAUSED_EVENT = "task.paused"
TASK_RESUMED_EVENT = "task.resumed"
TASK_CANCELLED_EVENT = "task.cancelled"
TASK_REVISED_EVENT = "task.revised"

PHASE2_EXECUTABLE_CANDIDATE_STATUS = "PLANNED"
PHASE2_ACTIVE_STATUS = PHASE2_EXECUTABLE_CANDIDATE_STATUS
PHASE2_PAUSED_STATUS = "PAUSED"
PHASE2_CANCELLED_STATUS = "CANCELLED"
PHASE2_STABLE_STATUS_VALUES = CURRENT_TASK_STATUS_VALUES
FUTURE_RUNTIME_STATUS_VALUES = MODEL_FUTURE_RUNTIME_STATUS_VALUES

ACTION_TRANSITIONS = {
    "pause": {PHASE2_ACTIVE_STATUS: PHASE2_PAUSED_STATUS},
    "resume": {PHASE2_PAUSED_STATUS: PHASE2_ACTIVE_STATUS},
    "cancel": {
        PHASE2_ACTIVE_STATUS: PHASE2_CANCELLED_STATUS,
        PHASE2_PAUSED_STATUS: PHASE2_CANCELLED_STATUS,
    },
    "revise": {
        PHASE2_ACTIVE_STATUS: PHASE2_ACTIVE_STATUS,
        PHASE2_PAUSED_STATUS: PHASE2_ACTIVE_STATUS,
    },
}


class TaskNotFoundError(Exception):
    def __init__(self, task_id: UUID) -> None:
        super().__init__(f"task {task_id} was not found")
        self.task_id = task_id


class TaskStateConflictError(Exception):
    def __init__(self, task_id: UUID, action: str, current_status: str) -> None:
        super().__init__(f"cannot {action} task {task_id} from status {current_status}")
        self.task_id = task_id
        self.action = action
        self.current_status = current_status


@dataclass(frozen=True)
class TaskSnapshot:
    task: ResearchTask
    events: list[TaskEvent]


def build_task_event_payload(
    *,
    from_status: str | None,
    to_status: str,
    changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_version": TASK_EVENT_VERSION,
        "source": "api",
        "from_status": from_status,
        "to_status": to_status,
        "changes": changes or {},
    }


class ResearchTaskService:
    def __init__(
        self,
        session: Session,
        task_repository: ResearchTaskRepository,
        event_repository: TaskEventRepository,
    ) -> None:
        self.session = session
        self.task_repository = task_repository
        self.event_repository = event_repository

    def create_task(self, *, query: str, constraints: dict[str, Any]) -> ResearchTask:
        task = self.task_repository.add(
            ResearchTask(
                query=query,
                status=PHASE2_EXECUTABLE_CANDIDATE_STATUS,
                constraints_json=constraints,
            )
        )
        self.event_repository.record(
            task_id=task.id,
            event_type=TASK_CREATED_EVENT,
            payload_json=build_task_event_payload(
                from_status=None,
                to_status=PHASE2_EXECUTABLE_CANDIDATE_STATUS,
                changes={
                    "query": query,
                    "constraints": constraints,
                    "revision_no": task.revision_no,
                },
            ),
        )
        self.session.commit()
        self.session.refresh(task)
        return task

    def get_task_snapshot(self, task_id: UUID) -> TaskSnapshot:
        task = self._get_task(task_id)
        events = self.event_repository.list_for_task(task_id)
        return TaskSnapshot(task=task, events=events)

    def get_events(
        self,
        task_id: UUID,
        *,
        after_sequence_no: int | None = None,
        limit: int | None = None,
    ) -> list[TaskEvent]:
        self._get_task(task_id)
        return self.event_repository.list_for_task(
            task_id,
            after_sequence_no=after_sequence_no,
            limit=limit,
        )

    def pause_task(self, task_id: UUID) -> ResearchTask:
        return self._transition_task(
            task_id=task_id,
            action="pause",
            event_type=TASK_PAUSED_EVENT,
        )

    def resume_task(self, task_id: UUID) -> ResearchTask:
        return self._transition_task(
            task_id=task_id,
            action="resume",
            event_type=TASK_RESUMED_EVENT,
        )

    def cancel_task(self, task_id: UUID) -> ResearchTask:
        return self._transition_task(
            task_id=task_id,
            action="cancel",
            event_type=TASK_CANCELLED_EVENT,
        )

    def revise_task(
        self,
        task_id: UUID,
        *,
        query: str | None,
        constraints: dict[str, Any] | None,
    ) -> ResearchTask:
        task = self._get_task(task_id)
        current_status = task.status
        next_status = self._next_status(
            task_id=task.id, current_status=current_status, action="revise"
        )

        changed_fields: dict[str, Any] = {}
        if query is not None:
            changed_fields["query"] = query
        if constraints is not None:
            changed_fields["constraints"] = constraints

        self.task_repository.apply_revision(
            task,
            query=query,
            constraints_patch=constraints,
            status=next_status,
        )
        self.event_repository.record(
            task_id=task.id,
            event_type=TASK_REVISED_EVENT,
            payload_json=build_task_event_payload(
                from_status=current_status,
                to_status=next_status,
                changes={**changed_fields, "revision_no": task.revision_no},
            ),
        )
        self.session.commit()
        self.session.refresh(task)
        return task

    def _transition_task(self, *, task_id: UUID, action: str, event_type: str) -> ResearchTask:
        task = self._get_task(task_id)
        current_status = task.status
        next_status = self._next_status(
            task_id=task.id, current_status=current_status, action=action
        )

        ended_at = datetime.now(UTC) if next_status == PHASE2_CANCELLED_STATUS else None
        self.task_repository.set_status(task, next_status, ended_at=ended_at)
        self.event_repository.record(
            task_id=task.id,
            event_type=event_type,
            payload_json=build_task_event_payload(
                from_status=current_status,
                to_status=next_status,
            ),
        )
        self.session.commit()
        self.session.refresh(task)
        return task

    def _get_task(self, task_id: UUID) -> ResearchTask:
        task = self.task_repository.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def _next_status(self, *, task_id: UUID, current_status: str, action: str) -> str:
        try:
            return ACTION_TRANSITIONS[action][current_status]
        except KeyError as error:
            raise TaskStateConflictError(
                task_id=task_id, action=action, current_status=current_status
            ) from error


def create_research_task_service(session: Session) -> ResearchTaskService:
    return ResearchTaskService(
        session=session,
        task_repository=ResearchTaskRepository(session),
        event_repository=TaskEventRepository(session),
    )
