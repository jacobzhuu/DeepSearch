from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from packages.db.models import CandidateUrl, ResearchRun, ResearchTask, SearchQuery
from packages.db.repositories import (
    CandidateUrlRepository,
    ContentSnapshotRepository,
    FetchAttemptRepository,
    FetchJobRepository,
    ResearchRunRepository,
    SearchQueryRepository,
)
from services.orchestrator.app.acquisition import HttpAcquisitionClient
from services.orchestrator.app.services.acquisition import (
    FETCH_MODE_HTTP,
    FETCH_STATUS_FAILED,
    FETCH_STATUS_SUCCEEDED,
    AcquisitionConflictError,
    AcquisitionService,
    create_acquisition_service,
)
from services.orchestrator.app.services.research_tasks import create_research_task_service
from services.orchestrator.app.storage import FilesystemSnapshotObjectStore


class StaticResolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        del host, port
        return self.addresses


def _create_acquisition_service(
    db_session: Session,
    *,
    transport: httpx.BaseTransport,
    snapshot_root: Path,
    resolver: StaticResolver,
) -> AcquisitionService:
    http_client = HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=3,
        max_response_bytes=1024,
        user_agent="deepresearch-tests/1.0",
        resolver=resolver,
        client=httpx.Client(transport=transport, trust_env=False),
    )
    return create_acquisition_service(
        db_session,
        http_client=http_client,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(snapshot_root)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=5,
    )


def _seed_candidate(
    db_session: Session,
    *,
    query: str = "Acquisition service task",
    canonical_url: str = "https://example.com/report",
) -> tuple[ResearchTask, CandidateUrl]:
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
            domain="example.com" if "example.com" in canonical_url else "blocked.example",
            title="Example source",
            rank=1,
            selected=False,
            metadata_json={},
        )
    )
    db_session.commit()
    return task, candidate_url


def test_acquisition_service_creates_job_attempt_and_snapshot_for_success(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, candidate_url = _seed_candidate(db_session)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html>service success</html>",
            request=request,
        )

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    result = service.acquire_candidates(task.id, candidate_url_ids=[candidate_url.id], limit=1)
    fetch_jobs = FetchJobRepository(db_session).list_for_task(task.id)
    fetch_attempts = FetchAttemptRepository(db_session).list_for_task(task.id)
    content_snapshots = ContentSnapshotRepository(db_session).list_for_task(task.id)

    assert result.created == 1
    assert result.succeeded == 1
    assert result.failed == 0
    assert len(fetch_jobs) == 1
    assert fetch_jobs[0].mode == FETCH_MODE_HTTP
    assert fetch_jobs[0].status == FETCH_STATUS_SUCCEEDED
    assert len(fetch_attempts) == 1
    assert fetch_attempts[0].http_status == 200
    assert len(content_snapshots) == 1
    assert content_snapshots[0].mime_type == "text/html"
    assert content_snapshots[0].content_hash.startswith("sha256:")
    assert (tmp_path / "snapshots" / Path(content_snapshots[0].storage_key)).exists()


def test_acquisition_service_skips_existing_jobs_and_advances_to_next_candidate(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, first_candidate = _seed_candidate(
        db_session,
        query="First candidate task",
        canonical_url="https://example.com/first",
    )
    second_candidate = CandidateUrlRepository(db_session).add(
        CandidateUrl(
            task_id=task.id,
            search_query_id=first_candidate.search_query_id,
            original_url="https://example.com/second",
            canonical_url="https://example.com/second",
            domain="example.com",
            title="Second example source",
            rank=2,
            selected=False,
            metadata_json={},
        )
    )
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=f"body:{request.url.path}".encode(),
            request=request,
        )

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    first_result = service.acquire_candidates(
        task.id,
        candidate_url_ids=[first_candidate.id],
        limit=1,
    )
    second_result = service.acquire_candidates(task.id, candidate_url_ids=None, limit=1)

    assert first_result.created == 1
    assert second_result.created == 1
    assert second_result.skipped_existing == 1
    assert second_result.entries[-1].candidate_url.id == second_candidate.id


def test_acquisition_service_records_failed_attempt_without_snapshot_for_blocked_target(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, candidate_url = _seed_candidate(
        db_session,
        canonical_url="http://blocked.example/internal",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("blocked target should not trigger a request")

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("127.0.0.1"),
    )

    result = service.acquire_candidates(task.id, candidate_url_ids=[candidate_url.id], limit=1)
    fetch_job = FetchJobRepository(db_session).list_for_task(task.id)[0]
    fetch_attempt = FetchAttemptRepository(db_session).list_for_task(task.id)[0]

    assert result.created == 1
    assert result.failed == 1
    assert fetch_job.status == FETCH_STATUS_FAILED
    assert fetch_attempt.error_code == "target_blocked"
    assert ContentSnapshotRepository(db_session).list_for_task(task.id) == []


def test_acquisition_service_rejects_paused_task(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, candidate_url = _seed_candidate(db_session)
    create_research_task_service(db_session).pause_task(task.id)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok", request=request)

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    with pytest.raises(AcquisitionConflictError):
        service.acquire_candidates(task.id, candidate_url_ids=[candidate_url.id], limit=1)
