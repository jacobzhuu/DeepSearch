from __future__ import annotations

from collections.abc import Generator, Sequence
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from packages.db.models import SourceChunk, SourceDocument
from packages.db.repositories import SourceChunkRepository, SourceDocumentRepository
from services.orchestrator.app.api.routes.indexing import get_chunk_index_backend
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.indexing import (
    ChunkIndexDocument,
    IndexBackendOperationError,
    IndexedChunkPage,
    IndexedChunkRecord,
)
from services.orchestrator.app.main import create_app
from services.orchestrator.app.services.research_tasks import create_research_task_service


class InMemoryChunkIndexBackend:
    def __init__(self) -> None:
        self.documents: dict[UUID, IndexedChunkRecord] = {}

    def validate_configuration(self) -> None:
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


class FailingChunkIndexBackend(InMemoryChunkIndexBackend):
    def __init__(self) -> None:
        super().__init__()

    def upsert_chunks(self, documents: Sequence[ChunkIndexDocument]) -> None:
        del documents
        raise IndexBackendOperationError(operation="chunk upsert", detail="backend unavailable")


def test_indexing_endpoints_index_list_and_retrieve(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        task_id, first_chunk_id = _seed_source_chunks(session)

    backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(session_factory, backend)
    client = next(client_generator)
    try:
        index_response = client.post(
            f"/api/v1/research/tasks/{task_id}/index",
            json={"source_chunk_ids": [first_chunk_id]},
        )
        indexed_chunks_response = client.get(
            f"/api/v1/research/tasks/{task_id}/indexed-chunks",
        )
        retrieval_response = client.get(
            f"/api/v1/research/tasks/{task_id}/retrieve",
            params={"query": "beta"},
        )

        assert index_response.status_code == 200
        assert index_response.json()["indexed_count"] == 1
        assert index_response.json()["indexed_chunks"][0]["source_chunk_id"] == first_chunk_id

        assert indexed_chunks_response.status_code == 200
        assert indexed_chunks_response.json()["total"] == 1
        assert indexed_chunks_response.json()["indexed_chunks"][0]["canonical_url"] == (
            "https://example.com/api-source"
        )

        assert retrieval_response.status_code == 200
        assert retrieval_response.json()["total"] == 1
        assert retrieval_response.json()["hits"][0]["score"] == 1.0
    finally:
        client_generator.close()


def test_indexing_endpoint_rejects_paused_task(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        task_id, first_chunk_id = _seed_source_chunks(session)
        create_research_task_service(session).pause_task(UUID(task_id))

    backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(session_factory, backend)
    client = next(client_generator)
    try:
        response = client.post(
            f"/api/v1/research/tasks/{task_id}/index",
            json={"source_chunk_ids": [first_chunk_id]},
        )

        assert response.status_code == 409
        assert "cannot index source chunks" in response.json()["detail"]
    finally:
        client_generator.close()


def test_indexing_endpoint_returns_502_on_backend_failure(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        task_id, first_chunk_id = _seed_source_chunks(session)

    client_generator = _build_client(session_factory, FailingChunkIndexBackend())
    client = next(client_generator)
    try:
        response = client.post(
            f"/api/v1/research/tasks/{task_id}/index",
            json={"source_chunk_ids": [first_chunk_id]},
        )

        assert response.status_code == 502
        assert "backend unavailable" in response.json()["detail"]
    finally:
        client_generator.close()


def _build_client(
    session_factory: sessionmaker[Session],
    backend: InMemoryChunkIndexBackend,
) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_db_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_chunk_index_backend] = lambda: backend

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _seed_source_chunks(session: Session) -> tuple[str, str]:
    task = create_research_task_service(session).create_task(
        query="Indexing API task",
        constraints={},
    )
    source_document_repo = SourceDocumentRepository(session)
    source_chunk_repo = SourceChunkRepository(session)

    source_document = source_document_repo.add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/api-source",
            domain="example.com",
            title="Indexed API source",
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
    source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text="Delta epsilon zeta",
            token_count=3,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    session.commit()
    return str(task.id), str(first_chunk.id)
