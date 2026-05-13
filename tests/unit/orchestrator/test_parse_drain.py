from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tests.unit.orchestrator.test_parsing_service import DEFAULT_HTML_CONTENT, _seed_snapshot

from packages.db.models import CandidateUrl, ContentSnapshot, FetchAttempt, FetchJob, SourceDocument
from packages.db.repositories import (
    CandidateUrlRepository,
    ContentSnapshotRepository,
    FetchAttemptRepository,
    FetchJobRepository,
)
from services.orchestrator.app.services.debug_pipeline import DebugRealPipelineRunner
from services.orchestrator.app.services.parsing import ParseBatchResult, create_parsing_service
from services.orchestrator.app.storage import FilesystemSnapshotObjectStore


def _append_html_snapshot(
    db_session: Session,
    snapshot_root: Path,
    *,
    task_id: UUID,
    search_query_id: UUID,
    rank: int,
    url_suffix: str,
) -> ContentSnapshot:
    cand_repo = CandidateUrlRepository(db_session)
    fj_repo = FetchJobRepository(db_session)
    fa_repo = FetchAttemptRepository(db_session)
    cs_repo = ContentSnapshotRepository(db_session)
    store = FilesystemSnapshotObjectStore(root_directory=str(snapshot_root))
    url = f"https://example.com/{url_suffix}"
    cand = cand_repo.add(
        CandidateUrl(
            task_id=task_id,
            search_query_id=search_query_id,
            original_url=url,
            canonical_url=url,
            domain="example.com",
            title=f"t{rank}",
            rank=rank,
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
        key=f"task/{task_id}/{url_suffix}.bin",
        content=DEFAULT_HTML_CONTENT,
        content_type="text/html",
    )
    snap = cs_repo.add(
        ContentSnapshot(
            fetch_attempt_id=fa.id,
            storage_bucket=stored.bucket,
            storage_key=stored.key,
            content_hash=f"sha256:{url_suffix}",
            mime_type="text/html",
            bytes=len(DEFAULT_HTML_CONTENT),
            extracted_title=None,
            fetched_at=datetime(2026, 4, 23, 12, 6, tzinfo=UTC),
        )
    )
    db_session.commit()
    return snap


def _minimal_runner(
    db_session: Session,
    parsing_service,
    *,
    parse_limit: int,
    parse_drain_enabled: bool,
    parse_drain_max_batches: int = 3,
    parse_drain_target_documents: int = 50,
    parse_drain_max_seconds: float = 0.0,
) -> DebugRealPipelineRunner:
    return DebugRealPipelineRunner(
        db_session,
        search_service=object(),
        acquisition_service=object(),
        parsing_service=parsing_service,
        indexing_service=object(),
        claims_service=object(),
        reporting_service=object(),
        planner_service=None,
        dependencies={"search_mode": "test"},
        parse_limit=parse_limit,
        parse_drain_enabled=parse_drain_enabled,
        parse_drain_max_batches=parse_drain_max_batches,
        parse_drain_target_documents=parse_drain_target_documents,
        parse_drain_max_seconds=parse_drain_max_seconds,
        max_gap_rounds=0,
    )


def test_parse_drain_disabled_single_batch(db_session: Session, tmp_path: Path) -> None:
    first, _, _ = _seed_snapshot(db_session, snapshot_root=tmp_path)
    task_id = first.fetch_attempt.fetch_job.task_id
    sq_id = first.fetch_attempt.fetch_job.candidate_url.search_query_id
    for r, suffix in enumerate(("p2", "p3", "p4", "p5"), start=2):
        _append_html_snapshot(
            db_session,
            tmp_path,
            task_id=task_id,
            search_query_id=sq_id,
            rank=r,
            url_suffix=suffix,
        )
    store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))
    parsing = create_parsing_service(db_session, snapshot_object_store=store)
    runner = _minimal_runner(
        db_session,
        parsing,
        parse_limit=2,
        parse_drain_enabled=False,
    )
    out = runner._run_parse(task_id)
    assert out["parse_drain_batches_run"] == 1
    assert out["parse_drain_stop_reason"] == "disabled"
    assert out["created"] == 2
    assert parsing.count_eligible_snapshots_without_source_document(task_id) == 3


def test_parse_drain_processes_multiple_batches(db_session: Session, tmp_path: Path) -> None:
    first, _, _ = _seed_snapshot(db_session, snapshot_root=tmp_path)
    task_id = first.fetch_attempt.fetch_job.task_id
    sq_id = first.fetch_attempt.fetch_job.candidate_url.search_query_id
    for r, suffix in enumerate(("p2", "p3", "p4", "p5"), start=2):
        _append_html_snapshot(
            db_session,
            tmp_path,
            task_id=task_id,
            search_query_id=sq_id,
            rank=r,
            url_suffix=suffix,
        )
    store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))
    parsing = create_parsing_service(db_session, snapshot_object_store=store)
    runner = _minimal_runner(
        db_session,
        parsing,
        parse_limit=2,
        parse_drain_enabled=True,
        parse_drain_max_batches=3,
    )
    out = runner._run_parse(task_id)
    assert out["parse_drain_batches_run"] == 3
    assert out["parse_drain_stop_reason"] == "fully_drained"
    assert out["created"] == 5
    assert parsing.count_eligible_snapshots_without_source_document(task_id) == 0
    assert out["parse_limit_exhausted"] is False


