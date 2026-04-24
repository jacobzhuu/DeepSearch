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


@dataclass(frozen=True)
class SeededVerificationTask:
    task_id: str
    support_chunk_id: UUID
    contradict_chunk_id: UUID
    source_document_id: UUID
    canonical_url: str
    domain: str


def test_claim_verification_endpoints_verify_and_filter_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seeded = _seed_verification_task(session)

    backend = InMemoryChunkIndexBackend(
        hits=[
            _indexed_hit(
                task_id=UUID(seeded.task_id),
                source_document_id=seeded.source_document_id,
                source_chunk_id=seeded.support_chunk_id,
                canonical_url=seeded.canonical_url,
                domain=seeded.domain,
                text=SUPPORT_TEXT,
                score=1.3,
            ),
            _indexed_hit(
                task_id=UUID(seeded.task_id),
                source_document_id=seeded.source_document_id,
                source_chunk_id=seeded.contradict_chunk_id,
                canonical_url=seeded.canonical_url,
                domain=seeded.domain,
                text=CONTRADICT_TEXT,
                score=1.1,
            ),
        ]
    )
    client_generator = _build_client(session_factory, backend)
    client = next(client_generator)
    try:
        draft_response = client.post(
            f"/api/v1/research/tasks/{seeded.task_id}/claims/draft",
            json={"source_chunk_ids": [str(seeded.support_chunk_id)], "limit": 1},
        )
        verify_response = client.post(
            f"/api/v1/research/tasks/{seeded.task_id}/claims/verify",
            json={"limit": 5},
        )
        claims_response = client.get(
            f"/api/v1/research/tasks/{seeded.task_id}/claims",
            params={"verification_status": "mixed"},
        )
        contradict_evidence_response = client.get(
            f"/api/v1/research/tasks/{seeded.task_id}/claim-evidence",
            params={"relation_type": "contradict"},
        )

        assert draft_response.status_code == 200
        assert verify_response.status_code == 200
        assert verify_response.json()["verified_claims"] == 1
        assert verify_response.json()["claims"][0]["verification_status"] == "mixed"
        assert verify_response.json()["claims"][0]["support_evidence_count"] == 1
        assert verify_response.json()["claims"][0]["contradict_evidence_count"] == 1

        assert claims_response.status_code == 200
        assert claims_response.json()["claims"][0]["verification_status"] == "mixed"

        assert contradict_evidence_response.status_code == 200
        evidence = contradict_evidence_response.json()["claim_evidence"][0]
        assert evidence["relation_type"] == "contradict"
        assert evidence["excerpt"] == CONTRADICT_SENTENCE
    finally:
        client_generator.close()


def test_claim_verification_endpoint_rejects_paused_task(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seeded = _seed_verification_task(session)
        create_research_task_service(session).pause_task(UUID(seeded.task_id))

    backend = InMemoryChunkIndexBackend(hits=[])
    client_generator = _build_client(session_factory, backend)
    client = next(client_generator)
    try:
        response = client.post(
            f"/api/v1/research/tasks/{seeded.task_id}/claims/verify",
            json={"limit": 1},
        )
        assert response.status_code == 409
        assert "cannot verify claims" in response.json()["detail"]
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


def _seed_verification_task(session: Session) -> SeededVerificationTask:
    task = create_research_task_service(session).create_task(
        query="illustrative examples",
        constraints={},
    )
    source_document = SourceDocumentRepository(session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/verification-api-source",
            domain="example.com",
            title="Verification source",
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
    support_chunk = SourceChunkRepository(session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=SUPPORT_TEXT,
            token_count=14,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    contradict_chunk = SourceChunkRepository(session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text=CONTRADICT_TEXT,
            token_count=14,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    session.commit()
    return SeededVerificationTask(
        task_id=str(task.id),
        support_chunk_id=support_chunk.id,
        contradict_chunk_id=contradict_chunk.id,
        source_document_id=source_document.id,
        canonical_url=source_document.canonical_url,
        domain=source_document.domain,
    )


def _indexed_hit(
    *,
    task_id: UUID,
    source_document_id: UUID,
    source_chunk_id: UUID,
    canonical_url: str,
    domain: str,
    text: str,
    score: float,
) -> IndexedChunkRecord:
    return IndexedChunkRecord(
        task_id=task_id,
        source_document_id=source_document_id,
        source_chunk_id=source_chunk_id,
        canonical_url=canonical_url,
        domain=domain,
        chunk_no=0,
        text=text,
        metadata={"strategy": "paragraph_window_v1"},
        score=score,
    )


SUPPORT_TEXT = (
    "Example Domain.\n\n"
    "This domain is for use in illustrative examples in documents and test content."
)
CONTRADICT_SENTENCE = "This domain is not for use in illustrative examples in documents."
CONTRADICT_TEXT = f"Counterpoint.\n\n{CONTRADICT_SENTENCE}"
