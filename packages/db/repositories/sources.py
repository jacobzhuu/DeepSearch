from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from packages.db.models import CitationSpan, SourceChunk, SourceDocument
from packages.db.repositories.base import SQLAlchemyRepository


class SourceDocumentRepository(SQLAlchemyRepository[SourceDocument]):
    model = SourceDocument

    def list_for_task(self, task_id: UUID) -> list[SourceDocument]:
        statement = (
            select(SourceDocument)
            .where(SourceDocument.task_id == task_id)
            .order_by(SourceDocument.final_source_score.desc(), SourceDocument.id.asc())
        )
        return list(self.session.scalars(statement))

    def get_for_task_url(self, task_id: UUID, canonical_url: str) -> SourceDocument | None:
        statement = select(SourceDocument).where(
            SourceDocument.task_id == task_id,
            SourceDocument.canonical_url == canonical_url,
        )
        return self.session.scalar(statement)


class SourceChunkRepository(SQLAlchemyRepository[SourceChunk]):
    model = SourceChunk

    def list_for_document(self, source_document_id: UUID) -> list[SourceChunk]:
        statement = (
            select(SourceChunk)
            .where(SourceChunk.source_document_id == source_document_id)
            .order_by(SourceChunk.chunk_no.asc())
        )
        return list(self.session.scalars(statement))


class CitationSpanRepository(SQLAlchemyRepository[CitationSpan]):
    model = CitationSpan

    def list_for_chunk(self, source_chunk_id: UUID) -> list[CitationSpan]:
        statement = (
            select(CitationSpan)
            .where(CitationSpan.source_chunk_id == source_chunk_id)
            .order_by(CitationSpan.start_offset.asc(), CitationSpan.end_offset.asc())
        )
        return list(self.session.scalars(statement))
