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
    unsupported_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=2,
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
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."}
        },
    )
    mixed_claim = Claim(
        task_id=task.id,
        statement="The mixed claim remains under dispute.",
        claim_type="fact",
        confidence=0.68,
        verification_status="mixed",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and 1 contradict evidence."}
        },
    )
    unsupported_claim = Claim(
        task_id=task.id,
        statement="The unsupported claim currently lacks support evidence.",
        claim_type="fact",
        confidence=0.41,
        verification_status="unsupported",
        notes_json={
            "verification": {"rationale": "No support evidence found; found 1 contradict evidence."}
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
        source_chunk_id=support_chunk.id,
        start_offset=0,
        end_offset=24,
        excerpt=support_chunk.text[:24],
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
