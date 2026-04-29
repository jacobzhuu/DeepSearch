from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from packages.db.models import SourceChunk, SourceDocument
from packages.db.repositories import SourceChunkRepository, SourceDocumentRepository
from services.orchestrator.app.services.debug_pipeline import _build_answer_yield_metrics
from services.orchestrator.app.services.research_tasks import create_research_task_service


def test_answer_yield_metrics_separate_answer_relevant_from_sentence_count(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is SearXNG and how does it work?",
        constraints={},
    )
    official_document = _add_source_document(
        db_session,
        task_id=task.id,
        canonical_url="https://docs.searxng.org/user/about.html",
        domain="docs.searxng.org",
        title="SearXNG about",
        score=0.95,
    )
    meta_document = _add_source_document(
        db_session,
        task_id=task.id,
        canonical_url="https://docs.searxng.org/dev/index.html",
        domain="docs.searxng.org",
        title="Developer documentation",
        score=0.95,
    )
    SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=official_document.id,
            chunk_no=0,
            text=(
                "SearXNG aggregates results from multiple search services.\n\n"
                "For more information, visit the documentation."
            ),
            token_count=18,
            metadata_json={
                "content_quality_score": 0.9,
                "eligible_for_claims": True,
                "extracted_text_length": 116,
            },
        )
    )
    SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=meta_document.id,
            chunk_no=0,
            text=(
                "For more information, visit the documentation. "
                "Read the documentation page to continue. "
                "Join Matrix and send contributions to the source code."
            ),
            token_count=24,
            metadata_json={
                "content_quality_score": 0.9,
                "eligible_for_claims": True,
                "extracted_text_length": 139,
            },
        )
    )
    db_session.commit()

    rows = _build_answer_yield_metrics(db_session, task.id, query=task.query)
    rows_by_url = {row["canonical_url"]: row for row in rows}

    official_row = rows_by_url["https://docs.searxng.org/user/about.html"]
    assert official_row["candidate_sentence_count"] == 2
    assert official_row["answer_relevant_candidate_count"] == 1
    assert official_row["answer_category_coverage"] == ["mechanism"]
    assert official_row["low_yield_reason"] is None

    meta_row = rows_by_url["https://docs.searxng.org/dev/index.html"]
    assert meta_row["candidate_sentence_count"] == 3
    assert meta_row["answer_relevant_candidate_count"] == 0
    assert meta_row["answer_category_coverage"] == []
    assert meta_row["low_yield_reason"] == "no_answer_relevant_candidates"


def _add_source_document(
    db_session: Session,
    *,
    task_id,
    canonical_url: str,
    domain: str,
    title: str,
    score: float,
) -> SourceDocument:
    return SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task_id,
            content_snapshot_id=None,
            canonical_url=canonical_url,
            domain=domain,
            title=title,
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=score,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=score,
        )
    )
