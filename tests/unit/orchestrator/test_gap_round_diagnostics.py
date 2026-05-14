from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from services.orchestrator.app.services.acquisition import (
    AcquisitionBatchResult,
    AcquisitionLedgerEntry,
)
from services.orchestrator.app.services.debug_pipeline import (
    DebugPipelinePreconditionError,
    DebugRealPipelineRunner,
)
from services.orchestrator.app.services.gap_round_diagnostics import (
    GAP_ROUND_OUTCOME_DRAFTED,
    GAP_ROUND_OUTCOME_SKIPPED,
    SKIP_NO_CANDIDATE_URLS,
    SKIP_NO_CONTENT_SNAPSHOTS,
    SKIP_NO_NEW_CHUNKS,
    SKIP_NO_SOURCE_CHUNKS,
    SKIP_NO_SUCCESSFUL_FETCHES,
    SKIP_SUPPLEMENTAL_SEARCH_FAILED_CONTINUING_EXISTING_EVIDENCE,
    SKIP_UNKNOWN,
    attach_gap_round_to_stage_result,
    build_gap_round_diagnostics,
    research_round_entry_from_gap_stage_result,
    summarize_gap_round_diagnostics_for_task,
)
from services.orchestrator.app.services.indexing import IndexingBatchResult
from services.orchestrator.app.services.parsing import ParseBatchResult, ParseLedgerEntry


def _snapshot_mock(snap_id: UUID) -> MagicMock:
    snap = MagicMock()
    snap.id = snap_id
    snap.mime_type = "text/html"
    snap.bytes = 120
    snap.storage_bucket = "snapshots"
    snap.storage_key = "k1"
    cand = MagicMock()
    cand.canonical_url = "https://example.com/doc"
    fj = MagicMock()
    fj.candidate_url = cand
    fa = MagicMock()
    fa.trace_json = {}
    fa.fetch_job = fj
    snap.fetch_attempt = fa
    return snap


def _high_value_search_payload(candidate_id: UUID) -> dict:
    return {
        "search_queries": [],
        "search_query_count": 1,
        "search_result_count": 2,
        "candidate_urls_added": 1,
        "candidate_urls_available": 1,
        "selected_sources": [
            {
                "candidate_url_id": str(candidate_id),
                "source_category": "official_docs_reference",
                "fetch_priority_score": 10,
            }
        ],
        "source_judgments": [],
    }


def _candidate_stub(cid: UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=cid,
        canonical_url="https://example.com/gap-doc",
        original_url="https://example.com/gap-doc",
        domain="example.com",
        title="stub",
        rank=1,
        selected=False,
        metadata_json={},
    )


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock(spec=Session)


@pytest.fixture
def mock_task() -> MagicMock:
    task = MagicMock()
    task.id = uuid4()
    task.query = "gap diagnostics unit test"
    task.status = "QUEUED"
    task.constraints_json = {}
    return task


def _runner(
    mock_session: MagicMock,
    mock_task: MagicMock,
    *,
    acquisition_service: MagicMock,
    parsing_service: MagicMock,
    indexing_service: MagicMock,
) -> DebugRealPipelineRunner:
    runner = DebugRealPipelineRunner(
        mock_session,
        search_service=MagicMock(),
        acquisition_service=acquisition_service,
        parsing_service=parsing_service,
        indexing_service=indexing_service,
        claims_service=MagicMock(),
        reporting_service=MagicMock(),
        dependencies={},
        max_gap_rounds=2,
    )
    mock_task_repo = MagicMock()
    mock_task_repo.get.return_value = mock_task
    runner.task_repository = mock_task_repo
    runner._get_task = MagicMock(return_value=mock_task)  # type: ignore[method-assign]
    runner._current_slot_coverage_summary = MagicMock(return_value=[])  # type: ignore[method-assign]
    return runner


def test_research_more_no_snapshots_sets_no_successful_fetches(
    mock_session: MagicMock,
    mock_task: MagicMock,
) -> None:
    cid = uuid4()
    acquisition = MagicMock()
    acquisition.acquire_candidates.return_value = AcquisitionBatchResult(
        task=mock_task,
        selected_candidates_from_search=[],
        selected_candidates_for_fetch=[],
        skipped_by_triage_candidates=[],
        unattempted_candidates=[],
        entries=[],
        created=0,
        skipped_existing=0,
        succeeded=0,
        failed=0,
    )
    runner = _runner(
        mock_session,
        mock_task,
        acquisition_service=acquisition,
        parsing_service=MagicMock(),
        indexing_service=MagicMock(),
    )
    runner._run_search = MagicMock(return_value=_high_value_search_payload(cid))  # type: ignore[method-assign]

    out = runner._run_research_more_round(
        mock_task.id,
        {"triggered": True, "round_no": 1, "strategy_decision": None},
    )
    assert out["gap_round_outcome"] == GAP_ROUND_OUTCOME_SKIPPED
    assert out["skip_drafting_reason"] == SKIP_NO_SUCCESSFUL_FETCHES
    diag = out["gap_round_diagnostics"]
    assert diag["content_snapshots_created"] == 0
    assert diag["parse_attempted"] is False
    assert diag["drafting_attempted"] is False


