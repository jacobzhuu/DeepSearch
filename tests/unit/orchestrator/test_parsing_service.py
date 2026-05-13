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
from services.orchestrator.app.parsing import (
    ParsedChunk,
    ParseResultReason,
)
from services.orchestrator.app.parsing import (
    chunk_text as real_chunk_text,
)
from services.orchestrator.app.parsing.chunk_text_validation import REJECT_BINARY_LIKE_CHUNK_TEXT
from services.orchestrator.app.services import parsing as parsing_service_module
from services.orchestrator.app.services.parsing import (
    ParsingConflictError,
    create_parsing_service,
)
from services.orchestrator.app.services.research_tasks import create_research_task_service
from services.orchestrator.app.storage import FilesystemSnapshotObjectStore

DEFAULT_HTML_CONTENT = (
    b"<html><head><title>Example</title></head><body><p>Alpha.</p><p>Beta.</p></body></html>"
)
UPDATED_HTML_CONTENT = (
    b"<html><head><title>New Title</title></head><body><p>Updated body text.</p></body></html>"
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
    store_content: bool = True,
    fetch_trace_json: dict[str, object] | None = None,
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
            trace_json=dict(fetch_trace_json or {}),
        )
    )
    object_store = FilesystemSnapshotObjectStore(root_directory=str(snapshot_root))
    if store_content:
        stored_object = object_store.put_bytes(
            bucket="snapshots",
            key=f"task/{task.id}/snapshot.bin",
            content=content,
            content_type=mime_type,
        )
        storage_bucket = stored_object.bucket
        storage_key = stored_object.key
    else:
        storage_bucket = "snapshots"
        storage_key = f"task/{task.id}/missing-snapshot.bin"
    content_snapshot = ContentSnapshotRepository(db_session).add(
        ContentSnapshot(
            fetch_attempt_id=fetch_attempt.id,
            storage_bucket=storage_bucket,
            storage_key=storage_key,
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
    assert result.entries[0].decision == "parsed"
    assert result.entries[0].body_length == len(DEFAULT_HTML_CONTENT)
    assert source_document is not None
    assert source_document.content_snapshot_id == content_snapshot.id
    assert source_document.title == "Example"
    chunks = source_chunk_repo.list_for_document(source_document.id)
    assert len(chunks) == 1
    assert "Alpha." in chunks[0].text
    assert chunks[0].metadata_json["content_snapshot_id"] == str(content_snapshot.id)
    assert chunks[0].metadata_json["extractor_strategy_used"] == "main_content"
    assert chunks[0].metadata_json["fallback_used"] is False
    assert isinstance(chunks[0].metadata_json["removed_boilerplate_count"], int)
    assert chunks[0].metadata_json["extracted_text_length"] >= len("Alpha.")


def test_parsing_service_records_leading_share_comment_ui_chunk_quality(
    db_session: Session,
    tmp_path: Path,
) -> None:
    html = """
    <html>
      <head><title>NVIDIA Technical Blog</title></head>
      <body>
        <main>
          <article>
            <p>
              Skip to content / 分享此文章 / 收件人的邮箱地址 / 您的名字 /
              Comments / 邮件已发送
            </p>
          </article>
        </main>
      </body>
    </html>
    """.encode()
    content_snapshot, source_document_repo, source_chunk_repo = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        query="NVIDIA open model ecosystem releases",
        canonical_url="https://blogs.nvidia.com/example",
        content=html,
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
    assert source_document is not None
    chunks = source_chunk_repo.list_for_document(source_document.id)
    assert len(chunks) == 1
    assert chunks[0].metadata_json["is_boilerplate_like"] is True
    assert chunks[0].metadata_json["eligible_for_claims"] is False
    assert "leading_boilerplate_like" in chunks[0].metadata_json["quality_reasons"]


def test_parsing_service_skips_static_html_parse_hold_without_source_document(
    db_session: Session,
    tmp_path: Path,
) -> None:
    spa_html = b"""<!doctype html><html><head><script>console.log(1)</script></head>
    <body><div id="app"></div><script src="/bundle.js"></script></body></html>"""
    content_snapshot, source_document_repo, _ = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        content=spa_html,
        fetch_trace_json={
            "eligible_for_evidence_parse": False,
            "static_html_quality_decision": "spa_shell",
            "parse_hold_reason": "spa_shell",
        },
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
    assert result.skipped_static_html_hold == 1
    assert result.entries[0].reason == ParseResultReason.ACQUISITION_STATIC_HTML_PARSE_HELD
    assert result.entries[0].decision == "skipped_static_html_quality"
    assert source_document_repo.get_for_content_snapshot(content_snapshot.id) is None


def test_parsing_service_skips_unsupported_mime_type(
    db_session: Session,
    tmp_path: Path,
) -> None:
    content_snapshot, source_document_repo, _ = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        mime_type="application/octet-stream",
        content=b"binary",
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
    assert result.entries[0].decision == "skipped_unsupported_mime"
    assert result.entries[0].body_length == len(b"binary")
    assert source_document_repo.get_for_content_snapshot(content_snapshot.id) is None


@pytest.mark.parametrize(
    ("canonical_url", "mime_type", "content"),
    [
        (
            "https://raw.githubusercontent.com/searxng/searxng/master/container/docker-compose.yml",
            "text/yaml",
            b"services:\n  searxng:\n    image: docker.io/searxng/searxng:latest\n",
        ),
        (
            "https://raw.githubusercontent.com/searxng/searxng/master/container/docker-compose.yml",
            "application/octet-stream",
            b"services:\n  searxng:\n    image: docker.io/searxng/searxng:latest\n",
        ),
        (
            "https://raw.githubusercontent.com/searxng/searxng/master/container/.env.example",
            "application/octet-stream",
            b"SEARXNG_BASE_URL=https://example.test/\n",
        ),
    ],
)
def test_parsing_service_parses_raw_yaml_and_env_as_safe_text(
    db_session: Session,
    tmp_path: Path,
    canonical_url: str,
    mime_type: str,
    content: bytes,
) -> None:
    content_snapshot, source_document_repo, source_chunk_repo = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        query="How to deploy SearXNG with Docker?",
        canonical_url=canonical_url,
        mime_type=mime_type,
        content=content,
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
    assert result.entries[0].decision == "parsed"
    assert source_document is not None
    chunks = source_chunk_repo.list_for_document(source_document.id)
    assert len(chunks) == 1
    assert chunks[0].text == content.decode().strip()


def test_parsing_service_creates_pdf_source_with_page_metadata(
    db_session: Session,
    tmp_path: Path,
) -> None:
    pdf_content = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type /Page>>stream\n"
        b"BT (PDF evidence sentence.) Tj ET\n"
        b"endstream\n%%EOF"
    )
    content_snapshot, source_document_repo, source_chunk_repo = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        mime_type="application/pdf",
        content=pdf_content,
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
    assert source_document is not None
    assert source_document.source_type == "pdf_document"
    chunks = source_chunk_repo.list_for_document(source_document.id)
    assert chunks[0].metadata_json["source_format"] == "pdf"
    assert chunks[0].metadata_json["parser_status"] == "success"
    assert chunks[0].metadata_json["page_range"] == [1, 1]
    assert chunks[0].metadata_json["page_locator_reliable"] is True


def test_parsing_service_records_empty_body_decision(
    db_session: Session,
    tmp_path: Path,
) -> None:
    content_snapshot, source_document_repo, _ = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        content=b"",
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
    assert result.failed == 0
    assert result.entries[0].status == "SKIPPED"
    assert result.entries[0].reason == ParseResultReason.EMPTY_EXTRACTED_TEXT
    assert result.entries[0].decision == "skipped_empty"
    assert result.entries[0].body_length == 0
    assert source_document_repo.get_for_content_snapshot(content_snapshot.id) is None


def test_parsing_service_records_missing_blob_decision(
    db_session: Session,
    tmp_path: Path,
) -> None:
    content_snapshot, source_document_repo, _ = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        store_content=False,
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
    assert result.failed == 1
    assert result.entries[0].status == "FAILED"
    assert result.entries[0].reason == ParseResultReason.SNAPSHOT_OBJECT_MISSING
    assert result.entries[0].decision == "missing_blob"
    assert result.entries[0].body_length is None
    assert "object store" in str(result.entries[0].parser_error)
    assert source_document_repo.get_for_content_snapshot(content_snapshot.id) is None


def test_parsing_service_records_parser_exception_decision(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content_snapshot, source_document_repo, _ = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
    )

    def raise_parser_error(*, mime_type: str, content: bytes) -> object:
        del mime_type, content
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(
        parsing_service_module,
        "extract_parsed_content",
        raise_parser_error,
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
    assert result.failed == 1
    assert result.entries[0].reason == ParseResultReason.PARSE_ERROR
    assert result.entries[0].decision == "parse_error"
    assert result.entries[0].body_length == len(DEFAULT_HTML_CONTENT)
    assert result.entries[0].parser_error == "parser exploded"
    assert source_document_repo.get_for_content_snapshot(content_snapshot.id) is None


def test_parsing_service_default_selection_skips_failed_fetch_snapshots_before_limit(
    db_session: Session,
    tmp_path: Path,
) -> None:
    failed_snapshot, source_document_repo, source_chunk_repo = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        canonical_url="https://blocked.example/source",
        content=b"<html><body>Forbidden</body></html>",
        fetch_status="FAILED",
        fetch_error_code="http_error_status",
    )
    task_id = failed_snapshot.fetch_attempt.fetch_job.task_id
    search_query_id = failed_snapshot.fetch_attempt.fetch_job.candidate_url.search_query_id
    candidate_url = CandidateUrlRepository(db_session).add(
        CandidateUrl(
            task_id=task_id,
            search_query_id=search_query_id,
            original_url="https://example.com/success",
            canonical_url="https://example.com/success",
            domain="example.com",
            title="Successful source",
            rank=2,
            selected=False,
            metadata_json={},
        )
    )
    fetch_job = FetchJobRepository(db_session).add(
        FetchJob(
            task_id=task_id,
            candidate_url_id=candidate_url.id,
            mode="HTTP",
            status="SUCCEEDED",
            scheduled_at=datetime(2026, 4, 23, 12, 3, tzinfo=UTC),
        )
    )
    fetch_attempt = FetchAttemptRepository(db_session).add(
        FetchAttempt(
            fetch_job_id=fetch_job.id,
            attempt_no=1,
            http_status=200,
            error_code=None,
            started_at=datetime(2026, 4, 23, 12, 3, tzinfo=UTC),
            finished_at=datetime(2026, 4, 23, 12, 4, tzinfo=UTC),
            trace_json={},
        )
    )
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))
    success_content = (
        b"<html><head><title>Success</title></head><body><p>Useful text.</p></body></html>"
    )
    stored_object = object_store.put_bytes(
        bucket="snapshots",
        key=f"task/{task_id}/success-snapshot.bin",
        content=success_content,
        content_type="text/html",
    )
    success_snapshot = ContentSnapshotRepository(db_session).add(
        ContentSnapshot(
            fetch_attempt_id=fetch_attempt.id,
            storage_bucket=stored_object.bucket,
            storage_key=stored_object.key,
            content_hash="sha256:success",
            mime_type="text/html",
            bytes=len(success_content),
            extracted_title=None,
            fetched_at=datetime(2026, 4, 23, 12, 4, tzinfo=UTC),
        )
    )
    db_session.commit()
    service = create_parsing_service(
        db_session,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
    )

    result = service.parse_snapshots(task_id, content_snapshot_ids=None, limit=1)

    assert [entry.content_snapshot.id for entry in result.entries] == [success_snapshot.id]
    assert result.created == 1
    assert source_document_repo.get_for_content_snapshot(failed_snapshot.id) is None
    success_document = source_document_repo.get_for_content_snapshot(success_snapshot.id)
    assert success_document is not None
    assert source_chunk_repo.list_for_document(success_document.id)[0].text == "Useful text."


