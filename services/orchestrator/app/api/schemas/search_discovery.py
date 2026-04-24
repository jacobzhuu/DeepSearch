from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class SearchQuerySummaryResponse(BaseModel):
    search_query_id: UUID
    query_text: str
    provider: str
    source_engines: list[str]
    round_no: int
    issued_at: datetime
    candidates_added: int
    duplicates_skipped: int
    filtered_out: int


class SearchDiscoveryResponse(BaseModel):
    task_id: UUID
    run_id: UUID
    round_no: int
    revision_no: int
    search_queries: list[SearchQuerySummaryResponse]
    candidate_urls_added: int
    duplicates_skipped: int
    filtered_out: int


class SearchQueryRecordResponse(BaseModel):
    search_query_id: UUID
    query_text: str
    provider: str
    source_engines: list[str]
    round_no: int
    issued_at: datetime
    result_count: int
    metadata: dict[str, Any]


class SearchQueryListResponse(BaseModel):
    task_id: UUID
    search_queries: list[SearchQueryRecordResponse]


class CandidateUrlResponse(BaseModel):
    candidate_url_id: UUID
    search_query_id: UUID
    original_url: str
    canonical_url: str
    domain: str
    title: str | None
    rank: int
    selected: bool
    metadata: dict[str, Any]


class CandidateUrlListResponse(BaseModel):
    task_id: UUID
    candidate_urls: list[CandidateUrlResponse]
