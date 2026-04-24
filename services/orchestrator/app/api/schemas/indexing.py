from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RunIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_chunk_ids: list[UUID] | None = None
    limit: int | None = Field(default=None, ge=1, le=100)


class IndexedChunkResponse(BaseModel):
    task_id: UUID
    source_document_id: UUID
    source_chunk_id: UUID
    canonical_url: str
    domain: str
    chunk_no: int
    text: str
    metadata: dict[str, Any]
    score: float | None = None


class RunIndexResponse(BaseModel):
    task_id: UUID
    indexed_count: int
    indexed_chunks: list[IndexedChunkResponse]


class IndexedChunkListResponse(BaseModel):
    task_id: UUID
    total: int
    offset: int
    limit: int
    indexed_chunks: list[IndexedChunkResponse]


class RetrievalResponse(BaseModel):
    task_id: UUID
    query: str
    total: int
    offset: int
    limit: int
    hits: list[IndexedChunkResponse]
