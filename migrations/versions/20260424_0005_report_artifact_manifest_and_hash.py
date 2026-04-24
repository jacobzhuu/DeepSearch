"""add report artifact hash and manifest

Revision ID: 20260424_0005
Revises: 20260423_0004
Create Date: 2026-04-24 08:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260424_0005"
down_revision = "20260423_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("report_artifact") as batch_op:
        batch_op.add_column(sa.Column("content_hash", sa.String(length=71), nullable=True))
        batch_op.add_column(sa.Column("manifest_json", sa.JSON(), nullable=True))
        batch_op.create_check_constraint(
            "report_artifact_content_hash_non_empty",
            "(content_hash IS NULL) OR (length(trim(content_hash)) > 0)",
        )


def downgrade() -> None:
    with op.batch_alter_table("report_artifact") as batch_op:
        batch_op.drop_constraint("report_artifact_content_hash_non_empty", type_="check")
        batch_op.drop_column("manifest_json")
        batch_op.drop_column("content_hash")
