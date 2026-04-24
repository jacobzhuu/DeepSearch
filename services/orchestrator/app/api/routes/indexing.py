from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from services.orchestrator.app.api.schemas.indexing import (
    IndexedChunkListResponse,
    IndexedChunkResponse,
    RetrievalResponse,
    RunIndexRequest,
    RunIndexResponse,
)
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.indexing import (
    ChunkIndexBackend,
    IndexBackendOperationError,
    build_chunk_index_backend,
)
from services.orchestrator.app.services.indexing import (
    IndexingConflictError,
    IndexingService,
    RetrievalQueryError,
    SourceChunkNotFoundError,
    create_indexing_service,
)
from services.orchestrator.app.services.research_tasks import TaskNotFoundError
from services.orchestrator.app.settings import get_settings

router = APIRouter(prefix="/api/v1/research/tasks", tags=["indexing"])
SessionDep = Annotated[Session, Depends(get_db_session)]


def get_chunk_index_backend() -> ChunkIndexBackend:
    settings = get_settings()
    return build_chunk_index_backend(
        backend=settings.index_backend,
        opensearch_base_url=settings.opensearch_base_url,
        opensearch_index_name=settings.opensearch_index_name,
        opensearch_username=settings.opensearch_username,
        opensearch_password=settings.opensearch_password,
        opensearch_verify_tls=settings.opensearch_verify_tls,
        opensearch_ca_bundle_path=settings.opensearch_ca_bundle_path,
        opensearch_timeout_seconds=settings.opensearch_timeout_seconds,
        opensearch_validate_connectivity=False,
    )


def get_indexing_service(
    session: SessionDep,
    index_backend: Annotated[ChunkIndexBackend, Depends(get_chunk_index_backend)],
) -> IndexingService:
    settings = get_settings()
    return create_indexing_service(
        session,
        index_backend=index_backend,
        indexing_max_chunks_per_request=settings.indexing_max_chunks_per_request,
        retrieval_max_results_per_request=settings.retrieval_max_results_per_request,
    )


ServiceDep = Annotated[IndexingService, Depends(get_indexing_service)]


@router.post(
    "/{task_id}/index",
    response_model=RunIndexResponse,
    status_code=status.HTTP_200_OK,
)
def index_task_source_chunks(
    task_id: UUID,
    service: ServiceDep,
    request: Annotated[RunIndexRequest | None, Body()] = None,
) -> RunIndexResponse:
    index_request = request or RunIndexRequest()
    try:
        result = service.index_source_chunks(
            task_id,
            source_chunk_ids=index_request.source_chunk_ids,
            limit=index_request.limit,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except SourceChunkNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except IndexingConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except IndexBackendOperationError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    return RunIndexResponse(
        task_id=result.task.id,
        indexed_count=len(result.indexed_chunks),
        indexed_chunks=[
            IndexedChunkResponse(
                task_id=result.task.id,
                source_document_id=source_chunk.source_document_id,
                source_chunk_id=source_chunk.id,
                canonical_url=source_chunk.source_document.canonical_url,
                domain=source_chunk.source_document.domain,
                chunk_no=source_chunk.chunk_no,
                text=source_chunk.text,
                metadata=source_chunk.metadata_json,
            )
            for source_chunk in result.indexed_chunks
        ],
    )


@router.get("/{task_id}/indexed-chunks", response_model=IndexedChunkListResponse)
def list_task_indexed_chunks(
    task_id: UUID,
    service: ServiceDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> IndexedChunkListResponse:
    try:
        result = service.list_indexed_chunks(task_id, offset=offset, limit=limit)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except IndexBackendOperationError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    effective_limit = _resolve_retrieval_limit(limit)
    return IndexedChunkListResponse(
        task_id=task_id,
        total=result.total,
        offset=offset,
        limit=effective_limit,
        indexed_chunks=[
            IndexedChunkResponse(
                task_id=item.task_id,
                source_document_id=item.source_document_id,
                source_chunk_id=item.source_chunk_id,
                canonical_url=item.canonical_url,
                domain=item.domain,
                chunk_no=item.chunk_no,
                text=item.text,
                metadata=item.metadata,
                score=item.score,
            )
            for item in result.hits
        ],
    )


@router.get("/{task_id}/retrieve", response_model=RetrievalResponse)
def retrieve_task_chunks(
    task_id: UUID,
    query: Annotated[str, Query(min_length=1)],
    service: ServiceDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> RetrievalResponse:
    try:
        result = service.retrieve_chunks(task_id, query=query, offset=offset, limit=limit)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RetrievalQueryError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except IndexBackendOperationError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    effective_limit = _resolve_retrieval_limit(limit)
    return RetrievalResponse(
        task_id=task_id,
        query=query.strip(),
        total=result.total,
        offset=offset,
        limit=effective_limit,
        hits=[
            IndexedChunkResponse(
                task_id=item.task_id,
                source_document_id=item.source_document_id,
                source_chunk_id=item.source_chunk_id,
                canonical_url=item.canonical_url,
                domain=item.domain,
                chunk_no=item.chunk_no,
                text=item.text,
                metadata=item.metadata,
                score=item.score,
            )
            for item in result.hits
        ],
    )


def _resolve_retrieval_limit(requested_limit: int | None) -> int:
    settings = get_settings()
    if requested_limit is None:
        return settings.retrieval_max_results_per_request
    return min(requested_limit, settings.retrieval_max_results_per_request)
