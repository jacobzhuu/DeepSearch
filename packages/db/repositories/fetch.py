from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from packages.db.models import ContentSnapshot, FetchAttempt, FetchJob
from packages.db.repositories.base import SQLAlchemyRepository


class FetchJobRepository(SQLAlchemyRepository[FetchJob]):
    model = FetchJob

    def get_for_candidate_mode(self, candidate_url_id: UUID, mode: str) -> FetchJob | None:
        statement = select(FetchJob).where(
            FetchJob.candidate_url_id == candidate_url_id,
            FetchJob.mode == mode,
        )
        return self.session.scalar(statement)

    def list_for_task(
        self,
        task_id: UUID,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[FetchJob]:
        statement = select(FetchJob).where(FetchJob.task_id == task_id)
        if status is not None:
            statement = statement.where(FetchJob.status == status)
        statement = statement.order_by(FetchJob.scheduled_at.asc(), FetchJob.id.asc())
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement))


class FetchAttemptRepository(SQLAlchemyRepository[FetchAttempt]):
    model = FetchAttempt

    def get_latest_for_job(self, fetch_job_id: UUID) -> FetchAttempt | None:
        statement = (
            select(FetchAttempt)
            .where(FetchAttempt.fetch_job_id == fetch_job_id)
            .order_by(FetchAttempt.attempt_no.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def list_for_job(self, fetch_job_id: UUID) -> list[FetchAttempt]:
        statement = (
            select(FetchAttempt)
            .where(FetchAttempt.fetch_job_id == fetch_job_id)
            .order_by(FetchAttempt.attempt_no.asc())
        )
        return list(self.session.scalars(statement))

    def list_for_task(
        self,
        task_id: UUID,
        *,
        fetch_job_id: UUID | None = None,
        limit: int | None = None,
    ) -> list[FetchAttempt]:
        statement = (
            select(FetchAttempt)
            .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
            .where(FetchJob.task_id == task_id)
        )
        if fetch_job_id is not None:
            statement = statement.where(FetchAttempt.fetch_job_id == fetch_job_id)
        statement = statement.order_by(FetchAttempt.started_at.asc(), FetchAttempt.id.asc())
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement))


class ContentSnapshotRepository(SQLAlchemyRepository[ContentSnapshot]):
    model = ContentSnapshot

    def get_for_fetch_attempt(self, fetch_attempt_id: UUID) -> ContentSnapshot | None:
        statement = select(ContentSnapshot).where(
            ContentSnapshot.fetch_attempt_id == fetch_attempt_id
        )
        return self.session.scalar(statement)

    def list_by_ids_for_task(
        self,
        task_id: UUID,
        content_snapshot_ids: list[UUID],
    ) -> list[ContentSnapshot]:
        if not content_snapshot_ids:
            return []
        statement = (
            select(ContentSnapshot)
            .join(FetchAttempt, FetchAttempt.id == ContentSnapshot.fetch_attempt_id)
            .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
            .where(
                FetchJob.task_id == task_id,
                ContentSnapshot.id.in_(content_snapshot_ids),
            )
            .order_by(ContentSnapshot.fetched_at.asc(), ContentSnapshot.id.asc())
        )
        return list(self.session.scalars(statement))

    def list_for_task(self, task_id: UUID, *, limit: int | None = None) -> list[ContentSnapshot]:
        statement = (
            select(ContentSnapshot)
            .join(FetchAttempt, FetchAttempt.id == ContentSnapshot.fetch_attempt_id)
            .join(FetchJob, FetchJob.id == FetchAttempt.fetch_job_id)
            .where(FetchJob.task_id == task_id)
            .order_by(ContentSnapshot.fetched_at.asc(), ContentSnapshot.id.asc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement))