def test_parsing_service_default_selection_prefers_unparsed_snapshots(
    db_session: Session,
    tmp_path: Path,
) -> None:
    """Prefer snapshots without a source_document when selecting default parse batches."""
    snap1, _, _ = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        canonical_url="https://example.com/parse-one",
        content=b"<html><head><title>One</title></head><body><p>Alpha.</p></body></html>",
    )
    task_id = snap1.fetch_attempt.fetch_job.task_id
    search_query_id = snap1.fetch_attempt.fetch_job.candidate_url.search_query_id
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))

    def _add_html_snapshot(
        *,
        canonical_url: str,
        body: str,
        fetched_at: datetime,
    ) -> ContentSnapshot:
        candidate_url = CandidateUrlRepository(db_session).add(
            CandidateUrl(
                task_id=task_id,
                search_query_id=search_query_id,
                original_url=canonical_url,
                canonical_url=canonical_url,
                domain="example.com",
                title="t",
                rank=2,
                selected=False,
                metadata_json={},
            )
        )
        fetch_job = FetchJobRepository(db_session).add(
            FetchJob(
                task_id=task_id,
                candidate_url_id=candidate_url.id,
                mode="HTTP",
                status="SUCCEEDED",
                scheduled_at=fetched_at,
            )
        )
        fetch_attempt = FetchAttemptRepository(db_session).add(
            FetchAttempt(
                fetch_job_id=fetch_job.id,
                attempt_no=1,
                http_status=200,
                error_code=None,
                started_at=fetched_at,
                finished_at=fetched_at,
                trace_json={},
            )
        )
        html = f"<html><head><title>T</title></head><body><p>{body}</p></body></html>".encode()
        stored = object_store.put_bytes(
            bucket="snapshots",
            key=f"task/{task_id}/{canonical_url.replace('/', '_')}.bin",
            content=html,
            content_type="text/html",
        )
        snap = ContentSnapshotRepository(db_session).add(
            ContentSnapshot(
                fetch_attempt_id=fetch_attempt.id,
                storage_bucket=stored.bucket,
                storage_key=stored.key,
                content_hash="sha256:test",
                mime_type="text/html",
                bytes=len(html),
                extracted_title=None,
                fetched_at=fetched_at,
            )
        )
        db_session.commit()
        return snap

    snap2 = _add_html_snapshot(
        canonical_url="https://example.com/parse-two",
        body="Beta.",
        fetched_at=datetime(2026, 4, 23, 12, 10, tzinfo=UTC),
    )
    assert snap2.id
    snap3 = _add_html_snapshot(
        canonical_url="https://example.com/parse-three",
        body="Gamma.",
        fetched_at=datetime(2026, 4, 23, 12, 11, tzinfo=UTC),
    )

    service = create_parsing_service(
        db_session,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
    )
    first = service.parse_snapshots(task_id, content_snapshot_ids=None, limit=2)
    assert first.created == 2
    second = service.parse_snapshots(task_id, content_snapshot_ids=None, limit=2)
    assert second.created == 1
    assert {snap3.id} == {e.content_snapshot.id for e in second.entries if e.status == "CREATED"}
    assert SourceDocumentRepository(db_session).get_for_content_snapshot(snap3.id) is not None


