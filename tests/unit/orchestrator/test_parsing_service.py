from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from packages.db.models import (
    CandidateUrl,
    ContentSnapshot,
    FetchAttempt,
    FetchJob,
    ResearchRun,
    SearchQuery,
    SourceChunk,
    SourceDocument,
)
from packages.db.repositories import (
    CandidateUrlRepository,
    ContentSnapshotRepository,
    FetchAttemptRepository,
    FetchJobRepository,
    ResearchRunRepository,
    SearchQueryRepository,
    SourceChunkRepository,
    SourceDocumentRepository,
)
from services.orchestrator.app.parsing import ParseResultReason
from services.orchestrator.app.services.parsing import (
    ParsingConflictError,
    create_parsing_service,
)
from services.orchestrator.app.services.research_tasks import create_research_task_service
from services.orchestrator.app.storage import FilesystemSnapshotObjectStore

DEFAULT_HTML_CONTENT = (
    b"<html><head><title>Example</title></head>" b"<body><p>Alpha.</p><p>Beta.</p></body></html>"
)
UPDATED_HTML_CONTENT = (
    b"<html><head><title>New Title</title></head>" b"<body><p>Updated body text.</p></body></html>"
)


def _seed_snapshot(
    db_session: Session,
    *,
    snapshot_root: Path,
    query: str = "Parsing service task",
    canonical_url: str = "https://example.com/source",
    mime_type: str = "text/html",
    content: bytes = DEFAULT_HTML_CONTENT,
    fetch_status: str = "SUCCEEDED",
    fetch_error_code: str | None = None,
) -> tuple[ContentSnapshot, SourceDocumentRepository, SourceChunkRepository]:
    task = create_research_task_service(db_session).create_task(query=query, constraints={})
    run = ResearchRunRepository(db_session).add(
        ResearchRun(
            task_id=task.id,
            round_no=1,
            current_state="PLANNED",
            checkpoint_json={"task_revision_no": 1},
        )
    )
    search_query = SearchQueryRepository(db_session).add(
        SearchQuery(
            task_id=task.id,
            run_id=run.id,
            query_text=query,
            provider="searxng",
            round_no=1,
            issued_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
            raw_response_json={"task_revision_no": 1},
        )
    )
    candidate_url = CandidateUrlRepository(db_session).add(
        CandidateUrl(
            task_id=task.id,
            search_query_id=search_query.id,
            original_url=canonical_url,
            canonical_url=canonical_url,
            domain="example.com",
            title="Candidate title",
            rank=1,
            selected=False,
            metadata_json={},
        )
    )
    fetch_job = FetchJobRepository(db_session).add(
        FetchJob(
            task_id=task.id,
            candidate_url_id=candidate_url.id,
            mode="HTTP",
            status=fetch_status,
            scheduled_at=datetime(2026, 4, 23, 12, 1, tzinfo=UTC),
        )
    )
    fetch_attempt = FetchAttemptRepository(db_session).add(
        FetchAttempt(
            fetch_job_id=fetch_job.id,
            attempt_no=1,
            http_status=200,
            error_code=fetch_error_code,
            started_at=datetime(2026, 4, 23, 12, 1, tzinfo=UTC),
            finished_at=datetime(2026, 4, 23, 12, 2, tzinfo=UTC),
            trace_json={},
        )
    )
    object_store = FilesystemSnapshotObjectStore(root_directory=str(snapshot_root))
    stored_object = object_store.put_bytes(
        bucket="snapshots",
        key=f"task/{task.id}/snapshot.bin",
        content=content,
        content_type=mime_type,
    )
    content_snapshot = ContentSnapshotRepository(db_session).add(
        ContentSnapshot(
            fetch_attempt_id=fetch_attempt.id,
            storage_bucket=stored_object.bucket,
            storage_key=stored_object.key,
            content_hash="sha256:test",
            mime_type=mime_type,
            bytes=len(content),
            extracted_title=None,
            fetched_at=datetime(2026, 4, 23, 12, 2, tzinfo=UTC),
        )
    )
    db_session.commit()
    return content_snapshot, SourceDocumentRepository(db_session), SourceChunkRepository(db_session)


