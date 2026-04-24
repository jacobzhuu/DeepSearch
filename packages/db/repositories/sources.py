from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from packages.db.models import CitationSpan, SourceChunk, SourceDocument
from packages.db.repositories.base import SQLAlchemyRepository


class SourceDocumentRepository(SQLAlchemyRepository[SourceDocument]):
    model = SourceDocument

    def list_for_task(self, task_id: UUID, *, limit: int | None = None) -> list[SourceDocument]:
        statement = (
            select(SourceDocument)
            .where(SourceDocument.task_id == task_id)
            .order_by(SourceDocument.fetched_at.asc(), SourceDocument.id.asc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement))

    def get_for_task_url(self, task_id: UUID, canonical_url: str) -> SourceDocument | None:
        statement = select(SourceDocument).where(
            SourceDocument.task_id == task_id,
            SourceDocument.canonical_url == canonical_url,
        )
        return self.session.scalar(statement)

    def get_for_content_snapshot(self, content_snapshot_id: UUID) -> SourceDocument | None:
        statement = select(SourceDocument).where(
            SourceDocument.content_snapshot_id == content_snapshot_id
        )
        return self.session.scalar(statement)


class SourceChunkRepository(SQLAlchemyRepository[SourceChunk]):
    model = SourceChunk

    def list_for_document(self, source_document_id: UUID) -> list[SourceChunk]:
        statement = (
            select(SourceChunk)
            .options(joinedload(SourceChunk.source_document))
            .where(SourceChunk.source_document_id == source_document_id)
            .order_by(SourceChunk.chunk_no.asc())
        )
        return list(self.session.scalars(statement))

    def list_for_task(
        self,
        task_id: UUID,
        *,
        source_document_id: UUID | None = None,
        limit: int | None = None,
    ) -> list[SourceChunk]:
        statement = (
            select(SourceChunk)
            .options(joinedload(SourceChunk.source_document))
            .join(SourceDocument, SourceDocument.id == SourceChunk.source_document_id)
            .where(SourceDocument.task_id == task_id)
            .order_by(
                SourceDocument.fetched_at.asc(),
                SourceDocument.id.asc(),
                SourceChunk.chunk_no.asc(),
            )
        )
        if source_document_id is not None:
            statement = statement.where(SourceChunk.source_document_id == source_document_id)
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement))

    def list_by_ids_for_task(
        self,
        task_id: UUID,
        source_chunk_ids: list[UUID],
    ) -> list[SourceChunk]:
        if not source_chunk_ids:
            return []

        statement = (
            select(SourceChunk)
            .options(joinedload(SourceChunk.source_document))
            .join(SourceDocument, SourceDocument.id == SourceChunk.source_document_id)
            .where(
                SourceDocument.task_id == task_id,
                SourceChunk.id.in_(source_chunk_ids),
            )
            .order_by(
                SourceDocument.fetched_at.asc(),
                SourceDocument.id.asc(),
                SourceChunk.chunk_no.asc(),
            )
        )
        return list(self.session.scalars(statement))


class CitationSpanRepository(SQLAlchemyRepository[CitationSpan]):
    model = CitationSpan

    def get_for_chunk_offsets(
        self,
        source_chunk_id: UUID,
        *,
        start_offset: int,
        end_offset: int,
    ) -> CitationSpan | None:
        statement = select(CitationSpan).where(
            CitationSpan.source_chunk_id == source_chunk_id,
            CitationSpan.start_offset == start_offset,
            CitationSpan.end_offset == end_offset,
        )
        return self.session.scalar(statement)

    def list_for_chunk(self, source_chunk_id: UUID) -> list[CitationSpan]:
        statement = (
            select(CitationSpan)
            .where(CitationSpan.source_chunk_id == source_chunk_id)
            .order_by(CitationSpan.start_offset.asc(), CitationSpan.end_offset.asc())
        )
        return list(self.session.scalars(statement))
