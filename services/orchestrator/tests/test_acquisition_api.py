from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from packages.db.models import CandidateUrl, ResearchRun, ResearchTask, SearchQuery
from packages.db.repositories import (
    CandidateUrlRepository,
    ResearchRunRepository,
    SearchQueryRepository,
)
from services.orchestrator.app.acquisition import HttpAcquisitionClient
from services.orchestrator.app.api.routes.acquisition import (
    get_http_acquisition_client,
    get_snapshot_object_store,
)
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.main import create_app
from services.orchestrator.app.services.research_tasks import create_research_task_service
from services.orchestrator.app.storage import FilesystemSnapshotObjectStore


class StaticResolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        del host, port
        return self.addresses


def _seed_candidate(
    session: Session,
    *,
    query: str,
    canonical_url: str,
) -> tuple[ResearchTask, CandidateUrl]:
    task = create_research_task_service(session).create_task(query=query, constraints={})
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
            query_text=query,
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
            original_url=canonical_url,
            canonical_url=canonical_url,
            domain="example.com",
            title="Example source",
            rank=1,
            selected=False,
            metadata_json={},
        )
    )
    session.commit()
    return task, candidate_url


def _build_http_client() -> HttpAcquisitionClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"api-body",
            request=request,
        )

    return HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=3,
        max_response_bytes=1024,
        user_agent="deepresearch-tests/1.0",
        resolver=StaticResolver("93.184.216.34"),
        client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False),
    )


def _build_client(
    session_factory: sessionmaker[Session],
    snapshot_root: Path,
) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_db_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_http_acquisition_client] = _build_http_client
    app.dependency_overrides[get_snapshot_object_store] = lambda: FilesystemSnapshotObjectStore(
        root_directory=str(snapshot_root)
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_acquisition_endpoints_create_fetch_ledgers(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        task, candidate_url = _seed_candidate(
            session,
            query="API acquisition task",
            canonical_url="https://example.com/api",
        )
        task_id = str(task.id)
        candidate_url_id = str(candidate_url.id)

    client_generator = _build_client(session_factory, tmp_path)
    client = next(client_generator)
    try:
        run_response = client.post(
            f"/api/v1/research/tasks/{task_id}/fetches",
            json={"candidate_url_ids": [candidate_url_id], "limit": 1},
        )
        fetch_jobs_response = client.get(f"/api/v1/research/tasks/{task_id}/fetch-jobs")
        fetch_attempts_response = client.get(f"/api/v1/research/tasks/{task_id}/fetch-attempts")
        content_snapshots_response = client.get(
            f"/api/v1/research/tasks/{task_id}/content-snapshots"
        )

        assert run_response.status_code == 200
        assert run_response.json()["created"] == 1
        assert run_response.json()["succeeded"] == 1
        assert run_response.json()["entries"][0]["status"] == "SUCCEEDED"
        assert fetch_jobs_response.status_code == 200
        assert fetch_jobs_response.json()["fetch_jobs"][0]["status"] == "SUCCEEDED"
        assert fetch_attempts_response.status_code == 200
        assert fetch_attempts_response.json()["fetch_attempts"][0]["http_status"] == 200
        assert content_snapshots_response.status_code == 200
        snapshot = content_snapshots_response.json()["content_snapshots"][0]
        assert snapshot["mime_type"] == "text/plain"
        assert (tmp_path / "snapshots" / Path(snapshot["storage_key"])).exists()
    finally:
        client_generator.close()


def test_acquisition_endpoint_rejects_paused_task(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        task, candidate_url = _seed_candidate(
            session,
            query="Paused acquisition task",
            canonical_url="https://example.com/paused",
        )
        create_research_task_service(session).pause_task(task.id)
        task_id = str(task.id)
        candidate_url_id = str(candidate_url.id)

    client_generator = _build_client(session_factory, tmp_path)
    client = next(client_generator)
    try:
        run_response = client.post(
            f"/api/v1/research/tasks/{task_id}/fetches",
            json={"candidate_url_ids": [candidate_url_id]},
        )

        assert run_response.status_code == 409
        assert "cannot acquire candidates" in run_response.json()["detail"]
    finally:
        client_generator.close()
