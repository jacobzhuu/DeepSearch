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
    notes = first_result.entries[0].claim.notes_json
    assert notes["evidence_candidate_id"].startswith("ec_")
    assert notes["source_document_id"] == str(seeded.source_document_id)
    assert notes["source_chunk_id"] == str(seeded.source_chunk_id)
    assert notes["citation_span_id"] == str(first_result.entries[0].citation_span.id)
    assert notes["claim_evidence_id"] == str(first_result.entries[0].claim_evidence.id)
    assert notes["evidence_candidate"]["citation_span_id"] == str(
        first_result.entries[0].citation_span.id
    )
    assert "slot_ids" in notes
    assert first_result.entries[0].evidence_candidate_id == notes["evidence_candidate_id"]

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


def test_claim_drafting_service_excludes_ineligible_quality_chunks(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is SearXNG and how does it work?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://searxng.org/",
            domain="searxng.org",
            title="SearXNG redirect",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=0.8,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.8,
        )
    )
    source_chunk_repo = SourceChunkRepository(db_session)
    redirect_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text="Redirecting to https://docs.searxng.org/",
            token_count=8,
            metadata_json={
                "eligible_for_claims": False,
                "should_generate_claims": False,
                "reason": "redirect_stub",
            },
        )
    )
    nav_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text=(
                "Jump to content Main menu move to sidebar Privacy policy About Wikipedia "
                "Edit links."
            ),
            token_count=14,
            metadata_json={"eligible_for_claims": False, "is_navigation_noise": True},
        )
    )
    reference_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=2,
            text="Implementación De Un Prototipo (Bachelor Thesis).",
            token_count=10,
            metadata_json={"eligible_for_claims": False, "is_reference_section": True},
        )
    )
    valid_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=3,
            text=(
                "SearXNG is a free internet metasearch engine that sends queries to "
                "multiple search services and aggregates the results."
            ),
            token_count=22,
            metadata_json={"eligible_for_claims": True, "content_quality_score": 0.9},
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
        source_chunk_ids=[redirect_chunk.id, nav_chunk.id, reference_chunk.id, valid_chunk.id],
        limit=5,
    )

    claims = ClaimRepository(db_session).list_for_task(task.id)
    assert result.created_claims == 1
    assert len(claims) == 1
    assert claims[0].statement.startswith("SearXNG is a free internet metasearch engine")


def test_claim_drafting_service_ranks_query_answer_candidates_over_cta_text(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is SearXNG and how does it work?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://docs.searxng.org/user/about.html",
            domain="docs.searxng.org",
            title="SearXNG about",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=0.95,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.95,
        )
    )
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=(
                "2 Reference architecture of a public SearXNG setup.\n\n"
                "digraph G { rp -> uwsgi; node [style=filled]; secret_key: change-me }\n\n"
                "SearXNG sources and run it yourself!\n\n"
                "Track development, send contributions, and report issues at SearXNG sources.\n\n"
                "SearXNG is a metasearch engine, aggregating the results of other search "
                "engines while not storing information about its users.\n\n"
                "It provides basic privacy by mixing your queries with searches on other "
                "platforms without storing search data.\n\n"
                "Come join us on Matrix if you have questions.\n\n"
                "SearXNG supports OpenSearch."
            ),
            token_count=70,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.95},
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
        source_chunk_ids=[source_chunk.id],
        limit=5,
    )

    statements = [entry.claim.statement for entry in result.entries]
    claims = ClaimRepository(db_session).list_for_task(task.id)

    assert result.created_claims == 3
    assert statements == [
        (
            "SearXNG is a metasearch engine, aggregating the results of other search "
            "engines while not storing information about its users."
        ),
        (
            "It provides basic privacy by mixing your queries with searches on other "
            "platforms without storing search data."
        ),
        "SearXNG supports OpenSearch.",
    ]
    assert "Track development" not in " ".join(statements)
    assert "Come join" not in " ".join(statements)
    assert "Reference architecture" not in " ".join(statements)
    assert "digraph G" not in " ".join(statements)
    assert result.diagnostics["rejection_reason_distribution"]["figure_caption_or_diagram"] >= 1
    assert result.diagnostics["rejection_reason_distribution"]["diagram_or_config_fragment"] >= 1
    assert {claim.notes_json["claim_category"] for claim in claims} == {
        "definition",
        "privacy",
        "feature",
    }
    for claim in claims:
        assert claim.notes_json["claim_quality_score"] >= 0.45
        assert claim.notes_json["query_answer_score"] >= 0.35


