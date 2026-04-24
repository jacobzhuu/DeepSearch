from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from packages.db.models import ReportArtifact
from packages.db.repositories.base import SQLAlchemyRepository


class ReportArtifactRepository(SQLAlchemyRepository[ReportArtifact]):
    model = ReportArtifact

    def list_for_task(self, task_id: UUID, *, format: str | None = None) -> list[ReportArtifact]:
        statement = select(ReportArtifact).where(ReportArtifact.task_id == task_id)
        if format is not None:
            statement = statement.where(ReportArtifact.format == format)
        statement = statement.order_by(
            ReportArtifact.version.desc(),
            ReportArtifact.created_at.desc(),
            ReportArtifact.format.asc(),
            ReportArtifact.id.asc(),
        )
        return list(self.session.scalars(statement))

    def get_latest_for_task(self, task_id: UUID) -> ReportArtifact | None:
        statement = (
            select(ReportArtifact)
            .where(ReportArtifact.task_id == task_id)
            .order_by(
                ReportArtifact.version.desc(),
                ReportArtifact.created_at.desc(),
                ReportArtifact.format.asc(),
                ReportArtifact.id.asc(),
            )
            .limit(1)
        )
        return self.session.scalar(statement)

    def get_latest_for_task_format(self, task_id: UUID, *, format: str) -> ReportArtifact | None:
        statement = (
            select(ReportArtifact)
            .where(
                ReportArtifact.task_id == task_id,
                ReportArtifact.format == format,
            )
            .order_by(
                ReportArtifact.version.desc(),
                ReportArtifact.created_at.desc(),
                ReportArtifact.id.asc(),
            )
            .limit(1)
        )
        return self.session.scalar(statement)