def test_parsing_service_creates_source_document_and_chunks(
    db_session: Session,
    tmp_path: Path,
) -> None:
    content_snapshot, source_document_repo, source_chunk_repo = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
    )
    service = create_parsing_service(
        db_session,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
    )

    result = service.parse_snapshots(
        content_snapshot.fetch_attempt.fetch_job.task_id,
        content_snapshot_ids=[content_snapshot.id],
        limit=1,
    )

    source_document = source_document_repo.get_for_content_snapshot(content_snapshot.id)
    assert result.created == 1
    assert result.updated == 0
    assert result.failed == 0
    assert source_document is not None
    assert source_document.content_snapshot_id == content_snapshot.id
    assert source_document.title == "Example"
    chunks = source_chunk_repo.list_for_document(source_document.id)
    assert len(chunks) == 1
    assert "Alpha." in chunks[0].text
    assert chunks[0].metadata_json["content_snapshot_id"] == str(content_snapshot.id)


def test_parsing_service_skips_unsupported_mime_type(
    db_session: Session,
    tmp_path: Path,
) -> None:
    content_snapshot, source_document_repo, _ = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        mime_type="application/pdf",
        content=b"%PDF-1.7",
    )
    service = create_parsing_service(
        db_session,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
    )

    result = service.parse_snapshots(
        content_snapshot.fetch_attempt.fetch_job.task_id,
        content_snapshot_ids=[content_snapshot.id],
        limit=1,
    )

    assert result.created == 0
    assert result.skipped_unsupported == 1
    assert result.entries[0].reason == ParseResultReason.UNSUPPORTED_MIME_TYPE
    assert source_document_repo.get_for_content_snapshot(content_snapshot.id) is None


def test_parsing_service_updates_existing_document_for_same_canonical_url(
    db_session: Session,
    tmp_path: Path,
) -> None:
    content_snapshot, source_document_repo, source_chunk_repo = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        content=UPDATED_HTML_CONTENT,
    )
    task_id = content_snapshot.fetch_attempt.fetch_job.task_id
    existing_document = source_document_repo.add(
        SourceDocument(
            task_id=task_id,
            content_snapshot_id=None,
            canonical_url=content_snapshot.fetch_attempt.fetch_job.candidate_url.canonical_url,
            domain="example.com",
            title="Old Title",
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
    source_chunk_repo.add(
        SourceChunk(
            source_document_id=existing_document.id,
            chunk_no=0,
            text="Old chunk",
            token_count=2,
            metadata_json={"strategy": "old"},
        )
    )
    db_session.commit()

    service = create_parsing_service(
        db_session,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
    )

    result = service.parse_snapshots(task_id, content_snapshot_ids=[content_snapshot.id], limit=1)

    refreshed_document = source_document_repo.get(existing_document.id)
    refreshed_chunks = source_chunk_repo.list_for_document(existing_document.id)
    assert result.updated == 1
    assert refreshed_document is not None
    assert refreshed_document.content_snapshot_id == content_snapshot.id
    assert refreshed_document.title == "New Title"
    assert len(refreshed_chunks) == 1
    assert refreshed_chunks[0].text == "Updated body text."


def test_parsing_service_rejects_paused_task(db_session: Session, tmp_path: Path) -> None:
    content_snapshot, _, _ = _seed_snapshot(db_session, snapshot_root=tmp_path)
    task_id = content_snapshot.fetch_attempt.fetch_job.task_id
    create_research_task_service(db_session).pause_task(task_id)
    service = create_parsing_service(
        db_session,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
    )

    with pytest.raises(ParsingConflictError):
        service.parse_snapshots(task_id, content_snapshot_ids=[content_snapshot.id], limit=1)
