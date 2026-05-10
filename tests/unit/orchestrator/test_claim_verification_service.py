from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from packages.db.models import Claim, SourceChunk, SourceDocument
from packages.db.repositories import (
    ClaimEvidenceRepository,
    ClaimRepository,
    SourceChunkRepository,
    SourceDocumentRepository,
)
from services.orchestrator.app.claims import (
    CLAIM_EVIDENCE_RELATION_CANDIDATE_SUPPORT,
    CLAIM_EVIDENCE_RELATION_CONTRADICT,
    CLAIM_EVIDENCE_RELATION_SUPPORT,
    CLAIM_VERIFICATION_STATUS_CONTRADICTED,
    CLAIM_VERIFICATION_STATUS_DRAFT,
    CLAIM_VERIFICATION_STATUS_MIXED,
    CLAIM_VERIFICATION_STATUS_SUPPORTED,
)
from services.orchestrator.app.indexing import (
    ChunkIndexDocument,
    IndexedChunkPage,
    IndexedChunkRecord,
)
from services.orchestrator.app.services.claims import (
    ClaimVerificationConflictError,
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
class SeededChunks:
    task_id: UUID
    support_chunk_id: UUID
    contradict_chunk_id: UUID
    source_document_id: UUID
    canonical_url: str
    domain: str


def test_claim_verification_service_marks_claim_mixed_when_support_and_contradict_evidence_exist(
    db_session: Session,
) -> None:
    seeded = _seed_support_and_contradict_chunks(db_session)
    backend = InMemoryChunkIndexBackend(
        hits=[
            _indexed_hit(
                task_id=seeded.task_id,
                source_document_id=seeded.source_document_id,
                source_chunk_id=seeded.support_chunk_id,
                canonical_url=seeded.canonical_url,
                domain=seeded.domain,
                text=SUPPORT_TEXT,
                score=1.4,
            ),
            _indexed_hit(
                task_id=seeded.task_id,
                source_document_id=seeded.source_document_id,
                source_chunk_id=seeded.contradict_chunk_id,
                canonical_url=seeded.canonical_url,
                domain=seeded.domain,
                text=CONTRADICT_TEXT,
                score=1.1,
            ),
        ]
    )
    service = create_claim_drafting_service(
        db_session,
        index_backend=backend,
        max_candidates_per_request=5,
        verification_max_claims_per_request=5,
        retrieval_max_results_per_request=5,
    )

    draft_result = service.draft_claims(
        seeded.task_id,
        query=None,
        source_chunk_ids=[seeded.support_chunk_id],
        limit=1,
    )
    verify_result = service.verify_claims(
        seeded.task_id,
        claim_ids=None,
        limit=5,
    )

    claim = ClaimRepository(db_session).get(draft_result.entries[0].claim.id)
    assert claim is not None
    evidence = ClaimEvidenceRepository(db_session).list_for_claim(claim.id)
    relation_types = {item.relation_type for item in evidence}

    assert verify_result.verified_claims == 1
    assert verify_result.created_citation_spans == 1
    assert verify_result.reused_citation_spans == 1
    assert verify_result.created_claim_evidence == 2
    assert verify_result.reused_claim_evidence == 0
    assert verify_result.entries[0].support_evidence_count == 1
    assert verify_result.entries[0].weak_support_evidence_count == 0
    assert verify_result.entries[0].contradict_evidence_count == 1
    assert claim.verification_status == CLAIM_VERIFICATION_STATUS_MIXED
    assert relation_types == {
        CLAIM_EVIDENCE_RELATION_SUPPORT,
        CLAIM_EVIDENCE_RELATION_CANDIDATE_SUPPORT,
        CLAIM_EVIDENCE_RELATION_CONTRADICT,
    }
    assert claim.notes_json["verification"]["rationale"] == (
        "Found 1 support evidence and 1 contradict evidence."
    )
    assert (
        claim.notes_json["verification"]["verifier_method"]
        == "lexical_overlap_contradiction_scan_v2"
    )
    assert claim.notes_json["verification"]["strong_support_evidence_count"] == 1
    assert claim.notes_json["verification"]["weak_support_evidence_count"] == 0
    assert claim.notes_json["verification"]["evidence_relations"][0]["relation_detail"] in {
        "strong_support",
        "contradiction",
    }


def test_claim_verification_service_marks_claim_unsupported_when_only_contradict_evidence_exists(
    db_session: Session,
) -> None:
    seeded = _seed_support_and_contradict_chunks(db_session)
    backend = InMemoryChunkIndexBackend(
        hits=[
            _indexed_hit(
                task_id=seeded.task_id,
                source_document_id=seeded.source_document_id,
                source_chunk_id=seeded.contradict_chunk_id,
                canonical_url=seeded.canonical_url,
                domain=seeded.domain,
                text=CONTRADICT_TEXT,
                score=1.2,
            )
        ]
    )
    service = create_claim_drafting_service(
        db_session,
        index_backend=backend,
        max_candidates_per_request=5,
        verification_max_claims_per_request=5,
        retrieval_max_results_per_request=5,
    )

    claim = ClaimRepository(db_session).add(
        Claim(
            task_id=seeded.task_id,
            statement="This domain is for use in illustrative examples in documents.",
            claim_type="fact",
            confidence=0.6,
            verification_status=CLAIM_VERIFICATION_STATUS_DRAFT,
            notes_json={},
        )
    )
    db_session.commit()

    verify_result = service.verify_claims(
        seeded.task_id,
        claim_ids=[claim.id],
        limit=1,
    )

    refreshed_claim = ClaimRepository(db_session).get(claim.id)
    assert refreshed_claim is not None
    assert verify_result.verified_claims == 1
    assert verify_result.entries[0].support_evidence_count == 0
    assert verify_result.entries[0].contradict_evidence_count == 1
    assert refreshed_claim.verification_status == CLAIM_VERIFICATION_STATUS_CONTRADICTED
    assert refreshed_claim.notes_json["verification"]["rationale"] == (
        "No support evidence found; found 1 contradict evidence."
    )


def test_claim_verification_service_downranks_reused_chunk_for_later_claim(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="SearXNG privacy and engine aggregation",
        constraints={},
    )
    first_source = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/source-a",
            domain="example.com",
            title="Source A",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
            authority_score=0.8,
            freshness_score=None,
            originality_score=0.8,
            consistency_score=0.8,
            safety_score=0.9,
            final_source_score=0.8,
        )
    )
    second_source = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.net/source-b",
            domain="example.net",
            title="Source B",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
            authority_score=0.8,
            freshness_score=None,
            originality_score=0.8,
            consistency_score=0.8,
            safety_score=0.9,
            final_source_score=0.8,
        )
    )
    first_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=first_source.id,
            chunk_no=0,
            text=DIVERSITY_TEXT,
            token_count=24,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.8},
        )
    )
    second_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=second_source.id,
            chunk_no=0,
            text=DIVERSITY_TEXT,
            token_count=24,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.8},
        )
    )
    first_claim = ClaimRepository(db_session).add(
        Claim(
            task_id=task.id,
            statement=(
                "SearXNG aggregates results from multiple search engines without storing "
                "user profiles."
            ),
            claim_type="fact",
            confidence=0.7,
            verification_status=CLAIM_VERIFICATION_STATUS_DRAFT,
            notes_json={},
        )
    )
    second_claim = ClaimRepository(db_session).add(
        Claim(
            task_id=task.id,
            statement="SearXNG lets operators choose which engines to enable.",
            claim_type="fact",
            confidence=0.7,
            verification_status=CLAIM_VERIFICATION_STATUS_DRAFT,
            notes_json={},
        )
    )
    db_session.commit()
    backend = InMemoryChunkIndexBackend(
        hits=[
            _indexed_hit(
                task_id=task.id,
                source_document_id=first_source.id,
                source_chunk_id=first_chunk.id,
                canonical_url=first_source.canonical_url,
                domain=first_source.domain,
                text=DIVERSITY_TEXT,
                score=1.5,
            ),
            _indexed_hit(
                task_id=task.id,
                source_document_id=second_source.id,
                source_chunk_id=second_chunk.id,
                canonical_url=second_source.canonical_url,
                domain=second_source.domain,
                text=DIVERSITY_TEXT,
                score=1.45,
            ),
        ]
    )
    service = create_claim_drafting_service(
        db_session,
        index_backend=backend,
        max_candidates_per_request=5,
        verification_max_claims_per_request=5,
        retrieval_max_results_per_request=5,
    )

    service.verify_claims(task.id, claim_ids=[first_claim.id, second_claim.id], limit=2)

    refreshed_first = ClaimRepository(db_session).get(first_claim.id)
    refreshed_second = ClaimRepository(db_session).get(second_claim.id)
    assert refreshed_first is not None
    assert refreshed_second is not None
    first_relation = refreshed_first.notes_json["verification"]["evidence_relations"][0]
    second_relation = refreshed_second.notes_json["verification"]["evidence_relations"][0]

    assert first_relation["source_chunk_id"] == str(first_chunk.id)
    assert first_relation["reuse_penalty"] == 0.0
    assert second_relation["source_chunk_id"] == str(second_chunk.id)
    assert second_relation["reuse_penalty"] == 0.0
    assert (
        refreshed_second.notes_json["verification"]["evidence_diversity"]["unique_chunk_count"] == 1
    )


