from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from packages.db.models import ReportArtifact
from packages.db.repositories.base import SQLAlchemyRepository


class ReportArtifactRepository(SQLAlchemyRepository[ReportArtifact]):
    model = ReportArtifact

    def list_for_task(self, task_id: UUID) -> list[ReportArtifact]:
        statement = (
            select(ReportArtifact)
            .where(ReportArtifact.task_id == task_id)
            .order_by(ReportArtifact.version.desc(), ReportArtifact.created_at.desc())
        )
        return list(self.session.scalars(statement))

    def get_latest_for_task(self, task_id: UUID) -> ReportArtifact | None:
        statement = (
            select(ReportArtifact)
            .where(ReportArtifact.task_id == task_id)
            .order_by(ReportArtifact.version.desc(), ReportArtifact.created_at.desc())
            .limit(1)
        )
        return self.session.scalar(statement)
