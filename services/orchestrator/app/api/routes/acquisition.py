from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from packages.db.repositories import ResearchTaskRepository, TaskEventRepository
from services.orchestrator.app.acquisition import HttpAcquisitionClient, SmokeAcquisitionClient
from services.orchestrator.app.acquisition.playwright_backend import (
    build_playwright_browser_fetch_backend,
)
from services.orchestrator.app.acquisition.response_cap_policy import (
    parse_trusted_docs_domain_allowlist,
)
from services.orchestrator.app.api.schemas.acquisition import (
    AcquisitionEntryResponse,
    ContentSnapshotListResponse,
    ContentSnapshotResponse,
    FetchAttemptListResponse,
    FetchAttemptResponse,
    FetchJobListResponse,
    FetchJobResponse,
    RunAcquisitionRequest,
    RunAcquisitionResponse,
)
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.services.acquisition import (
    AcquisitionConflictError,
    AcquisitionService,
    CandidateUrlNotFoundError,
    create_acquisition_service,
)
from services.orchestrator.app.services.acquisition_diagnostics import (
    compute_acquisition_funnel_diagnostics,
)
from services.orchestrator.app.services.research_tasks import TaskNotFoundError
from services.orchestrator.app.settings import get_settings
from services.orchestrator.app.storage import SnapshotObjectStore, build_snapshot_object_store

router = APIRouter(prefix="/api/v1/research/tasks", tags=["acquisition"])
SessionDep = Annotated[Session, Depends(get_db_session)]


def get_http_acquisition_client() -> HttpAcquisitionClient:
    settings = get_settings()
    if settings.search_provider.strip().lower() == "smoke":
        return SmokeAcquisitionClient()
    trusted_domains = parse_trusted_docs_domain_allowlist(settings.acquisition_trusted_docs_domains)
    trusted_max = settings.acquisition_trusted_docs_max_response_bytes
    if trusted_max is not None and trusted_max <= settings.acquisition_max_response_bytes:
        trusted_max = None
    return HttpAcquisitionClient(
        timeout_seconds=settings.acquisition_timeout_seconds,
        max_redirects=settings.acquisition_max_redirects,
        max_response_bytes=settings.acquisition_max_response_bytes,
        user_agent=settings.acquisition_user_agent,
        accept_language=settings.acquisition_accept_language,
        trust_env_proxy=settings.acquisition_trust_env_proxy,
        trusted_docs_domains=trusted_domains,
        trusted_docs_max_response_bytes=trusted_max,
    )


def get_snapshot_object_store() -> SnapshotObjectStore:
    settings = get_settings()
    return build_snapshot_object_store(
        backend=settings.snapshot_storage_backend,
        root_directory=settings.snapshot_storage_root,
        minio_endpoint=settings.minio_endpoint,
        minio_access_key=settings.minio_access_key,
        minio_secret_key=settings.minio_secret_key,
        minio_secure=settings.minio_secure,
        minio_region=settings.minio_region,
        required_buckets=[settings.snapshot_storage_bucket, settings.report_storage_bucket],
    )


def get_acquisition_service(
    session: SessionDep,
    http_client: Annotated[HttpAcquisitionClient, Depends(get_http_acquisition_client)],
    snapshot_object_store: Annotated[SnapshotObjectStore, Depends(get_snapshot_object_store)],
) -> AcquisitionService:
    settings = get_settings()
    browser_backend = build_playwright_browser_fetch_backend(settings, http_client)
    return create_acquisition_service(
        session,
        http_client=http_client,
        snapshot_object_store=snapshot_object_store,
        snapshot_bucket=settings.snapshot_storage_bucket,
        max_candidates_per_request=settings.acquisition_max_candidates_per_request,
        max_must_fetch_per_round=settings.research_acquisition_max_must_fetch_per_round,
        browser_fetch_backend_impl=browser_backend,
        browser_fetch_backend_setting=settings.browser_fetch_backend,
        task_event_repository=TaskEventRepository(session),
        min_successful_authoritative_snapshots=(
            settings.acquisition_min_successful_authoritative_snapshots
        ),
        defer_success_target_for_high_priority=(
            settings.acquisition_defer_success_target_for_high_priority
        ),
    )


ServiceDep = Annotated[AcquisitionService, Depends(get_acquisition_service)]


@router.get("/{task_id}/acquisition/funnel-metrics")
def get_acquisition_funnel_metrics(task_id: UUID, session: SessionDep) -> dict[str, object]:
    """Return ledger-derived funnel diagnostics for search → fetch → parse → chunk."""
    task = ResearchTaskRepository(session).get(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="research task not found")
    settings = get_settings()
    settings_snapshot = {
        "acquisition_max_candidates_per_request": settings.acquisition_max_candidates_per_request,
        "acquisition_target_successful_snapshots": settings.acquisition_target_successful_snapshots,
        "acquisition_max_response_bytes": settings.acquisition_max_response_bytes,
        "acquisition_trusted_docs_domains": settings.acquisition_trusted_docs_domains,
        "acquisition_trusted_docs_max_response_bytes": (
            settings.acquisition_trusted_docs_max_response_bytes
        ),
        "acquisition_min_successful_authoritative_snapshots": (
            settings.acquisition_min_successful_authoritative_snapshots
        ),
        "acquisition_defer_success_target_for_high_priority": (
            settings.acquisition_defer_success_target_for_high_priority
        ),
        "research_acquisition_max_must_fetch_per_round": (
            settings.research_acquisition_max_must_fetch_per_round
        ),
        "research_parse_limit": settings.research_parse_limit,
        "research_parse_drain_enabled": settings.research_parse_drain_enabled,
        "research_parse_max_batches": settings.research_parse_max_batches,
        "research_parse_target_documents": settings.research_parse_target_documents,
        "research_parse_drain_max_seconds": settings.research_parse_drain_max_seconds,
    }
    return compute_acquisition_funnel_diagnostics(
        session,
        task_id,
        task_query=task.query,
        settings_snapshot=settings_snapshot,
    )


