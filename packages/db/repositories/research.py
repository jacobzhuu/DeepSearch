from __future__ import annotations

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

    def list_for_task(self, task_id: UUID) -> list[TaskEvent]:
        statement = (
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.created_at.asc(), TaskEvent.id.asc())
        )
        return list(self.session.scalars(statement))
