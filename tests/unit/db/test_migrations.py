from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from packages.db.session import build_engine


def test_upgrade_and_downgrade_apply_core_ledger_schema(
    alembic_config: Config,
    database_url: str,
) -> None:
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
    report_uniques = {item["name"] for item in inspector.get_unique_constraints("report_artifact")}
    task_indexes = {item["name"] for item in inspector.get_indexes("research_task")}
    fetch_indexes = {item["name"] for item in inspector.get_indexes("fetch_job")}

    assert "uq_research_run_task_id_round_no" in run_uniques
    assert "uq_candidate_url_search_query_id_canonical_url" in candidate_uniques
    assert "uq_report_artifact_task_id_version_format" in report_uniques
    assert "ix_research_task_status_created_at" in task_indexes
    assert "ix_fetch_job_status_lease_until" in fetch_indexes

    upgraded_engine.dispose()

    command.downgrade(alembic_config, "base")

    downgraded_engine = build_engine(database_url)
    downgraded_inspector = inspect(downgraded_engine)
    assert downgraded_inspector.get_table_names() == ["alembic_version"]
    downgraded_engine.dispose()
