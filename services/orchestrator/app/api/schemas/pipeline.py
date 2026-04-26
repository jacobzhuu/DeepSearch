from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel


class PipelineCountsResponse(BaseModel):
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


class PipelineFailureResponse(BaseModel):
    failed_stage: str
    reason: str
    exception: str | None = None
    message: str
    next_action: str
    counts: PipelineCountsResponse
    details: dict[str, Any] | None = None


class PipelineRunResponse(BaseModel):
    task_id: UUID
    status: str
    completed: bool
    running_mode: str
    stages_completed: list[str]
    counts: PipelineCountsResponse
    report_artifact_id: UUID | None = None
    report_version: int | None = None
    report_markdown_preview: str | None = None
    failure: PipelineFailureResponse | None = None
    dependencies: dict[str, Any]
