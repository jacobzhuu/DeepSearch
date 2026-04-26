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


class FiveCandidateSearchProvider:
    name = "searxng"

    def __init__(self, urls: Sequence[str] | None = None) -> None:
        self.urls = list(
            urls
            or (
                "https://example.com/source-1",
                "https://example.com/source-2",
                "https://example.com/source-3",
                "https://example.com/source-4",
                "https://example.com/source-5",
            )
        )

    def search(self, request: SearchRequest) -> SearchResponse:
        results = tuple(
            SearchResultItem(
                url=url,
                title=f"Candidate {index}",
                snippet=f"Candidate source {index}",
                source_engine="fake-search",
                rank=index,
            )
            for index, url in enumerate(self.urls, start=1)
        )
        return SearchResponse(
            provider=self.name,
            source_engines=("fake-search",),
            result_count=len(results),
            results=results[: request.limit],
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


class OneSuccessOneFailureHttpAcquisitionClient:
    def fetch(self, url: str) -> HttpFetchResult:
        if "developer.nvidia.com" in url:
            return HttpFetchResult(
                requested_url=url,
                final_url=url,
                http_status=403,
                error_code="http_error_status",
                mime_type=None,
                content=None,
                content_hash=None,
                trace={
                    "requested_url": url,
                    "final_url": url,
                    "message": "access denied",
                },
            )
        return FakeHttpAcquisitionClient().fetch(url)


class EmptyBodyHttpAcquisitionClient:
    def fetch(self, url: str) -> HttpFetchResult:
        return HttpFetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            error_code=None,
            mime_type="text/html",
            content=b"",
            content_hash="sha256:empty",
            trace={"test_fetch": True},
        )


class LaterSuccessHttpAcquisitionClient:
    def fetch(self, url: str) -> HttpFetchResult:
        if url.endswith("/source-4"):
            body = b"""
            <html>
              <head><title>Fallback source</title></head>
              <body>
                <p>SearXNG is a privacy-respecting metasearch engine.</p>
                <p>It sends queries to multiple search services and aggregates the results.</p>
              </body>
            </html>
            """
            return HttpFetchResult(
                requested_url=url,
                final_url=url,
                http_status=200,
                error_code=None,
                mime_type="text/html",
                content=body,
                content_hash="sha256:fallback",
                trace={"test_fetch": True, "requested_url": url, "final_url": url},
            )
        return HttpFetchResult(
            requested_url=url,
            final_url=None,
            http_status=None,
            error_code="network_error",
            mime_type=None,
            content=None,
            content_hash=None,
            trace={
                "requested_url": url,
                "final_url": url,
                "exception_type": "ReadTimeout",
                "message": "timed out",
            },
        )


