from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from packages.db.models import SourceChunk, SourceDocument
from packages.db.repositories import (
    CitationSpanRepository,
    ClaimEvidenceRepository,
    ClaimRepository,
    SourceChunkRepository,
    SourceDocumentRepository,
)
from services.orchestrator.app.claims import (
    CLAIM_EVIDENCE_RELATION_SUPPORT,
    CLAIM_TYPE_FACT,
    CLAIM_VERIFICATION_STATUS_DRAFT,
)
from services.orchestrator.app.indexing import (
    ChunkIndexDocument,
    IndexedChunkPage,
    IndexedChunkRecord,
)
from services.orchestrator.app.services.claims import (
    ClaimDraftingConflictError,
    create_claim_drafting_service,
)
from services.orchestrator.app.services.research_tasks import create_research_task_service


class InMemoryChunkIndexBackend:
    def __init__(self, *, hits: list[IndexedChunkRecord]) -> None:
        self.hits = hits

    def validate_configuration(self) -> None:
        return None

    def ensure_index(self) -> None:
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
class SeededChunk:
    task_id: UUID
    source_chunk_id: UUID
    source_document_id: UUID
    canonical_url: str
    domain: str


def test_claim_drafting_service_creates_claim_citation_and_evidence(db_session: Session) -> None:
    seeded = _seed_source_chunk(db_session)
    backend = InMemoryChunkIndexBackend(
        hits=[
            IndexedChunkRecord(
                task_id=seeded.task_id,
                source_document_id=seeded.source_document_id,
                source_chunk_id=seeded.source_chunk_id,
                canonical_url=seeded.canonical_url,
                domain=seeded.domain,
                chunk_no=0,
                text=(
                    "Example Domain\n\n"
                    "This domain is for use in illustrative examples in documents and test content."
                ),
                metadata={"strategy": "paragraph_window_v1"},
                score=1.2,
            )
        ]
    )
    service = create_claim_drafting_service(
        db_session,
        index_backend=backend,
        max_candidates_per_request=5,
    )

    first_result = service.draft_claims(
        seeded.task_id,
        query="illustrative examples",
        source_chunk_ids=None,
        limit=5,
    )
    second_result = service.draft_claims(
        seeded.task_id,
        query="illustrative examples",
        source_chunk_ids=None,
        limit=5,
    )

    source_chunk = SourceChunkRepository(db_session).get(seeded.source_chunk_id)
    assert source_chunk is not None
    claims = ClaimRepository(db_session).list_for_task(seeded.task_id)
    citation_spans = CitationSpanRepository(db_session).list_for_chunk(seeded.source_chunk_id)
    claim_evidence = ClaimEvidenceRepository(db_session).list_for_task(seeded.task_id)

    assert first_result.created_claims == 1
    assert first_result.created_citation_spans == 1
    assert first_result.created_claim_evidence == 1
    assert first_result.entries[0].claim.claim_type == CLAIM_TYPE_FACT
    assert first_result.entries[0].claim.verification_status == CLAIM_VERIFICATION_STATUS_DRAFT
    assert first_result.entries[0].claim_evidence.relation_type == CLAIM_EVIDENCE_RELATION_SUPPORT
    assert (
        source_chunk.text[
            first_result.entries[0]
            .citation_span.start_offset : first_result.entries[0]
            .citation_span.end_offset
        ]
        == first_result.entries[0].citation_span.excerpt
    )
    assert len(claims) == 1
    assert len(citation_spans) == 1
    assert len(claim_evidence) == 1

    assert second_result.created_claims == 0
    assert second_result.reused_claims == 1
    assert second_result.reused_citation_spans == 1
    assert second_result.reused_claim_evidence == 1


def test_claim_drafting_service_supports_explicit_source_chunk_ids_and_rejects_paused_tasks(
    db_session: Session,
) -> None:
    seeded = _seed_source_chunk(db_session)
    service = create_claim_drafting_service(
        db_session,
        index_backend=InMemoryChunkIndexBackend(hits=[]),
        max_candidates_per_request=5,
    )

    result = service.draft_claims(
        seeded.task_id,
        query=None,
        source_chunk_ids=[seeded.source_chunk_id],
        limit=1,
    )
    assert result.created_claims == 1

    create_research_task_service(db_session).pause_task(seeded.task_id)
    with pytest.raises(ClaimDraftingConflictError):
        service.draft_claims(
            seeded.task_id,
            query="illustrative examples",
            source_chunk_ids=None,
            limit=1,
        )


def test_claim_drafting_service_filters_title_short_and_duplicate_claims(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is OpenAI?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/openai",
            domain="example.com",
            title="OpenAI source",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=None,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=None,
        )
    )
    source_chunk_repo = SourceChunkRepository(db_session)
    title_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text="What Is OpenAI?\n\nData\n\nC",
            token_count=4,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    first_fact_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text="OpenAI is an artificial intelligence research and deployment company.",
            token_count=10,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    duplicate_fact_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=2,
            text="OpenAI is an Artificial Intelligence Research and Deployment Company.",
            token_count=10,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    db_session.commit()

    service = create_claim_drafting_service(
        db_session,
        index_backend=InMemoryChunkIndexBackend(hits=[]),
        max_candidates_per_request=5,
    )

    result = service.draft_claims(
        task.id,
        query=task.query,
        source_chunk_ids=[title_chunk.id, first_fact_chunk.id, duplicate_fact_chunk.id],
        limit=5,
    )

    claims = ClaimRepository(db_session).list_for_task(task.id)
    claim_evidence = ClaimEvidenceRepository(db_session).list_for_task(task.id)

    assert result.created_claims == 1
    assert result.reused_claims == 1
    assert len(result.entries) == 2
    assert len(claims) == 1
    assert len(claim_evidence) == 2
    assert claims[0].statement == (
        "OpenAI is an artificial intelligence research and deployment company."
    )


def _seed_source_chunk(db_session: Session) -> SeededChunk:
    task = create_research_task_service(db_session).create_task(
        query="illustrative examples",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/source",
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
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=(
                "Example Domain\n\n"
                "This domain is for use in illustrative examples in documents and test content."
            ),
            token_count=14,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    db_session.commit()
    return SeededChunk(
        task_id=task.id,
        source_chunk_id=source_chunk.id,
        source_document_id=source_document.id,
        canonical_url=source_document.canonical_url,
        domain=source_document.domain,
    )
