"""add fetch_job candidate mode uniqueness

Revision ID: 20260423_0003
Revises: 20260423_0002
Create Date: 2026-04-23 00:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260423_0003"
down_revision = "20260423_0002"
branch_labels = None
depends_on = None


def _assert_no_duplicate_fetch_jobs() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    fetch_job = sa.Table("fetch_job", metadata, autoload_with=connection)

    duplicate_rows = connection.execute(
        sa.select(
            fetch_job.c.candidate_url_id,
            fetch_job.c.mode,
            sa.func.count().label("row_count"),
        )
        .group_by(fetch_job.c.candidate_url_id, fetch_job.c.mode)
        .having(sa.func.count() > 1)
    ).all()

    if duplicate_rows:
        raise RuntimeError(
            "cannot add uq_fetch_job_candidate_url_id_mode while duplicate fetch_job rows exist"
        )


def upgrade() -> None:
    _assert_no_duplicate_fetch_jobs()
    with op.batch_alter_table("fetch_job") as batch_op:
        batch_op.create_unique_constraint(
            "uq_fetch_job_candidate_url_id_mode",
            ["candidate_url_id", "mode"],
        )


def downgrade() -> None:
    with op.batch_alter_table("fetch_job") as batch_op:
        batch_op.drop_constraint("uq_fetch_job_candidate_url_id_mode", type_="unique")
