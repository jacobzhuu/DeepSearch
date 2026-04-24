from __future__ import annotations

from collections.abc import Generator, Sequence
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from services.orchestrator.app.acquisition import HttpFetchResult
from services.orchestrator.app.api.routes.acquisition import (
    get_http_acquisition_client,
    get_snapshot_object_store,
)
from services.orchestrator.app.api.routes.claims import get_claim_chunk_index_backend
from services.orchestrator.app.api.routes.indexing import get_chunk_index_backend
from services.orchestrator.app.api.routes.reporting import get_report_object_store
from services.orchestrator.app.api.routes.search_discovery import get_search_provider
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.indexing import (
    ChunkIndexDocument,
    IndexedChunkPage,
    IndexedChunkRecord,
)
from services.orchestrator.app.main import create_app
from services.orchestrator.app.search import (
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from services.orchestrator.app.storage import FilesystemSnapshotObjectStore


class FakeSearchProvider:
    name = "searxng"

    def search(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            source_engines=("fake-search",),
            result_count=2,
            results=(
                SearchResultItem(
                    url="https://example.com/nvidia-open-models",
                    title="NVIDIA open model update",
                    snippet="NVIDIA released open model ecosystem updates.",
                    source_engine="fake-search",
                    rank=1,
                ),
                SearchResultItem(
                    url="https://developer.nvidia.com/open-model-tools",
                    title="NVIDIA open model tools",
                    snippet="NVIDIA published tooling updates for open models.",
                    source_engine="fake-search",
                    rank=2,
                ),
            )[: request.limit],
            metadata={"test_provider": True},
        )


class EmptySearchProvider:
    name = "searxng"

    def search(self, request: SearchRequest) -> SearchResponse:
        del request
        return SearchResponse(
            provider=self.name,
            source_engines=("empty-search",),
            result_count=0,
            results=(),
            metadata={"test_provider": True},
        )


class FakeHttpAcquisitionClient:
    def fetch(self, url: str) -> HttpFetchResult:
        body = f"""
        <html>
          <head><title>{url}</title></head>
          <body>
            <p>NVIDIA released open model ecosystem updates for developers.</p>
            <p>The updates include tooling, examples, and integration guidance.</p>
          </body>
        </html>
        """.encode()
        return HttpFetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            error_code=None,
            mime_type="text/html",
            content=body,
            content_hash="sha256:test",
            trace={"test_fetch": True},
        )


class InMemoryChunkIndexBackend:
    def __init__(self) -> None:
        self.documents: dict[UUID, ChunkIndexDocument] = {}

    def validate_configuration(self) -> None:
        return None

    def ensure_index(self) -> None:
        return None

    def upsert_chunks(self, documents: Sequence[ChunkIndexDocument]) -> None:
        for document in documents:
            self.documents[document.source_chunk_id] = document

    def list_chunks(self, *, task_id: UUID, offset: int, limit: int) -> IndexedChunkPage:
        records = self._records_for_task(task_id)
        return IndexedChunkPage(total=len(records), hits=records[offset : offset + limit])

    def retrieve_chunks(
        self,
        *,
        task_id: UUID,
        query: str,
        offset: int,
        limit: int,
    ) -> IndexedChunkPage:
        del query
        records = self._records_for_task(task_id)
        return IndexedChunkPage(total=len(records), hits=records[offset : offset + limit])

    def _records_for_task(self, task_id: UUID) -> list[IndexedChunkRecord]:
        return [
            IndexedChunkRecord(
                task_id=document.task_id,
                source_document_id=document.source_document_id,
                source_chunk_id=document.source_chunk_id,
                canonical_url=document.canonical_url,
                domain=document.domain,
                chunk_no=document.chunk_no,
                text=document.text,
                metadata=document.metadata,
                score=1.0,
            )
            for document in self.documents.values()
            if document.task_id == task_id
        ]


