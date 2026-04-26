from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel


class DebugPipelineCountsResponse(BaseModel):
    search_queries: int
    candidate_urls: int
    fetch_attempts: int
    content_snapshots: int
    source_documents: int
    source_chunks: int
    indexed_chunks: int
    claims: int
    claim_evidence: int
    report_artifacts: int


class DebugPipelineFailureResponse(BaseModel):
    stage: str
    reason: str
    exception: str | None = None
    message: str
    next_action: str
    counts: DebugPipelineCountsResponse
    details: dict[str, Any] | None = None


class DebugPipelineResponse(BaseModel):
    task_id: UUID
    status: str
    completed: bool
    stages_completed: list[str]
    counts: DebugPipelineCountsResponse
    report_artifact_id: UUID | None = None
    report_version: int | None = None
    report_markdown_preview: str | None = None
    failure: DebugPipelineFailureResponse | None = None
    dependencies: dict[str, Any]
