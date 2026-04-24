from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, inspect, select

from packages.db.session import build_engine


def test_upgrade_and_downgrade_apply_core_ledger_schema(
    alembic_config: Config,
    database_url: str,
) -> None:
    command.upgrade(alembic_config, "20260422_0001")

    phase1_engine = build_engine(database_url)
    task_id = uuid4()
    event_id = uuid4()
    seeded_at = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    phase1_metadata = MetaData()
    research_task_phase1 = Table("research_task", phase1_metadata, autoload_with=phase1_engine)
    task_event_phase1 = Table("task_event", phase1_metadata, autoload_with=phase1_engine)

    with phase1_engine.begin() as connection:
        connection.execute(
            research_task_phase1.insert().values(
                id=str(task_id),
                query="seeded task before revision and sequence migration",
                user_id=None,
                status="PLANNED",
                priority=100,
                constraints_json={"language": "en"},
                created_at=seeded_at,
                updated_at=seeded_at,
                started_at=None,
                ended_at=None,
            )
        )
        connection.execute(
            task_event_phase1.insert().values(
                id=str(event_id),
                task_id=str(task_id),
                run_id=None,
                event_type="task.created",
                payload_json={
                    "event_version": 1,
                    "source": "api",
                    "from_status": None,
                    "to_status": "PLANNED",
                    "changes": {"query": "seeded task before revision and sequence migration"},
                },
                created_at=seeded_at,
            )
        )

    phase1_engine.dispose()

    command.upgrade(alembic_config, "head")

    upgraded_engine = build_engine(database_url)
    inspector = inspect(upgraded_engine)

    assert {
        "candidate_url",
        "citation_span",
        "claim",
        "claim_evidence",
        "content_snapshot",
        "fetch_attempt",
        "fetch_job",
        "report_artifact",
        "research_run",
        "research_task",
        "search_query",
        "source_chunk",
        "source_document",
        "task_event",
    }.issubset(set(inspector.get_table_names()))

    run_uniques = {item["name"] for item in inspector.get_unique_constraints("research_run")}
    candidate_uniques = {item["name"] for item in inspector.get_unique_constraints("candidate_url")}
    fetch_uniques = {item["name"] for item in inspector.get_unique_constraints("fetch_job")}
    report_uniques = {item["name"] for item in inspector.get_unique_constraints("report_artifact")}
    event_uniques = {item["name"] for item in inspector.get_unique_constraints("task_event")}
    source_document_uniques = {
        item["name"] for item in inspector.get_unique_constraints("source_document")
    }
    task_indexes = {item["name"] for item in inspector.get_indexes("research_task")}
    fetch_indexes = {item["name"] for item in inspector.get_indexes("fetch_job")}
    event_indexes = {item["name"] for item in inspector.get_indexes("task_event")}
    source_document_indexes = {item["name"] for item in inspector.get_indexes("source_document")}
    task_columns = {column["name"] for column in inspector.get_columns("research_task")}
    event_columns = {column["name"] for column in inspector.get_columns("task_event")}
    source_document_columns = {
        column["name"] for column in inspector.get_columns("source_document")
    }
    report_columns = {column["name"] for column in inspector.get_columns("report_artifact")}

    assert "uq_research_run_task_id_round_no" in run_uniques
    assert "uq_candidate_url_search_query_id_canonical_url" in candidate_uniques
    assert "uq_fetch_job_candidate_url_id_mode" in fetch_uniques
    assert "uq_report_artifact_task_id_version_format" in report_uniques
    assert "uq_task_event_task_id_sequence_no" in event_uniques
    assert "uq_source_document_content_snapshot_id" in source_document_uniques
    assert "ix_research_task_status_created_at" in task_indexes
    assert "ix_fetch_job_status_lease_until" in fetch_indexes
    assert "ix_task_event_task_id_sequence_no" in event_indexes
    assert "ix_source_document_content_snapshot_id" in source_document_indexes
    assert "revision_no" in task_columns
    assert "last_event_sequence_no" in task_columns
    assert "sequence_no" in event_columns
    assert "content_snapshot_id" in source_document_columns
    assert "content_hash" in report_columns
    assert "manifest_json" in report_columns

    upgraded_metadata = MetaData()
    research_task = Table("research_task", upgraded_metadata, autoload_with=upgraded_engine)
    research_run = Table("research_run", upgraded_metadata, autoload_with=upgraded_engine)
    task_event = Table("task_event", upgraded_metadata, autoload_with=upgraded_engine)

    with upgraded_engine.begin() as connection:
        migrated_task = connection.execute(
            select(
                research_task.c.revision_no,
                research_task.c.last_event_sequence_no,
            ).where(research_task.c.id == str(task_id))
        ).one()
        migrated_event = connection.execute(
            select(task_event.c.sequence_no).where(task_event.c.id == str(event_id))
        ).one()

        queued_task_id = uuid4()
        running_run_id = uuid4()
        connection.execute(
            research_task.insert().values(
                id=str(queued_task_id),
                query="queued runtime placeholder task",
                user_id=None,
                status="QUEUED",
                priority=100,
                constraints_json={},
                revision_no=1,
                last_event_sequence_no=0,
                created_at=seeded_at,
                updated_at=seeded_at,
                started_at=None,
                ended_at=None,
            )
        )
        connection.execute(
            research_run.insert().values(
                id=str(running_run_id),
                task_id=str(queued_task_id),
                round_no=1,
                current_state="RUNNING",
                checkpoint_json={},
                started_at=seeded_at,
                ended_at=None,
            )
        )
        connection.execute(research_run.delete().where(research_run.c.id == str(running_run_id)))
        connection.execute(research_task.delete().where(research_task.c.id == str(queued_task_id)))

    assert migrated_task.revision_no == 1
    assert migrated_task.last_event_sequence_no == 1
    assert migrated_event.sequence_no == 1

    upgraded_engine.dispose()

    command.downgrade(alembic_config, "base")

    downgraded_engine = build_engine(database_url)
    downgraded_inspector = inspect(downgraded_engine)
    assert downgraded_inspector.get_table_names() == ["alembic_version"]
    downgraded_engine.dispose()