def test_parse_drain_stops_at_max_batches_with_backlog(db_session: Session, tmp_path: Path) -> None:
    first, _, _ = _seed_snapshot(db_session, snapshot_root=tmp_path)
    task_id = first.fetch_attempt.fetch_job.task_id
    sq_id = first.fetch_attempt.fetch_job.candidate_url.search_query_id
    for r, suffix in enumerate(
        ("p2", "p3", "p4", "p5", "p6", "p7", "p8"),
        start=2,
    ):
        _append_html_snapshot(
            db_session,
            tmp_path,
            task_id=task_id,
            search_query_id=sq_id,
            rank=r,
            url_suffix=suffix,
        )
    store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))
    parsing = create_parsing_service(db_session, snapshot_object_store=store)
    runner = _minimal_runner(
        db_session,
        parsing,
        parse_limit=3,
        parse_drain_enabled=True,
        parse_drain_max_batches=2,
    )
    out = runner._run_parse(task_id)
    assert out["parse_drain_batches_run"] == 2
    assert out["parse_drain_stop_reason"] == "max_batches"
    assert out["created"] == 6
    assert parsing.count_eligible_snapshots_without_source_document(task_id) == 2
    assert out["parse_limit_exhausted"] is True


def test_parse_drain_stops_on_no_progress_when_batch_produces_no_documents(
    db_session: Session,
    tmp_path: Path,
) -> None:
    """If a later batch creates nothing, drain stops with ``no_progress`` (backlog may remain)."""
    first, _, _ = _seed_snapshot(db_session, snapshot_root=tmp_path)
    task_id = first.fetch_attempt.fetch_job.task_id
    sq_id = first.fetch_attempt.fetch_job.candidate_url.search_query_id
    _append_html_snapshot(
        db_session,
        tmp_path,
        task_id=task_id,
        search_query_id=sq_id,
        rank=2,
        url_suffix="p2",
    )
    store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))
    inner = create_parsing_service(db_session, snapshot_object_store=store)

    class _StallSecondBatch:
        def __init__(self) -> None:
            self.calls = 0

        def parse_snapshots(
            self,
            tid: UUID,
            *,
            content_snapshot_ids: list | None,
            limit: int | None,
        ) -> ParseBatchResult:
            self.calls += 1
            if self.calls == 1:
                return inner.parse_snapshots(
                    tid, content_snapshot_ids=content_snapshot_ids, limit=limit
                )
            return ParseBatchResult(
                task_id=tid,
                created=0,
                updated=0,
                skipped_existing=0,
                skipped_unsupported=0,
                skipped_static_html_hold=0,
                skipped_no_valid_chunks=0,
                failed=0,
                entries=[],
            )

        def count_source_documents_for_task(self, tid: UUID) -> int:
            return inner.count_source_documents_for_task(tid)

        def count_eligible_snapshots_without_source_document(self, tid: UUID) -> int:
            return inner.count_eligible_snapshots_without_source_document(tid)

    parsing = _StallSecondBatch()
    runner = _minimal_runner(
        db_session,
        parsing,
        parse_limit=1,
        parse_drain_enabled=True,
        parse_drain_max_batches=5,
    )
    out = runner._run_parse(task_id)
    assert out["parse_drain_batches_run"] == 2
    assert out["parse_drain_stop_reason"] == "no_progress"
    assert out["created"] == 1
    assert inner.count_eligible_snapshots_without_source_document(task_id) == 1


def test_parse_drain_fully_drains_with_padding_batch(db_session: Session, tmp_path: Path) -> None:
    """Four eligible snapshots and ``limit=3``: second batch finishes the last one then stops."""
    first, _, _ = _seed_snapshot(db_session, snapshot_root=tmp_path)
    task_id = first.fetch_attempt.fetch_job.task_id
    sq_id = first.fetch_attempt.fetch_job.candidate_url.search_query_id
    for r, suffix in enumerate(("p2", "p3", "p4"), start=2):
        _append_html_snapshot(
            db_session,
            tmp_path,
            task_id=task_id,
            search_query_id=sq_id,
            rank=r,
            url_suffix=suffix,
        )
    store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))
    parsing = create_parsing_service(db_session, snapshot_object_store=store)
    runner = _minimal_runner(
        db_session,
        parsing,
        parse_limit=3,
        parse_drain_enabled=True,
        parse_drain_max_batches=5,
    )
    out = runner._run_parse(task_id)
    assert out["parse_drain_batches_run"] == 2
    assert out["parse_drain_stop_reason"] == "fully_drained"
    assert out["created"] == 4


def test_parse_drain_idempotent_second_run_skips_existing(
    db_session: Session,
    tmp_path: Path,
) -> None:
    first, _, _ = _seed_snapshot(db_session, snapshot_root=tmp_path)
    task_id = first.fetch_attempt.fetch_job.task_id
    store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))
    parsing = create_parsing_service(db_session, snapshot_object_store=store)
    runner = _minimal_runner(
        db_session,
        parsing,
        parse_limit=5,
        parse_drain_enabled=False,
    )
    out1 = runner._run_parse(task_id)
    assert out1["created"] == 1
    out2 = runner._run_parse(task_id)
    assert out2["created"] == 0
    assert out2["skipped_existing"] >= 1
    n_docs = int(
        db_session.scalar(
            select(func.count())
            .select_from(SourceDocument)
            .where(SourceDocument.task_id == task_id)
        )
        or 0
    )
    assert n_docs == 1