def test_claim_verification_service_supports_deployment_slot_evidence(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="How to deploy SearXNG with Docker?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://docs.searxng.org/admin/installation-docker",
            domain="docs.searxng.org",
            title="Installation container",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 5, 6, 10, 0, tzinfo=UTC),
            authority_score=0.95,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.95,
        )
    )
    source_text = (
        "The host requires Docker or Podman, and operators can run sudo usermod -aG docker "
        "$USER when they need Docker group access.\n\n"
        "Configure settings.yml, .env, .env.example, SEARXNG_SECRET, and SEARXNG_BASE_URL "
        "for the container deployment.\n\n"
        "Use a reverse proxy with certificates and limiter bot protection before exposing a "
        "public SearXNG instance.\n\n"
        "Update SearXNG by running docker compose pull and reviewing new templates.\n\n"
        "Use docker compose logs and docker compose exec searxng sh for troubleshooting."
    )
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=source_text,
            token_count=92,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.95,
                "eligible_for_claims": True,
                "quality_reasons": ["deployment_code_or_config"],
            },
        )
    )
    db_session.commit()
    backend = InMemoryChunkIndexBackend(
        hits=[
            _indexed_hit(
                task_id=task.id,
                source_document_id=source_document.id,
                source_chunk_id=source_chunk.id,
                canonical_url=source_document.canonical_url,
                domain=source_document.domain,
                text=source_text,
                score=1.5,
            )
        ]
    )
    service = create_claim_drafting_service(
        db_session,
        index_backend=backend,
        max_candidates_per_request=5,
        verification_max_claims_per_request=5,
        retrieval_max_results_per_request=5,
    )

    draft_result = service.draft_claims(
        task.id,
        query=task.query,
        source_chunk_ids=[source_chunk.id],
        limit=5,
    )
    verify_result = service.verify_claims(task.id, claim_ids=None, limit=5)

    claims = ClaimRepository(db_session).list_for_task(task.id)
    supported_claims = [
        claim
        for claim in claims
        if claim.verification_status == CLAIM_VERIFICATION_STATUS_SUPPORTED
    ]
    supported_text = " ".join(claim.statement for claim in supported_claims)
    supported_slots = {
        slot_id for claim in supported_claims for slot_id in claim.notes_json.get("slot_ids", [])
    }
    support_evidence = [
        item
        for item in ClaimEvidenceRepository(db_session).list_for_task(task.id)
        if item.relation_type == CLAIM_EVIDENCE_RELATION_SUPPORT
    ]

    assert len(draft_result.entries) == 5
    assert verify_result.verified_claims == 5
    assert len(supported_claims) == 5
    assert {
        "deployment_prerequisites",
        "deployment_configuration",
        "deployment_security",
        "deployment_update_maintenance",
        "deployment_troubleshooting",
    }.issubset(supported_slots)
    assert "Docker or Podman" in supported_text
    assert "sudo usermod -aG docker" in supported_text
    assert "settings.yml" in supported_text
    assert "SEARXNG_SECRET" in supported_text
    assert "reverse proxy" in supported_text
    assert "docker compose pull" in supported_text
    assert len(support_evidence) == 5
    assert all(item.citation_span.excerpt in source_text for item in support_evidence)


