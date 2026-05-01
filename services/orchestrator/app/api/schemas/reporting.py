from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ReportResponse(BaseModel):
    task_id: UUID
    report_artifact_id: UUID
    version: int
    format: str
    title: str
    storage_bucket: str
    storage_key: str
    created_at: datetime
    markdown: str
    report_language: str
    writer_mode: str
    llm_writer_status: str | None = None


class GenerateReportResponse(ReportResponse):
    supported_claims: int
    mixed_claims: int
    contradicted_claims: int
    unsupported_claims: int
    draft_claims: int
    reused_existing: bool