def test_debug_real_pipeline_runs_existing_services_to_report(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(session_factory, tmp_path, index_backend)
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "近30天 NVIDIA 在开源模型生态上的关键发布与影响"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")
        report_response = client.get(f"/api/v1/research/tasks/{task_id}/report")

        assert pipeline_response.status_code == 200
        pipeline_payload = pipeline_response.json()
        assert pipeline_payload["completed"] is True
        assert pipeline_payload["status"] == "COMPLETED"
        assert pipeline_payload["counts"]["source_documents"] > 0
        assert pipeline_payload["counts"]["source_chunks"] > 0
        assert pipeline_payload["counts"]["indexed_chunks"] > 0
        assert pipeline_payload["counts"]["claims"] > 0
        assert pipeline_payload["counts"]["claim_evidence"] > 0
        assert pipeline_payload["counts"]["report_artifacts"] > 0

        assert detail_response.status_code == 200
        assert detail_response.json()["status"] == "COMPLETED"

        event_types = [event["event_type"] for event in events_response.json()["events"]]
        assert "debug.pipeline.started" in event_types
        assert "debug.pipeline.stage_started" in event_types
        assert "debug.pipeline.stage_completed" in event_types
        assert "debug.pipeline.completed" in event_types

        assert report_response.status_code == 200
        assert "## Executive Summary" in report_response.json()["markdown"]
    finally:
        client_generator.close()


def test_pipeline_run_endpoint_completes_task_and_records_product_events(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(session_factory, tmp_path, index_backend)
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "Can the frontend run the DeepSearch loop?"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/run")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

        assert pipeline_response.status_code == 200
        pipeline_payload = pipeline_response.json()
        assert pipeline_payload["completed"] is True
        assert pipeline_payload["status"] == "COMPLETED"
        assert pipeline_payload["running_mode"].endswith("+no-LLM")
        assert pipeline_payload["stages_completed"] == [
            "SEARCHING",
            "ACQUIRING",
            "PARSING",
            "INDEXING",
            "DRAFTING_CLAIMS",
            "VERIFYING",
            "REPORTING",
        ]
        assert pipeline_payload["counts"]["source_documents"] > 0
        assert pipeline_payload["counts"]["source_chunks"] > 0
        assert pipeline_payload["counts"]["claims"] > 0
        assert pipeline_payload["counts"]["claim_evidence"] > 0
        assert pipeline_payload["counts"]["report_artifacts"] > 0

        assert detail_response.status_code == 200
        assert detail_response.json()["status"] == "COMPLETED"
        assert detail_response.json()["progress"]["current_state"] == "COMPLETED"

        event_types = [event["event_type"] for event in events_response.json()["events"]]
        assert "pipeline.started" in event_types
        assert "pipeline.stage_started" in event_types
        assert "pipeline.stage_completed" in event_types
        assert "pipeline.completed" in event_types
    finally:
        client_generator.close()


def test_pipeline_run_endpoint_returns_structured_failure_and_failed_status(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(
        session_factory,
        tmp_path,
        index_backend,
        search_provider=EmptySearchProvider(),
    )
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "No search results should fail clearly"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/run")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

        assert pipeline_response.status_code == 200
        pipeline_payload = pipeline_response.json()
        assert pipeline_payload["completed"] is False
        assert pipeline_payload["status"] == "FAILED"
        assert pipeline_payload["failure"]["failed_stage"] == "SEARCHING"
        assert pipeline_payload["failure"]["reason"] == "pipeline_precondition_failed"
        assert "SEARCH_PROVIDER" in pipeline_payload["failure"]["next_action"]

        assert detail_response.json()["status"] == "FAILED"
        event_types = [event["event_type"] for event in events_response.json()["events"]]
        assert "pipeline.failed" in event_types
    finally:
        client_generator.close()


def _build_client(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    index_backend: InMemoryChunkIndexBackend,
    search_provider: object | None = None,
) -> Generator[TestClient, None, None]:
    app = create_app()
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))

    def override_db_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_search_provider] = lambda: search_provider or FakeSearchProvider()
    app.dependency_overrides[get_http_acquisition_client] = lambda: FakeHttpAcquisitionClient()
    app.dependency_overrides[get_snapshot_object_store] = lambda: object_store
    app.dependency_overrides[get_report_object_store] = lambda: object_store
    app.dependency_overrides[get_chunk_index_backend] = lambda: index_backend
    app.dependency_overrides[get_claim_chunk_index_backend] = lambda: index_backend

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
