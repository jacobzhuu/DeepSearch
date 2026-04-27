from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from packages.db.models import (
    CitationSpan,
    Claim,
    ClaimEvidence,
    ResearchTask,
    SourceChunk,
    SourceDocument,
)
from packages.db.repositories import ReportArtifactRepository
from packages.db.repositories.sources import SourceChunkRepository, SourceDocumentRepository
from services.orchestrator.app.services.reporting import (
    ReportArtifactContentMismatchError,
    create_report_synthesis_service,
)
from services.orchestrator.app.services.research_tasks import create_research_task_service
from services.orchestrator.app.storage import FilesystemSnapshotObjectStore


def test_report_synthesis_service_generates_and_reuses_markdown_artifact(
    db_session: Session,
    tmp_path: Path,
) -> None:
    seeded = _seed_verified_claims(db_session)
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    first_result = service.generate_markdown_report(seeded.task_id)
    second_result = service.generate_markdown_report(seeded.task_id)
    latest_result = service.get_latest_markdown_report(seeded.task_id)

    artifacts = ReportArtifactRepository(db_session).list_for_task(
        seeded.task_id, format="markdown"
    )
    stored_bytes = object_store.get_bytes(
        bucket=first_result.artifact.storage_bucket,
        key=first_result.artifact.storage_key,
    )

    assert first_result.artifact.version == 1
    assert first_result.reused_existing is False
    assert second_result.reused_existing is True
    assert second_result.artifact.id == first_result.artifact.id
    assert latest_result.artifact.id == first_result.artifact.id
    assert len(artifacts) == 1
    assert first_result.supported_claims == 1
    assert first_result.mixed_claims == 1
    assert first_result.unsupported_claims == 1
    assert first_result.artifact.content_hash == _markdown_hash(first_result.markdown)
    assert first_result.artifact.manifest_json is not None
    assert first_result.artifact.manifest_json["manifest_version"] == 1
    assert first_result.artifact.manifest_json["claim_counts"]["supported"] == 1
    assert first_result.artifact.manifest_json["claim_counts"]["mixed"] == 1
    assert first_result.artifact.manifest_json["claim_counts"]["unsupported"] == 1
    assert "## Executive Summary" in first_result.markdown
    assert "## Appendix: Claim To Citation Spans Mapping" in first_result.markdown
    assert "[MIXED] The mixed claim remains under dispute." in first_result.markdown
    assert (
        "[UNSUPPORTED] The unsupported claim currently lacks support evidence."
        in first_result.markdown
    )
    assert stored_bytes.decode("utf-8") == first_result.markdown


def test_get_latest_report_returns_stored_artifact_even_if_ledger_later_changes(
    db_session: Session,
    tmp_path: Path,
) -> None:
    seeded = _seed_verified_claims(db_session)
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    first_result = service.generate_markdown_report(seeded.task_id)
    task = db_session.get(ResearchTask, seeded.task_id)
    assert task is not None
    task.query = "A later task query that should not rewrite the stored artifact title"
    db_session.commit()

    latest_result = service.get_latest_markdown_report(seeded.task_id)

    assert latest_result.artifact.id == first_result.artifact.id
    assert latest_result.title == first_result.title
    assert latest_result.markdown == first_result.markdown
    assert latest_result.supported_claims == 0
    assert latest_result.mixed_claims == 0
    assert latest_result.unsupported_claims == 0
    assert latest_result.draft_claims == 0


def test_get_latest_report_detects_content_hash_mismatch(
    db_session: Session,
    tmp_path: Path,
) -> None:
    seeded = _seed_verified_claims(db_session)
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = service.generate_markdown_report(seeded.task_id)
    object_store.put_bytes(
        bucket=result.artifact.storage_bucket,
        key=result.artifact.storage_key,
        content=b"tampered report bytes",
        content_type="text/markdown",
    )

    with pytest.raises(ReportArtifactContentMismatchError):
        service.get_latest_markdown_report(seeded.task_id)


