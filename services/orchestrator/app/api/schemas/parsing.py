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
    content_type: str
    storage_bucket: str
    storage_key: str
    snapshot_bytes: int
    body_length: int | None
    chunks_created: int
    status: str
    reason: ParseResultReason | None
    decision: str
    parser_error: str | None = None
    source_format: str | None = None
    parser_status: str | None = None
    parser_kind: str | None = None
    parser_warnings: list[str] | None = None
    parser_failure_reason: str | None = None
    mime_policy: dict[str, Any] | None = None
    page_range: list[int] | None = None
    page_locator_reliable: bool | None = None
    locator_fallback_reason: str | None = None
    slide_range: list[int] | None = None
    sheet_names: list[str] | None = None
    cell_ranges: list[str] | None = None
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
    content_hash: str | None = None
    canonical_url: str
    domain: str
    title: str | None
    source_type: str
    published_at: datetime | None
    fetched_at: datetime
    authority_score: float | None = None
    freshness_score: float | None = None
    originality_score: float | None = None
    consistency_score: float | None = None
    safety_score: float | None = None
    final_source_score: float | None = None
    quality: dict[str, Any] = Field(default_factory=dict)
    parser_metadata: dict[str, Any] = Field(default_factory=dict)


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