def test_claim_verification_service_rejects_paused_tasks(db_session: Session) -> None:
    seeded = _seed_support_and_contradict_chunks(db_session)
    service = create_claim_drafting_service(
        db_session,
        index_backend=InMemoryChunkIndexBackend(hits=[]),
        max_candidates_per_request=5,
        verification_max_claims_per_request=5,
        retrieval_max_results_per_request=5,
    )

    service.draft_claims(
        seeded.task_id,
        query=None,
        source_chunk_ids=[seeded.support_chunk_id],
        limit=1,
    )
    create_research_task_service(db_session).pause_task(seeded.task_id)

    with pytest.raises(ClaimVerificationConflictError):
        service.verify_claims(seeded.task_id, claim_ids=None, limit=1)


def test_claim_verification_promotes_exact_candidate_support_without_retrieval_hit(
    db_session: Session,
) -> None:
    seeded = _seed_support_and_contradict_chunks(db_session)
    service = create_claim_drafting_service(
        db_session,
        index_backend=InMemoryChunkIndexBackend(hits=[]),
        max_candidates_per_request=5,
        verification_max_claims_per_request=5,
        retrieval_max_results_per_request=5,
    )

    draft_result = service.draft_claims(
        seeded.task_id,
        query=None,
        source_chunk_ids=[seeded.support_chunk_id],
        limit=1,
    )
    verify_result = service.verify_claims(seeded.task_id, claim_ids=None, limit=1)

    claim = ClaimRepository(db_session).get(draft_result.entries[0].claim.id)
    assert claim is not None
    evidence = ClaimEvidenceRepository(db_session).list_for_claim(claim.id)

    assert verify_result.verified_claims == 1
    assert verify_result.entries[0].support_evidence_count == 1
    assert claim.verification_status == CLAIM_VERIFICATION_STATUS_SUPPORTED
    assert {item.relation_type for item in evidence} == {
        CLAIM_EVIDENCE_RELATION_CANDIDATE_SUPPORT,
        CLAIM_EVIDENCE_RELATION_SUPPORT,
    }
    assert claim.notes_json["verification"]["strong_support_evidence_count"] == 1
    assert claim.notes_json["verification"]["evidence_relations"][0]["relation_detail"] == (
        "strong_support"
    )


def _seed_support_and_contradict_chunks(db_session: Session) -> SeededChunks:
    task = create_research_task_service(db_session).create_task(
        query="illustrative examples",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/verification-source",
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
    support_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=SUPPORT_TEXT,
            token_count=14,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    contradict_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text=CONTRADICT_TEXT,
            token_count=14,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    db_session.commit()
    return SeededChunks(
        task_id=task.id,
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

CONTRADICT_TEXT = (
    "Counterpoint.\n\nThis domain is not for use in illustrative examples in documents."
)

DIVERSITY_TEXT = (
    "SearXNG aggregates results from multiple search engines without storing user profiles. "
    "SearXNG lets operators choose which engines to enable."
)