def test_report_synthesis_filters_bad_claims_and_short_evidence(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="Explain OpenAI",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/weak",
            domain="example.com",
            title="Weak source",
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
    weak_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text="C",
            token_count=1,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    bad_claim = Claim(
        task_id=task.id,
        statement="Data",
        claim_type="fact",
        confidence=0.9,
        verification_status="supported",
        notes_json={"verification": {"rationale": "Found 1 support evidence."}},
    )
    db_session.add(bad_claim)
    db_session.flush()
    weak_span = CitationSpan(
        source_chunk_id=weak_chunk.id,
        start_offset=0,
        end_offset=1,
        excerpt="C",
        normalized_excerpt_hash="sha256:weak",
    )
    db_session.add(weak_span)
    db_session.flush()
    db_session.add(
        ClaimEvidence(
            claim_id=bad_claim.id,
            citation_span_id=weak_span.id,
            relation_type="support",
            score=0.9,
        )
    )
    db_session.commit()

    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = service.generate_markdown_report(task.id)

    assert result.supported_claims == 0
    assert "Data" not in result.markdown
    assert 'excerpt: "C"' not in result.markdown


def test_report_synthesis_excludes_low_quality_supported_claims_and_warns(
    db_session: Session,
    tmp_path: Path,
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
    source_chunk_repo = SourceChunkRepository(db_session)
    good_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=(
                "SearXNG is a metasearch engine, aggregating the results of other search "
                "engines while not storing information about its users."
            ),
            token_count=18,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.95},
        )
    )
    bad_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text="Track development, send contributions, and report issues at SearXNG sources.",
            token_count=10,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.95},
        )
    )
    good_claim = Claim(
        task_id=task.id,
        statement=good_chunk.text,
        claim_type="fact",
        confidence=0.93,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "definition",
            "claim_quality_score": 0.95,
            "query_answer_score": 1.0,
        },
    )
    bad_claim = Claim(
        task_id=task.id,
        statement=bad_chunk.text,
        claim_type="fact",
        confidence=0.9,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "community",
            "claim_quality_score": 0.2,
            "query_answer_score": 0.1,
        },
    )
    db_session.add_all([good_claim, bad_claim])
    db_session.flush()
    good_span = CitationSpan(
        source_chunk_id=good_chunk.id,
        start_offset=0,
        end_offset=len(good_chunk.text),
        excerpt=good_chunk.text,
        normalized_excerpt_hash="sha256:good-searxng",
    )
    bad_span = CitationSpan(
        source_chunk_id=bad_chunk.id,
        start_offset=0,
        end_offset=len(bad_chunk.text),
        excerpt=bad_chunk.text,
        normalized_excerpt_hash="sha256:bad-searxng",
    )
    db_session.add_all([good_span, bad_span])
    db_session.flush()
    db_session.add_all(
        [
            ClaimEvidence(
                claim_id=good_claim.id,
                citation_span_id=good_span.id,
                relation_type="support",
                score=0.93,
            ),
            ClaimEvidence(
                claim_id=bad_claim.id,
                citation_span_id=bad_span.id,
                relation_type="support",
                score=0.9,
            ),
        ]
    )
    db_session.commit()
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = service.generate_markdown_report(task.id)

    assert result.supported_claims == 1
    assert good_chunk.text in result.markdown
    assert bad_chunk.text not in result.markdown
    assert "Low answer coverage: only 1 answer-relevant claims were generated." in result.markdown
    assert "Answer-relevant claims included: 1." in result.markdown
    assert "Excluded low-quality or off-query claims: 1." in result.markdown


def test_report_synthesis_excludes_supported_other_and_setup_claims(
    db_session: Session,
    tmp_path: Path,
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
    source_chunk_repo = SourceChunkRepository(db_session)
    good_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text="SearXNG is a metasearch engine that aggregates results from search engines.",
            token_count=16,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.95},
        )
    )
    other_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text="SearXNG has a public instances list.",
            token_count=8,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.95},
        )
    )
    setup_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=2,
            text="Get started with SearXNG by using one of the instances listed at .",
            token_count=14,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.95},
        )
    )
    good_claim = Claim(
        task_id=task.id,
        statement=good_chunk.text,
        claim_type="fact",
        confidence=0.93,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "definition",
            "claim_quality_score": 0.95,
            "query_answer_score": 1.0,
        },
    )
    other_claim = Claim(
        task_id=task.id,
        statement=other_chunk.text,
        claim_type="fact",
        confidence=0.88,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "other",
            "claim_quality_score": 0.8,
            "query_answer_score": 0.55,
        },
    )
    setup_claim = Claim(
        task_id=task.id,
        statement=setup_chunk.text,
        claim_type="fact",
        confidence=0.88,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "setup",
            "claim_quality_score": 0.8,
            "query_answer_score": 0.55,
        },
    )
    db_session.add_all([good_claim, other_claim, setup_claim])
    db_session.flush()
    spans = [
        CitationSpan(
            source_chunk_id=good_chunk.id,
            start_offset=0,
            end_offset=len(good_chunk.text),
            excerpt=good_chunk.text,
            normalized_excerpt_hash="sha256:good-definition",
        ),
        CitationSpan(
            source_chunk_id=other_chunk.id,
            start_offset=0,
            end_offset=len(other_chunk.text),
            excerpt=other_chunk.text,
            normalized_excerpt_hash="sha256:other-off-query",
        ),
        CitationSpan(
            source_chunk_id=setup_chunk.id,
            start_offset=0,
            end_offset=len(setup_chunk.text),
            excerpt=setup_chunk.text,
            normalized_excerpt_hash="sha256:setup-instruction",
        ),
    ]
    db_session.add_all(spans)
    db_session.flush()
    db_session.add_all(
        [
            ClaimEvidence(
                claim_id=good_claim.id,
                citation_span_id=spans[0].id,
                relation_type="support",
                score=0.93,
            ),
            ClaimEvidence(
                claim_id=other_claim.id,
                citation_span_id=spans[1].id,
                relation_type="support",
                score=0.88,
            ),
            ClaimEvidence(
                claim_id=setup_claim.id,
                citation_span_id=spans[2].id,
                relation_type="support",
                score=0.88,
            ),
        ]
    )
    db_session.commit()
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = service.generate_markdown_report(task.id)

    assert result.supported_claims == 1
    assert good_chunk.text in result.markdown
    assert other_chunk.text not in result.markdown
    assert setup_chunk.text not in result.markdown
    assert "Excluded low-quality or off-query claims: 2." in result.markdown


