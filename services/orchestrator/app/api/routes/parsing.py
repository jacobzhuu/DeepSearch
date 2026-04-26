from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from services.orchestrator.app.api.schemas.parsing import (
    ParseEntryResponse,
    RunParseRequest,
    RunParseResponse,
    SourceChunkListResponse,
    SourceChunkResponse,
    SourceDocumentListResponse,
    SourceDocumentResponse,
    SourceListResponse,
)
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.services.parsing import (
    ContentSnapshotNotFoundError,
    ParsingConflictError,
    ParsingService,
    create_parsing_service,
)
from services.orchestrator.app.services.research_tasks import TaskNotFoundError
from services.orchestrator.app.settings import get_settings
from services.orchestrator.app.storage import SnapshotObjectStore, build_snapshot_object_store

router = APIRouter(prefix="/api/v1/research/tasks", tags=["parsing"])
SessionDep = Annotated[Session, Depends(get_db_session)]


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


def get_parsing_service(
    session: SessionDep,
    snapshot_object_store: Annotated[SnapshotObjectStore, Depends(get_snapshot_object_store)],
) -> ParsingService:
    return create_parsing_service(
        session,
        snapshot_object_store=snapshot_object_store,
    )


ServiceDep = Annotated[ParsingService, Depends(get_parsing_service)]


@router.post(
    "/{task_id}/parse",
    response_model=RunParseResponse,
    status_code=status.HTTP_200_OK,
)
def parse_task_snapshots(
    task_id: UUID,
    service: ServiceDep,
    request: Annotated[RunParseRequest | None, Body()] = None,
) -> RunParseResponse:
    parse_request = request or RunParseRequest()
    try:
        result = service.parse_snapshots(
            task_id,
            content_snapshot_ids=parse_request.content_snapshot_ids,
            limit=parse_request.limit,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ContentSnapshotNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ParsingConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    return RunParseResponse(
        task_id=result.task_id,
        created=result.created,
        updated=result.updated,
        skipped_existing=result.skipped_existing,
        skipped_unsupported=result.skipped_unsupported,
        failed=result.failed,
        entries=[
            ParseEntryResponse(
                content_snapshot_id=entry.content_snapshot.id,
                source_document_id=(
                    entry.source_document.id if entry.source_document is not None else None
                ),
                canonical_url=entry.content_snapshot.fetch_attempt.fetch_job.candidate_url.canonical_url,
                mime_type=entry.content_snapshot.mime_type,
                content_type=entry.content_snapshot.mime_type,
                storage_bucket=entry.content_snapshot.storage_bucket,
                storage_key=entry.content_snapshot.storage_key,
                snapshot_bytes=entry.content_snapshot.bytes,
                body_length=entry.body_length,
                chunks_created=entry.chunks_created,
                status=entry.status,
                reason=entry.reason,
                decision=entry.decision,
                parser_error=entry.parser_error,
                updated_existing=entry.updated_existing,
            )
            for entry in result.entries
        ],
    )


@router.get("/{task_id}/source-documents", response_model=SourceDocumentListResponse)
def list_task_source_documents(
    task_id: UUID,
    service: ServiceDep,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> SourceDocumentListResponse:
    try:
        source_documents = service.list_source_documents(task_id, limit=limit)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return SourceDocumentListResponse(
        task_id=task_id,
        source_documents=[
            SourceDocumentResponse(
                source_document_id=source_document.id,
                content_snapshot_id=source_document.content_snapshot_id,
                canonical_url=source_document.canonical_url,
                domain=source_document.domain,
                title=source_document.title,
                source_type=source_document.source_type,
                published_at=source_document.published_at,
                fetched_at=source_document.fetched_at,
            )
            for source_document in source_documents
        ],
    )


@router.get("/{task_id}/sources", response_model=SourceListResponse)
def list_task_sources(
    task_id: UUID,
    service: ServiceDep,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> SourceListResponse:
    try:
        source_documents = service.list_source_documents(task_id, limit=limit)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return SourceListResponse(
        task_id=task_id,
        sources=[
            SourceDocumentResponse(
                source_document_id=source_document.id,
                content_snapshot_id=source_document.content_snapshot_id,
                canonical_url=source_document.canonical_url,
                domain=source_document.domain,
                title=source_document.title,
                source_type=source_document.source_type,
                published_at=source_document.published_at,
                fetched_at=source_document.fetched_at,
            )
            for source_document in source_documents
        ],
    )


@router.get("/{task_id}/source-chunks", response_model=SourceChunkListResponse)
def list_task_source_chunks(
    task_id: UUID,
    service: ServiceDep,
    source_document_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> SourceChunkListResponse:
    try:
        source_chunks = service.list_source_chunks(
            task_id,
            source_document_id=source_document_id,
            limit=limit,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return SourceChunkListResponse(
        task_id=task_id,
        source_chunks=[
            SourceChunkResponse(
                source_chunk_id=source_chunk.id,
                source_document_id=source_chunk.source_document_id,
                content_snapshot_id=source_chunk.source_document.content_snapshot_id,
                chunk_no=source_chunk.chunk_no,
                token_count=source_chunk.token_count,
                text=source_chunk.text,
                metadata=source_chunk.metadata_json,
            )
            for source_chunk in source_chunks
        ],
    )
