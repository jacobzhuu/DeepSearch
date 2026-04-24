from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from services.orchestrator.app.api.schemas.reporting import (
    GenerateReportResponse,
    ReportResponse,
)
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.services.reporting import (
    ReportArtifactContentMismatchError,
    ReportArtifactNotFoundError,
    ReportArtifactObjectMissingError,
    ReportSynthesisService,
    create_report_synthesis_service,
)
from services.orchestrator.app.services.research_tasks import TaskNotFoundError
from services.orchestrator.app.settings import get_settings
from services.orchestrator.app.storage import SnapshotObjectStore, build_snapshot_object_store

router = APIRouter(prefix="/api/v1/research/tasks", tags=["reporting"])
SessionDep = Annotated[Session, Depends(get_db_session)]


def get_report_object_store() -> SnapshotObjectStore:
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


def get_report_synthesis_service(
    session: SessionDep,
    object_store: Annotated[SnapshotObjectStore, Depends(get_report_object_store)],
) -> ReportSynthesisService:
    settings = get_settings()
    return create_report_synthesis_service(
        session,
        object_store=object_store,
        report_storage_bucket=settings.report_storage_bucket,
    )


ServiceDep = Annotated[ReportSynthesisService, Depends(get_report_synthesis_service)]


@router.post(
    "/{task_id}/report",
    response_model=GenerateReportResponse,
    status_code=status.HTTP_200_OK,
)
def generate_task_report(
    task_id: UUID,
    service: ServiceDep,
) -> GenerateReportResponse:
    try:
        result = service.generate_markdown_report(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return GenerateReportResponse(
        task_id=result.task.id,
        report_artifact_id=result.artifact.id,
        version=result.artifact.version,
        format=result.artifact.format,
        title=result.title,
        storage_bucket=result.artifact.storage_bucket,
        storage_key=result.artifact.storage_key,
        created_at=result.artifact.created_at,
        supported_claims=result.supported_claims,
        mixed_claims=result.mixed_claims,
        unsupported_claims=result.unsupported_claims,
        draft_claims=result.draft_claims,
        markdown=result.markdown,
        reused_existing=result.reused_existing,
    )


@router.get("/{task_id}/report", response_model=ReportResponse)
def get_task_report(
    task_id: UUID,
    service: ServiceDep,
) -> ReportResponse:
    try:
        result = service.get_latest_markdown_report(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ReportArtifactNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ReportArtifactObjectMissingError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)
        ) from error
    except ReportArtifactContentMismatchError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)
        ) from error

    return ReportResponse(
        task_id=result.task.id,
        report_artifact_id=result.artifact.id,
        version=result.artifact.version,
        format=result.artifact.format,
        title=result.title,
        storage_bucket=result.artifact.storage_bucket,
        storage_key=result.artifact.storage_key,
        created_at=result.artifact.created_at,
        markdown=result.markdown,
    )
