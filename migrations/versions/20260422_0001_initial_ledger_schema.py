"""create initial ledger schema

Revision ID: 20260422_0001
Revises:
Create Date: 2026-04-22 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260422_0001"
down_revision = None
branch_labels = None
depends_on = None


TASK_STATES = (
    "PLANNED",
    "SEARCHING",
    "ACQUIRING",
    "PARSING",
    "INDEXING",
    "DRAFTING_CLAIMS",
    "VERIFYING",
    "RESEARCHING_MORE",
    "REPORTING",
    "COMPLETED",
    "FAILED",
    "PAUSED",
    "CANCELLED",
    "NEEDS_REVISION",
)


def _sql_in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "research_task",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'PLANNED'"),
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("100"),
        ),
        sa.Column(
            "constraints_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(query)) > 0", name="ck_research_task_query_non_empty"),
        sa.CheckConstraint(
            f"status IN ({_sql_in(TASK_STATES)})",
            name="ck_research_task_status_valid",
        ),
        sa.CheckConstraint("priority >= 0", name="ck_research_task_priority_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_research_task"),
    )
    op.create_index(
        "ix_research_task_status_created_at",
        "research_task",
        ["status", "created_at"],
        unique=False,
    )

    op.create_table(
        "research_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("current_state", sa.String(length=32), nullable=False),
        sa.Column(
            "checkpoint_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("round_no > 0", name="ck_research_run_round_no_positive"),
        sa.CheckConstraint(
            f"current_state IN ({_sql_in(TASK_STATES)})",
            name="ck_research_run_current_state_valid",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["research_task.id"],
            name="fk_research_run_task_id_research_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_research_run"),
        sa.UniqueConstraint("task_id", "round_no", name="uq_research_run_task_id_round_no"),
    )
    op.create_index(
        "ix_research_run_task_id_started_at",
        "research_run",
        ["task_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_research_run_current_state",
        "research_run",
        ["current_state"],
        unique=False,
    )

    op.create_table(
        "task_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "length(trim(event_type)) > 0",
            name="ck_task_event_event_type_non_empty",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_run.id"],
            name="fk_task_event_run_id_research_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["research_task.id"],
            name="fk_task_event_task_id_research_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_event"),
    )
    op.create_index(
        "ix_task_event_task_id_created_at",
        "task_event",
        ["task_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_task_event_run_id_created_at",
        "task_event",
        ["run_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "search_query",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("raw_response_json", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "length(trim(query_text)) > 0",
            name="ck_search_query_query_text_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(provider)) > 0",
            name="ck_search_query_provider_non_empty",
        ),
        sa.CheckConstraint("round_no > 0", name="ck_search_query_round_no_positive"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_run.id"],
            name="fk_search_query_run_id_research_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["research_task.id"],
            name="fk_search_query_task_id_research_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_query"),
    )
    op.create_index(
        "ix_search_query_task_id_issued_at",
        "search_query",
        ["task_id", "issued_at"],
        unique=False,
    )
    op.create_index(
        "ix_search_query_run_id_issued_at",
        "search_query",
        ["run_id", "issued_at"],
        unique=False,
    )

    op.create_table(
        "candidate_url",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("search_query_id", sa.Uuid(), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "selected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.CheckConstraint(
            "length(trim(original_url)) > 0",
            name="ck_candidate_url_original_url_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(canonical_url)) > 0",
            name="ck_candidate_url_canonical_url_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(domain)) > 0",
            name="ck_candidate_url_domain_non_empty",
        ),
        sa.CheckConstraint("rank >= 0", name="ck_candidate_url_rank_non_negative"),
        sa.ForeignKeyConstraint(
            ["search_query_id"],
            ["search_query.id"],
            name="fk_candidate_url_search_query_id_search_query",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["research_task.id"],
            name="fk_candidate_url_task_id_research_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_candidate_url"),
        sa.UniqueConstraint(
            "search_query_id",
            "canonical_url",
            name="uq_candidate_url_search_query_id_canonical_url",
        ),
    )
    op.create_index(
        "ix_candidate_url_task_id_domain",
        "candidate_url",
        ["task_id", "domain"],
        unique=False,
    )
    op.create_index(
        "ix_candidate_url_search_query_id_rank",
        "candidate_url",
        ["search_query_id", "rank"],
        unique=False,
    )

    op.create_table(
        "fetch_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_url_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.CheckConstraint("length(trim(mode)) > 0", name="ck_fetch_job_mode_non_empty"),
        sa.CheckConstraint(
            "length(trim(status)) > 0",
            name="ck_fetch_job_status_non_empty",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_url_id"],
            ["candidate_url.id"],
            name="fk_fetch_job_candidate_url_id_candidate_url",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["research_task.id"],
            name="fk_fetch_job_task_id_research_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_fetch_job"),
    )
    op.create_index(
        "ix_fetch_job_task_id_status",
        "fetch_job",
        ["task_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_fetch_job_candidate_url_id_status",
        "fetch_job",
        ["candidate_url_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_fetch_job_status_lease_until",
        "fetch_job",
        ["status", "lease_until"],
        unique=False,
    )

    op.create_table(
        "fetch_attempt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fetch_job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_json", sa.JSON(), nullable=True),
        sa.CheckConstraint("attempt_no > 0", name="ck_fetch_attempt_attempt_no_positive"),
        sa.CheckConstraint(
            "(http_status IS NULL) OR (http_status BETWEEN 100 AND 599)",
            name="ck_fetch_attempt_http_status_range",
        ),
        sa.ForeignKeyConstraint(
            ["fetch_job_id"],
            ["fetch_job.id"],
            name="fk_fetch_attempt_fetch_job_id_fetch_job",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_fetch_attempt"),
        sa.UniqueConstraint(
            "fetch_job_id",
            "attempt_no",
            name="uq_fetch_attempt_fetch_job_id_attempt_no",
        ),
    )
    op.create_index(
        "ix_fetch_attempt_fetch_job_id_started_at",
        "fetch_attempt",
        ["fetch_job_id", "started_at"],
        unique=False,
    )

    op.create_table(
        "content_snapshot",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fetch_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("storage_bucket", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("extracted_title", sa.Text(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "length(trim(storage_bucket)) > 0",
            name="ck_content_snapshot_storage_bucket_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(storage_key)) > 0",
            name="ck_content_snapshot_storage_key_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(content_hash)) > 0",
            name="ck_content_snapshot_content_hash_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(mime_type)) > 0",
            name="ck_content_snapshot_mime_type_non_empty",
        ),
        sa.CheckConstraint("bytes >= 0", name="ck_content_snapshot_bytes_non_negative"),
        sa.ForeignKeyConstraint(
            ["fetch_attempt_id"],
            ["fetch_attempt.id"],
            name="fk_content_snapshot_fetch_attempt_id_fetch_attempt",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_content_snapshot"),
        sa.UniqueConstraint(
            "fetch_attempt_id",
            name="uq_content_snapshot_fetch_attempt_id",
        ),
        sa.UniqueConstraint(
            "storage_bucket",
            "storage_key",
            name="uq_content_snapshot_storage_bucket_storage_key",
        ),
    )
    op.create_index(
        "ix_content_snapshot_content_hash",
        "content_snapshot",
        ["content_hash"],
        unique=False,
    )

    op.create_table(
        "source_document",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authority_score", sa.Float(), nullable=True),
        sa.Column("freshness_score", sa.Float(), nullable=True),
        sa.Column("originality_score", sa.Float(), nullable=True),
        sa.Column("consistency_score", sa.Float(), nullable=True),
        sa.Column("safety_score", sa.Float(), nullable=True),
        sa.Column("final_source_score", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "length(trim(canonical_url)) > 0",
            name="ck_source_document_canonical_url_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(domain)) > 0",
            name="ck_source_document_domain_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(source_type)) > 0",
            name="ck_source_document_source_type_non_empty",
        ),
        sa.CheckConstraint(
            "(authority_score IS NULL) OR (authority_score BETWEEN 0 AND 1)",
            name="ck_source_document_authority_score_range",
        ),
        sa.CheckConstraint(
            "(freshness_score IS NULL) OR (freshness_score BETWEEN 0 AND 1)",
            name="ck_source_document_freshness_score_range",
        ),
        sa.CheckConstraint(
            "(originality_score IS NULL) OR (originality_score BETWEEN 0 AND 1)",
            name="ck_source_document_originality_score_range",
        ),
        sa.CheckConstraint(
            "(consistency_score IS NULL) OR (consistency_score BETWEEN 0 AND 1)",
            name="ck_source_document_consistency_score_range",
        ),
        sa.CheckConstraint(
            "(safety_score IS NULL) OR (safety_score BETWEEN 0 AND 1)",
            name="ck_source_document_safety_score_range",
        ),
        sa.CheckConstraint(
            "(final_source_score IS NULL) OR (final_source_score BETWEEN 0 AND 1)",
            name="ck_source_document_final_source_score_range",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["research_task.id"],
            name="fk_source_document_task_id_research_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_document"),
        sa.UniqueConstraint(
            "task_id",
            "canonical_url",
            name="uq_source_document_task_id_canonical_url",
        ),
    )
    op.create_index(
        "ix_source_document_task_id_final_source_score",
        "source_document",
        ["task_id", "final_source_score"],
        unique=False,
    )
    op.create_index(
        "ix_source_document_domain",
        "source_document",
        ["domain"],
        unique=False,
    )

    op.create_table(
        "source_chunk",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_no", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.CheckConstraint("chunk_no >= 0", name="ck_source_chunk_chunk_no_non_negative"),
        sa.CheckConstraint(
            "length(trim(text)) > 0",
            name="ck_source_chunk_text_non_empty",
        ),
        sa.CheckConstraint(
            "token_count >= 0",
            name="ck_source_chunk_token_count_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_document.id"],
            name="fk_source_chunk_source_document_id_source_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_chunk"),
        sa.UniqueConstraint(
            "source_document_id",
            "chunk_no",
            name="uq_source_chunk_source_document_id_chunk_no",
        ),
    )
    op.create_index(
        "ix_source_chunk_source_document_id",
        "source_chunk",
        ["source_document_id"],
        unique=False,
    )

    op.create_table(
        "citation_span",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_chunk_id", sa.Uuid(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("normalized_excerpt_hash", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "start_offset >= 0",
            name="ck_citation_span_start_offset_non_negative",
        ),
        sa.CheckConstraint(
            "end_offset > start_offset",
            name="ck_citation_span_end_offset_gt_start_offset",
        ),
        sa.CheckConstraint(
            "length(trim(excerpt)) > 0",
            name="ck_citation_span_excerpt_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(normalized_excerpt_hash)) > 0",
            name="ck_citation_span_hash_non_empty",
        ),
        sa.ForeignKeyConstraint(
            ["source_chunk_id"],
            ["source_chunk.id"],
            name="fk_citation_span_source_chunk_id_source_chunk",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_citation_span"),
        sa.UniqueConstraint(
            "source_chunk_id",
            "start_offset",
            "end_offset",
            name="uq_citation_span_source_chunk_id_offsets",
        ),
    )
    op.create_index(
        "ix_citation_span_source_chunk_id",
        "citation_span",
        ["source_chunk_id"],
        unique=False,
    )
    op.create_index(
        "ix_citation_span_normalized_excerpt_hash",
        "citation_span",
        ["normalized_excerpt_hash"],
        unique=False,
    )

    op.create_table(
        "claim",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column(
            "notes_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.CheckConstraint("length(trim(statement)) > 0", name="ck_claim_statement_non_empty"),
        sa.CheckConstraint(
            "length(trim(claim_type)) > 0",
            name="ck_claim_claim_type_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(verification_status)) > 0",
            name="ck_claim_verification_status_non_empty",
        ),
        sa.CheckConstraint(
            "(confidence IS NULL) OR (confidence BETWEEN 0 AND 1)",
            name="ck_claim_confidence_range",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["research_task.id"],
            name="fk_claim_task_id_research_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_claim"),
    )
    op.create_index(
        "ix_claim_task_id_verification_status",
        "claim",
        ["task_id", "verification_status"],
        unique=False,
    )
    op.create_index(
        "ix_claim_task_id_claim_type",
        "claim",
        ["task_id", "claim_type"],
        unique=False,
    )

    op.create_table(
        "claim_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("citation_span_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "length(trim(relation_type)) > 0",
            name="ck_claim_evidence_relation_type_non_empty",
        ),
        sa.CheckConstraint(
            "(score IS NULL) OR (score BETWEEN 0 AND 1)",
            name="ck_claim_evidence_score_range",
        ),
        sa.ForeignKeyConstraint(
            ["citation_span_id"],
            ["citation_span.id"],
            name="fk_claim_evidence_citation_span_id_citation_span",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claim.id"],
            name="fk_claim_evidence_claim_id_claim",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_claim_evidence"),
        sa.UniqueConstraint(
            "claim_id",
            "citation_span_id",
            "relation_type",
            name="uq_claim_evidence_claim_id_citation_span_id_relation_type",
        ),
    )
    op.create_index(
        "ix_claim_evidence_claim_id",
        "claim_evidence",
        ["claim_id"],
        unique=False,
    )
    op.create_index(
        "ix_claim_evidence_citation_span_id",
        "claim_evidence",
        ["citation_span_id"],
        unique=False,
    )

    op.create_table(
        "report_artifact",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("storage_bucket", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("version > 0", name="ck_report_artifact_version_positive"),
        sa.CheckConstraint(
            "length(trim(storage_bucket)) > 0",
            name="ck_report_artifact_storage_bucket_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(storage_key)) > 0",
            name="ck_report_artifact_storage_key_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(format)) > 0",
            name="ck_report_artifact_format_non_empty",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["research_task.id"],
            name="fk_report_artifact_task_id_research_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_report_artifact"),
        sa.UniqueConstraint(
            "task_id",
            "version",
            "format",
            name="uq_report_artifact_task_id_version_format",
        ),
    )
    op.create_index(
        "ix_report_artifact_task_id_created_at",
        "report_artifact",
        ["task_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("report_artifact")
    op.drop_table("claim_evidence")
    op.drop_table("claim")
    op.drop_table("citation_span")
    op.drop_table("source_chunk")
    op.drop_table("source_document")
    op.drop_table("content_snapshot")
    op.drop_table("fetch_attempt")
    op.drop_table("fetch_job")
    op.drop_table("candidate_url")
    op.drop_table("search_query")
    op.drop_table("task_event")
    op.drop_table("research_run")
    op.drop_table("research_task")
