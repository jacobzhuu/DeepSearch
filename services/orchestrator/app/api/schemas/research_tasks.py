from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CreateResearchTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    constraints: dict[str, Any] = Field(default_factory=dict)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized


class ReviseResearchTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = None
    constraints: dict[str, Any] | None = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_revision_payload(self) -> ReviseResearchTaskRequest:
        if self.query is None and self.constraints is None:
            raise ValueError("at least one of query or constraints must be provided")
        return self


class ResearchTaskMutationResponse(BaseModel):
    task_id: UUID
    status: str
    revision_no: int
    updated_at: datetime


class ResearchTaskObservabilityResponse(BaseModel):
    planner_enabled: bool | None = None
    planner_mode: str | None = None
    planner_status: str | None = None
    subquestion_count: int | None = None
    search_query_count: int | None = None
    research_plan: dict[str, Any] | None = None
    raw_planner_queries: list[dict[str, Any]] = Field(default_factory=list)
    final_search_queries: list[dict[str, Any]] = Field(default_factory=list)
    dropped_or_downweighted_planner_queries: list[dict[str, Any]] = Field(default_factory=list)
    planner_guardrail_warnings: list[str] = Field(default_factory=list)
    intent_classification: str | None = None
    extracted_entity: str | None = None
    search_result_count: int | None = None
    selected_sources_from_search: list[dict[str, Any]] = Field(default_factory=list)
    selected_sources: list[dict[str, Any]] = Field(default_factory=list)
    fetch_succeeded: int | None = None
    fetch_failed: int | None = None
    attempted_sources: list[dict[str, Any]] = Field(default_factory=list)
    unattempted_sources: list[dict[str, Any]] = Field(default_factory=list)
    failed_sources: list[dict[str, Any]] = Field(default_factory=list)
    parse_decisions: list[dict[str, Any]] = Field(default_factory=list)
    source_quality_summary: dict[str, Any] | None = None
    source_yield_summary: list[dict[str, Any]] = Field(default_factory=list)
    dropped_sources: list[dict[str, Any]] = Field(default_factory=list)
    answer_coverage: dict[str, bool] | None = None
    answer_slots: list[dict[str, Any]] = Field(default_factory=list)
    report_slot_coverage: list[dict[str, Any]] = Field(default_factory=list)
    slot_coverage_summary: list[dict[str, Any]] = Field(default_factory=list)
    answer_yield: list[dict[str, Any]] = Field(default_factory=list)
    evidence_yield_summary: dict[str, Any] = Field(default_factory=dict)
    verification_summary: dict[str, Any] = Field(default_factory=dict)
    supplemental_acquisition: dict[str, Any] | None = None
    failure_diagnostics: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class ResearchTaskProgressResponse(BaseModel):
    current_state: str
    events_total: int
    latest_event_at: datetime | None
    observability: ResearchTaskObservabilityResponse | None = None


class ResearchTaskDetailResponse(BaseModel):
    task_id: UUID
    query: str
    status: str
    constraints: dict[str, Any]
    revision_no: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    progress: ResearchTaskProgressResponse


class TaskEventResponse(BaseModel):
    event_id: UUID
    run_id: UUID | None
    event_type: str
    sequence_no: int
    payload: dict[str, Any]
    created_at: datetime


class TaskEventListResponse(BaseModel):
    task_id: UUID
    events: list[TaskEventResponse]
