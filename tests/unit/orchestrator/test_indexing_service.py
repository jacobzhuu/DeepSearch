from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from packages.db.models import SourceChunk, SourceDocument
from packages.db.repositories import SourceChunkRepository, SourceDocumentRepository
from services.orchestrator.app.indexing import (
    ChunkIndexDocument,
    IndexedChunkPage,
    IndexedChunkRecord,
)
from services.orchestrator.app.services.indexing import (
    IndexingConflictError,
    RetrievalQueryError,
    create_indexing_service,
)
from services.orchestrator.app.services.research_tasks import create_research_task_service


class InMemoryChunkIndexBackend:
    def __init__(self) -> None:
        self.documents: dict[UUID, IndexedChunkRecord] = {}

    def validate_configuration(self) -> None:
        return None

    def ensure_index(self) -> None:
        return None

    def upsert_chunks(self, documents: Sequence[ChunkIndexDocument]) -> None:
        for document in documents:
            self.documents[document.source_chunk_id] = IndexedChunkRecord(
                task_id=document.task_id,
                source_document_id=document.source_document_id,
                source_chunk_id=document.source_chunk_id,
                canonical_url=document.canonical_url,
                domain=document.domain,
                chunk_no=document.chunk_no,
                text=document.text,
                metadata=document.metadata,
            )

    def list_chunks(self, *, task_id: UUID, offset: int, limit: int) -> IndexedChunkPage:
        hits = [item for item in self.documents.values() if item.task_id == task_id]
        hits.sort(
            key=lambda item: (
                str(item.source_document_id),
                item.chunk_no,
                str(item.source_chunk_id),
            )
        )
        return IndexedChunkPage(total=len(hits), hits=hits[offset : offset + limit])

    def retrieve_chunks(
        self,
        *,
        task_id: UUID,
        query: str,
        offset: int,
        limit: int,
    ) -> IndexedChunkPage:
        query_tokens = [token for token in query.lower().split() if token]
        hits = []
        for item in self.documents.values():
            if item.task_id != task_id:
                continue
            score = float(sum(1 for token in query_tokens if token in item.text.lower()))
            if score <= 0:
                continue
            hits.append(
                IndexedChunkRecord(
                    task_id=item.task_id,
                    source_document_id=item.source_document_id,
                    source_chunk_id=item.source_chunk_id,
                    canonical_url=item.canonical_url,
                    domain=item.domain,
                    chunk_no=item.chunk_no,
                    text=item.text,
                    metadata=item.metadata,
                    score=score,
                )
            )
        hits.sort(key=lambda item: (-(item.score or 0.0), item.chunk_no, str(item.source_chunk_id)))
        return IndexedChunkPage(total=len(hits), hits=hits[offset : offset + limit])


def test_indexing_service_indexes_selected_chunks_and_retrieves_hits(db_session: Session) -> None:
    task_id, chunks = _seed_source_chunks(db_session)
    backend = InMemoryChunkIndexBackend()
    service = create_indexing_service(
        db_session,
        index_backend=backend,
        indexing_max_chunks_per_request=10,
        retrieval_max_results_per_request=10,
    )

    result = service.index_source_chunks(
        task_id,
        source_chunk_ids=[chunks[1].id, chunks[0].id, chunks[1].id],
        limit=10,
    )
    listed = service.list_indexed_chunks(task_id, offset=0, limit=10)
    retrieved = service.retrieve_chunks(task_id, query="beta", offset=0, limit=10)

    assert [item.id for item in result.indexed_chunks] == [chunks[1].id, chunks[0].id]
    assert listed.total == 2
    assert [item.source_chunk_id for item in listed.hits] == [chunks[0].id, chunks[1].id]
    assert retrieved.total == 1
    assert retrieved.hits[0].source_chunk_id == chunks[0].id
    assert retrieved.hits[0].score == 1.0


def test_indexing_service_rejects_paused_task_and_blank_query(db_session: Session) -> None:
    task_id, chunks = _seed_source_chunks(db_session)
    create_research_task_service(db_session).pause_task(task_id)
    backend = InMemoryChunkIndexBackend()
    service = create_indexing_service(
        db_session,
        index_backend=backend,
        indexing_max_chunks_per_request=10,
        retrieval_max_results_per_request=10,
    )

    with pytest.raises(IndexingConflictError):
        service.index_source_chunks(task_id, source_chunk_ids=[chunks[0].id], limit=1)

    create_research_task_service(db_session).resume_task(task_id)
    with pytest.raises(RetrievalQueryError):
        service.retrieve_chunks(task_id, query="   ", offset=0, limit=10)


def _seed_source_chunks(db_session: Session) -> tuple[UUID, list[SourceChunk]]:
    task = create_research_task_service(db_session).create_task(
        query="Indexing service task",
        constraints={},
    )
    source_document_repo = SourceDocumentRepository(db_session)
    source_chunk_repo = SourceChunkRepository(db_session)

    source_document = source_document_repo.add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/source",
            domain="example.com",
            title="Indexed source",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
            authority_score=None,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=None,
        )
    )
    first_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text="Alpha beta gamma",
            token_count=3,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    second_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text="Delta epsilon zeta",
            token_count=3,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    db_session.commit()
    return task.id, [first_chunk, second_chunk]