@router.post(
    "/{task_id}/fetches",
    response_model=RunAcquisitionResponse,
    status_code=status.HTTP_200_OK,
)
def run_task_acquisition(
    task_id: UUID,
    service: ServiceDep,
    request: Annotated[RunAcquisitionRequest | None, Body()] = None,
) -> RunAcquisitionResponse:
    acquisition_request = request or RunAcquisitionRequest()
    try:
        result = service.acquire_candidates(
            task_id,
            candidate_url_ids=acquisition_request.candidate_url_ids,
            limit=acquisition_request.limit,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except CandidateUrlNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except AcquisitionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    return RunAcquisitionResponse(
        task_id=result.task.id,
        created=result.created,
        skipped_existing=result.skipped_existing,
        succeeded=result.succeeded,
        failed=result.failed,
        entries=[
            AcquisitionEntryResponse(
                candidate_url_id=entry.candidate_url.id,
                canonical_url=entry.candidate_url.canonical_url,
                fetch_job_id=entry.fetch_job.id,
                fetch_attempt_id=(
                    entry.fetch_attempt.id if entry.fetch_attempt is not None else None
                ),
                snapshot_id=(
                    entry.content_snapshot.id if entry.content_snapshot is not None else None
                ),
                status=entry.fetch_job.status,
                http_status=(
                    entry.fetch_attempt.http_status if entry.fetch_attempt is not None else None
                ),
                error_code=(
                    entry.fetch_attempt.error_code if entry.fetch_attempt is not None else None
                ),
                error_reason=(
                    _fetch_error_reason(entry.fetch_attempt.trace_json)
                    if entry.fetch_attempt is not None
                    else None
                ),
                skipped_existing=entry.skipped_existing,
            )
            for entry in result.entries
        ],
    )


@router.get("/{task_id}/fetch-jobs", response_model=FetchJobListResponse)
def list_task_fetch_jobs(
    task_id: UUID,
    service: ServiceDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> FetchJobListResponse:
    try:
        fetch_jobs = service.list_fetch_jobs(task_id, status=status_filter, limit=limit)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return FetchJobListResponse(
        task_id=task_id,
        fetch_jobs=[
            FetchJobResponse(
                fetch_job_id=entry.fetch_job.id,
                candidate_url_id=entry.fetch_job.candidate_url_id,
                canonical_url=entry.fetch_job.candidate_url.canonical_url,
                mode=entry.fetch_job.mode,
                status=entry.fetch_job.status,
                scheduled_at=entry.fetch_job.scheduled_at,
                latest_attempt_id=(
                    entry.latest_attempt.id if entry.latest_attempt is not None else None
                ),
                latest_attempt_no=(
                    entry.latest_attempt.attempt_no if entry.latest_attempt is not None else None
                ),
                latest_http_status=(
                    entry.latest_attempt.http_status if entry.latest_attempt is not None else None
                ),
                latest_error_code=(
                    entry.latest_attempt.error_code if entry.latest_attempt is not None else None
                ),
                latest_error_reason=(
                    _fetch_error_reason(entry.latest_attempt.trace_json)
                    if entry.latest_attempt is not None
                    else None
                ),
                snapshot_id=(
                    entry.content_snapshot.id if entry.content_snapshot is not None else None
                ),
            )
            for entry in fetch_jobs
        ],
    )


def _fetch_error_reason(trace: dict[str, object] | None) -> str | None:
    if not isinstance(trace, dict):
        return None
    for key in ("message", "reason"):
        value = trace.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    storage_error = trace.get("storage_error")
    if isinstance(storage_error, dict):
        value = storage_error.get("message")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@router.get("/{task_id}/fetch-attempts", response_model=FetchAttemptListResponse)
def list_task_fetch_attempts(
    task_id: UUID,
    service: ServiceDep,
    fetch_job_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> FetchAttemptListResponse:
    try:
        fetch_attempts = service.list_fetch_attempts(
            task_id,
            fetch_job_id=fetch_job_id,
            limit=limit,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return FetchAttemptListResponse(
        task_id=task_id,
        fetch_attempts=[
            FetchAttemptResponse(
                fetch_attempt_id=fetch_attempt.id,
                fetch_job_id=fetch_attempt.fetch_job_id,
                attempt_no=fetch_attempt.attempt_no,
                http_status=fetch_attempt.http_status,
                error_code=fetch_attempt.error_code,
                started_at=fetch_attempt.started_at,
                finished_at=fetch_attempt.finished_at,
                trace=fetch_attempt.trace_json,
            )
            for fetch_attempt in fetch_attempts
        ],
    )


@router.get("/{task_id}/content-snapshots", response_model=ContentSnapshotListResponse)
def list_task_content_snapshots(
    task_id: UUID,
    service: ServiceDep,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> ContentSnapshotListResponse:
    try:
        content_snapshots = service.list_content_snapshots(task_id, limit=limit)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return ContentSnapshotListResponse(
        task_id=task_id,
        content_snapshots=[
            ContentSnapshotResponse(
                snapshot_id=content_snapshot.id,
                fetch_attempt_id=content_snapshot.fetch_attempt_id,
                storage_bucket=content_snapshot.storage_bucket,
                storage_key=content_snapshot.storage_key,
                content_hash=content_snapshot.content_hash,
                mime_type=content_snapshot.mime_type,
                bytes=content_snapshot.bytes,
                extracted_title=content_snapshot.extracted_title,
                fetched_at=content_snapshot.fetched_at,
            )
            for content_snapshot in content_snapshots
        ],
    )
