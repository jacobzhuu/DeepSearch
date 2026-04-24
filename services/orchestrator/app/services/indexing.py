from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from packages.db.models import ResearchTask, SourceChunk
from packages.db.repositories import ResearchTaskRepository, SourceChunkRepository
from services.orchestrator.app.indexing import (
    ChunkIndexBackend,
    ChunkIndexDocument,
    IndexedChunkPage,
)
from services.orchestrator.app.services.research_tasks import (
    PHASE2_ACTIVE_STATUS,
    TaskNotFoundError,
)


class IndexingConflictError(Exception):
    def __init__(self, task_id: UUID, current_status: str) -> None:
        super().__init__(
            f"cannot index source chunks for task {task_id} from status {current_status}"
        )
        self.task_id = task_id
        self.current_status = current_status


class SourceChunkNotFoundError(Exception):
    def __init__(self, task_id: UUID, source_chunk_id: UUID) -> None:
        super().__init__(f"source_chunk {source_chunk_id} was not found for task {task_id}")
        self.task_id = task_id
        self.source_chunk_id = source_chunk_id


class RetrievalQueryError(Exception):
    def __init__(self) -> None:
        super().__init__("retrieval query must not be blank")


@dataclass(frozen=True)
class IndexingBatchResult:
    task: ResearchTask
    indexed_chunks: list[SourceChunk]


class IndexingService:
    def __init__(
        self,
        session: Session,
        *,
        task_repository: ResearchTaskRepository,
        source_chunk_repository: SourceChunkRepository,
        index_backend: ChunkIndexBackend,
        indexing_max_chunks_per_request: int,
        retrieval_max_results_per_request: int,
        allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
    ) -> None:
        self.session = session
        self.task_repository = task_repository
        self.source_chunk_repository = source_chunk_repository
        self.index_backend = index_backend
        self.indexing_max_chunks_per_request = indexing_max_chunks_per_request
        self.retrieval_max_results_per_request = retrieval_max_results_per_request
        self.allowed_statuses = allowed_statuses

    def index_source_chunks(
        self,
        task_id: UUID,
        *,
        source_chunk_ids: list[UUID] | None,
        limit: int | None,
    ) -> IndexingBatchResult:
        task = self._get_task(task_id)
        if task.status not in self.allowed_statuses:
            raise IndexingConflictError(task.id, task.status)

        effective_limit = self.indexing_max_chunks_per_request
        if limit is not None:
            effective_limit = min(limit, self.indexing_max_chunks_per_request)

        selected_chunks = self._select_chunks(
            task.id,
            source_chunk_ids=source_chunk_ids,
            limit=effective_limit,
        )
        self.index_backend.upsert_chunks(
            [
                ChunkIndexDocument(
                    task_id=task.id,
                    source_document_id=source_chunk.source_document_id,
                    source_chunk_id=source_chunk.id,
                    canonical_url=source_chunk.source_document.canonical_url,
                    domain=source_chunk.source_document.domain,
                    chunk_no=source_chunk.chunk_no,
                    text=source_chunk.text,
                    metadata=dict(source_chunk.metadata_json),
                )
                for source_chunk in selected_chunks
            ]
        )
        return IndexingBatchResult(task=task, indexed_chunks=selected_chunks)

    def list_indexed_chunks(
        self,
        task_id: UUID,
        *,
        offset: int,
        limit: int | None,
    ) -> IndexedChunkPage:
        self._get_task(task_id)
        return self.index_backend.list_chunks(
            task_id=task_id,
            offset=offset,
            limit=self._normalize_retrieval_limit(limit),
        )

    def retrieve_chunks(
        self,
        task_id: UUID,
        *,
        query: str,
        offset: int,
        limit: int | None,
    ) -> IndexedChunkPage:
        self._get_task(task_id)
        normalized_query = query.strip()
        if not normalized_query:
            raise RetrievalQueryError()
        return self.index_backend.retrieve_chunks(
            task_id=task_id,
            query=normalized_query,
            offset=offset,
            limit=self._normalize_retrieval_limit(limit),
        )

    def _select_chunks(
        self,
        task_id: UUID,
        *,
        source_chunk_ids: list[UUID] | None,
        limit: int,
    ) -> list[SourceChunk]:
        if source_chunk_ids is None:
            return self.source_chunk_repository.list_for_task(task_id, limit=limit)

        selected = self.source_chunk_repository.list_by_ids_for_task(task_id, source_chunk_ids)
        selected_by_id = {item.id: item for item in selected}

        ordered_chunks: list[SourceChunk] = []
        seen_ids: set[UUID] = set()
        for source_chunk_id in source_chunk_ids:
            if source_chunk_id in seen_ids:
                continue
            if source_chunk_id not in selected_by_id:
                raise SourceChunkNotFoundError(task_id, source_chunk_id)
            ordered_chunks.append(selected_by_id[source_chunk_id])
            seen_ids.add(source_chunk_id)
            if len(ordered_chunks) >= limit:
                break
        return ordered_chunks

    def _normalize_retrieval_limit(self, limit: int | None) -> int:
        if limit is None:
            return self.retrieval_max_results_per_request
        return min(limit, self.retrieval_max_results_per_request)

    def _get_task(self, task_id: UUID) -> ResearchTask:
        task = self.task_repository.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task


def create_indexing_service(
    session: Session,
    *,
    index_backend: ChunkIndexBackend,
    indexing_max_chunks_per_request: int,
    retrieval_max_results_per_request: int,
    allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
) -> IndexingService:
    return IndexingService(
        session,
        task_repository=ResearchTaskRepository(session),
        source_chunk_repository=SourceChunkRepository(session),
        index_backend=index_backend,
        indexing_max_chunks_per_request=indexing_max_chunks_per_request,
        retrieval_max_results_per_request=retrieval_max_results_per_request,
        allowed_statuses=allowed_statuses,
    )