def test_parsing_service_short_html_with_title_creates_source_document(
    db_session: Session,
    tmp_path: Path,
) -> None:
    content = b"<html><head><title>SearXNG</title></head><body></body></html>"
    content_snapshot, source_document_repo, source_chunk_repo = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        canonical_url="https://searxng.org/",
        content=content,
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
    assert result.failed == 0
    assert result.entries[0].decision == "parsed"
    assert result.entries[0].body_length == len(content)
    assert source_document is not None
    assert source_document.title == "SearXNG"
    chunks = source_chunk_repo.list_for_document(source_document.id)
    assert len(chunks) == 1
    assert chunks[0].text == "SearXNG"


def test_parsing_service_marks_redirect_stub_chunks_ineligible_and_records_followup_url(
    db_session: Session,
    tmp_path: Path,
) -> None:
    content = b"<html><body>Redirecting to https://docs.searxng.org/</body></html>"
    content_snapshot, source_document_repo, source_chunk_repo = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        query="What is SearXNG and how does it work?",
        canonical_url="https://searxng.org/",
        content=content,
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
    assert source_document is not None
    assert source_document.final_source_score == 0.1
    chunks = source_chunk_repo.list_for_document(source_document.id)
    assert len(chunks) == 1
    assert chunks[0].metadata_json["content_quality"] == "low"
    assert chunks[0].metadata_json["reason"] == "redirect_stub"
    assert chunks[0].metadata_json["eligible_for_claims"] is False
    assert chunks[0].metadata_json["should_generate_claims"] is False
    assert chunks[0].metadata_json["discovered_followup_url"] == "https://docs.searxng.org/"


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


