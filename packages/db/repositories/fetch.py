from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from packages.db.models import ContentSnapshot, FetchAttempt, FetchJob
from packages.db.repositories.base import SQLAlchemyRepository


class FetchJobRepository(SQLAlchemyRepository[FetchJob]):
    model = FetchJob

    def list_for_task(self, task_id: UUID) -> list[FetchJob]:
        statement = (
            select(FetchJob)
            .where(FetchJob.task_id == task_id)
            .order_by(FetchJob.scheduled_at.asc(), FetchJob.id.asc())
        )
        return list(self.session.scalars(statement))


class FetchAttemptRepository(SQLAlchemyRepository[FetchAttempt]):
    model = FetchAttempt

    def list_for_job(self, fetch_job_id: UUID) -> list[FetchAttempt]:
        statement = (
            select(FetchAttempt)
            .where(FetchAttempt.fetch_job_id == fetch_job_id)
            .order_by(FetchAttempt.attempt_no.asc())
        )
        return list(self.session.scalars(statement))


class ContentSnapshotRepository(SQLAlchemyRepository[ContentSnapshot]):
    model = ContentSnapshot

    def get_for_fetch_attempt(self, fetch_attempt_id: UUID) -> ContentSnapshot | None:
        statement = select(ContentSnapshot).where(
            ContentSnapshot.fetch_attempt_id == fetch_attempt_id
        )
        return self.session.scalar(statement)
