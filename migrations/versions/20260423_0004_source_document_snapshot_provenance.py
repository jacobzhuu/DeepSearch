"""add source_document snapshot provenance

Revision ID: 20260423_0004
Revises: 20260423_0003
Create Date: 2026-04-23 02:15:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260423_0004"
down_revision = "20260423_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_document") as batch_op:
        batch_op.add_column(sa.Column("content_snapshot_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_source_document_content_snapshot_id_content_snapshot",
            "content_snapshot",
            ["content_snapshot_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_source_document_content_snapshot_id",
            ["content_snapshot_id"],
        )
        batch_op.create_index(
            "ix_source_document_content_snapshot_id",
            ["content_snapshot_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("source_document") as batch_op:
        batch_op.drop_index("ix_source_document_content_snapshot_id")
        batch_op.drop_constraint("uq_source_document_content_snapshot_id", type_="unique")
        batch_op.drop_constraint(
            "fk_source_document_content_snapshot_id_content_snapshot",
            type_="foreignkey",
        )
        batch_op.drop_column("content_snapshot_id")