def test_parsing_service_filters_invalid_chunks_before_db_insert(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content_snapshot, source_document_repo, source_chunk_repo = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
    )
    task_id = content_snapshot.fetch_attempt.fetch_job.task_id

    def chunk_text_with_invalid_prefix(text: str, **kwargs: object) -> list[ParsedChunk]:
        real = real_chunk_text(text, **kwargs)  # type: ignore[arg-type]
        return [
            ParsedChunk(0, "\x00binary-prefix", 1, {}),
            ParsedChunk(1, "   \n\t", 1, {}),
        ] + list(real)

    monkeypatch.setattr(parsing_service_module, "chunk_text", chunk_text_with_invalid_prefix)
    service = create_parsing_service(
        db_session,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
    )
    result = service.parse_snapshots(task_id, content_snapshot_ids=[content_snapshot.id], limit=1)

    assert result.failed == 0
    assert result.created == 1
    entry = result.entries[0]
    assert entry.decision == "parsed"
    cv = entry.chunk_validation or {}
    assert cv.get("invalid_chunk_rejection_count") == 2
    dist = cv.get("invalid_chunk_rejection_reason_distribution") or {}
    assert dist.get(REJECT_BINARY_LIKE_CHUNK_TEXT) == 1
    assert dist.get("whitespace_only_chunk_text") == 1

    source_document = source_document_repo.get_for_content_snapshot(content_snapshot.id)
    assert source_document is not None
    chunks = source_chunk_repo.list_for_document(source_document.id)
    assert len(chunks) == 1
    assert "Alpha." in chunks[0].text
    assert "\x00" not in chunks[0].text


