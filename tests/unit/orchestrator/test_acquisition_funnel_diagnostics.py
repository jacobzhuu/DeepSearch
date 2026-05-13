from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session
from tests.unit.orchestrator.test_acquisition_service import (
    _add_candidate,
    _seed_candidate,
)

from packages.db.models import FetchJob
from packages.db.repositories import FetchJobRepository, TaskEventRepository
from services.orchestrator.app.services.acquisition import ACQUISITION_FETCH_BATCH_SUMMARY_EVENT
from services.orchestrator.app.services.acquisition_diagnostics import (
    compute_acquisition_funnel_diagnostics,
)


def test_funnel_classifies_unattempted_via_batch_summary(db_session: Session) -> None:
    task, c1 = _seed_candidate(db_session)
    c2 = _add_candidate(
        db_session,
        c1,
        canonical_url="https://other.example/doc",
        domain="other.example",
        rank=2,
    )
    FetchJobRepository(db_session).add(
        FetchJob(
            task_id=task.id,
            candidate_url_id=c1.id,
            mode="HTTP",
            status="SUCCEEDED",
            scheduled_at=datetime.now(UTC),
        )
    )
    TaskEventRepository(db_session).record(
        task_id=task.id,
        event_type=ACQUISITION_FETCH_BATCH_SUMMARY_EVENT,
        payload_json={
            "stop_reason": "fetch_budget_exhausted",
            "unattempted_candidate_ids": [str(c2.id)],
        },
    )
    db_session.commit()
    out = compute_acquisition_funnel_diagnostics(
        db_session,
        task.id,
        task_query=task.query,
        settings_snapshot={"research_acquisition_max_must_fetch_per_round": 3},
    )
    assert out["counts"]["candidates_without_fetch_job"] == 1
    dist = out["candidate_not_fetched_reason_distribution"]
    assert dist.get("fetch_budget_exhausted") == 1


def test_funnel_flags_parse_limit_exhausted_with_eligible_backlog(
    db_session: Session,
    tmp_path,
) -> None:
    """Five eligible HTML snapshots with parse limit 2 and drain off → funnel infers backlog."""
    from datetime import UTC, datetime

    from tests.unit.orchestrator.test_parsing_service import DEFAULT_HTML_CONTENT, _seed_snapshot

    from packages.db.models import CandidateUrl, ContentSnapshot, FetchAttempt, FetchJob
    from packages.db.repositories import (
        CandidateUrlRepository,
        ContentSnapshotRepository,
        FetchAttemptRepository,
        FetchJobRepository,
    )
    from services.orchestrator.app.storage import FilesystemSnapshotObjectStore

    first, _, _ = _seed_snapshot(db_session, snapshot_root=tmp_path)
    fj0 = first.fetch_attempt.fetch_job
    task_id = fj0.task_id
    sq_id = fj0.candidate_url.search_query_id

    cand_repo = CandidateUrlRepository(db_session)
    fj_repo = FetchJobRepository(db_session)
    fa_repo = FetchAttemptRepository(db_session)
    cs_repo = ContentSnapshotRepository(db_session)
    store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))
    for i in range(2, 6):
        url = f"https://example.com/page{i}"
        cand = cand_repo.add(
            CandidateUrl(
                task_id=task_id,
                search_query_id=sq_id,
                original_url=url,
                canonical_url=url,
                domain="example.com",
                title=f"t{i}",
                rank=i,
                selected=False,
                metadata_json={},
            )
        )
        fj = fj_repo.add(
            FetchJob(
                task_id=task_id,
                candidate_url_id=cand.id,
                mode="HTTP",
                status="SUCCEEDED",
                scheduled_at=datetime(2026, 4, 23, 12, 5, tzinfo=UTC),
            )
        )
        fa = fa_repo.add(
            FetchAttempt(
                fetch_job_id=fj.id,
                attempt_no=1,
                http_status=200,
                error_code=None,
                started_at=datetime(2026, 4, 23, 12, 5, tzinfo=UTC),
                finished_at=datetime(2026, 4, 23, 12, 6, tzinfo=UTC),
                trace_json={},
            )
        )
        stored = store.put_bytes(
            bucket="snapshots",
            key=f"task/{task_id}/s{i}.bin",
            content=DEFAULT_HTML_CONTENT,
            content_type="text/html",
        )
        cs_repo.add(
            ContentSnapshot(
                fetch_attempt_id=fa.id,
                storage_bucket=stored.bucket,
                storage_key=stored.key,
                content_hash=f"sha256:test{i}",
                mime_type="text/html",
                bytes=len(DEFAULT_HTML_CONTENT),
                extracted_title=None,
                fetched_at=datetime(2026, 4, 23, 12, 6, tzinfo=UTC),
            )
        )
    db_session.commit()

    out = compute_acquisition_funnel_diagnostics(
        db_session,
        task_id,
        task_query="Parsing service task",
        settings_snapshot={
            "research_acquisition_max_must_fetch_per_round": 3,
            "research_parse_limit": 2,
            "research_parse_drain_enabled": False,
        },
    )
    assert out["eligible_snapshots_without_source_document"] == 5
    assert out["parse_limit_exhausted"] is True
    assert out["parse_not_attempted_reason_distribution"].get("parse_limit_exhausted") == 5
    assert out["unparsed_eligible_snapshot_domains"].get("example.com") == 5


def test_funnel_includes_official_repository_readme_counters(db_session: Session) -> None:
    task, first = _seed_candidate(
        db_session,
        query="What is LangGraph and how does it work?",
        canonical_url="https://example.com/seed",
    )
    md_readme: dict[str, object] = {
        "official_repository_readme_derivative": True,
        "source_intent": "official_repository_readme",
        "source_role": "official_repository",
    }
    _add_candidate(
        db_session,
        first,
        canonical_url="https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md",
        domain="raw.githubusercontent.com",
        rank=1,
        metadata_json=md_readme,
    )
    db_session.commit()
    out = compute_acquisition_funnel_diagnostics(
        db_session,
        task.id,
        task_query=task.query,
        settings_snapshot={"research_acquisition_max_must_fetch_per_round": 3},
    )
    assert out["official_repository_readme_candidate_count"] == 1
    assert out["official_repository_readme_selected_for_fetch_count"] == 1
    assert out["official_repository_readme_fetch_job_count"] == 0
