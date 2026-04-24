from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from packages.db.models import CitationSpan, Claim, ClaimEvidence, SourceChunk
from packages.db.repositories.base import SQLAlchemyRepository


class ClaimRepository(SQLAlchemyRepository[Claim]):
    model = Claim

    def get_for_task_statement(self, task_id: UUID, statement_text: str) -> Claim | None:
        statement = select(Claim).where(
            Claim.task_id == task_id,
            Claim.statement == statement_text,
        )
        return self.session.scalar(statement)

    def list_by_ids_for_task(
        self,
        task_id: UUID,
        claim_ids: list[UUID],
    ) -> list[Claim]:
        if not claim_ids:
            return []

        statement = select(Claim).where(
            Claim.task_id == task_id,
            Claim.id.in_(claim_ids),
        )
        return list(self.session.scalars(statement))

    def list_for_task(
        self,
        task_id: UUID,
        *,
        verification_status: str | None = None,
        limit: int | None = None,
    ) -> list[Claim]:
        statement = select(Claim).where(Claim.task_id == task_id).order_by(Claim.id.asc())
        if verification_status is not None:
            statement = statement.where(Claim.verification_status == verification_status)
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement))


class ClaimEvidenceRepository(SQLAlchemyRepository[ClaimEvidence]):
    model = ClaimEvidence

    def get_for_claim_citation_relation(
        self,
        claim_id: UUID,
        *,
        citation_span_id: UUID,
        relation_type: str,
    ) -> ClaimEvidence | None:
        statement = select(ClaimEvidence).where(
            ClaimEvidence.claim_id == claim_id,
            ClaimEvidence.citation_span_id == citation_span_id,
            ClaimEvidence.relation_type == relation_type,
        )
        return self.session.scalar(statement)

    def list_for_claim(self, claim_id: UUID) -> list[ClaimEvidence]:
        statement = (
            select(ClaimEvidence)
            .options(
                joinedload(ClaimEvidence.claim),
                joinedload(ClaimEvidence.citation_span)
                .joinedload(CitationSpan.source_chunk)
                .joinedload(SourceChunk.source_document),
            )
            .where(ClaimEvidence.claim_id == claim_id)
            .order_by(ClaimEvidence.id.asc())
        )
        return list(self.session.scalars(statement))

    def list_for_task(
        self,
        task_id: UUID,
        *,
        claim_id: UUID | None = None,
        relation_type: str | None = None,
        limit: int | None = None,
    ) -> list[ClaimEvidence]:
        statement = (
            select(ClaimEvidence)
            .options(
                joinedload(ClaimEvidence.claim),
                joinedload(ClaimEvidence.citation_span)
                .joinedload(CitationSpan.source_chunk)
                .joinedload(SourceChunk.source_document),
            )
            .join(Claim, Claim.id == ClaimEvidence.claim_id)
            .where(Claim.task_id == task_id)
            .order_by(Claim.id.asc(), ClaimEvidence.id.asc())
        )
        if claim_id is not None:
            statement = statement.where(ClaimEvidence.claim_id == claim_id)
        if relation_type is not None:
            statement = statement.where(ClaimEvidence.relation_type == relation_type)
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement))
