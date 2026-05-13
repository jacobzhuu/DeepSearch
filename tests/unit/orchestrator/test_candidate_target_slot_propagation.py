from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from packages.db.models import (
    CandidateUrl,
    ContentSnapshot,
    FetchAttempt,
    FetchJob,
    ResearchRun,
    SearchQuery,
    SourceChunk,
    SourceDocument,
)
from packages.db.repositories import (
    CandidateUrlRepository,
    ContentSnapshotRepository,
    FetchAttemptRepository,
    FetchJobRepository,
    ResearchRunRepository,
    SearchQueryRepository,
    SourceChunkRepository,
    SourceDocumentRepository,
)
from services.orchestrator.app.indexing import (
    ChunkIndexDocument,
    IndexedChunkPage,
    IndexedChunkRecord,
)
from services.orchestrator.app.research_quality.candidate_target_slots import (
    ROLES_ELIGIBLE_FOR_PLANNER_TARGET_SLOT_MERGE,
    load_candidate_target_slots_by_source_document,
    merge_technical_lexical_and_planner_slots,
)
from services.orchestrator.app.services.claims import create_claim_drafting_service
from services.orchestrator.app.services.research_tasks import create_research_task_service


class _IndexForTask:
    def __init__(
        self,
        *,
        task_id: UUID,
        source_document_id: UUID,
        source_chunk_id: UUID,
        canonical_url: str,
        domain: str,
        chunk_no: int,
        text: str,
    ) -> None:
        self._task_id = task_id
        self._source_document_id = source_document_id
        self._source_chunk_id = source_chunk_id
        self._canonical_url = canonical_url
        self._domain = domain
        self._chunk_no = chunk_no
        self._text = text

    def validate_configuration(self) -> None:
        return None

    def ensure_index(self) -> None:
        return None

    def upsert_chunks(self, documents: list[ChunkIndexDocument]) -> None:
        del documents

    def list_chunks(self, *, task_id: UUID, offset: int, limit: int) -> IndexedChunkPage:
        del offset, limit
        return IndexedChunkPage(total=0, hits=[])

    def retrieve_chunks(
        self,
        *,
        task_id: UUID,
        query: str,
        offset: int,
        limit: int,
    ) -> IndexedChunkPage:
        del query
        if task_id != self._task_id:
            return IndexedChunkPage(total=0, hits=[])
        hit = IndexedChunkRecord(
            task_id=task_id,
            source_document_id=self._source_document_id,
            source_chunk_id=self._source_chunk_id,
            canonical_url=self._canonical_url,
            domain=self._domain,
            chunk_no=self._chunk_no,
            text=self._text,
            metadata={},
            score=1.0,
        )
        return IndexedChunkPage(total=1, hits=[hit])


def _seed_official_doc_with_planner_limitations(db_session: Session) -> tuple[UUID, SourceChunk]:
    task = create_research_task_service(db_session).create_task(
        query="What is LangGraph and how does it work?",
        constraints={},
    )
    run = ResearchRunRepository(db_session).add(
        ResearchRun(
            task_id=task.id,
            round_no=1,
            current_state="PLANNED",
            checkpoint_json={},
        )
    )
    sq = SearchQueryRepository(db_session).add(
        SearchQuery(
            task_id=task.id,
            run_id=run.id,
            query_text="LangGraph limitations official documentation",
            provider="searxng",
            round_no=1,
            issued_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
            raw_response_json={},
        )
    )
    cand = CandidateUrlRepository(db_session).add(
        CandidateUrl(
            task_id=task.id,
            search_query_id=sq.id,
            original_url="https://docs.langchain.com/oss/python/langgraph/overview",
            canonical_url="https://docs.langchain.com/oss/python/langgraph/overview",
            domain="docs.langchain.com",
            title="LangGraph overview",
            rank=1,
            selected=True,
            metadata_json={"target_slots": ["limitations"]},
        )
    )
    fj = FetchJobRepository(db_session).add(
        FetchJob(
            task_id=task.id,
            candidate_url_id=cand.id,
            mode="HTTP",
            status="SUCCEEDED",
            scheduled_at=datetime(2026, 4, 23, 12, 1, tzinfo=UTC),
        )
    )
    fa = FetchAttemptRepository(db_session).add(
        FetchAttempt(
            fetch_job_id=fj.id,
            attempt_no=1,
            http_status=200,
            error_code=None,
            started_at=datetime(2026, 4, 23, 12, 1, tzinfo=UTC),
            finished_at=datetime(2026, 4, 23, 12, 2, tzinfo=UTC),
            trace_json={},
        )
    )
    snap = ContentSnapshotRepository(db_session).add(
        ContentSnapshot(
            fetch_attempt_id=fa.id,
            storage_bucket="snapshots",
            storage_key=f"task/{task.id}/snap.bin",
            content_hash="sha256:testhash",
            mime_type="text/html",
            bytes=120,
            extracted_title=None,
            fetched_at=datetime(2026, 4, 23, 12, 2, tzinfo=UTC),
        )
    )
    body = (
        "LangGraph overview. LangGraph provides durable execution and checkpointing for "
        "long-running agent workflows using StateGraph nodes and edges. "
        "LangGraph caveats for integrators include unbounded growth of persisted state "
        "without pruning."
    )
    doc = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=snap.id,
            canonical_url="https://docs.langchain.com/oss/python/langgraph/overview",
            domain="docs.langchain.com",
            title="LangGraph overview",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 23, 12, 2, tzinfo=UTC),
            authority_score=0.9,
            freshness_score=0.8,
            originality_score=0.7,
            consistency_score=0.8,
            safety_score=0.9,
            final_source_score=0.85,
        )
    )
    chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=doc.id,
            chunk_no=0,
            text=body,
            token_count=40,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    db_session.commit()
    db_session.refresh(chunk)
    return task.id, chunk


