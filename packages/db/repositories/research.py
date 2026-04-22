from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from packages.db.models import ResearchRun, ResearchTask, TaskEvent
from packages.db.repositories.base import SQLAlchemyRepository


class ResearchTaskRepository(SQLAlchemyRepository[ResearchTask]):
    model = ResearchTask

    def list_by_status(self, status: str) -> list[ResearchTask]:
        statement = (
            select(ResearchTask)
            .where(ResearchTask.status == status)
            .order_by(ResearchTask.created_at.desc())
        )
        return list(self.session.scalars(statement))

    def set_status(
        self,
        task: ResearchTask,
        status: str,
        *,
        ended_at: datetime | None,
    ) -> ResearchTask:
        task.status = status
        task.updated_at = datetime.now(UTC)
        task.ended_at = ended_at
        self.session.flush()
        return task

    def apply_revision(
        self,
        task: ResearchTask,
        *,
        query: str | None,
        constraints_patch: dict[str, Any] | None,
        status: str,
    ) -> ResearchTask:
        if query is not None:
            task.query = query
        if constraints_patch is not None:
            merged_constraints = dict(task.constraints_json)
            merged_constraints.update(constraints_patch)
            task.constraints_json = merged_constraints
        task.status = status
        task.updated_at = datetime.now(UTC)
        task.ended_at = None
        self.session.flush()
        return task


class ResearchRunRepository(SQLAlchemyRepository[ResearchRun]):
    model = ResearchRun

    def list_for_task(self, task_id: UUID) -> list[ResearchRun]:
        statement = (
            select(ResearchRun)
            .where(ResearchRun.task_id == task_id)
            .order_by(ResearchRun.round_no.asc())
        )
        return list(self.session.scalars(statement))

    def get_for_task_round(self, task_id: UUID, round_no: int) -> ResearchRun | None:
        statement = select(ResearchRun).where(
            ResearchRun.task_id == task_id,
            ResearchRun.round_no == round_no,
        )
        return self.session.scalar(statement)


class TaskEventRepository(SQLAlchemyRepository[TaskEvent]):
    model = TaskEvent

    def record(
        self,
        *,
        task_id: UUID,
        event_type: str,
        payload_json: dict[str, Any],
        run_id: UUID | None = None,
    ) -> TaskEvent:
        event = TaskEvent(
            task_id=task_id,
            run_id=run_id,
            event_type=event_type,
            payload_json=payload_json,
            created_at=datetime.now(UTC),
        )
        return self.add(event)

    def list_for_task(self, task_id: UUID) -> list[TaskEvent]:
        statement = (
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.created_at.asc(), TaskEvent.id.asc())
        )
        return list(self.session.scalars(statement))
