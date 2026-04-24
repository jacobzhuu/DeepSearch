from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from services.orchestrator.app.parsing import ParseResultReason


class RunParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_snapshot_ids: list[UUID] | None = None
    limit: int | None = Field(default=None, ge=1, le=50)


class ParseEntryResponse(BaseModel):
    content_snapshot_id: UUID
    source_document_id: UUID | None
    canonical_url: str
    mime_type: str
    chunks_created: int
    status: str
    reason: ParseResultReason | None
    updated_existing: bool


class RunParseResponse(BaseModel):
    task_id: UUID
    created: int
    updated: int
    skipped_existing: int
    skipped_unsupported: int
    failed: int
    entries: list[ParseEntryResponse]


class SourceDocumentResponse(BaseModel):
    source_document_id: UUID
    content_snapshot_id: UUID | None
    canonical_url: str
    domain: str
    title: str | None
    source_type: str
    published_at: datetime | None
    fetched_at: datetime


class SourceDocumentListResponse(BaseModel):
    task_id: UUID
    source_documents: list[SourceDocumentResponse]


class SourceListResponse(BaseModel):
    task_id: UUID
    sources: list[SourceDocumentResponse]


class SourceChunkResponse(BaseModel):
    source_chunk_id: UUID
    source_document_id: UUID
    content_snapshot_id: UUID | None
    chunk_no: int
    token_count: int
    text: str
    metadata: dict[str, Any]


class SourceChunkListResponse(BaseModel):
    task_id: UUID
    source_chunks: list[SourceChunkResponse]
