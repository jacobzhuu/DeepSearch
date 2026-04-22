from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.db.models.base import Base, TimestampMixin, UUIDPrimaryKey
from packages.db.models.constants import TASK_STATE_VALUES, sql_in_check


class ResearchTask(TimestampMixin, Base):
    __tablename__ = "research_task"
    __table_args__ = (
        sa.CheckConstraint("length(trim(query)) > 0", name="research_task_query_non_empty"),
        sa.CheckConstraint(
            f"status IN ({sql_in_check(TASK_STATE_VALUES)})",
            name="research_task_status_valid",
        ),
        sa.CheckConstraint("priority >= 0", name="research_task_priority_non_negative"),
        sa.Index("ix_research_task_status_created_at", "status", "created_at"),
    )

    id: Mapped[UUIDPrimaryKey]
    query: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    user_id: Mapped[str | None] = mapped_column(sa.String(length=255))
    status: Mapped[str] = mapped_column(
        sa.String(length=32),
        nullable=False,
        default="PLANNED",
        server_default=sa.text("'PLANNED'"),
    )
    priority: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        default=100,
        server_default=sa.text("100"),
    )
    constraints_json: Mapped[dict[str, Any]] = mapped_column(
        sa.JSON(),
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'"),
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    runs: Mapped[list[ResearchRun]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )
    events: Mapped[list[TaskEvent]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )
    search_queries: Mapped[list[SearchQuery]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )
    candidate_urls: Mapped[list[CandidateUrl]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )
    fetch_jobs: Mapped[list[FetchJob]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )
    source_documents: Mapped[list[SourceDocument]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )
    claims: Mapped[list[Claim]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )
    report_artifacts: Mapped[list[ReportArtifact]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )


class ResearchRun(Base):
    __tablename__ = "research_run"
    __table_args__ = (
        sa.CheckConstraint("round_no > 0", name="research_run_round_no_positive"),
        sa.CheckConstraint(
            f"current_state IN ({sql_in_check(TASK_STATE_VALUES)})",
            name="research_run_current_state_valid",
        ),
        sa.UniqueConstraint("task_id", "round_no", name="uq_research_run_task_id_round_no"),
        sa.Index("ix_research_run_task_id_started_at", "task_id", "started_at"),
        sa.Index("ix_research_run_current_state", "current_state"),
    )

    id: Mapped[UUIDPrimaryKey]
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("research_task.id", ondelete="CASCADE"),
        nullable=False,
    )
    round_no: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    current_state: Mapped[str] = mapped_column(sa.String(length=32), nullable=False)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(
        sa.JSON(),
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'"),
    )
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    ended_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    task: Mapped[ResearchTask] = relationship(back_populates="runs")
    events: Mapped[list[TaskEvent]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    search_queries: Mapped[list[SearchQuery]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class TaskEvent(Base):
    __tablename__ = "task_event"
    __table_args__ = (
        sa.CheckConstraint("length(trim(event_type)) > 0", name="task_event_event_type_non_empty"),
        sa.Index("ix_task_event_task_id_created_at", "task_id", "created_at"),
        sa.Index("ix_task_event_run_id_created_at", "run_id", "created_at"),
    )

    id: Mapped[UUIDPrimaryKey]
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("research_task.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("research_run.id", ondelete="CASCADE"),
    )
    event_type: Mapped[str] = mapped_column(sa.String(length=64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        sa.JSON(),
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )

    task: Mapped[ResearchTask] = relationship(back_populates="events")
    run: Mapped[ResearchRun | None] = relationship(back_populates="events")


class SearchQuery(Base):
    __tablename__ = "search_query"
    __table_args__ = (
        sa.CheckConstraint(
            "length(trim(query_text)) > 0", name="search_query_query_text_non_empty"
        ),
        sa.CheckConstraint(
            "length(trim(provider)) > 0",
            name="search_query_provider_non_empty",
        ),
        sa.CheckConstraint("round_no > 0", name="search_query_round_no_positive"),
        sa.Index("ix_search_query_task_id_issued_at", "task_id", "issued_at"),
        sa.Index("ix_search_query_run_id_issued_at", "run_id", "issued_at"),
    )

    id: Mapped[UUIDPrimaryKey]
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("research_task.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("research_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    query_text: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(length=64), nullable=False)
    round_no: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    raw_response_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON())

    task: Mapped[ResearchTask] = relationship(back_populates="search_queries")
    run: Mapped[ResearchRun] = relationship(back_populates="search_queries")
    candidate_urls: Mapped[list[CandidateUrl]] = relationship(
        back_populates="search_query",
        cascade="all, delete-orphan",
    )


class CandidateUrl(Base):
    __tablename__ = "candidate_url"
    __table_args__ = (
        sa.CheckConstraint(
            "length(trim(original_url)) > 0",
            name="candidate_url_original_url_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(canonical_url)) > 0",
            name="candidate_url_canonical_url_non_empty",
        ),
        sa.CheckConstraint("length(trim(domain)) > 0", name="candidate_url_domain_non_empty"),
        sa.CheckConstraint("rank >= 0", name="candidate_url_rank_non_negative"),
        sa.UniqueConstraint(
            "search_query_id",
            "canonical_url",
            name="uq_candidate_url_search_query_id_canonical_url",
        ),
        sa.Index("ix_candidate_url_task_id_domain", "task_id", "domain"),
        sa.Index("ix_candidate_url_search_query_id_rank", "search_query_id", "rank"),
    )

    id: Mapped[UUIDPrimaryKey]
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("research_task.id", ondelete="CASCADE"),
        nullable=False,
    )
    search_query_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("search_query.id", ondelete="CASCADE"),
        nullable=False,
    )
    original_url: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    canonical_url: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    domain: Mapped[str] = mapped_column(sa.String(length=255), nullable=False)
    title: Mapped[str | None] = mapped_column(sa.Text())
    rank: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    selected: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        default=False,
        server_default=sa.false(),
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        sa.JSON(),
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'"),
    )

    task: Mapped[ResearchTask] = relationship(back_populates="candidate_urls")
    search_query: Mapped[SearchQuery] = relationship(back_populates="candidate_urls")
    fetch_jobs: Mapped[list[FetchJob]] = relationship(
        back_populates="candidate_url",
        cascade="all, delete-orphan",
    )


