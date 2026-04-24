from __future__ import annotations

from collections.abc import Generator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from packages.db.models import SourceChunk, SourceDocument
from packages.db.repositories import SourceChunkRepository, SourceDocumentRepository
from services.orchestrator.app.api.routes.claims import get_claim_chunk_index_backend
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
    def __init__(self, *, hits: list[IndexedChunkRecord]) -> None:
        self.hits = hits

    def validate_configuration(self) -> None:
        return None

    def upsert_chunks(self, documents: Sequence[ChunkIndexDocument]) -> None:
        del documents

    def list_chunks(self, *, task_id: UUID, offset: int, limit: int) -> IndexedChunkPage:
        del task_id, offset, limit
        return IndexedChunkPage(total=0, hits=[])

    def retrieve_chunks(
        self,
        *,
        task_id: UUID,
        query: str,
        offset: int,
        limit: int,
    ) -> IndexedChunkPage:
        del query
        task_hits = [hit for hit in self.hits if hit.task_id == task_id]
        return IndexedChunkPage(total=len(task_hits), hits=task_hits[offset : offset + limit])


class FailingChunkIndexBackend(InMemoryChunkIndexBackend):
    def __init__(self) -> None:
        super().__init__(hits=[])

    def retrieve_chunks(
        self,
        *,
        task_id: UUID,
        query: str,
        offset: int,
        limit: int,
    ) -> IndexedChunkPage:
        del task_id, query, offset, limit
        raise IndexBackendOperationError(operation="chunk retrieval", detail="backend unavailable")


@dataclass(frozen=True)
class SeededChunk:
    task_id: str
    source_chunk_id: UUID
    source_document_id: UUID
    canonical_url: str
    domain: str
    text: str
    metadata: dict[str, str]


def test_claim_drafting_endpoints_draft_and_list_claims(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seeded = _seed_source_chunk(session)

    backend = InMemoryChunkIndexBackend(
        hits=[
            IndexedChunkRecord(
                task_id=UUID(seeded.task_id),
                source_document_id=seeded.source_document_id,
                source_chunk_id=seeded.source_chunk_id,
                canonical_url=seeded.canonical_url,
                domain=seeded.domain,
                chunk_no=0,
                text=seeded.text,
                metadata=seeded.metadata,
                score=1.0,
            )
        ]
    )
    client_generator = _build_client(session_factory, backend)
    client = next(client_generator)
    try:
        draft_response = client.post(
            f"/api/v1/research/tasks/{seeded.task_id}/claims/draft",
            json={"query": "illustrative examples", "limit": 5},
        )
        claims_response = client.get(f"/api/v1/research/tasks/{seeded.task_id}/claims")
        evidence_response = client.get(f"/api/v1/research/tasks/{seeded.task_id}/claim-evidence")

        assert draft_response.status_code == 200
        assert draft_response.json()["created_claims"] == 1
        assert draft_response.json()["claims"][0]["relation_type"] == "support"

        assert claims_response.status_code == 200
        assert claims_response.json()["claims"][0]["verification_status"] == "draft"

        assert evidence_response.status_code == 200
        evidence = evidence_response.json()["claim_evidence"][0]
        assert evidence["relation_type"] == "support"
        assert evidence["excerpt"] == seeded.text[evidence["start_offset"] : evidence["end_offset"]]
    finally:
        client_generator.close()


def test_claims_endpoint_returns_empty_list_for_planned_task(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        task = create_research_task_service(session).create_task(
            query="Planned task without claims",
            constraints={},
        )
        task_id = str(task.id)

    backend = InMemoryChunkIndexBackend(hits=[])
    client_generator = _build_client(session_factory, backend)
    client = next(client_generator)
    try:
        response = client.get(f"/api/v1/research/tasks/{task_id}/claims")

        assert response.status_code == 200
        assert response.json() == {"task_id": task_id, "claims": []}
    finally:
        client_generator.close()


def test_claim_drafting_endpoint_rejects_paused_task(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seeded = _seed_source_chunk(session)
        create_research_task_service(session).pause_task(UUID(seeded.task_id))

    backend = InMemoryChunkIndexBackend(
        hits=[
            IndexedChunkRecord(
                task_id=UUID(seeded.task_id),
                source_document_id=seeded.source_document_id,
                source_chunk_id=seeded.source_chunk_id,
                canonical_url=seeded.canonical_url,
                domain=seeded.domain,
                chunk_no=0,
                text=seeded.text,
                metadata=seeded.metadata,
                score=1.0,
            )
        ]
    )
    client_generator = _build_client(session_factory, backend)
    client = next(client_generator)
    try:
        response = client.post(
            f"/api/v1/research/tasks/{seeded.task_id}/claims/draft",
            json={"query": "illustrative examples"},
        )
        assert response.status_code == 409
        assert "cannot draft claims" in response.json()["detail"]
    finally:
        client_generator.close()


def test_claim_drafting_endpoint_returns_502_on_backend_failure(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seeded = _seed_source_chunk(session)

    client_generator = _build_client(session_factory, FailingChunkIndexBackend())
    client = next(client_generator)
    try:
        response = client.post(
            f"/api/v1/research/tasks/{seeded.task_id}/claims/draft",
            json={"query": "illustrative examples"},
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
    app.dependency_overrides[get_claim_chunk_index_backend] = lambda: backend

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _seed_source_chunk(session: Session) -> SeededChunk:
    task = create_research_task_service(session).create_task(
        query="illustrative examples",
        constraints={},
    )
    source_document = SourceDocumentRepository(session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/api-source",
            domain="example.com",
            title="Example source",
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
    source_chunk = SourceChunkRepository(session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=TEXT,
            token_count=14,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    session.commit()
    return SeededChunk(
        task_id=str(task.id),
        source_chunk_id=source_chunk.id,
        source_document_id=source_document.id,
        canonical_url=source_document.canonical_url,
        domain=source_document.domain,
        text=TEXT,
        metadata={"strategy": "paragraph_window_v1"},
    )


TEXT = (
    "Example Domain\n\n"
    "This domain is for use in illustrative examples in documents and test content."
)
