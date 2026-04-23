from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.db.models import (
    CandidateUrl,
    CitationSpan,
    Claim,
    ClaimEvidence,
    ContentSnapshot,
    FetchAttempt,
    FetchJob,
    ReportArtifact,
    ResearchRun,
    ResearchTask,
    SearchQuery,
    SourceChunk,
    SourceDocument,
)
from packages.db.repositories import (
    CandidateUrlRepository,
    CitationSpanRepository,
    ClaimEvidenceRepository,
    ClaimRepository,
    ContentSnapshotRepository,
    FetchAttemptRepository,
    FetchJobRepository,
    ReportArtifactRepository,
    ResearchRunRepository,
    ResearchTaskRepository,
    SearchQueryRepository,
    SourceChunkRepository,
    SourceDocumentRepository,
    TaskEventRepository,
)


def _seed_task(db_session: Session) -> ResearchTask:
    task = ResearchTask(
        query="NVIDIA open model ecosystem releases in the last 30 days",
        status="PLANNED",
        priority=50,
        constraints_json={"language": "en"},
    )
    ResearchTaskRepository(db_session).add(task)
    db_session.commit()
    return task


def test_repositories_round_trip_for_task_search_and_fetch_ledgers(db_session: Session) -> None:
    task_repo = ResearchTaskRepository(db_session)
    run_repo = ResearchRunRepository(db_session)
    event_repo = TaskEventRepository(db_session)
    search_query_repo = SearchQueryRepository(db_session)
    candidate_url_repo = CandidateUrlRepository(db_session)
    fetch_job_repo = FetchJobRepository(db_session)
    fetch_attempt_repo = FetchAttemptRepository(db_session)
    content_snapshot_repo = ContentSnapshotRepository(db_session)

    task = task_repo.add(
        ResearchTask(
            query="open source NVIDIA model announcements",
            status="PLANNED",
            constraints_json={"domains_allow": ["nvidia.com", "github.com"]},
        )
    )
    run = run_repo.add(
        ResearchRun(
            task_id=task.id,
            round_no=1,
            current_state="PLANNED",
            checkpoint_json={"cursor": None},
        )
    )
    event = event_repo.record(
        task_id=task.id,
        run_id=run.id,
        event_type="TASK_PLANNED",
        payload_json={
            "event_version": 1,
            "source": "test",
            "from_status": None,
            "to_status": "PLANNED",
            "changes": {},
        },
    )
    search_query = search_query_repo.add(
        SearchQuery(
            task_id=task.id,
            run_id=run.id,
            query_text="site:nvidia.com open model release",
            provider="searxng",
            round_no=1,
            raw_response_json={"items": 1},
        )
    )
    candidate_url = candidate_url_repo.add(
        CandidateUrl(
            task_id=task.id,
            search_query_id=search_query.id,
            original_url="https://www.nvidia.com/en-us/blog/example",
            canonical_url="https://www.nvidia.com/en-us/blog/example",
            domain="nvidia.com",
            title="Example source",
            rank=1,
            selected=True,
            metadata_json={"provider_rank": 1},
        )
    )
    fetch_job = fetch_job_repo.add(
        FetchJob(
            task_id=task.id,
            candidate_url_id=candidate_url.id,
            mode="HTTP",
            status="PENDING",
        )
    )
    fetch_attempt = fetch_attempt_repo.add(
        FetchAttempt(
            fetch_job_id=fetch_job.id,
            attempt_no=1,
            http_status=200,
            trace_json={"duration_ms": 120},
        )
    )
    content_snapshot = content_snapshot_repo.add(
        ContentSnapshot(
            fetch_attempt_id=fetch_attempt.id,
            storage_bucket="snapshots",
            storage_key=f"{task.id}/run-1/example.html",
            content_hash="sha256:example",
            mime_type="text/html",
            bytes=512,
            extracted_title="Example source",
        )
    )
    db_session.commit()

    assert task_repo.get(task.id) is not None
    assert [item.id for item in task_repo.list_by_status("PLANNED")] == [task.id]
    assert run_repo.get_for_task_round(task.id, 1) is not None
    assert [item.id for item in run_repo.list_for_task(task.id)] == [run.id]
    assert [item.id for item in event_repo.list_for_task(task.id)] == [event.id]
    assert event.sequence_no == 1
    assert [item.id for item in search_query_repo.list_for_run(run.id)] == [search_query.id]
    assert [item.id for item in candidate_url_repo.list_for_search_query(search_query.id)] == [
        candidate_url.id
    ]
    assert [item.id for item in fetch_job_repo.list_for_task(task.id)] == [fetch_job.id]
    assert [item.id for item in fetch_attempt_repo.list_for_job(fetch_job.id)] == [fetch_attempt.id]
    assert content_snapshot_repo.get_for_fetch_attempt(fetch_attempt.id) == content_snapshot


