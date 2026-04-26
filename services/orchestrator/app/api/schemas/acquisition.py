from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RunAcquisitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_url_ids: list[UUID] | None = None
    limit: int | None = Field(default=None, ge=1, le=50)


class AcquisitionEntryResponse(BaseModel):
    candidate_url_id: UUID
    canonical_url: str
    fetch_job_id: UUID
    fetch_attempt_id: UUID | None
    snapshot_id: UUID | None
    status: str
    http_status: int | None
    error_code: str | None
    error_reason: str | None = None
    skipped_existing: bool


class RunAcquisitionResponse(BaseModel):
    task_id: UUID
    created: int
    skipped_existing: int
    succeeded: int
    failed: int
    entries: list[AcquisitionEntryResponse]


class FetchJobResponse(BaseModel):
    fetch_job_id: UUID
    candidate_url_id: UUID
    canonical_url: str
    mode: str
    status: str
    scheduled_at: datetime
    latest_attempt_id: UUID | None
    latest_attempt_no: int | None
    latest_http_status: int | None
    latest_error_code: str | None
    latest_error_reason: str | None = None
    snapshot_id: UUID | None


class FetchJobListResponse(BaseModel):
    task_id: UUID
    fetch_jobs: list[FetchJobResponse]


class FetchAttemptResponse(BaseModel):
    fetch_attempt_id: UUID
    fetch_job_id: UUID
    attempt_no: int
    http_status: int | None
    error_code: str | None
    started_at: datetime
    finished_at: datetime | None
    trace: dict[str, Any] | None


class FetchAttemptListResponse(BaseModel):
    task_id: UUID
    fetch_attempts: list[FetchAttemptResponse]


class ContentSnapshotResponse(BaseModel):
    snapshot_id: UUID
    fetch_attempt_id: UUID
    storage_bucket: str
    storage_key: str
    content_hash: str
    mime_type: str
    bytes: int
    extracted_title: str | None
    fetched_at: datetime


class ContentSnapshotListResponse(BaseModel):
    task_id: UUID
    content_snapshots: list[ContentSnapshotResponse]
