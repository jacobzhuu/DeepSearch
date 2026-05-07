from __future__ import annotations

from collections.abc import Generator, Sequence
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from packages.db.repositories import ResearchTaskRepository, TaskEventRepository
from services.orchestrator.app.acquisition import HttpAcquisitionClient, HttpFetchResult
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
    SearchProviderError,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from services.orchestrator.app.services.debug_pipeline import (
    DebugPipelineInterrupted,
    collect_debug_pipeline_counts,
)
from services.orchestrator.app.services.pipeline_runtime import create_pipeline_runner
from services.orchestrator.app.services.pipeline_worker import ResearchPipelineWorker
from services.orchestrator.app.services.research_tasks import create_research_task_service
from services.orchestrator.app.settings import Settings, get_settings
from services.orchestrator.app.storage import FilesystemSnapshotObjectStore


@pytest.fixture(autouse=True)
def _disable_planner_for_baseline_pipeline_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("RESEARCH_PLANNER_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


class LangGraphMixedSearchProvider:
    name = "searxng"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, request: SearchRequest) -> SearchResponse:
        self.queries.append(request.query_text)
        results = (
            SearchResultItem(
                url="https://blockchain.news/news/langchain-unveils-langgraph-cloud",
                title="LangChain unveils LangGraph Cloud",
                snippet="A generic article mentioning LangGraph.",
                source_engine="fake-search",
                rank=1,
            ),
            SearchResultItem(
                url="https://docs.langchain.com/oss/python/langgraph/overview",
                title="LangGraph overview - Docs by LangChain",
                snippet="Official LangGraph documentation.",
                source_engine="fake-search",
                rank=2,
            ),
            SearchResultItem(
                url="https://reference.langchain.com/python/langgraph/",
                title="langgraph - LangChain Reference Docs",
                snippet="LangGraph reference docs.",
                source_engine="fake-search",
                rank=3,
            ),
            SearchResultItem(
                url="https://github.com/langchain-ai/langgraph",
                title="langchain-ai/langgraph",
                snippet="Upstream LangGraph repository.",
                source_engine="fake-search",
                rank=4,
            ),
            SearchResultItem(
                url="https://wfcoding.com/articles/practice/langgraph",
                title="LangGraph tutorial",
                snippet="Generic tutorial content.",
                source_engine="fake-search",
                rank=5,
            ),
        )
        return SearchResponse(
            provider=self.name,
            source_engines=("fake-search",),
            result_count=len(results),
            results=results[: request.limit],
            metadata={"test_provider": True},
        )


class GapFailingAfterInitialSearchProvider:
    name = "searxng"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, request: SearchRequest) -> SearchResponse:
        self.queries.append(request.query_text)
        if len(self.queries) > 1:
            raise SearchProviderError(
                reason="searxng_empty_results_with_unresponsive_engines",
                message=(
                    "SearXNG returned no results and reported unresponsive engines: "
                    "brave, duckduckgo."
                ),
                status_code=200,
                content_type="application/json",
                body_preview=None,
                unresponsive_engines=["brave", "duckduckgo"],
            )
        return SearchResponse(
            provider=self.name,
            source_engines=("fake-search",),
            result_count=1,
            results=(
                SearchResultItem(
                    url="https://docs.langchain.com/oss/python/langgraph/overview",
                    title="LangGraph overview - Docs by LangChain",
                    snippet="Official LangGraph definition source.",
                    source_engine="fake-search",
                    rank=1,
                ),
            ),
            metadata={"test_provider": True},
        )


class MainSearchUnresponsiveProvider:
    name = "searxng"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, request: SearchRequest) -> SearchResponse:
        self.queries.append(request.query_text)
        raise SearchProviderError(
            reason="searxng_empty_results_with_unresponsive_engines",
            message=(
                "SearXNG returned no results and reported unresponsive engines: "
                "brave, duckduckgo."
            ),
            status_code=200,
            content_type="application/json",
            body_preview=None,
            unresponsive_engines=["brave", "duckduckgo"],
        )


