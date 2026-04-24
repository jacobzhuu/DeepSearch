from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from packages.db.models import (
    CandidateUrl,
    ContentSnapshot,
    FetchAttempt,
    FetchJob,
    ResearchRun,
    SearchQuery,
)
from packages.db.repositories import (
    CandidateUrlRepository,
    ContentSnapshotRepository,
    FetchAttemptRepository,
    FetchJobRepository,
    ResearchRunRepository,
    SearchQueryRepository,
)
from services.orchestrator.app.api.routes.parsing import get_snapshot_object_store
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.main import create_app
from services.orchestrator.app.services.research_tasks import create_research_task_service
from services.orchestrator.app.storage import FilesystemSnapshotObjectStore

DEFAULT_HTML_CONTENT = (
    b"<html><head><title>API Title</title></head>" b"<body><p>Alpha.</p><p>Beta.</p></body></html>"
)


def _seed_snapshot(
    session: Session,
    *,
    snapshot_root: Path,
    mime_type: str = "text/html",
    content: bytes = DEFAULT_HTML_CONTENT,
) -> tuple[str, str]:
    task = create_research_task_service(session).create_task(
        query="Parsing API task", constraints={}
    )
    run = ResearchRunRepository(session).add(
        ResearchRun(
            task_id=task.id,
            round_no=1,
            current_state="PLANNED",
            checkpoint_json={"task_revision_no": 1},
        )
    )
    search_query = SearchQueryRepository(session).add(
        SearchQuery(
            task_id=task.id,
            run_id=run.id,
            query_text="Parsing API task",
            provider="searxng",
            round_no=1,
            issued_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
            raw_response_json={"task_revision_no": 1},
        )
    )
    candidate_url = CandidateUrlRepository(session).add(
        CandidateUrl(
            task_id=task.id,
            search_query_id=search_query.id,
            original_url="https://example.com/api-source",
            canonical_url="https://example.com/api-source",
            domain="example.com",
            title="Candidate title",
            rank=1,
            selected=False,
            metadata_json={},
        )
    )
    fetch_job = FetchJobRepository(session).add(
        FetchJob(
            task_id=task.id,
            candidate_url_id=candidate_url.id,
            mode="HTTP",
            status="SUCCEEDED",
            scheduled_at=datetime(2026, 4, 23, 12, 1, tzinfo=UTC),
        )
    )
    fetch_attempt = FetchAttemptRepository(session).add(
        FetchAttempt(
            fetch_job_id=fetch_job.id,
            attempt_no=1,
            http_status=200,
            error_code=None,
            started_at=datetime(2026, 4, 23, 12, 1, tzinfo=UTC),
            finished_at=datetime(2026, 4, 23, 12, 2, tzinfo=UTC),
            trace_json={},
        )
    )
    object_store = FilesystemSnapshotObjectStore(root_directory=str(snapshot_root))
    stored_object = object_store.put_bytes(
        bucket="snapshots",
        key=f"task/{task.id}/parse.bin",
        content=content,
        content_type=mime_type,
    )
    content_snapshot = ContentSnapshotRepository(session).add(
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
    session.commit()
    return str(task.id), str(content_snapshot.id)


def _build_client(
    session_factory: sessionmaker[Session],
    snapshot_root: Path,
) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_db_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_snapshot_object_store] = lambda: FilesystemSnapshotObjectStore(
        root_directory=str(snapshot_root)
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_parsing_endpoints_create_and_list_sources(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        task_id, content_snapshot_id = _seed_snapshot(session, snapshot_root=tmp_path)

    client_generator = _build_client(session_factory, tmp_path)
    client = next(client_generator)
    try:
        parse_response = client.post(
            f"/api/v1/research/tasks/{task_id}/parse",
            json={"content_snapshot_ids": [content_snapshot_id]},
        )
        source_documents_response = client.get(f"/api/v1/research/tasks/{task_id}/source-documents")
        source_chunks_response = client.get(f"/api/v1/research/tasks/{task_id}/source-chunks")

        assert parse_response.status_code == 200
        assert parse_response.json()["created"] == 1
        assert parse_response.json()["entries"][0]["status"] == "CREATED"

        assert source_documents_response.status_code == 200
        source_document = source_documents_response.json()["source_documents"][0]
        assert source_document["content_snapshot_id"] == content_snapshot_id
        assert source_document["title"] == "API Title"

        assert source_chunks_response.status_code == 200
        source_chunk = source_chunks_response.json()["source_chunks"][0]
        assert source_chunk["content_snapshot_id"] == content_snapshot_id
        assert "Alpha." in source_chunk["text"]
        assert source_chunk["metadata"]["strategy"] == "paragraph_window_v1"
    finally:
        client_generator.close()


def test_sources_endpoint_returns_empty_list_for_planned_task(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        task = create_research_task_service(session).create_task(
            query="Planned task without sources",
            constraints={},
        )
        task_id = str(task.id)

    client_generator = _build_client(session_factory, tmp_path)
    client = next(client_generator)
    try:
        response = client.get(f"/api/v1/research/tasks/{task_id}/sources")

        assert response.status_code == 200
        assert response.json() == {"task_id": task_id, "sources": []}
    finally:
        client_generator.close()


def test_parsing_endpoint_skips_unsupported_mime_type(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        task_id, content_snapshot_id = _seed_snapshot(
            session,
            snapshot_root=tmp_path,
            mime_type="application/pdf",
            content=b"%PDF-1.7",
        )

    client_generator = _build_client(session_factory, tmp_path)
    client = next(client_generator)
    try:
        parse_response = client.post(
            f"/api/v1/research/tasks/{task_id}/parse",
            json={"content_snapshot_ids": [content_snapshot_id]},
        )

        assert parse_response.status_code == 200
        assert parse_response.json()["skipped_unsupported"] == 1
        assert parse_response.json()["entries"][0]["reason"] == "unsupported_mime_type"
    finally:
        client_generator.close()


def test_parsing_endpoint_rejects_paused_task(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        task_id, content_snapshot_id = _seed_snapshot(session, snapshot_root=tmp_path)
        create_research_task_service(session).pause_task(UUID(task_id))

    client_generator = _build_client(session_factory, tmp_path)
    client = next(client_generator)
    try:
        parse_response = client.post(
            f"/api/v1/research/tasks/{task_id}/parse",
            json={"content_snapshot_ids": [content_snapshot_id]},
        )

        assert parse_response.status_code == 409
        assert "cannot parse snapshots" in parse_response.json()["detail"]
    finally:
        client_generator.close()