def test_parsing_service_all_invalid_chunks_skips_one_snapshot_batch_continues(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_html = b"<html><head><title>Bad</title></head><body><p>BADONLY_MARKER</p></body></html>"
    bad_snapshot, source_document_repo, source_chunk_repo = _seed_snapshot(
        db_session,
        snapshot_root=tmp_path,
        canonical_url="https://example.com/bad-only",
        content=bad_html,
    )
    task_id = bad_snapshot.fetch_attempt.fetch_job.task_id
    search_query_id = bad_snapshot.fetch_attempt.fetch_job.candidate_url.search_query_id

    candidate_url = CandidateUrlRepository(db_session).add(
        CandidateUrl(
            task_id=task_id,
            search_query_id=search_query_id,
            original_url="https://example.com/good",
            canonical_url="https://example.com/good",
            domain="example.com",
            title="Good source",
            rank=2,
            selected=False,
            metadata_json={},
        )
    )
    fetch_job = FetchJobRepository(db_session).add(
        FetchJob(
            task_id=task_id,
            candidate_url_id=candidate_url.id,
            mode="HTTP",
            status="SUCCEEDED",
            scheduled_at=datetime(2026, 4, 23, 12, 5, tzinfo=UTC),
        )
    )
    fetch_attempt = FetchAttemptRepository(db_session).add(
        FetchAttempt(
            fetch_job_id=fetch_job.id,
            attempt_no=1,
            http_status=200,
            error_code=None,
            started_at=datetime(2026, 4, 23, 12, 5, tzinfo=UTC),
            finished_at=datetime(2026, 4, 23, 12, 5, tzinfo=UTC),
            trace_json={},
        )
    )
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))
    good_content = (
        b"<html><head><title>Good</title></head><body><p>Useful follow-up text.</p></body></html>"
    )
    stored_object = object_store.put_bytes(
        bucket="snapshots",
        key=f"task/{task_id}/good-snapshot.bin",
        content=good_content,
        content_type="text/html",
    )
    good_snapshot = ContentSnapshotRepository(db_session).add(
        ContentSnapshot(
            fetch_attempt_id=fetch_attempt.id,
            storage_bucket=stored_object.bucket,
            storage_key=stored_object.key,
            content_hash="sha256:good",
            mime_type="text/html",
            bytes=len(good_content),
            extracted_title=None,
            fetched_at=datetime(2026, 4, 23, 12, 6, tzinfo=UTC),
        )
    )
    db_session.commit()

    def selective_chunk_text(text: str, **kwargs: object) -> list[ParsedChunk]:
        if "BADONLY_MARKER" in text:
            return [ParsedChunk(0, "\x00only-invalid", 1, {})]
        return real_chunk_text(text, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(parsing_service_module, "chunk_text", selective_chunk_text)
    service = create_parsing_service(
        db_session,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
    )
    result = service.parse_snapshots(
        task_id,
        content_snapshot_ids=[bad_snapshot.id, good_snapshot.id],
        limit=2,
    )

    assert result.failed == 0
    assert result.skipped_no_valid_chunks == 1
    assert result.created == 1
    assert result.invalid_chunk_rejection_count >= 1
    assert source_document_repo.get_for_content_snapshot(bad_snapshot.id) is None
    good_doc = source_document_repo.get_for_content_snapshot(good_snapshot.id)
    assert good_doc is not None
    assert source_chunk_repo.list_for_document(good_doc.id)[0].text == "Useful follow-up text."

    bad_entry = next(e for e in result.entries if e.content_snapshot.id == bad_snapshot.id)
    assert bad_entry.reason == ParseResultReason.NO_VALID_CHUNKS
    assert bad_entry.chunk_validation is not None
    assert bad_entry.chunk_validation.get("parser_invalid_output") is True


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