def test_research_more_empty_chunks_sets_no_source_chunks(
    mock_session: MagicMock,
    mock_task: MagicMock,
) -> None:
    cid = uuid4()
    snap_id = uuid4()
    snap = _snapshot_mock(snap_id)
    entry = AcquisitionLedgerEntry(
        candidate_url=_candidate_stub(cid),
        fetch_job=MagicMock(status="SUCCEEDED"),
        fetch_attempt=MagicMock(),
        content_snapshot=snap,
        skipped_existing=False,
    )
    acquisition = MagicMock()
    acquisition.acquire_candidates.return_value = AcquisitionBatchResult(
        task=mock_task,
        selected_candidates_from_search=[],
        selected_candidates_for_fetch=[],
        skipped_by_triage_candidates=[],
        unattempted_candidates=[],
        entries=[entry],
        created=1,
        skipped_existing=0,
        succeeded=1,
        failed=0,
    )
    doc = MagicMock()
    doc.id = uuid4()
    doc.chunks = []
    parse_entry = ParseLedgerEntry(
        content_snapshot=snap,
        source_document=doc,
        chunks_created=0,
        status="created",
        reason=None,
        updated_existing=False,
        decision="created",
    )
    parse_batch = ParseBatchResult(
        task_id=mock_task.id,
        created=1,
        updated=0,
        skipped_existing=0,
        skipped_unsupported=0,
        skipped_static_html_hold=0,
        skipped_no_valid_chunks=0,
        failed=0,
        entries=[parse_entry],
    )
    parsing = MagicMock()
    parsing.parse_snapshots.return_value = parse_batch

    runner = _runner(
        mock_session,
        mock_task,
        acquisition_service=acquisition,
        parsing_service=parsing,
        indexing_service=MagicMock(),
    )
    runner._run_search = MagicMock(return_value=_high_value_search_payload(cid))  # type: ignore[method-assign]

    out = runner._run_research_more_round(
        mock_task.id,
        {"triggered": True, "round_no": 2, "strategy_decision": None},
    )
    assert out["gap_round_outcome"] == GAP_ROUND_OUTCOME_SKIPPED
    assert out["skip_drafting_reason"] == SKIP_NO_SOURCE_CHUNKS


def test_research_more_drafted_records_claim_counts(
    mock_session: MagicMock,
    mock_task: MagicMock,
) -> None:
    cid = uuid4()
    chunk_id = uuid4()
    chunk = MagicMock()
    chunk.id = chunk_id
    snap = _snapshot_mock(uuid4())
    doc = MagicMock()
    doc.id = uuid4()
    doc.chunks = [chunk]
    entry = AcquisitionLedgerEntry(
        candidate_url=_candidate_stub(cid),
        fetch_job=MagicMock(status="SUCCEEDED"),
        fetch_attempt=MagicMock(),
        content_snapshot=snap,
        skipped_existing=False,
    )
    acquisition = MagicMock()
    acquisition.acquire_candidates.return_value = AcquisitionBatchResult(
        task=mock_task,
        selected_candidates_from_search=[],
        selected_candidates_for_fetch=[],
        skipped_by_triage_candidates=[],
        unattempted_candidates=[],
        entries=[entry],
        created=1,
        skipped_existing=0,
        succeeded=1,
        failed=0,
    )
    parse_entry = ParseLedgerEntry(
        content_snapshot=snap,
        source_document=doc,
        chunks_created=1,
        status="created",
        reason=None,
        updated_existing=False,
        decision="created",
    )
    parse_batch = ParseBatchResult(
        task_id=mock_task.id,
        created=1,
        updated=0,
        skipped_existing=0,
        skipped_unsupported=0,
        skipped_static_html_hold=0,
        skipped_no_valid_chunks=0,
        failed=0,
        entries=[parse_entry],
    )
    parsing = MagicMock()
    parsing.parse_snapshots.return_value = parse_batch
    indexing = MagicMock()
    indexing.index_source_chunks.return_value = IndexingBatchResult(
        task=mock_task,
        indexed_chunks=[chunk],
    )

    runner = _runner(
        mock_session,
        mock_task,
        acquisition_service=acquisition,
        parsing_service=parsing,
        indexing_service=indexing,
    )
    runner._run_search = MagicMock(return_value=_high_value_search_payload(cid))  # type: ignore[method-assign]
    runner._run_draft_claims = MagicMock(  # type: ignore[method-assign]
        return_value={"created_claims": 4, "reused_claims": 0},
    )
    runner._run_verify_claims = MagicMock(  # type: ignore[method-assign]
        return_value={
            "verified_claims": 4,
            "verification_summary": {"claim_status_counts": {"supported": 2}},
        },
    )

    out = runner._run_research_more_round(
        mock_task.id,
        {"triggered": True, "round_no": 1, "strategy_decision": None},
    )
    assert out["gap_round_outcome"] == GAP_ROUND_OUTCOME_DRAFTED
    assert out["skip_drafting_reason"] is None
    assert out["gap_round_diagnostics"]["drafting_created_claims"] == 4
    assert out["gap_round_diagnostics"]["verification_supported_claims"] == 2
    assert out["drafting"]["created_claims"] == 4