def test_claim_drafting_service_diagnostics_explain_no_claims(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is SearXNG and how does it work?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://docs.searxng.org/",
            domain="docs.searxng.org",
            title="SearXNG docs",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=0.95,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.95,
        )
    )
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text="Welcome to SearXNG\n\nSearch without being tracked.",
            token_count=13,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.2,
                "query_relevance_score": 1.0,
                "eligible_for_claims": False,
                "should_generate_claims": False,
                "quality_reasons": ["very_short"],
            },
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
        source_chunk_ids=[source_chunk.id],
        limit=5,
    )

    diagnostics = result.diagnostics
    assert result.entries == []
    assert diagnostics["total_chunks_seen"] == 1
    assert diagnostics["eligible_chunks_seen"] == 0
    assert diagnostics["candidate_sentences_count"] >= 2
    assert diagnostics["rejected_candidates_count"] >= 2
    assert diagnostics["rejection_reason_distribution"]["chunk_ineligible"] >= 2
    rejected_text = " ".join(
        item["candidate_text"] for item in diagnostics["top_rejected_candidates"]
    )
    assert "Welcome to SearXNG" in rejected_text
    assert "Search without being tracked." in rejected_text
    assert diagnostics["chunks"][0]["text_preview"] == (
        "Welcome to SearXNG Search without being tracked."
    )


def test_claim_drafting_fallback_accepts_explanatory_definition_from_soft_ineligible_chunk(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is SearXNG and how does it work?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://en.wikipedia.org/wiki/SearXNG",
            domain="en.wikipedia.org",
            title="SearXNG",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=0.78,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.78,
        )
    )
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=(
                "SearXNG is a free and open-source metasearch engine that aggregates "
                "results from other search engines."
            ),
            token_count=16,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.25,
                "query_relevance_score": 1.0,
                "eligible_for_claims": False,
                "should_generate_claims": False,
                "quality_reasons": ["low_source_coverage"],
            },
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
        source_chunk_ids=[source_chunk.id],
        limit=5,
    )

    assert result.created_claims == 1
    assert result.entries[0].claim.statement.startswith("SearXNG is a free")
    assert result.entries[0].claim.notes_json["draft_mode"] == "fallback_relaxed"
    assert (
        result.entries[0].claim.notes_json["fallback_reason"] == "strict_filters_produced_no_claims"
    )
    assert result.entries[0].claim.notes_json["original_rejected_reason"] == "chunk_ineligible"


def test_claim_drafting_fallback_rejects_short_slogan_and_community_text(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is SearXNG and how does it work?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://docs.searxng.org/",
            domain="docs.searxng.org",
            title="SearXNG docs",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=0.95,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.95,
        )
    )
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=(
                "Search without being tracked.\n\n"
                "SearXNG sources and run it yourself!\n\n"
                "Track development, send contributions, and report issues at SearXNG sources."
            ),
            token_count=24,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.25,
                "query_relevance_score": 1.0,
                "eligible_for_claims": False,
                "should_generate_claims": False,
            },
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
        source_chunk_ids=[source_chunk.id],
        limit=5,
    )

    assert result.entries == []
    rejected_text = " ".join(
        item["candidate_text"] for item in result.diagnostics["top_rejected_candidates"]
    )
    assert "Search without being tracked." in rejected_text
    assert "Track development" in rejected_text
    assert result.diagnostics["fallback_attempted"] is True
    assert result.diagnostics["fallback_candidates_count"] == 0