class FakeHttpAcquisitionClient(HttpAcquisitionClient):
    def __init__(self) -> None:
        super().__init__(
            timeout_seconds=1.0,
            max_redirects=0,
            max_response_bytes=1_048_576,
            user_agent="deepsearch-tests/1.0",
        )

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


class ShortSloganHttpAcquisitionClient:
    def fetch(self, url: str) -> HttpFetchResult:
        body = b"""
        <html>
          <head><title>SearXNG</title></head>
          <body>
            <main>
              <p>Welcome to SearXNG</p>
              <p>Search without being tracked.</p>
            </main>
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
            content_hash="sha256:short-slogan",
            trace={"test_fetch": True},
        )


class SearXNGExplanatoryHttpAcquisitionClient:
    def fetch(self, url: str) -> HttpFetchResult:
        body = b"""
        <html>
          <head><title>SearXNG overview</title></head>
          <body>
            <main>
              <p>SearXNG is a free and open-source metasearch engine.</p>
              <p>As a metasearch engine, SearXNG functions by sending queries to upstream
              search engines and returning results to the user.</p>
              <p>SearXNG removes private data from requests sent to search services.</p>
            </main>
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
            content_hash="sha256:searxng-explanatory",
            trace={"test_fetch": True},
        )


class LangGraphExplanatoryHttpAcquisitionClient:
    def fetch(self, url: str) -> HttpFetchResult:
        body = (
            "<html><head><title>LangGraph</title></head><body><main>"
            "<p>LangGraph is a low-level orchestration framework and runtime for "
            "building stateful agents.</p>"
            "<p>LangGraph works by representing application steps as graph nodes and "
            "routing state between those nodes until a workflow reaches a result.</p>"
            "<p>LangGraph provides durable execution, streaming, memory, checkpointing, "
            "and human-in-the-loop controls for long-running agents.</p>"
            f"<p>Fetched from {url}</p>"
            "</main></body></html>"
        ).encode()
        return HttpFetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            error_code=None,
            mime_type="text/html",
            content=body,
            content_hash=f"sha256:langgraph:{url}",
            trace={"test_fetch": True, "requested_url": url, "final_url": url},
        )


class LangGraphDefinitionOnlyHttpAcquisitionClient:
    def fetch(self, url: str) -> HttpFetchResult:
        body = (
            "<html><head><title>LangGraph definition</title></head><body><main>"
            "<p>LangGraph is a low-level orchestration framework and runtime for "
            "building stateful agents.</p>"
            "<p>LangGraph supports durable execution and human-in-the-loop controls "
            "for long-running agent applications.</p>"
            f"<p>Fetched from {url}</p>"
            "</main></body></html>"
        ).encode()
        return HttpFetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            error_code=None,
            mime_type="text/html",
            content=body,
            content_hash=f"sha256:langgraph-definition:{url}",
            trace={"test_fetch": True, "requested_url": url, "final_url": url},
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


