from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from packages.db.models import (
    CitationSpan,
    Claim,
    ClaimEvidence,
    ResearchTask,
    SourceChunk,
    SourceDocument,
)
from packages.db.repositories.sources import SourceChunkRepository, SourceDocumentRepository
from services.orchestrator.app.api.routes.reporting import get_report_object_store
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.main import create_app
from services.orchestrator.app.services.research_tasks import create_research_task_service
from services.orchestrator.app.storage import FilesystemSnapshotObjectStore


def test_report_endpoints_generate_and_return_latest_markdown(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        task_id = _seed_verified_claims(session)

    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "reports"))
    object_store.validate_configuration()
    client_generator = _build_client(session_factory, object_store)
    client = next(client_generator)
    try:
        generate_response = client.post(f"/api/v1/research/tasks/{task_id}/report")
        get_response = client.get(f"/api/v1/research/tasks/{task_id}/report")

        assert generate_response.status_code == 200
        assert generate_response.json()["version"] == 1
        assert generate_response.json()["reused_existing"] is False
        assert generate_response.json()["supported_claims"] == 1
        assert generate_response.json()["mixed_claims"] == 1
        assert generate_response.json()["unsupported_claims"] == 1
        assert "## Executive Summary" in generate_response.json()["markdown"]
        assert "[UNSUPPORTED] The unsupported claim currently lacks support evidence." in (
            generate_response.json()["markdown"]
        )

        assert get_response.status_code == 200
        assert (
            get_response.json()["report_artifact_id"]
            == generate_response.json()["report_artifact_id"]
        )
        assert get_response.json()["markdown"] == generate_response.json()["markdown"]
        assert "supported_claims" not in get_response.json()
        assert "mixed_claims" not in get_response.json()
        assert "unsupported_claims" not in get_response.json()
        assert "draft_claims" not in get_response.json()
    finally:
        client_generator.close()


def test_get_report_returns_404_when_no_report_exists(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        task = create_research_task_service(session).create_task(
            query="No report yet",
            constraints={},
        )

    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "reports"))
    object_store.validate_configuration()
    client_generator = _build_client(session_factory, object_store)
    client = next(client_generator)
    try:
        response = client.get(f"/api/v1/research/tasks/{task.id}/report")
        assert response.status_code == 404
        assert "no markdown report artifact was found" in response.json()["detail"]
    finally:
        client_generator.close()


def test_get_report_returns_stored_artifact_after_task_query_changes(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        task_id = _seed_verified_claims(session)

    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "reports"))
    object_store.validate_configuration()
    client_generator = _build_client(session_factory, object_store)
    client = next(client_generator)
    try:
        generate_response = client.post(f"/api/v1/research/tasks/{task_id}/report")
        assert generate_response.status_code == 200

        with session_factory() as session:
            task = session.get(ResearchTask, task_id)
            assert task is not None
            task.query = "A changed query after the report artifact was generated"
            session.commit()

        get_response = client.get(f"/api/v1/research/tasks/{task_id}/report")

        assert get_response.status_code == 200
        assert get_response.json()["title"] == generate_response.json()["title"]
        assert get_response.json()["markdown"] == generate_response.json()["markdown"]
    finally:
        client_generator.close()


def test_get_report_returns_500_when_artifact_hash_verification_fails(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        task_id = _seed_verified_claims(session)

    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "reports"))
    object_store.validate_configuration()
    client_generator = _build_client(session_factory, object_store)
    client = next(client_generator)
    try:
        generate_response = client.post(f"/api/v1/research/tasks/{task_id}/report")
        assert generate_response.status_code == 200
        object_store.put_bytes(
            bucket=generate_response.json()["storage_bucket"],
            key=generate_response.json()["storage_key"],
            content=b"tampered report bytes",
            content_type="text/markdown",
        )

        get_response = client.get(f"/api/v1/research/tasks/{task_id}/report")

        assert get_response.status_code == 500
        assert "failed hash verification" in get_response.json()["detail"]
    finally:
        client_generator.close()


def _build_client(
    session_factory: sessionmaker[Session],
    object_store: FilesystemSnapshotObjectStore,
) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_db_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_report_object_store] = lambda: object_store

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _seed_verified_claims(session: Session) -> UUID:
    task = create_research_task_service(session).create_task(
        query="What is the current verified position?",
        constraints={},
    )
    source_document = SourceDocumentRepository(session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/report-api-source",
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
    source_chunk_repo = SourceChunkRepository(session)
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
    session.add_all([supported_claim, mixed_claim, unsupported_claim])
    session.flush()

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
    session.add_all(
        [support_span, mixed_support_span, mixed_contradict_span, unsupported_contradict_span]
    )
    session.flush()

    session.add_all(
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
    session.commit()
    return task.id