def test_research_more_draft_precondition_maps_to_no_new_chunks(
    mock_session: MagicMock,
    mock_task: MagicMock,
) -> None:
    cid = uuid4()
    chunk = MagicMock()
    chunk.id = uuid4()
    snap = _snapshot_mock(uuid4())
    doc = MagicMock()
    doc.id = uuid4()
    doc.chunks = [chunk]
    entry = AcquisitionLedgerEntry(
        candidate_url=_candidate_stub(cid),
        fetch_job=MagicMock(status="SUCCEEDED"),
        fetch_attempt=MagicMock(),
        content_snapshot=snap,
        skipped_existing=False,
    )
    acquisition = MagicMock()
    acquisition.acquire_candidates.return_value = AcquisitionBatchResult(
        task=mock_task,
        selected_candidates_from_search=[],
        selected_candidates_for_fetch=[],
        skipped_by_triage_candidates=[],
        unattempted_candidates=[],
        entries=[entry],
        created=1,
        skipped_existing=0,
        succeeded=1,
        failed=0,
    )
    parse_entry = ParseLedgerEntry(
        content_snapshot=snap,
        source_document=doc,
        chunks_created=1,
        status="created",
        reason=None,
        updated_existing=False,
        decision="created",
    )
    parse_batch = ParseBatchResult(
        task_id=mock_task.id,
        created=1,
        updated=0,
        skipped_existing=0,
        skipped_unsupported=0,
        skipped_static_html_hold=0,
        skipped_no_valid_chunks=0,
        failed=0,
        entries=[parse_entry],
    )
    parsing = MagicMock()
    parsing.parse_snapshots.return_value = parse_batch
    indexing = MagicMock()
    indexing.index_source_chunks.return_value = IndexingBatchResult(
        task=mock_task,
        indexed_chunks=[chunk],
    )

    runner = _runner(
        mock_session,
        mock_task,
        acquisition_service=acquisition,
        parsing_service=parsing,
        indexing_service=indexing,
    )
    runner._run_search = MagicMock(return_value=_high_value_search_payload(cid))  # type: ignore[method-assign]
    runner._run_draft_claims = MagicMock(  # type: ignore[method-assign]
        side_effect=DebugPipelinePreconditionError("no new chunks"),
    )

    out = runner._run_research_more_round(
        mock_task.id,
        {"triggered": True, "round_no": 1, "strategy_decision": None},
    )
    assert out["gap_round_outcome"] == GAP_ROUND_OUTCOME_SKIPPED
    assert out["skip_drafting_reason"] == SKIP_NO_NEW_CHUNKS