class FetchJob(Base):
    __tablename__ = "fetch_job"
    __table_args__ = (
        sa.CheckConstraint("length(trim(mode)) > 0", name="fetch_job_mode_non_empty"),
        sa.CheckConstraint("length(trim(status)) > 0", name="fetch_job_status_non_empty"),
        sa.Index("ix_fetch_job_task_id_status", "task_id", "status"),
        sa.Index("ix_fetch_job_candidate_url_id_status", "candidate_url_id", "status"),
        sa.Index("ix_fetch_job_status_lease_until", "status", "lease_until"),
    )

    id: Mapped[UUIDPrimaryKey]
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("research_task.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_url_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("candidate_url.id", ondelete="CASCADE"),
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(sa.String(length=32), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(length=32), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(sa.String(length=255))

    task: Mapped[ResearchTask] = relationship(back_populates="fetch_jobs")
    candidate_url: Mapped[CandidateUrl] = relationship(back_populates="fetch_jobs")
    attempts: Mapped[list[FetchAttempt]] = relationship(
        back_populates="fetch_job",
        cascade="all, delete-orphan",
    )


class FetchAttempt(Base):
    __tablename__ = "fetch_attempt"
    __table_args__ = (
        sa.CheckConstraint("attempt_no > 0", name="fetch_attempt_attempt_no_positive"),
        sa.CheckConstraint(
            "(http_status IS NULL) OR (http_status BETWEEN 100 AND 599)",
            name="fetch_attempt_http_status_range",
        ),
        sa.UniqueConstraint(
            "fetch_job_id",
            "attempt_no",
            name="uq_fetch_attempt_fetch_job_id_attempt_no",
        ),
        sa.Index("ix_fetch_attempt_fetch_job_id_started_at", "fetch_job_id", "started_at"),
    )

    id: Mapped[UUIDPrimaryKey]
    fetch_job_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("fetch_job.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    http_status: Mapped[int | None] = mapped_column(sa.Integer())
    error_code: Mapped[str | None] = mapped_column(sa.String(length=64))
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    trace_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON())

    fetch_job: Mapped[FetchJob] = relationship(back_populates="attempts")
    content_snapshot: Mapped[ContentSnapshot | None] = relationship(
        back_populates="fetch_attempt",
        cascade="all, delete-orphan",
        uselist=False,
    )


class ContentSnapshot(Base):
    __tablename__ = "content_snapshot"
    __table_args__ = (
        sa.CheckConstraint(
            "length(trim(storage_bucket)) > 0",
            name="content_snapshot_storage_bucket_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(storage_key)) > 0",
            name="content_snapshot_storage_key_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(content_hash)) > 0",
            name="content_snapshot_content_hash_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(mime_type)) > 0",
            name="content_snapshot_mime_type_non_empty",
        ),
        sa.CheckConstraint("bytes >= 0", name="content_snapshot_bytes_non_negative"),
        sa.UniqueConstraint("fetch_attempt_id", name="uq_content_snapshot_fetch_attempt_id"),
        sa.UniqueConstraint(
            "storage_bucket",
            "storage_key",
            name="uq_content_snapshot_storage_bucket_storage_key",
        ),
        sa.Index("ix_content_snapshot_content_hash", "content_hash"),
    )

    id: Mapped[UUIDPrimaryKey]
    fetch_attempt_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("fetch_attempt.id", ondelete="CASCADE"),
        nullable=False,
    )
    storage_bucket: Mapped[str] = mapped_column(sa.String(length=255), nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    content_hash: Mapped[str] = mapped_column(sa.String(length=128), nullable=False)
    mime_type: Mapped[str] = mapped_column(sa.String(length=255), nullable=False)
    bytes: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    extracted_title: Mapped[str | None] = mapped_column(sa.Text())
    fetched_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )

    fetch_attempt: Mapped[FetchAttempt] = relationship(back_populates="content_snapshot")


class SourceDocument(Base):
    __tablename__ = "source_document"
    __table_args__ = (
        sa.CheckConstraint(
            "length(trim(canonical_url)) > 0",
            name="source_document_canonical_url_non_empty",
        ),
        sa.CheckConstraint("length(trim(domain)) > 0", name="source_document_domain_non_empty"),
        sa.CheckConstraint(
            "length(trim(source_type)) > 0",
            name="source_document_source_type_non_empty",
        ),
        sa.CheckConstraint(
            "(authority_score IS NULL) OR (authority_score BETWEEN 0 AND 1)",
            name="source_document_authority_score_range",
        ),
        sa.CheckConstraint(
            "(freshness_score IS NULL) OR (freshness_score BETWEEN 0 AND 1)",
            name="source_document_freshness_score_range",
        ),
        sa.CheckConstraint(
            "(originality_score IS NULL) OR (originality_score BETWEEN 0 AND 1)",
            name="source_document_originality_score_range",
        ),
        sa.CheckConstraint(
            "(consistency_score IS NULL) OR (consistency_score BETWEEN 0 AND 1)",
            name="source_document_consistency_score_range",
        ),
        sa.CheckConstraint(
            "(safety_score IS NULL) OR (safety_score BETWEEN 0 AND 1)",
            name="source_document_safety_score_range",
        ),
        sa.CheckConstraint(
            "(final_source_score IS NULL) OR (final_source_score BETWEEN 0 AND 1)",
            name="source_document_final_source_score_range",
        ),
        sa.UniqueConstraint(
            "task_id",
            "canonical_url",
            name="uq_source_document_task_id_canonical_url",
        ),
        sa.Index("ix_source_document_task_id_final_source_score", "task_id", "final_source_score"),
        sa.Index("ix_source_document_domain", "domain"),
    )

    id: Mapped[UUIDPrimaryKey]
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("research_task.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_url: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    domain: Mapped[str] = mapped_column(sa.String(length=255), nullable=False)
    title: Mapped[str | None] = mapped_column(sa.Text())
    source_type: Mapped[str] = mapped_column(sa.String(length=64), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    authority_score: Mapped[float | None] = mapped_column(sa.Float())
    freshness_score: Mapped[float | None] = mapped_column(sa.Float())
    originality_score: Mapped[float | None] = mapped_column(sa.Float())
    consistency_score: Mapped[float | None] = mapped_column(sa.Float())
    safety_score: Mapped[float | None] = mapped_column(sa.Float())
    final_source_score: Mapped[float | None] = mapped_column(sa.Float())

    task: Mapped[ResearchTask] = relationship(back_populates="source_documents")
    chunks: Mapped[list[SourceChunk]] = relationship(
        back_populates="source_document",
        cascade="all, delete-orphan",
    )


class SourceChunk(Base):
    __tablename__ = "source_chunk"
    __table_args__ = (
        sa.CheckConstraint("chunk_no >= 0", name="source_chunk_chunk_no_non_negative"),
        sa.CheckConstraint("length(trim(text)) > 0", name="source_chunk_text_non_empty"),
        sa.CheckConstraint("token_count >= 0", name="source_chunk_token_count_non_negative"),
        sa.UniqueConstraint(
            "source_document_id",
            "chunk_no",
            name="uq_source_chunk_source_document_id_chunk_no",
        ),
        sa.Index("ix_source_chunk_source_document_id", "source_document_id"),
    )

    id: Mapped[UUIDPrimaryKey]
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("source_document.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_no: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    text: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    token_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        sa.JSON(),
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'"),
    )

    source_document: Mapped[SourceDocument] = relationship(back_populates="chunks")
    citation_spans: Mapped[list[CitationSpan]] = relationship(
        back_populates="source_chunk",
        cascade="all, delete-orphan",
    )


class CitationSpan(Base):
    __tablename__ = "citation_span"
    __table_args__ = (
        sa.CheckConstraint("start_offset >= 0", name="citation_span_start_offset_non_negative"),
        sa.CheckConstraint(
            "end_offset > start_offset",
            name="citation_span_end_offset_gt_start_offset",
        ),
        sa.CheckConstraint("length(trim(excerpt)) > 0", name="citation_span_excerpt_non_empty"),
        sa.CheckConstraint(
            "length(trim(normalized_excerpt_hash)) > 0",
            name="citation_span_hash_non_empty",
        ),
        sa.UniqueConstraint(
            "source_chunk_id",
            "start_offset",
            "end_offset",
            name="uq_citation_span_source_chunk_id_offsets",
        ),
        sa.Index("ix_citation_span_source_chunk_id", "source_chunk_id"),
        sa.Index("ix_citation_span_normalized_excerpt_hash", "normalized_excerpt_hash"),
    )

    id: Mapped[UUIDPrimaryKey]
    source_chunk_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("source_chunk.id", ondelete="CASCADE"),
        nullable=False,
    )
    start_offset: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    end_offset: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    excerpt: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    normalized_excerpt_hash: Mapped[str] = mapped_column(sa.String(length=128), nullable=False)

    source_chunk: Mapped[SourceChunk] = relationship(back_populates="citation_spans")
    claim_evidences: Mapped[list[ClaimEvidence]] = relationship(
        back_populates="citation_span",
        cascade="all, delete-orphan",
    )


class Claim(Base):
    __tablename__ = "claim"
    __table_args__ = (
        sa.CheckConstraint("length(trim(statement)) > 0", name="claim_statement_non_empty"),
        sa.CheckConstraint("length(trim(claim_type)) > 0", name="claim_claim_type_non_empty"),
        sa.CheckConstraint(
            "length(trim(verification_status)) > 0",
            name="claim_verification_status_non_empty",
        ),
        sa.CheckConstraint(
            "(confidence IS NULL) OR (confidence BETWEEN 0 AND 1)",
            name="claim_confidence_range",
        ),
        sa.Index("ix_claim_task_id_verification_status", "task_id", "verification_status"),
        sa.Index("ix_claim_task_id_claim_type", "task_id", "claim_type"),
    )

    id: Mapped[UUIDPrimaryKey]
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("research_task.id", ondelete="CASCADE"),
        nullable=False,
    )
    statement: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    claim_type: Mapped[str] = mapped_column(sa.String(length=64), nullable=False)
    confidence: Mapped[float | None] = mapped_column(sa.Float())
    verification_status: Mapped[str] = mapped_column(sa.String(length=32), nullable=False)
    notes_json: Mapped[dict[str, Any]] = mapped_column(
        sa.JSON(),
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'"),
    )

    task: Mapped[ResearchTask] = relationship(back_populates="claims")
    claim_evidences: Mapped[list[ClaimEvidence]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
    )


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        sa.CheckConstraint(
            "length(trim(relation_type)) > 0",
            name="claim_evidence_relation_type_non_empty",
        ),
        sa.CheckConstraint(
            "(score IS NULL) OR (score BETWEEN 0 AND 1)",
            name="claim_evidence_score_range",
        ),
        sa.UniqueConstraint(
            "claim_id",
            "citation_span_id",
            "relation_type",
            name="uq_claim_evidence_claim_id_citation_span_id_relation_type",
        ),
        sa.Index("ix_claim_evidence_claim_id", "claim_id"),
        sa.Index("ix_claim_evidence_citation_span_id", "citation_span_id"),
    )

    id: Mapped[UUIDPrimaryKey]
    claim_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("claim.id", ondelete="CASCADE"),
        nullable=False,
    )
    citation_span_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("citation_span.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(sa.String(length=32), nullable=False)
    score: Mapped[float | None] = mapped_column(sa.Float())

    claim: Mapped[Claim] = relationship(back_populates="claim_evidences")
    citation_span: Mapped[CitationSpan] = relationship(back_populates="claim_evidences")


class ReportArtifact(Base):
    __tablename__ = "report_artifact"
    __table_args__ = (
        sa.CheckConstraint("version > 0", name="report_artifact_version_positive"),
        sa.CheckConstraint(
            "length(trim(storage_bucket)) > 0",
            name="report_artifact_storage_bucket_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(storage_key)) > 0",
            name="report_artifact_storage_key_non_empty",
        ),
        sa.CheckConstraint("length(trim(format)) > 0", name="report_artifact_format_non_empty"),
        sa.UniqueConstraint(
            "task_id",
            "version",
            "format",
            name="uq_report_artifact_task_id_version_format",
        ),
        sa.Index("ix_report_artifact_task_id_created_at", "task_id", "created_at"),
    )

    id: Mapped[UUIDPrimaryKey]
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("research_task.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(sa.String(length=255), nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    format: Mapped[str] = mapped_column(sa.String(length=32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )

    task: Mapped[ResearchTask] = relationship(back_populates="report_artifacts")
