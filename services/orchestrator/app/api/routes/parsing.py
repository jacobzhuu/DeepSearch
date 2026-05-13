from __future__ import annotations

from typing import Annotated, Any
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
        skipped_static_html_hold=result.skipped_static_html_hold,
        skipped_no_valid_chunks=result.skipped_no_valid_chunks,
        failed=result.failed,
        invalid_chunk_rejection_count=result.invalid_chunk_rejection_count,
        invalid_chunk_rejection_reason_distribution=(
            result.invalid_chunk_rejection_reason_distribution or {}
        ),
        snapshots_with_no_valid_chunks=result.snapshots_with_no_valid_chunks,
        parser_invalid_output_count=result.snapshots_with_no_valid_chunks,
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
                source_format=_str_metadata(entry.source_document, "source_format"),
                parser_status=_str_metadata(entry.source_document, "parser_status"),
                parser_kind=_str_metadata(entry.source_document, "parser_kind"),
                parser_warnings=_list_metadata(entry.source_document, "parser_warnings"),
                parser_failure_reason=_str_metadata(
                    entry.source_document,
                    "parser_failure_reason",
                ),
                mime_policy=_dict_metadata(entry.source_document, "mime_policy"),
                page_range=_int_list_metadata(entry.source_document, "page_range"),
                page_locator_reliable=_bool_metadata(
                    entry.source_document,
                    "page_locator_reliable",
                ),
                locator_fallback_reason=_str_metadata(
                    entry.source_document,
                    "locator_fallback_reason",
                ),
                slide_range=_int_list_metadata(entry.source_document, "slide_range"),
                sheet_names=_str_list_metadata(entry.source_document, "sheet_names"),
                cell_ranges=_str_list_metadata(entry.source_document, "cell_ranges"),
                updated_existing=entry.updated_existing,
                chunk_validation=entry.chunk_validation,
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
                content_hash=_source_content_hash(source_document),
                canonical_url=source_document.canonical_url,
                domain=source_document.domain,
                title=source_document.title,
                source_type=source_document.source_type,
                published_at=source_document.published_at,
                fetched_at=source_document.fetched_at,
                authority_score=source_document.authority_score,
                freshness_score=source_document.freshness_score,
                originality_score=source_document.originality_score,
                consistency_score=source_document.consistency_score,
                safety_score=source_document.safety_score,
                final_source_score=source_document.final_source_score,
                quality=_source_quality_payload(source_document),
                parser_metadata=_source_parser_metadata(source_document),
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
                content_hash=_source_content_hash(source_document),
                canonical_url=source_document.canonical_url,
                domain=source_document.domain,
                title=source_document.title,
                source_type=source_document.source_type,
                published_at=source_document.published_at,
                fetched_at=source_document.fetched_at,
                authority_score=source_document.authority_score,
                freshness_score=source_document.freshness_score,
                originality_score=source_document.originality_score,
                consistency_score=source_document.consistency_score,
                safety_score=source_document.safety_score,
                final_source_score=source_document.final_source_score,
                quality=_source_quality_payload(source_document),
                parser_metadata=_source_parser_metadata(source_document),
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


def _source_quality_payload(source_document: object) -> dict[str, object]:
    chunks = getattr(source_document, "chunks", []) or []
    first_chunk_metadata: dict[str, Any] = {}
    if chunks:
        first_chunk_metadata = getattr(chunks[0], "metadata_json", {}) or {}
    nested_quality = first_chunk_metadata.get("source_quality")
    if isinstance(nested_quality, dict):
        return dict(nested_quality)
    return {
        "final_score": getattr(source_document, "final_source_score", None),
        "authority_score": getattr(source_document, "authority_score", None),
        "freshness_score": getattr(source_document, "freshness_score", None),
        "relevance_score": getattr(source_document, "consistency_score", None),
        "information_density_score": getattr(source_document, "originality_score", None),
        "safety_score": getattr(source_document, "safety_score", None),
        "freshness_state": (
            "unknown" if getattr(source_document, "freshness_score", None) is None else "known"
        ),
    }


def _source_parser_metadata(source_document: object) -> dict[str, object]:
    chunks = getattr(source_document, "chunks", []) or []
    first_chunk_metadata: dict[str, Any] = {}
    if chunks:
        first_chunk_metadata = getattr(chunks[0], "metadata_json", {}) or {}
    keys = (
        "source_format",
        "parser_status",
        "parser_kind",
        "parser_warnings",
        "parser_failure_reason",
        "mime_policy",
        "page_range",
        "page_locator_reliable",
        "locator_fallback_reason",
        "slide_range",
        "sheet_names",
        "cell_ranges",
        "paragraph_range",
    )
    return {key: value for key in keys if (value := first_chunk_metadata.get(key)) is not None}


def _source_content_hash(source_document: object) -> str | None:
    content_snapshot = getattr(source_document, "content_snapshot", None)
    content_hash = getattr(content_snapshot, "content_hash", None)
    if isinstance(content_hash, str) and content_hash.strip():
        return content_hash
    return None


def _first_chunk_metadata(source_document: object | None, key: str) -> object | None:
    if source_document is None:
        return None
    chunks = getattr(source_document, "chunks", []) or []
    if not chunks:
        return None
    metadata = getattr(chunks[0], "metadata_json", {}) or {}
    return metadata.get(key)


def _dict_metadata(source_document: object | None, key: str) -> dict[str, Any] | None:
    value = _first_chunk_metadata(source_document, key)
    return dict(value) if isinstance(value, dict) else None


def _str_metadata(source_document: object | None, key: str) -> str | None:
    value = _first_chunk_metadata(source_document, key)
    return value if isinstance(value, str) else None


def _list_metadata(source_document: object | None, key: str) -> list[str] | None:
    return _str_list_metadata(source_document, key)


def _str_list_metadata(source_document: object | None, key: str) -> list[str] | None:
    value = _first_chunk_metadata(source_document, key)
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def _int_list_metadata(source_document: object | None, key: str) -> list[int] | None:
    value = _first_chunk_metadata(source_document, key)
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, int)]


def _bool_metadata(source_document: object | None, key: str) -> bool | None:
    value = _first_chunk_metadata(source_document, key)
    return value if isinstance(value, bool) else None