def test_claim_drafting_service_uses_docs_and_wikipedia_chunks_for_answer_claims(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is SearXNG and how does it work?",
        constraints={},
    )
    docs_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://docs.searxng.org/",
            domain="docs.searxng.org",
            title="SearXNG docs",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=0.95,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.95,
        )
    )
    wiki_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://en.wikipedia.org/wiki/SearXNG",
            domain="en.wikipedia.org",
            title="SearXNG",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 1, tzinfo=UTC),
            authority_score=0.78,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.78,
        )
    )
    source_chunk_repo = SourceChunkRepository(db_session)
    docs_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=docs_document.id,
            chunk_no=0,
            text=(
                "Welcome to SearXNG\n\n"
                "Search without being tracked.\n\n"
                "Get started with SearXNG by using one of the instances listed at .\n\n"
                "SearXNG does not generate a profile about users.\n\n"
                "SearXNG aggregates results from multiple search services."
            ),
            token_count=28,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.9,
                "query_relevance_score": 1.0,
                "eligible_for_claims": True,
                "should_generate_claims": True,
            },
        )
    )
    wiki_definition_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=wiki_document.id,
            chunk_no=0,
            text=(
                "SearXNG is a free and open-source metasearch engine that aggregates "
                "results from other search engines.\n\n"
                "SearXNG supports over 70 different search engines."
            ),
            token_count=28,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.9,
                "query_relevance_score": 1.0,
                "eligible_for_claims": True,
                "should_generate_claims": True,
            },
        )
    )
    wiki_mechanism_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=wiki_document.id,
            chunk_no=1,
            text=(
                "As a metasearch engine, SearXNG functions by sending queries to upstream "
                "search engines and returning them to the user."
            ),
            token_count=24,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.9,
                "query_relevance_score": 1.0,
                "eligible_for_claims": True,
                "should_generate_claims": True,
            },
        )
    )
    wiki_duplicate_feature_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=wiki_document.id,
            chunk_no=3,
            text="More than 70 different search engines are supported by SearXNG.",
            token_count=10,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.9,
                "query_relevance_score": 1.0,
                "eligible_for_claims": True,
                "should_generate_claims": True,
            },
        )
    )
    wiki_privacy_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=wiki_document.id,
            chunk_no=4,
            text=(
                "Privacy\n\n"
                "SearXNG removes private data from requests sent to search services. "
                "SearXNG itself stores little to no information that can be used to identify "
                "users.\n\n"
                "See also\n\n"
                "Free and open-source software portal\n\n"
                "References"
            ),
            token_count=44,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.9,
                "query_relevance_score": 1.0,
                "eligible_for_claims": True,
                "should_generate_claims": True,
            },
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
        source_chunk_ids=[
            docs_chunk.id,
            wiki_definition_chunk.id,
            wiki_mechanism_chunk.id,
            wiki_duplicate_feature_chunk.id,
            wiki_privacy_chunk.id,
        ],
        limit=5,
    )

    statements = [entry.claim.statement for entry in result.entries]
    categories = {entry.claim.notes_json["claim_category"] for entry in result.entries}
    joined_statements = " ".join(statements)
    assert len(statements) >= 3
    assert {"definition", "mechanism", "privacy"}.issubset(categories)
    assert any("metasearch engine" in statement for statement in statements)
    assert any(
        "sending queries to upstream search engines" in statement
        or "aggregates results from multiple search services" in statement
        for statement in statements
    )
    assert any(
        "does not generate a profile" in statement or "removes private data" in statement
        for statement in statements
    )
    assert any(
        entry.source_chunk.source_document_id == docs_document.id
        for entry in result.entries
        if entry.claim.notes_json["claim_category"] in {"mechanism", "privacy"}
    )
    assert "Get started with SearXNG" not in joined_statements
    assert "listed at ." not in joined_statements
    assert "Search without being tracked" not in joined_statements
    assert "See also" not in joined_statements
    assert "References" not in joined_statements
    assert sum("70 different search engines" in statement for statement in statements) <= 1


def test_claim_drafting_service_collapses_near_duplicate_feature_claims(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is SearXNG and how does it work?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://en.wikipedia.org/wiki/SearXNG",
            domain="en.wikipedia.org",
            title="SearXNG",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 1, tzinfo=UTC),
            authority_score=0.78,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.78,
        )
    )
    first_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text="SearXNG supports over 70 different search engines.",
            token_count=8,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.9,
                "eligible_for_claims": True,
            },
        )
    )
    duplicate_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text="More than 70 different search engines are supported by SearXNG.",
            token_count=10,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.9,
                "eligible_for_claims": True,
            },
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
        source_chunk_ids=[first_chunk.id, duplicate_chunk.id],
        limit=5,
    )

    assert len(result.entries) == 1
    assert result.diagnostics["near_duplicate_claims_removed"] >= 1


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