def test_pipeline_run_endpoint_queues_task_for_worker(
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
        assert pipeline_payload["completed"] is False
        assert pipeline_payload["status"] == "QUEUED"
        assert pipeline_payload["running_mode"].endswith("+no-LLM")
        assert pipeline_payload["stages_completed"] == []
        assert pipeline_payload["counts"]["source_documents"] == 0
        assert pipeline_payload["dependencies"]["uses_worker_or_queue"] is True

        assert detail_response.status_code == 200
        assert detail_response.json()["status"] == "QUEUED"
        assert detail_response.json()["progress"]["current_state"] == "QUEUED"
        observability = detail_response.json()["progress"]["observability"]
        assert observability["dependencies"]["uses_worker_or_queue"] is True

        event_types = [event["event_type"] for event in events_response.json()["events"]]
        assert "pipeline.queued" in event_types
        with session_factory() as session:
            task = ResearchTaskRepository(session).get(UUID(task_id))
            assert task is not None
            ResearchTaskRepository(session).set_status(task, "PLANNED", ended_at=None)
            session.commit()
    finally:
        client_generator.close()


def test_run_endpoint_queue_is_consumed_by_host_local_worker(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(session_factory, tmp_path, index_backend)
    client = next(client_generator)
    worker_object_store = FilesystemSnapshotObjectStore(
        root_directory=str(tmp_path / "worker-objects")
    )
    worker_object_store.validate_configuration()
    worker_settings = Settings(
        search_provider="smoke",
        index_backend="local",
        snapshot_storage_backend="filesystem",
        snapshot_storage_root=str(tmp_path / "worker-objects"),
    )
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "Can the host-local worker finish the queued DeepSearch loop?"},
        )
        task_id = create_response.json()["task_id"]
        queue_response = client.post(f"/api/v1/research/tasks/{task_id}/run")

        worker = ResearchPipelineWorker(
            session_factory=session_factory,
            settings=worker_settings,
            runner_factory=lambda session: create_pipeline_runner(
                session,
                settings=worker_settings,
                search_provider=FakeSearchProvider(),
                http_client=FakeHttpAcquisitionClient(),
                snapshot_object_store=worker_object_store,
                index_backend=index_backend,
            ),
        )
        processed = worker.run_once()

        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")
        with session_factory() as session:
            counts = collect_debug_pipeline_counts(
                session,
                UUID(task_id),
                indexed_chunk_counter=lambda: index_backend.list_chunks(
                    task_id=UUID(task_id),
                    offset=0,
                    limit=100,
                ),
            )

        assert queue_response.status_code == 200
        assert queue_response.json()["status"] == "QUEUED"
        assert processed == 1
        assert detail_response.status_code == 200
        detail_payload = detail_response.json()
        assert detail_payload["status"] == "COMPLETED"
        observability = detail_payload["progress"]["observability"]
        pipeline_counts = observability["pipeline_counts"]
        assert observability["running_mode"].endswith("+no-LLM")
        assert observability["llm_assistance"]
        assert all(
            stage_diagnostics["used"] is False
            for stage_diagnostics in observability["llm_assistance"].values()
        )
        assert pipeline_counts["claims"] > 0
        assert pipeline_counts["report_artifacts"] > 0
        assert counts.source_documents > 0
        assert counts.source_chunks > 0
        assert counts.claims > 0
        assert counts.claim_evidence > 0
        assert counts.report_artifacts > 0
        for count_name in (
            "search_queries",
            "candidate_urls",
            "fetch_attempts",
            "content_snapshots",
            "source_documents",
            "source_chunks",
            "indexed_chunks",
            "claims",
            "claim_evidence",
            "report_artifacts",
        ):
            assert pipeline_counts[count_name] == getattr(counts, count_name)
        event_types = [event["event_type"] for event in events_response.json()["events"]]
        assert "pipeline.queued" in event_types
        assert "pipeline.started" in event_types
        assert "pipeline.completed" in event_types
    finally:
        client_generator.close()