def test_stage_completed_payload_mirrors_recorded_gap_fields() -> None:
    """Event root copies ``gap_round_*`` for RESEARCHING_MORE (see ``_record_stage_completed``)."""
    diag = build_gap_round_diagnostics(
        gap_round_outcome=GAP_ROUND_OUTCOME_SKIPPED,
        skip_drafting_reason=SKIP_NO_CONTENT_SNAPSHOTS,
        gap_round_index=3,
        strategy_decision=None,
        gap_triggered=True,
        search_attempted=True,
        search_skipped_reason=None,
        search_queries_count=1,
        search_result_count=0,
        candidate_urls_added=0,
        selected_candidate_ids=[],
        selected_candidate_urls=[],
        fetch_jobs_created=None,
        fetch_attempts_created=None,
        content_snapshots_created=0,
        source_documents_created=None,
        source_chunks_created=None,
        parse_attempted=False,
        index_attempted=False,
        drafting_attempted=False,
        drafting_created_claims=None,
        drafting_reused_claims=None,
        verification_attempted=False,
        verification_supported_claims=None,
        coverage_before=[],
        coverage_after=[],
    )
    stage_result = attach_gap_round_to_stage_result({"gap_analysis": {}}, diag)
    payload: dict = {
        "stage": "RESEARCHING_MORE",
        "result": stage_result,
    }
    gap_diag = stage_result.get("gap_round_diagnostics")
    if isinstance(gap_diag, dict):
        payload["gap_round_outcome"] = gap_diag.get("gap_round_outcome")
        payload["skip_drafting_reason"] = gap_diag.get("skip_drafting_reason")
        payload["gap_round_index"] = gap_diag.get("gap_round_index")
    assert payload["gap_round_outcome"] == GAP_ROUND_OUTCOME_SKIPPED
    assert payload["skip_drafting_reason"] == SKIP_NO_CONTENT_SNAPSHOTS
    assert payload["gap_round_index"] == 3
    assert payload["result"]["gap_round_diagnostics"]["gap_round_index"] == 3


def test_build_gap_round_diagnostics_accepts_supplemental_search_failed_continuing() -> None:
    diag = build_gap_round_diagnostics(
        gap_round_outcome=GAP_ROUND_OUTCOME_SKIPPED,
        skip_drafting_reason=SKIP_SUPPLEMENTAL_SEARCH_FAILED_CONTINUING_EXISTING_EVIDENCE,
        gap_round_index=1,
        strategy_decision=None,
        gap_triggered=True,
        search_attempted=True,
        search_skipped_reason=None,
        search_queries_count=0,
        search_result_count=0,
        candidate_urls_added=0,
        selected_candidate_ids=[],
        selected_candidate_urls=[],
        fetch_jobs_created=None,
        fetch_attempts_created=None,
        content_snapshots_created=None,
        source_documents_created=None,
        source_chunks_created=None,
        parse_attempted=False,
        index_attempted=False,
        drafting_attempted=False,
        drafting_created_claims=None,
        drafting_reused_claims=None,
        verification_attempted=False,
        verification_supported_claims=None,
        coverage_before=[],
        coverage_after=[],
        supplemental_search_failed=True,
        continuing_with_existing_evidence=True,
    )
    assert (
        diag["skip_drafting_reason"] == SKIP_SUPPLEMENTAL_SEARCH_FAILED_CONTINUING_EXISTING_EVIDENCE
    )


def test_build_gap_round_diagnostics_maps_non_canonical_skip_to_unknown() -> None:
    diag = build_gap_round_diagnostics(
        gap_round_outcome=GAP_ROUND_OUTCOME_SKIPPED,
        skip_drafting_reason="not_a_real_skip_reason",
        gap_round_index=1,
        strategy_decision=None,
        gap_triggered=True,
        search_attempted=True,
        search_skipped_reason=None,
        search_queries_count=0,
        search_result_count=0,
        candidate_urls_added=0,
        selected_candidate_ids=[],
        selected_candidate_urls=[],
        fetch_jobs_created=None,
        fetch_attempts_created=None,
        content_snapshots_created=None,
        source_documents_created=None,
        source_chunks_created=None,
        parse_attempted=False,
        index_attempted=False,
        drafting_attempted=False,
        drafting_created_claims=None,
        drafting_reused_claims=None,
        verification_attempted=False,
        verification_supported_claims=None,
        coverage_before=[],
        coverage_after=[],
    )
    assert diag["skip_drafting_reason"] == SKIP_UNKNOWN


