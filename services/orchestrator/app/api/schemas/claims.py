from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DraftClaimsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = None
    source_chunk_ids: list[UUID] | None = None
    limit: int | None = Field(default=None, ge=1, le=100)

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
    def validate_payload(self) -> DraftClaimsRequest:
        if self.query is None and self.source_chunk_ids is None:
            raise ValueError("at least one of query or source_chunk_ids must be provided")
        return self


class VerifyClaimsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_ids: list[UUID] | None = None
    limit: int | None = Field(default=None, ge=1, le=100)


class ClaimResponse(BaseModel):
    claim_id: UUID
    statement: str
    claim_type: str
    confidence: float | None
    verification_status: str
    support_evidence_count: int = 0
    contradict_evidence_count: int = 0
    rationale: str | None = None
    notes: dict[str, Any]


class ClaimListResponse(BaseModel):
    task_id: UUID
    claims: list[ClaimResponse]


class ClaimEvidenceResponse(BaseModel):
    claim_evidence_id: UUID
    claim_id: UUID
    citation_span_id: UUID
    source_chunk_id: UUID
    source_document_id: UUID
    statement: str
    relation_type: str
    score: float | None
    start_offset: int
    end_offset: int
    excerpt: str
    normalized_excerpt_hash: str


class ClaimEvidenceListResponse(BaseModel):
    task_id: UUID
    claim_evidence: list[ClaimEvidenceResponse]


class VerifiedClaimResponse(BaseModel):
    claim_id: UUID
    statement: str
    claim_type: str
    confidence: float | None
    verification_status: str
    support_evidence_count: int
    contradict_evidence_count: int
    rationale: str
    notes: dict[str, Any]


class DraftClaimEntryResponse(BaseModel):
    claim_id: UUID
    citation_span_id: UUID
    claim_evidence_id: UUID
    source_chunk_id: UUID
    source_document_id: UUID
    statement: str
    claim_type: str
    confidence: float | None
    verification_status: str
    relation_type: str
    evidence_score: float | None
    start_offset: int
    end_offset: int
    excerpt: str
    reused_claim: bool
    reused_citation_span: bool
    reused_claim_evidence: bool
    retrieval_score: float | None


class DraftClaimsResponse(BaseModel):
    task_id: UUID
    effective_query: str
    created_claims: int
    reused_claims: int
    created_citation_spans: int
    reused_citation_spans: int
    created_claim_evidence: int
    reused_claim_evidence: int
    claims: list[DraftClaimEntryResponse]


class VerifyClaimsResponse(BaseModel):
    task_id: UUID
    verified_claims: int
    created_citation_spans: int
    reused_citation_spans: int
    created_claim_evidence: int
    reused_claim_evidence: int
    claims: list[VerifiedClaimResponse]