def test_pipeline_boundary_refreshes_external_pause_before_next_stage(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    index_backend = InMemoryChunkIndexBackend()
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    settings = Settings(
        search_provider="smoke",
        index_backend="local",
        snapshot_storage_backend="filesystem",
        snapshot_storage_root=str(tmp_path / "objects"),
    )

    with session_factory() as session:
        service = create_research_task_service(session)
        task = service.create_task(query="Pause should be observed between stages", constraints={})
        ResearchTaskRepository(session).set_status(task, "SEARCHING", ended_at=None)
        session.commit()
        task_id = task.id

    with session_factory() as runner_session:
        runner = create_pipeline_runner(
            runner_session,
            settings=settings,
            search_provider=FakeSearchProvider(),
            http_client=FakeHttpAcquisitionClient(),
            snapshot_object_store=object_store,
            index_backend=index_backend,
        )
        cached_task = ResearchTaskRepository(runner_session).get(task_id)
        assert cached_task is not None
        assert cached_task.status == "SEARCHING"

        with session_factory() as control_session:
            control_task = ResearchTaskRepository(control_session).get(task_id)
            assert control_task is not None
            ResearchTaskRepository(control_session).set_status(
                control_task,
                "PAUSED",
                ended_at=None,
            )
            control_session.commit()

        with pytest.raises(DebugPipelineInterrupted) as interrupted:
            runner._ensure_task_can_continue(task_id)

        assert interrupted.value.status == "PAUSED"


def test_task_detail_observability_defaults_for_legacy_pipeline_events(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(session_factory, tmp_path, index_backend)
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "Legacy diagnostics should remain readable"},
        )
        task_id = UUID(create_response.json()["task_id"])
        with session_factory() as session:
            TaskEventRepository(session).record(
                task_id=task_id,
                event_type="pipeline.stage_completed",
                payload_json={
                    "stage": "REPORTING",
                    "result": {
                        "source_quality_summary": {"source_count": 1},
                    },
                },
            )
            session.commit()

        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")

        assert detail_response.status_code == 200
        observability = detail_response.json()["progress"]["observability"]
        assert observability["source_quality_summary"] == {"source_count": 1}
        assert observability["source_yield_summary"] == []
        assert observability["dropped_sources"] == []
        assert observability["slot_coverage_summary"] == []
        assert observability["evidence_yield_summary"] == {}
        assert observability["verification_summary"] == {}
    finally:
        client_generator.close()


def test_task_detail_fetch_observability_sums_initial_and_gap_acquisition_events(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(session_factory, tmp_path, index_backend)
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "What is LangGraph and how does it work?"},
        )
        task_id = UUID(create_response.json()["task_id"])
        with session_factory() as session:
            event_repository = TaskEventRepository(session)
            event_repository.record(
                task_id=task_id,
                event_type="debug.pipeline.stage_completed",
                payload_json={
                    "stage": "ACQUIRING",
                    "result": {"fetch_succeeded": 2, "fetch_failed": 1},
                },
            )
            event_repository.record(
                task_id=task_id,
                event_type="debug.pipeline.stage_completed",
                payload_json={
                    "stage": "RESEARCHING_MORE",
                    "result": {
                        "acquisition": {"fetch_succeeded": 0, "fetch_failed": 1},
                    },
                },
            )
            session.commit()

        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")

        assert detail_response.status_code == 200
        observability = detail_response.json()["progress"]["observability"]
        assert observability["fetch_succeeded"] == 2
        assert observability["fetch_failed"] == 2
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

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
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
            if event["event_type"] == "debug.pipeline.stage_completed"
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

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
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

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

        assert pipeline_response.status_code == 200
        payload = pipeline_response.json()
        assert payload["completed"] is False
        assert payload["status"] == "FAILED"
        assert payload["failure"]["stage"] == "ACQUIRING"
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
            if event["event_type"] == "debug.pipeline.failed"
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

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

        assert pipeline_response.status_code == 200
        pipeline_payload = pipeline_response.json()
        assert pipeline_payload["completed"] is False
        assert pipeline_payload["status"] == "FAILED"
        assert pipeline_payload["failure"]["stage"] == "SEARCHING"
        assert pipeline_payload["failure"]["reason"] == "pipeline_precondition_failed"
        assert "SEARCH_PROVIDER" in pipeline_payload["failure"]["next_action"]

        assert detail_response.json()["status"] == "FAILED"
        event_types = [event["event_type"] for event in events_response.json()["events"]]
        assert "debug.pipeline.failed" in event_types
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

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

        assert pipeline_response.status_code == 200
        payload = pipeline_response.json()
        assert payload["completed"] is False
        assert payload["status"] == "FAILED"
        assert payload["failure"]["stage"] == "PARSING"
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
            if event["event_type"] == "debug.pipeline.failed"
        ]
        assert failed_events
        assert (
            failed_events[-1]["payload"]["details"]["parse_decisions"][0]["decision"]
            == "skipped_empty"
        )
    finally:
        client_generator.close()