def test_repositories_round_trip_for_sources_claims_and_reports(db_session: Session) -> None:
    task = _seed_task(db_session)

    source_document_repo = SourceDocumentRepository(db_session)
    source_chunk_repo = SourceChunkRepository(db_session)
    citation_span_repo = CitationSpanRepository(db_session)
    claim_repo = ClaimRepository(db_session)
    claim_evidence_repo = ClaimEvidenceRepository(db_session)
    report_artifact_repo = ReportArtifactRepository(db_session)

    source_document = source_document_repo.add(
        SourceDocument(
            task_id=task.id,
            canonical_url="https://example.com/source",
            domain="example.com",
            title="Source document",
            source_type="web",
            published_at=datetime(2026, 4, 1, tzinfo=UTC),
            fetched_at=datetime(2026, 4, 22, tzinfo=UTC),
            authority_score=0.8,
            freshness_score=0.7,
            originality_score=0.9,
            consistency_score=0.85,
            safety_score=0.95,
            final_source_score=0.84,
        )
    )
    source_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text="NVIDIA released a new open model toolkit in April.",
            token_count=12,
            metadata_json={"section": "summary"},
        )
    )
    citation_span = citation_span_repo.add(
        CitationSpan(
            source_chunk_id=source_chunk.id,
            start_offset=0,
            end_offset=30,
            excerpt="NVIDIA released a new open model toolkit",
            normalized_excerpt_hash="hash:example",
        )
    )
    claim = claim_repo.add(
        Claim(
            task_id=task.id,
            statement="NVIDIA released a new open model toolkit in April 2026.",
            claim_type="fact",
            confidence=0.88,
            verification_status="supported",
            notes_json={"source_count": 1},
        )
    )
    claim_evidence = claim_evidence_repo.add(
        ClaimEvidence(
            claim_id=claim.id,
            citation_span_id=citation_span.id,
            relation_type="support",
            score=0.91,
        )
    )
    report_artifact = report_artifact_repo.add(
        ReportArtifact(
            task_id=task.id,
            version=1,
            storage_bucket="reports",
            storage_key=f"{task.id}/v1/report.md",
            format="markdown",
        )
    )
    db_session.commit()

    assert (
        source_document_repo.get_for_task_url(task.id, source_document.canonical_url)
        == source_document
    )
    assert [item.id for item in source_document_repo.list_for_task(task.id)] == [source_document.id]
    assert [item.id for item in source_chunk_repo.list_for_document(source_document.id)] == [
        source_chunk.id
    ]
    assert [item.id for item in citation_span_repo.list_for_chunk(source_chunk.id)] == [
        citation_span.id
    ]
    assert [item.id for item in claim_repo.list_for_task(task.id)] == [claim.id]
    assert [item.id for item in claim_evidence_repo.list_for_claim(claim.id)] == [claim_evidence.id]
    assert report_artifact_repo.get_latest_for_task(task.id) == report_artifact
    assert [item.id for item in report_artifact_repo.list_for_task(task.id)] == [report_artifact.id]


def test_unique_constraints_reject_duplicate_rounds_and_candidate_urls(db_session: Session) -> None:
    task = _seed_task(db_session)

    run = ResearchRun(task_id=task.id, round_no=1, current_state="PLANNED", checkpoint_json={})
    db_session.add(run)
    db_session.commit()

    db_session.add(
        ResearchRun(task_id=task.id, round_no=1, current_state="PLANNED", checkpoint_json={})
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    search_query = SearchQuery(
        task_id=task.id,
        run_id=run.id,
        query_text="duplicate URL coverage",
        provider="searxng",
        round_no=1,
    )
    db_session.add(search_query)
    db_session.flush()

    db_session.add(
        CandidateUrl(
            task_id=task.id,
            search_query_id=search_query.id,
            original_url="https://example.com/a",
            canonical_url="https://example.com/a",
            domain="example.com",
            rank=1,
            metadata_json={},
        )
    )
    db_session.flush()

    db_session.add(
        CandidateUrl(
            task_id=task.id,
            search_query_id=search_query.id,
            original_url="https://example.com/a?dup=1",
            canonical_url="https://example.com/a",
            domain="example.com",
            rank=2,
            metadata_json={},
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