class SeededReportClaims:
    def __init__(self, task_id: UUID) -> None:
        self.task_id = task_id


def _seed_verified_claims(db_session: Session) -> SeededReportClaims:
    task = create_research_task_service(db_session).create_task(
        query="What is the current verified position?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/report-source",
            domain="example.com",
            title="Example report source",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 24, 10, 0, tzinfo=UTC),
            authority_score=None,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=None,
        )
    )
    source_chunk_repo = SourceChunkRepository(db_session)
    support_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text="This supported claim is backed by the source document.",
            token_count=11,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    contradict_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text="This mixed claim is not fully supported by the source document.",
            token_count=12,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    mixed_support_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=2,
            text="The mixed claim remains under dispute according to this source.",
            token_count=10,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    unsupported_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=3,
            text="This unsupported claim is contradicted by the source document.",
            token_count=11,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )

    supported_claim = Claim(
        task_id=task.id,
        statement="This supported claim is backed by the source document.",
        claim_type="fact",
        confidence=0.92,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "definition",
            "claim_quality_score": 0.9,
            "query_answer_score": 0.9,
        },
    )
    mixed_claim = Claim(
        task_id=task.id,
        statement="The mixed claim remains under dispute.",
        claim_type="fact",
        confidence=0.68,
        verification_status="mixed",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and 1 contradict evidence."},
            "claim_category": "mechanism",
            "claim_quality_score": 0.82,
            "query_answer_score": 0.8,
        },
    )
    unsupported_claim = Claim(
        task_id=task.id,
        statement="The unsupported claim currently lacks support evidence.",
        claim_type="fact",
        confidence=0.41,
        verification_status="unsupported",
        notes_json={
            "verification": {
                "rationale": "No support evidence found; found 1 contradict evidence."
            },
            "claim_category": "privacy",
            "claim_quality_score": 0.78,
            "query_answer_score": 0.75,
        },
    )
    db_session.add_all([supported_claim, mixed_claim, unsupported_claim])
    db_session.flush()

    support_span = CitationSpan(
        source_chunk_id=support_chunk.id,
        start_offset=0,
        end_offset=len(support_chunk.text),
        excerpt=support_chunk.text,
        normalized_excerpt_hash="sha256:support",
    )
    mixed_support_span = CitationSpan(
        source_chunk_id=mixed_support_chunk.id,
        start_offset=0,
        end_offset=len(mixed_support_chunk.text),
        excerpt=mixed_support_chunk.text,
        normalized_excerpt_hash="sha256:mixed-support",
    )
    mixed_contradict_span = CitationSpan(
        source_chunk_id=contradict_chunk.id,
        start_offset=0,
        end_offset=len(contradict_chunk.text),
        excerpt=contradict_chunk.text,
        normalized_excerpt_hash="sha256:mixed-contradict",
    )
    unsupported_contradict_span = CitationSpan(
        source_chunk_id=unsupported_chunk.id,
        start_offset=0,
        end_offset=len(unsupported_chunk.text),
        excerpt=unsupported_chunk.text,
        normalized_excerpt_hash="sha256:unsupported-contradict",
    )
    db_session.add_all(
        [
            support_span,
            mixed_support_span,
            mixed_contradict_span,
            unsupported_contradict_span,
        ]
    )
    db_session.flush()

    db_session.add_all(
        [
            ClaimEvidence(
                claim_id=supported_claim.id,
                citation_span_id=support_span.id,
                relation_type="support",
                score=0.92,
            ),
            ClaimEvidence(
                claim_id=mixed_claim.id,
                citation_span_id=mixed_support_span.id,
                relation_type="support",
                score=0.66,
            ),
            ClaimEvidence(
                claim_id=mixed_claim.id,
                citation_span_id=mixed_contradict_span.id,
                relation_type="contradict",
                score=0.81,
            ),
            ClaimEvidence(
                claim_id=unsupported_claim.id,
                citation_span_id=unsupported_contradict_span.id,
                relation_type="contradict",
                score=0.79,
            ),
        ]
    )
    db_session.commit()
    return SeededReportClaims(task.id)


def _markdown_hash(markdown: str) -> str:
    return f"sha256:{sha256(markdown.encode('utf-8')).hexdigest()}"