def test_pipeline_claim_drafting_failure_includes_candidate_diagnostics(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(
        session_factory,
        tmp_path,
        index_backend,
        http_client=ShortSloganHttpAcquisitionClient(),
    )
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "What is SearXNG and how does it work?"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

        assert pipeline_response.status_code == 200
        payload = pipeline_response.json()
        assert payload["completed"] is False
        assert payload["status"] == "FAILED"
        assert payload["failure"]["stage"] == "DRAFTING_CLAIMS"

        details = payload["failure"]["details"]
        assert details["total_chunks_seen"] >= 1
        assert details["candidate_sentences_count"] >= 2
        assert details["rejected_candidates_count"] >= 2
        assert details["top_rejected_candidates"]
        rejected_text = " ".join(
            item["candidate_text"] for item in details["top_rejected_candidates"]
        )
        assert "Welcome to SearXNG" in rejected_text
        assert "Search without being tracked." in rejected_text
        assert details["rejection_reason_distribution"]
        assert details["chunks"][0]["text_preview"]

        failed_events = [
            event
            for event in events_response.json()["events"]
            if event["event_type"] == "debug.pipeline.failed"
        ]
        assert failed_events
        assert failed_events[-1]["payload"]["details"]["candidate_sentences_count"] >= 2
    finally:
        client_generator.close()


def test_pipeline_planner_disabled_records_no_research_plan_event(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("RESEARCH_PLANNER_ENABLED", "false")
    get_settings.cache_clear()
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(session_factory, tmp_path, index_backend)
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "Planner disabled baseline"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

        assert pipeline_response.status_code == 200
        assert pipeline_response.json()["completed"] is True
        event_types = [event["event_type"] for event in events_response.json()["events"]]
        assert "research_plan.created" not in event_types
        assert "research_plan.failed" not in event_types
    finally:
        client_generator.close()
        monkeypatch.delenv("LLM_ENABLED", raising=False)
        monkeypatch.delenv("RESEARCH_PLANNER_ENABLED", raising=False)
        get_settings.cache_clear()


def test_pipeline_reuses_pre_run_research_plan_when_planner_disabled(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("RESEARCH_PLANNER_ENABLED", "false")
    monkeypatch.setenv("RESEARCH_PLANNER_MAX_SEARCH_QUERIES", "3")
    get_settings.cache_clear()
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(
        session_factory,
        tmp_path,
        index_backend,
        http_client=SearXNGExplanatoryHttpAcquisitionClient(),
    )
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "What is SearXNG and how does it work?"},
        )
        task_id = create_response.json()["task_id"]

        plan_response = client.post(f"/api/v1/research/tasks/{task_id}/plan")
        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")
        search_queries_response = client.get(f"/api/v1/research/tasks/{task_id}/search-queries")

        assert plan_response.status_code == 200
        assert plan_response.json()["planner_mode"] == "deterministic"
        assert pipeline_response.status_code == 200
        assert pipeline_response.json()["completed"] is True

        events = events_response.json()["events"]
        event_types = [event["event_type"] for event in events]
        assert event_types.count("research_plan.created") == 1
        assert event_types.index("research_plan.created") < event_types.index(
            "debug.pipeline.started"
        )

        persisted_queries = search_queries_response.json()["search_queries"]
        query_texts = [item["query_text"] for item in persisted_queries]
        assert "What is SearXNG and how does it work?" in query_texts
        assert "SearXNG official documentation" in query_texts
        assert len(query_texts) == len(set(query_texts))
    finally:
        client_generator.close()
        monkeypatch.delenv("LLM_ENABLED", raising=False)
        monkeypatch.delenv("RESEARCH_PLANNER_ENABLED", raising=False)
        monkeypatch.delenv("RESEARCH_PLANNER_MAX_SEARCH_QUERIES", raising=False)
        get_settings.cache_clear()


def test_pipeline_noop_planner_records_plan_and_uses_planner_queries(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "noop")
    monkeypatch.setenv("LLM_API_KEY", "test-api-key")
    monkeypatch.setenv("RESEARCH_PLANNER_ENABLED", "true")
    monkeypatch.setenv("RESEARCH_PLANNER_MAX_SEARCH_QUERIES", "3")
    get_settings.cache_clear()

    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(
        session_factory,
        tmp_path,
        index_backend,
        http_client=SearXNGExplanatoryHttpAcquisitionClient(),
    )
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "What is SearXNG and how does it work?"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")
        search_queries_response = client.get(f"/api/v1/research/tasks/{task_id}/search-queries")

        assert pipeline_response.status_code == 200
        assert pipeline_response.json()["completed"] is True
        assert pipeline_response.json()["dependencies"]["llm_mode"] == "planner-noop"

        events_payload = events_response.json()
        event_types = [event["event_type"] for event in events_payload["events"]]
        assert "research_plan.created" in event_types
        assert "test-api-key" not in str(events_payload)

        observability = detail_response.json()["progress"]["observability"]
        assert observability["planner_enabled"] is True
        assert observability["planner_status"] == "created"
        assert observability["planner_mode"] == "noop"
        assert observability["subquestion_count"] >= 3
        assert observability["search_query_count"] >= 5
        assert observability["research_plan"]["intent"] == "definition_how_it_works"
        assert observability["intent_classification"] == "overview_definition_intent"
        assert observability["extracted_entity"] == "SearXNG"

        persisted_queries = search_queries_response.json()["search_queries"]
        query_texts = [item["query_text"] for item in persisted_queries]
        assert "What is SearXNG and how does it work?" in query_texts
        assert "SearXNG official documentation" in query_texts
        assert "SearXNG about how does it work" in query_texts
        assert "SearXNG Wikipedia" in query_texts
        assert len(query_texts) == len(set(query_texts))
    finally:
        client_generator.close()
        monkeypatch.delenv("LLM_ENABLED", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("RESEARCH_PLANNER_ENABLED", raising=False)
        monkeypatch.delenv("RESEARCH_PLANNER_MAX_SEARCH_QUERIES", raising=False)
        get_settings.cache_clear()


def test_pipeline_planner_mode_attempts_langgraph_owned_sources_before_generic_articles(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "noop")
    monkeypatch.setenv("LLM_API_KEY", "test-api-key")
    monkeypatch.setenv("RESEARCH_PLANNER_ENABLED", "true")
    monkeypatch.setenv("RESEARCH_PLANNER_MAX_SEARCH_QUERIES", "5")
    get_settings.cache_clear()

    search_provider = LangGraphMixedSearchProvider()
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(
        session_factory,
        tmp_path,
        index_backend,
        search_provider=search_provider,
        http_client=LangGraphExplanatoryHttpAcquisitionClient(),
    )
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "What is LangGraph and how does it work?"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")

        assert pipeline_response.status_code == 200
        assert pipeline_response.json()["completed"] is True
        observability = detail_response.json()["progress"]["observability"]
        assert observability["planner_enabled"] is True
        final_queries = [
            item["query_text"] for item in observability["research_plan"]["final_search_queries"]
        ]
        assert "LangGraph site:docs.langchain.com how it works" in final_queries
        assert "LangGraph github langchain-ai langgraph" in final_queries

        attempted_urls = [source["canonical_url"] for source in observability["attempted_sources"]]
        official_guardrail_urls = [
            "https://docs.langchain.com/oss/python/langgraph/overview",
            "https://docs.langchain.com/oss/javascript/langgraph/overview",
            "https://reference.langchain.com/python/langgraph",
            "https://github.com/langchain-ai/langgraph",
        ]
        assert all(url in attempted_urls for url in official_guardrail_urls)
        generic_urls = [
            url for url in attempted_urls if "blockchain.news" in url or "wfcoding.com" in url
        ]
        assert not generic_urls
    finally:
        client_generator.close()
        monkeypatch.delenv("LLM_ENABLED", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("RESEARCH_PLANNER_ENABLED", raising=False)
        monkeypatch.delenv("RESEARCH_PLANNER_MAX_SEARCH_QUERIES", raising=False)
        get_settings.cache_clear()


def test_gap_search_provider_failure_continues_to_reporting_with_existing_evidence(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    search_provider = GapFailingAfterInitialSearchProvider()
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(
        session_factory,
        tmp_path,
        index_backend,
        search_provider=search_provider,
        http_client=LangGraphDefinitionOnlyHttpAcquisitionClient(),
    )
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "What is LangGraph and how does it work?"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

        assert pipeline_response.status_code == 200
        payload = pipeline_response.json()
        assert payload["completed"] is True
        assert payload["status"] == "COMPLETED"
        assert len(search_provider.queries) > 1

        observability = detail_response.json()["progress"]["observability"]
        assert "gap_search_unavailable" in observability["warnings"]
        assert detail_response.json()["status"] == "COMPLETED"

        gap_events = [
            event
            for event in events_response.json()["events"]
            if event["event_type"] == "debug.pipeline.stage_completed"
            and event["payload"].get("stage") == "RESEARCHING_MORE"
        ]
        assert gap_events
        gap_result = gap_events[-1]["payload"]["result"]
        assert gap_result["search"]["failed"] is True
        assert gap_result["search"]["reason"] == "searxng_empty_results_with_unresponsive_engines"
        assert gap_result["existing_evidence"]["usable_evidence"] is True
        assert gap_result["continuing_with_existing_evidence"] is True
    finally:
        client_generator.close()


def test_main_search_unresponsive_uses_langgraph_known_path_fallback_and_continues(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "noop")
    monkeypatch.setenv("LLM_API_KEY", "test-api-key")
    monkeypatch.setenv("RESEARCH_PLANNER_ENABLED", "true")
    monkeypatch.setenv("RESEARCH_PLANNER_MAX_SEARCH_QUERIES", "5")
    get_settings.cache_clear()

    search_provider = MainSearchUnresponsiveProvider()
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(
        session_factory,
        tmp_path,
        index_backend,
        search_provider=search_provider,
        http_client=LangGraphExplanatoryHttpAcquisitionClient(),
    )
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "What is LangGraph and how does it work?"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

        assert pipeline_response.status_code == 200
        payload = pipeline_response.json()
        assert payload["completed"] is True
        assert payload["status"] == "COMPLETED"
        assert len(search_provider.queries) == 1

        observability = detail_response.json()["progress"]["observability"]
        assert observability["planner_enabled"] is True
        assert observability["known_path_fallback"]["applied"] is True
        assert observability["known_path_fallback"]["candidate_count"] == 6
        assert (
            observability["search_queries"][0]["known_path_fallback"]["provider_error_reason"]
            == "searxng_empty_results_with_unresponsive_engines"
        )

        attempted_sources = observability["attempted_sources"]
        attempted_urls = [source["canonical_url"] for source in attempted_sources]
        assert "https://docs.langchain.com/oss/python/langgraph/overview" in attempted_urls
        assert "https://reference.langchain.com/python/langgraph" in attempted_urls
        assert "https://github.com/langchain-ai/langgraph" in attempted_urls
        assert all(
            source.get("candidate_source") == "known_path_fallback" for source in attempted_sources
        )
        assert all(
            source.get("fallback_reason") == "searxng_empty_results_with_unresponsive_engines"
            for source in attempted_sources
        )
        assert all(
            source.get("original_search_provider") == "searxng" for source in attempted_sources
        )

        searching_events = [
            event
            for event in events_response.json()["events"]
            if event["event_type"] == "debug.pipeline.stage_completed"
            and event["payload"].get("stage") == "SEARCHING"
        ]
        assert searching_events
        search_result = searching_events[-1]["payload"]["result"]
        assert search_result["known_path_fallback"]["applied"] is True
        assert search_result["known_path_fallback"]["candidate_count"] == 6
    finally:
        client_generator.close()
        monkeypatch.delenv("LLM_ENABLED", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("RESEARCH_PLANNER_ENABLED", raising=False)
        monkeypatch.delenv("RESEARCH_PLANNER_MAX_SEARCH_QUERIES", raising=False)
        get_settings.cache_clear()


def test_main_search_unresponsive_unknown_entity_still_fails_with_diagnostics(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    search_provider = MainSearchUnresponsiveProvider()
    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(
        session_factory,
        tmp_path,
        index_backend,
        search_provider=search_provider,
        http_client=LangGraphExplanatoryHttpAcquisitionClient(),
    )
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "What is UnknownFramework and how does it work?"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")

        assert pipeline_response.status_code == 200
        payload = pipeline_response.json()
        assert payload["completed"] is False
        assert payload["status"] == "FAILED"
        assert payload["failure"]["stage"] == "SEARCHING"
        assert payload["failure"]["reason"] == "searxng_empty_results_with_unresponsive_engines"
        details = payload["failure"]["details"]
        assert details["known_path_fallback_applied"] is False
        assert details["known_path_fallback_candidate_count"] == 0
        assert details["query_count_attempted"] == 1
        assert details["empty_query_count"] == 1
        assert details["provider_error_type"] == "SearchProviderError"
    finally:
        client_generator.close()


def test_pipeline_llm_planner_failure_falls_back_to_deterministic_plan(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_API_KEY", "test-api-key")
    monkeypatch.setenv("RESEARCH_PLANNER_ENABLED", "true")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    get_settings.cache_clear()

    index_backend = InMemoryChunkIndexBackend()
    client_generator = _build_client(session_factory, tmp_path, index_backend)
    client = next(client_generator)
    try:
        create_response = client.post(
            "/api/v1/research/tasks",
            json={"query": "Planner provider failure should not fail the pipeline"},
        )
        task_id = create_response.json()["task_id"]

        pipeline_response = client.post(f"/api/v1/research/tasks/{task_id}/debug/run-real-pipeline")
        detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
        events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")
        search_queries_response = client.get(f"/api/v1/research/tasks/{task_id}/search-queries")

        assert pipeline_response.status_code == 200
        assert pipeline_response.json()["completed"] is True

        events_payload = events_response.json()
        event_types = [event["event_type"] for event in events_payload["events"]]
        assert "research_plan.failed" in event_types
        assert "research_plan.created" in event_types
        assert "test-api-key" not in str(events_payload)

        observability = detail_response.json()["progress"]["observability"]
        assert observability["planner_status"] == "fallback"
        assert observability["plan_source"] == "pipeline_deterministic_fallback_after_llm_failure"
        assert (
            "LLM planner failed validation/provider call; deterministic fallback was used."
            in observability["warnings"]
        )
        assert (
            "No LLM planner is active; deterministic planner used." not in observability["warnings"]
        )

        persisted_queries = search_queries_response.json()["search_queries"]
        assert any(
            item["query_text"].startswith("Planner provider failure should not fail the pipeline")
            for item in persisted_queries
        )
        gap_queries = [
            item
            for item in persisted_queries[1:]
            if item["metadata"].get("expansion_metadata", {}).get("query_source") == "gap_analyzer"
        ]
        if gap_queries:
            assert any(
                item["metadata"].get("expansion_metadata", {}).get("gap_round_no") == 1
                for item in gap_queries
            )
        else:
            remaining_required_gaps = [
                slot
                for slot in observability.get("slot_coverage_summary", [])
                if slot.get("required") is True and slot.get("status") in {"missing", "weak"}
            ]
            assert remaining_required_gaps == []
        assert not any(
            item["metadata"].get("expansion_metadata", {}).get("gap_round_no") == 1
            for item in persisted_queries[1:]
            if item not in gap_queries
        )
    finally:
        client_generator.close()
        monkeypatch.delenv("LLM_ENABLED", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("RESEARCH_PLANNER_ENABLED", raising=False)
        get_settings.cache_clear()


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
