from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from packages.db.models import Claim, ClaimEvidence
from packages.db.repositories.base import SQLAlchemyRepository


class ClaimRepository(SQLAlchemyRepository[Claim]):
    model = Claim

    def list_for_task(self, task_id: UUID) -> list[Claim]:
        statement = select(Claim).where(Claim.task_id == task_id).order_by(Claim.id.asc())
        return list(self.session.scalars(statement))


class ClaimEvidenceRepository(SQLAlchemyRepository[ClaimEvidence]):
    model = ClaimEvidence

    def list_for_claim(self, claim_id: UUID) -> list[ClaimEvidence]:
        statement = (
            select(ClaimEvidence)
            .where(ClaimEvidence.claim_id == claim_id)
            .order_by(ClaimEvidence.id.asc())
        )
        return list(self.session.scalars(statement))
