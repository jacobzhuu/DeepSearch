"""add task revision and event sequence foundations

Revision ID: 20260423_0002
Revises: 20260422_0001
Create Date: 2026-04-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260423_0002"
down_revision = "20260422_0001"
branch_labels = None
depends_on = None


PREVIOUS_TASK_STATES = (
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

TASK_STATES = (
    "PLANNED",
    "QUEUED",
    "RUNNING",
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


def _backfill_revision_and_event_sequence() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    research_task = sa.Table("research_task", metadata, autoload_with=connection)
    task_event = sa.Table("task_event", metadata, autoload_with=connection)

    connection.execute(
        sa.update(research_task).where(research_task.c.revision_no.is_(None)).values(revision_no=1)
    )
    connection.execute(
        sa.update(research_task)
        .where(research_task.c.last_event_sequence_no.is_(None))
        .values(last_event_sequence_no=0)
    )

    rows = connection.execute(
        sa.select(task_event.c.id, task_event.c.task_id).order_by(
            task_event.c.task_id.asc(), task_event.c.created_at.asc(), task_event.c.id.asc()
        )
    ).all()

    current_task_id = None
    current_sequence_no = 0
    max_sequence_by_task: dict[object, int] = {}

    for row in rows:
        if row.task_id != current_task_id:
            current_task_id = row.task_id
            current_sequence_no = 1
        else:
            current_sequence_no += 1

        connection.execute(
            sa.update(task_event)
            .where(task_event.c.id == row.id)
            .values(sequence_no=current_sequence_no)
        )
        max_sequence_by_task[row.task_id] = current_sequence_no

    for task_id, max_sequence_no in max_sequence_by_task.items():
        connection.execute(
            sa.update(research_task)
            .where(research_task.c.id == task_id)
            .values(last_event_sequence_no=max_sequence_no)
        )


def upgrade() -> None:
    with op.batch_alter_table("research_task") as batch_op:
        batch_op.add_column(
            sa.Column(
                "revision_no",
                sa.Integer(),
                nullable=True,
                server_default=sa.text("1"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "last_event_sequence_no",
                sa.Integer(),
                nullable=True,
                server_default=sa.text("0"),
            )
        )

    with op.batch_alter_table("task_event") as batch_op:
        batch_op.add_column(sa.Column("sequence_no", sa.Integer(), nullable=True))

    _backfill_revision_and_event_sequence()

    with op.batch_alter_table("research_task") as batch_op:
        batch_op.drop_constraint("ck_research_task_status_valid", type_="check")
        batch_op.create_check_constraint(
            "ck_research_task_status_valid",
            f"status IN ({_sql_in(TASK_STATES)})",
        )
        batch_op.alter_column(
            "revision_no",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        )
        batch_op.alter_column(
            "last_event_sequence_no",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        )
        batch_op.create_check_constraint(
            "ck_research_task_revision_no_positive",
            "revision_no > 0",
        )
        batch_op.create_check_constraint(
            "ck_research_task_last_event_sequence_no_non_negative",
            "last_event_sequence_no >= 0",
        )

    with op.batch_alter_table("research_run") as batch_op:
        batch_op.drop_constraint("ck_research_run_current_state_valid", type_="check")
        batch_op.create_check_constraint(
            "ck_research_run_current_state_valid",
            f"current_state IN ({_sql_in(TASK_STATES)})",
        )

    with op.batch_alter_table("task_event") as batch_op:
        batch_op.alter_column(
            "sequence_no",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_task_event_sequence_no_positive",
            "sequence_no > 0",
        )
        batch_op.create_unique_constraint(
            "uq_task_event_task_id_sequence_no",
            ["task_id", "sequence_no"],
        )
        batch_op.create_index(
            "ix_task_event_task_id_sequence_no",
            ["task_id", "sequence_no"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("task_event") as batch_op:
        batch_op.drop_index("ix_task_event_task_id_sequence_no")
        batch_op.drop_constraint("uq_task_event_task_id_sequence_no", type_="unique")
        batch_op.drop_constraint("ck_task_event_sequence_no_positive", type_="check")
        batch_op.drop_column("sequence_no")

    with op.batch_alter_table("research_run") as batch_op:
        batch_op.drop_constraint("ck_research_run_current_state_valid", type_="check")
        batch_op.create_check_constraint(
            "ck_research_run_current_state_valid",
            f"current_state IN ({_sql_in(PREVIOUS_TASK_STATES)})",
        )

    with op.batch_alter_table("research_task") as batch_op:
        batch_op.drop_constraint(
            "ck_research_task_last_event_sequence_no_non_negative",
            type_="check",
        )
        batch_op.drop_constraint("ck_research_task_revision_no_positive", type_="check")
        batch_op.drop_constraint("ck_research_task_status_valid", type_="check")
        batch_op.create_check_constraint(
            "ck_research_task_status_valid",
            f"status IN ({_sql_in(PREVIOUS_TASK_STATES)})",
        )
        batch_op.drop_column("last_event_sequence_no")
        batch_op.drop_column("revision_no")