class AllFailedHttpAcquisitionClient:
    def fetch(self, url: str) -> HttpFetchResult:
        return HttpFetchResult(
            requested_url=url,
            final_url=None,
            http_status=None,
            error_code="network_error",
            mime_type=None,
            content=None,
            content_hash=None,
            trace={
                "requested_url": url,
                "final_url": url,
                "exception_type": "ConnectError",
                "message": "network unreachable",
            },
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
        observability = detail_response.json()["progress"]["observability"]
        assert observability["search_result_count"] == 2
        assert len(observability["selected_sources"]) == 2
        assert observability["fetch_succeeded"] == 2
        assert observability["fetch_failed"] == 0

        event_types = [event["event_type"] for event in events_response.json()["events"]]
        assert "pipeline.started" in event_types
        assert "pipeline.stage_started" in event_types
        assert "pipeline.stage_completed" in event_types
        assert "pipeline.completed" in event_types
    finally:
        client_generator.close()


def test_pipeline_events_record_fetch_failure_details_and_low_source_warning(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(
        session_factory,
        tmp_path,
        index_backend,
        http_client=OneSuccessOneFailureHttpAcquisitionClient(),
    )
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "Observe weak fetch coverage"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/run")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

        assert pipeline_response.status_code == 200
        assert pipeline_response.json()["completed"] is True
        observability = detail_response.json()["progress"]["observability"]
        assert observability["fetch_succeeded"] == 1
        assert observability["fetch_failed"] == 1
        assert "fewer than 2 sources" in observability["warnings"][0]
        assert observability["failed_sources"][0]["http_status"] == 403
        assert observability["failed_sources"][0]["error_code"] == "http_error_status"
        assert observability["failed_sources"][0]["error_reason"] == "access denied"

        acquire_events = [
            event
            for event in events_response.json()["events"]
            if event["event_type"] == "pipeline.stage_completed"
            and event["payload"].get("stage") == "ACQUIRING"
        ]
        assert acquire_events
        result = acquire_events[-1]["payload"]["result"]
        assert result["failed_sources"][0]["canonical_url"].startswith("https://developer")
        assert result["failed_sources"][0]["http_status"] == 403
    finally:
        client_generator.close()


def test_pipeline_acquisition_continues_after_failures_and_runs_with_one_snapshot(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(
        session_factory,
        tmp_path,
        index_backend,
        search_provider=FiveCandidateSearchProvider(),
        http_client=LaterSuccessHttpAcquisitionClient(),
    )
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "What is SearXNG and how does it work?"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/run")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")

        assert pipeline_response.status_code == 200
        payload = pipeline_response.json()
        assert payload["completed"] is True
        assert payload["status"] == "COMPLETED"
        assert payload["counts"]["fetch_attempts"] == 5
        assert payload["counts"]["content_snapshots"] == 1

        observability = detail_response.json()["progress"]["observability"]
        assert observability["fetch_succeeded"] == 1
        assert observability["fetch_failed"] == 4
        assert len(observability["attempted_sources"]) == 5
        assert observability["unattempted_sources"] == []
        assert "fewer than 2 sources" in observability["warnings"][0]
    finally:
        client_generator.close()


def test_pipeline_acquisition_failure_records_attempt_diagnostics(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(
        session_factory,
        tmp_path,
        index_backend,
        search_provider=FiveCandidateSearchProvider(),
        http_client=AllFailedHttpAcquisitionClient(),
    )
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "All acquisition attempts should be observable"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/run")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

        assert pipeline_response.status_code == 200
        payload = pipeline_response.json()
        assert payload["completed"] is False
        assert payload["status"] == "FAILED"
        assert payload["failure"]["failed_stage"] == "ACQUIRING"
        assert payload["failure"]["reason"] == "pipeline_precondition_failed"

        details = payload["failure"]["details"]
        assert details["fetch_succeeded"] == 0
        assert details["fetch_failed"] == 5
        assert len(details["attempted_sources"]) == 5
        assert details["attempted_sources"][0]["attempted"] is True
        assert details["attempted_sources"][0]["error_code"] == "network_error"
        assert details["attempted_sources"][0]["trace"]["exception_type"] == "ConnectError"
        assert len(details["fetch_attempts_summary"]) == 5

        observability = detail_response.json()["progress"]["observability"]
        assert observability["fetch_succeeded"] == 0
        assert observability["fetch_failed"] == 5
        assert len(observability["failed_sources"]) == 5
        assert len(observability["attempted_sources"]) == 5
        assert observability["unattempted_sources"] == []

        failed_events = [
            event
            for event in events_response.json()["events"]
            if event["event_type"] == "pipeline.failed"
        ]
        assert failed_events
        failed_details = failed_events[-1]["payload"]["details"]
        assert failed_details["failed_sources"][0]["trace"]["message"] == "network unreachable"
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


def test_pipeline_parse_failure_records_snapshot_decisions(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(
        session_factory,
        tmp_path,
        index_backend,
        http_client=EmptyBodyHttpAcquisitionClient(),
    )
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "Parse diagnostics should explain empty snapshots"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/run")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

        assert pipeline_response.status_code == 200
        payload = pipeline_response.json()
        assert payload["completed"] is False
        assert payload["status"] == "FAILED"
        assert payload["failure"]["failed_stage"] == "PARSING"
        assert payload["failure"]["reason"] == "pipeline_precondition_failed"
        assert "snapshot_id=" in payload["failure"]["message"]
        assert "mime_type=text/html" in payload["failure"]["message"]
        assert "storage_key=" in payload["failure"]["message"]
        assert "body_length=0" in payload["failure"]["message"]
        assert "decision=skipped_empty" in payload["failure"]["message"]

        decisions = payload["failure"]["details"]["parse_decisions"]
        assert len(decisions) == 2
        assert decisions[0]["decision"] == "skipped_empty"
        assert decisions[0]["mime_type"] == "text/html"
        assert decisions[0]["body_length"] == 0
        assert decisions[0]["storage_bucket"] == "snapshots"

        observability = detail_response.json()["progress"]["observability"]
        assert observability["parse_decisions"][0]["decision"] == "skipped_empty"

        failed_events = [
            event
            for event in events_response.json()["events"]
            if event["event_type"] == "pipeline.failed"
        ]
        assert failed_events
        assert (
            failed_events[-1]["payload"]["details"]["parse_decisions"][0]["decision"]
            == "skipped_empty"
        )
    finally:
        client_generator.close()


def _build_client(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    index_backend: InMemoryChunkIndexBackend,
    search_provider: object | None = None,
    http_client: object | None = None,
) -> Generator[TestClient, None, None]:
    app = create_app()
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))

    def override_db_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_search_provider] = lambda: search_provider or FakeSearchProvider()
    app.dependency_overrides[get_http_acquisition_client] = (
        lambda: http_client or FakeHttpAcquisitionClient()
    )
    app.dependency_overrides[get_snapshot_object_store] = lambda: object_store
    app.dependency_overrides[get_report_object_store] = lambda: object_store
    app.dependency_overrides[get_chunk_index_backend] = lambda: index_backend
    app.dependency_overrides[get_claim_chunk_index_backend] = lambda: index_backend

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