def test_merge_technical_lexical_and_planner_slots_unions() -> None:
    doc_id = UUID("00000000-0000-4000-8000-000000000001")
    slots = merge_technical_lexical_and_planner_slots(
        lexical_slots=("definition", "core_abstractions"),
        source_document_id=doc_id,
        source_role="official_docs",
        document_target_slots={doc_id: frozenset({"limitations"})},
    )
    merged, planner_only = slots
    assert "limitations" in merged
    assert "definition" in merged
    assert planner_only == ("limitations",)


def test_merge_blocked_for_ineligible_source_role() -> None:
    doc_id = UUID("00000000-0000-4000-8000-000000000002")
    merged, planner_only = merge_technical_lexical_and_planner_slots(
        lexical_slots=("definition",),
        source_document_id=doc_id,
        source_role="generic_article",
        document_target_slots={doc_id: frozenset({"limitations"})},
    )
    assert merged == ("definition",)
    assert planner_only == ()
    assert "generic_article" not in ROLES_ELIGIBLE_FOR_PLANNER_TARGET_SLOT_MERGE


def test_load_candidate_target_slots_filters_unknown_slots(db_session: Session) -> None:
    task_id, _chunk = _seed_official_doc_with_planner_limitations(db_session)
    mapping, meta = load_candidate_target_slots_by_source_document(db_session, task_id)
    assert meta["documents_with_slots"] >= 1
    for slots in mapping.values():
        assert "limitations" in slots
        assert "not_a_real_slot_id" not in slots


def test_candidate_target_slots_propagate_into_draft_claim_slots(db_session: Session) -> None:
    task_id, chunk = _seed_official_doc_with_planner_limitations(db_session)
    backend = _IndexForTask(
        task_id=task_id,
        source_document_id=chunk.source_document_id,
        source_chunk_id=chunk.id,
        canonical_url="https://docs.langchain.com/oss/python/langgraph/overview",
        domain="docs.langchain.com",
        chunk_no=chunk.chunk_no,
        text=chunk.text,
    )
    service = create_claim_drafting_service(
        db_session,
        index_backend=backend,
        max_candidates_per_request=10,
    )
    result = service.draft_claims(
        task_id,
        query="What is LangGraph and how does it work?",
        source_chunk_ids=None,
        limit=10,
    )
    assert result.diagnostics.get("candidate_target_slots_seen_count", 0) >= 1
    assert result.diagnostics.get("claims_with_candidate_target_slots_count", 0) >= 1
    slot_union = set()
    for entry in result.entries:
        ec = entry.claim.notes_json.get("evidence_candidate") or {}
        for sid in ec.get("slot_ids") or []:
            slot_union.add(sid)
    assert "limitations" in slot_union
    tiers = result.diagnostics.get("candidate_tiers_by_slot") or {}
    lim_tiers = tiers.get("limitations") or {}
    assert int(lim_tiers.get("main_candidate", 0)) >= 1
    assert int(result.diagnostics.get("limitations_main_candidate_count", 0)) >= 1


def test_non_technical_query_does_not_apply_planner_slots(db_session: Session) -> None:
    """Same ledger chain but non-technical query skips merge path in drafting."""
    task_id, chunk = _seed_official_doc_with_planner_limitations(db_session)
    backend = _IndexForTask(
        task_id=task_id,
        source_document_id=chunk.source_document_id,
        source_chunk_id=chunk.id,
        canonical_url="https://docs.langchain.com/oss/python/langgraph/overview",
        domain="docs.langchain.com",
        chunk_no=chunk.chunk_no,
        text=chunk.text,
    )
    service = create_claim_drafting_service(
        db_session,
        index_backend=backend,
        max_candidates_per_request=10,
    )
    result = service.draft_claims(
        task_id,
        query="illustrative examples for tests",
        source_chunk_ids=None,
        limit=10,
    )
    assert result.diagnostics.get("candidate_target_slots_applied_count", 0) == 0
    assert result.diagnostics.get("limitations_claims_from_candidate_target_slots_count", 0) == 0