def test_summarize_gap_round_diagnostics_for_task() -> None:
    task_id = uuid4()

    def _ev(seq: int, *, outcome: str, skip: str | None, created: int) -> SimpleNamespace:
        return SimpleNamespace(
            payload_json={
                "stage": "RESEARCHING_MORE",
                "result": {
                    "gap_round_outcome": outcome,
                    "skip_drafting_reason": skip,
                    "gap_round_diagnostics": {
                        "gap_round_outcome": outcome,
                        "skip_drafting_reason": skip,
                        "drafting_attempted": outcome == GAP_ROUND_OUTCOME_DRAFTED,
                        "drafting_created_claims": created,
                        "verification_supported_claims": created,
                    },
                },
            },
        )

    events = [
        _ev(1, outcome=GAP_ROUND_OUTCOME_SKIPPED, skip=SKIP_NO_CONTENT_SNAPSHOTS, created=0),
        _ev(2, outcome=GAP_ROUND_OUTCOME_DRAFTED, skip=None, created=2),
        _ev(3, outcome=GAP_ROUND_OUTCOME_SKIPPED, skip=SKIP_NO_SOURCE_CHUNKS, created=0),
    ]
    session = MagicMock(spec=Session)
    session.scalars.return_value.all.return_value = events
    summary = summarize_gap_round_diagnostics_for_task(session, task_id)
    assert summary["gap_rounds_total"] == 3
    assert summary["gap_rounds_with_drafting"] == 1
    assert summary["gap_rounds_skipped_drafting"] == 2
    assert summary["skip_drafting_reason_distribution"] == {
        SKIP_NO_CONTENT_SNAPSHOTS: 1,
        SKIP_NO_SOURCE_CHUNKS: 1,
    }
    assert summary["nested_drafting_created_claims_total"] == 2
    assert summary["nested_verification_supported_claims_total"] == 2


def test_research_round_entry_search_timeout() -> None:
    stage = {
        "gap_analysis": {"round_no": 2},
        "search": {"failed": True, "reason": "searx_timeout", "search_queries": []},
        "gap_round_diagnostics": {
            "gap_round_index": 2,
            "supplemental_search_failed": True,
            "skip_drafting_reason": SKIP_SUPPLEMENTAL_SEARCH_FAILED_CONTINUING_EXISTING_EVIDENCE,
        },
    }
    row = research_round_entry_from_gap_stage_result(stage, sequence_index=1)
    assert row["status"] == "search_timeout"
    assert row["round"] == 2


def test_research_round_entry_no_new_urls() -> None:
    diag = build_gap_round_diagnostics(
        gap_round_outcome=GAP_ROUND_OUTCOME_SKIPPED,
        skip_drafting_reason=SKIP_NO_CANDIDATE_URLS,
        gap_round_index=1,
        strategy_decision=None,
        gap_triggered=True,
        search_attempted=True,
        search_skipped_reason=None,
        search_queries_count=1,
        search_result_count=0,
        candidate_urls_added=0,
        selected_candidate_ids=[],
        selected_candidate_urls=[],
        fetch_jobs_created=0,
        fetch_attempts_created=0,
        content_snapshots_created=0,
        source_documents_created=0,
        source_chunks_created=0,
        parse_attempted=False,
        index_attempted=False,
        drafting_attempted=False,
        drafting_created_claims=None,
        drafting_reused_claims=None,
        verification_attempted=False,
        verification_supported_claims=None,
        coverage_before=[],
        coverage_after=[],
    )
    stage = {
        "gap_analysis": {"round_no": 1},
        "search": {
            "search_queries": [{"query_text": "Claude API limits"}],
            "selected_sources": [],
            "candidate_urls_added": 0,
        },
        "gap_round_diagnostics": diag,
    }
    row = research_round_entry_from_gap_stage_result(stage, sequence_index=0)
    assert row["status"] == "no_new_urls"
    assert row["queries"] == ["Claude API limits"]
    assert row["new_candidate_urls"] == []


def test_research_round_entry_produced() -> None:
    diag = build_gap_round_diagnostics(
        gap_round_outcome=GAP_ROUND_OUTCOME_DRAFTED,
        skip_drafting_reason=None,
        gap_round_index=3,
        strategy_decision=None,
        gap_triggered=True,
        search_attempted=True,
        search_skipped_reason=None,
        search_queries_count=1,
        search_result_count=4,
        candidate_urls_added=2,
        selected_candidate_ids=[],
        selected_candidate_urls=["https://a.example/x", "https://b.example/y"],
        fetch_jobs_created=2,
        fetch_attempts_created=2,
        content_snapshots_created=2,
        source_documents_created=1,
        source_chunks_created=5,
        parse_attempted=True,
        index_attempted=True,
        drafting_attempted=True,
        drafting_created_claims=2,
        drafting_reused_claims=1,
        verification_attempted=True,
        verification_supported_claims=2,
        coverage_before=[],
        coverage_after=[],
    )
    stage = {
        "gap_analysis": {"round_no": 3},
        "search": {
            "search_queries": [{"query_text": "q1"}],
            "selected_sources": [{"canonical_url": "https://c.example/z"}],
        },
        "gap_round_diagnostics": diag,
    }
    row = research_round_entry_from_gap_stage_result(stage, sequence_index=2)
    assert row["status"] == "produced"
    assert row["fetch_succeeded"] == 2
    assert row["claims"] == 3
    assert row["supported_claims"] == 2
    assert len(row["new_candidate_urls"]) == 2

