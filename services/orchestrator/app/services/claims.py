from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from packages.db.models import CitationSpan, Claim, ClaimEvidence, ResearchTask, SourceChunk
from packages.db.repositories import (
    CitationSpanRepository,
    ClaimEvidenceRepository,
    ClaimRepository,
    ResearchTaskRepository,
    SourceChunkRepository,
)
from packages.observability import get_logger, record_verify_results
from services.orchestrator.app.claims import (
    CLAIM_EVIDENCE_RELATION_CONTRADICT,
    CLAIM_EVIDENCE_RELATION_SUPPORT,
    CLAIM_TYPE_FACT,
    CLAIM_VERIFICATION_STATUS_DRAFT,
    build_verification_rationale,
    compute_claim_confidence,
    draft_claim_statement,
    normalized_excerpt_hash,
    resolve_verification_status,
    select_supporting_span,
    select_verification_span,
    validate_citation_span,
)
from services.orchestrator.app.indexing import ChunkIndexBackend, IndexedChunkRecord
from services.orchestrator.app.services.research_tasks import (
    PHASE2_ACTIVE_STATUS,
    TaskNotFoundError,
)

logger = get_logger(__name__)


class ClaimDraftingConflictError(Exception):
    def __init__(self, task_id: UUID, current_status: str) -> None:
        super().__init__(f"cannot draft claims for task {task_id} from status {current_status}")
        self.task_id = task_id
        self.current_status = current_status


class ClaimDraftingInputError(Exception):
    def __init__(self) -> None:
        super().__init__("at least one of query or source_chunk_ids must be provided")


class ClaimDraftingDataIntegrityError(Exception):
    def __init__(self, source_chunk_id: UUID) -> None:
        super().__init__(f"retrieval hit references missing source_chunk {source_chunk_id}")
        self.source_chunk_id = source_chunk_id


class ClaimSourceChunkNotFoundError(Exception):
    def __init__(self, task_id: UUID, source_chunk_id: UUID) -> None:
        super().__init__(f"source_chunk {source_chunk_id} was not found for task {task_id}")
        self.task_id = task_id
        self.source_chunk_id = source_chunk_id


class ClaimNotFoundError(Exception):
    def __init__(self, task_id: UUID, claim_id: UUID) -> None:
        super().__init__(f"claim {claim_id} was not found for task {task_id}")
        self.task_id = task_id
        self.claim_id = claim_id


class ClaimVerificationConflictError(Exception):
    def __init__(self, task_id: UUID, current_status: str) -> None:
        super().__init__(f"cannot verify claims for task {task_id} from status {current_status}")
        self.task_id = task_id
        self.current_status = current_status


@dataclass(frozen=True)
class DraftClaimEntry:
    claim: Claim
    citation_span: CitationSpan
    claim_evidence: ClaimEvidence
    source_chunk: SourceChunk
    reused_claim: bool
    reused_citation_span: bool
    reused_claim_evidence: bool
    retrieval_score: float | None


@dataclass(frozen=True)
class ClaimDraftBatchResult:
    task: ResearchTask
    effective_query: str
    created_claims: int
    reused_claims: int
    created_citation_spans: int
    reused_citation_spans: int
    created_claim_evidence: int
    reused_claim_evidence: int
    entries: list[DraftClaimEntry]


@dataclass(frozen=True)
class ClaimSummaryEntry:
    claim: Claim
    support_evidence_count: int
    contradict_evidence_count: int
    rationale: str | None


@dataclass(frozen=True)
class VerifiedClaimEntry:
    claim: Claim
    support_evidence_count: int
    contradict_evidence_count: int
    rationale: str


@dataclass(frozen=True)
class ClaimVerificationBatchResult:
    task: ResearchTask
    verified_claims: int
    created_citation_spans: int
    reused_citation_spans: int
    created_claim_evidence: int
    reused_claim_evidence: int
    entries: list[VerifiedClaimEntry]


class ClaimDraftingService:
    def __init__(
        self,
        session: Session,
        *,
        task_repository: ResearchTaskRepository,
        source_chunk_repository: SourceChunkRepository,
        citation_span_repository: CitationSpanRepository,
        claim_repository: ClaimRepository,
        claim_evidence_repository: ClaimEvidenceRepository,
        index_backend: ChunkIndexBackend,
        max_candidates_per_request: int,
        verification_max_claims_per_request: int,
        retrieval_max_results_per_request: int,
        draft_allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
        verify_allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
    ) -> None:
        self.session = session
        self.task_repository = task_repository
        self.source_chunk_repository = source_chunk_repository
        self.citation_span_repository = citation_span_repository
        self.claim_repository = claim_repository
        self.claim_evidence_repository = claim_evidence_repository
        self.index_backend = index_backend
        self.max_candidates_per_request = max_candidates_per_request
        self.verification_max_claims_per_request = verification_max_claims_per_request
        self.retrieval_max_results_per_request = retrieval_max_results_per_request
        self.draft_allowed_statuses = draft_allowed_statuses
        self.verify_allowed_statuses = verify_allowed_statuses

    def draft_claims(
        self,
        task_id: UUID,
        *,
        query: str | None,
        source_chunk_ids: list[UUID] | None,
        limit: int | None,
    ) -> ClaimDraftBatchResult:
        task = self._get_task(task_id)
        if task.status not in self.draft_allowed_statuses:
            raise ClaimDraftingConflictError(task.id, task.status)
        if query is None and source_chunk_ids is None:
            raise ClaimDraftingInputError()

        effective_limit = self.max_candidates_per_request
        if limit is not None:
            effective_limit = min(limit, self.max_candidates_per_request)

        effective_query = (query or task.query).strip()
        selected_chunks = self._select_chunks(
            task.id,
            query=effective_query,
            source_chunk_ids=source_chunk_ids,
            limit=effective_limit,
        )

        created_claims = 0
        reused_claims = 0
        created_citation_spans = 0
        reused_citation_spans = 0
        created_claim_evidence = 0
        reused_claim_evidence = 0
        entries: list[DraftClaimEntry] = []

        for source_chunk, retrieval_score in selected_chunks:
            supporting_span = select_supporting_span(source_chunk.text, effective_query)
            statement = draft_claim_statement(supporting_span.excerpt)
            confidence = compute_claim_confidence(
                query=effective_query,
                statement=statement,
                retrieval_score=retrieval_score,
            )

            claim = self.claim_repository.get_for_task_statement(task.id, statement)
            reused_claim = claim is not None
            if claim is None:
                claim = self.claim_repository.add(
                    Claim(
                        task_id=task.id,
                        statement=statement,
                        claim_type=CLAIM_TYPE_FACT,
                        confidence=confidence,
                        verification_status=CLAIM_VERIFICATION_STATUS_DRAFT,
                        notes_json={
                            "draft_query": effective_query,
                            "draft_method": "chunk_sentence_v1",
                            "source_chunk_id": str(source_chunk.id),
                            "source_document_id": str(source_chunk.source_document_id),
                            "retrieval_score": retrieval_score,
                            "relation_type": CLAIM_EVIDENCE_RELATION_SUPPORT,
                        },
                    )
                )
                created_claims += 1
            else:
                reused_claims += 1

            citation_span, reused_citation_span = self._ensure_citation_span(
                source_chunk=source_chunk,
                start_offset=supporting_span.start_offset,
                end_offset=supporting_span.end_offset,
                excerpt=supporting_span.excerpt,
            )
            if reused_citation_span:
                reused_citation_spans += 1
            else:
                created_citation_spans += 1

            claim_evidence, reused_evidence = self._ensure_claim_evidence(
                claim=claim,
                citation_span=citation_span,
                relation_type=CLAIM_EVIDENCE_RELATION_SUPPORT,
                score=confidence,
            )
            if reused_evidence:
                reused_claim_evidence += 1
            else:
                created_claim_evidence += 1

            entries.append(
                DraftClaimEntry(
                    claim=claim,
                    citation_span=citation_span,
                    claim_evidence=claim_evidence,
                    source_chunk=source_chunk,
                    reused_claim=reused_claim,
                    reused_citation_span=reused_citation_span,
                    reused_claim_evidence=reused_evidence,
                    retrieval_score=retrieval_score,
                )
            )

        self.session.commit()
        return ClaimDraftBatchResult(
            task=task,
            effective_query=effective_query,
            created_claims=created_claims,
            reused_claims=reused_claims,
            created_citation_spans=created_citation_spans,
            reused_citation_spans=reused_citation_spans,
            created_claim_evidence=created_claim_evidence,
            reused_claim_evidence=reused_claim_evidence,
            entries=entries,
        )

    def list_claims(
        self,
        task_id: UUID,
        *,
        verification_status: str | None = None,
        limit: int | None = None,
    ) -> list[Claim]:
        self._get_task(task_id)
        return self.claim_repository.list_for_task(
            task_id,
            verification_status=verification_status,
            limit=limit,
        )

    def list_claim_summaries(
        self,
        task_id: UUID,
        *,
        verification_status: str | None = None,
        limit: int | None = None,
    ) -> list[ClaimSummaryEntry]:
        self._get_task(task_id)
        claims = self.claim_repository.list_for_task(
            task_id,
            verification_status=verification_status,
            limit=limit,
        )
        return self._summarize_claims(task_id, claims)

    def list_claim_evidence(
        self,
        task_id: UUID,
        *,
        claim_id: UUID | None = None,
        relation_type: str | None = None,
        limit: int | None = None,
    ) -> list[ClaimEvidence]:
        self._get_task(task_id)
        return self.claim_evidence_repository.list_for_task(
            task_id,
            claim_id=claim_id,
            relation_type=relation_type,
            limit=limit,
        )

    def verify_claims(
        self,
        task_id: UUID,
        *,
        claim_ids: list[UUID] | None,
        limit: int | None,
    ) -> ClaimVerificationBatchResult:
        task = self._get_task(task_id)
        if task.status not in self.verify_allowed_statuses:
            raise ClaimVerificationConflictError(task.id, task.status)

        effective_limit = self.verification_max_claims_per_request
        if limit is not None:
            effective_limit = min(limit, self.verification_max_claims_per_request)

        claims = self._select_claims(task.id, claim_ids=claim_ids, limit=effective_limit)
        created_citation_spans = 0
        reused_citation_spans = 0
        created_claim_evidence = 0
        reused_claim_evidence = 0
        entries: list[VerifiedClaimEntry] = []

        for claim in claims:
            retrieval_hits = self.index_backend.retrieve_chunks(
                task_id=task.id,
                query=claim.statement,
                offset=0,
                limit=self.retrieval_max_results_per_request,
            ).hits
            if retrieval_hits:
                for source_chunk, _ in self._load_retrieved_chunks(task.id, retrieval_hits):
                    matched_span = select_verification_span(source_chunk.text, claim.statement)
                    if matched_span is None:
                        continue

                    citation_span, reused_citation_span = self._ensure_citation_span(
                        source_chunk=source_chunk,
                        start_offset=matched_span.start_offset,
                        end_offset=matched_span.end_offset,
                        excerpt=matched_span.excerpt,
                    )
                    if reused_citation_span:
                        reused_citation_spans += 1
                    else:
                        created_citation_spans += 1

                    _, reused_evidence = self._ensure_claim_evidence(
                        claim=claim,
                        citation_span=citation_span,
                        relation_type=matched_span.relation_type,
                        score=matched_span.score,
                    )
                    if reused_evidence:
                        reused_claim_evidence += 1
                    else:
                        created_claim_evidence += 1

            support_count, contradict_count = self._count_claim_evidence(claim.id)
            verification_status = resolve_verification_status(
                support_count=support_count,
                contradict_count=contradict_count,
            )
            rationale = build_verification_rationale(
                support_count=support_count,
                contradict_count=contradict_count,
            )
            claim.verification_status = verification_status
            claim.notes_json = {
                **claim.notes_json,
                "verification": {
                    "method": "retrieval_conflict_scan_v1",
                    "verification_query": claim.statement,
                    "support_evidence_count": support_count,
                    "contradict_evidence_count": contradict_count,
                    "rationale": rationale,
                },
            }
            entries.append(
                VerifiedClaimEntry(
                    claim=claim,
                    support_evidence_count=support_count,
                    contradict_evidence_count=contradict_count,
                    rationale=rationale,
                )
            )

        self.session.commit()
        record_verify_results(
            verification_statuses=[entry.claim.verification_status for entry in entries]
        )
        logger.info(
            "verify.batch.completed",
            extra={
                "task_id": str(task.id),
                "verified_claims": len(entries),
                "created_citation_spans": created_citation_spans,
                "reused_citation_spans": reused_citation_spans,
                "created_claim_evidence": created_claim_evidence,
                "reused_claim_evidence": reused_claim_evidence,
                "verification_statuses": [entry.claim.verification_status for entry in entries],
            },
        )
        return ClaimVerificationBatchResult(
            task=task,
            verified_claims=len(entries),
            created_citation_spans=created_citation_spans,
            reused_citation_spans=reused_citation_spans,
            created_claim_evidence=created_claim_evidence,
            reused_claim_evidence=reused_claim_evidence,
            entries=entries,
        )

    def _select_chunks(
        self,
        task_id: UUID,
        *,
        query: str,
        source_chunk_ids: list[UUID] | None,
        limit: int,
    ) -> list[tuple[SourceChunk, float | None]]:
        if source_chunk_ids is not None:
            selected = self.source_chunk_repository.list_by_ids_for_task(task_id, source_chunk_ids)
            selected_by_id = {item.id: item for item in selected}
            ordered_chunks: list[tuple[SourceChunk, float | None]] = []
            seen_ids: set[UUID] = set()
            for source_chunk_id in source_chunk_ids:
                if source_chunk_id in seen_ids:
                    continue
                source_chunk = selected_by_id.get(source_chunk_id)
                if source_chunk is None:
                    raise ClaimSourceChunkNotFoundError(task_id, source_chunk_id)
                ordered_chunks.append((source_chunk, None))
                seen_ids.add(source_chunk_id)
                if len(ordered_chunks) >= limit:
                    break
            return ordered_chunks

        retrieval_hits = self.index_backend.retrieve_chunks(
            task_id=task_id,
            query=query,
            offset=0,
            limit=limit,
        ).hits
        if not retrieval_hits:
            return []

        return self._load_retrieved_chunks(task_id, retrieval_hits)

    def _load_retrieved_chunks(
        self,
        task_id: UUID,
        retrieval_hits: list[IndexedChunkRecord],
    ) -> list[tuple[SourceChunk, float | None]]:
        source_chunk_ids = [hit.source_chunk_id for hit in retrieval_hits]
        selected = self.source_chunk_repository.list_by_ids_for_task(task_id, source_chunk_ids)
        selected_by_id = {item.id: item for item in selected}

        ordered_chunks: list[tuple[SourceChunk, float | None]] = []
        for hit in retrieval_hits:
            source_chunk = selected_by_id.get(hit.source_chunk_id)
            if source_chunk is None:
                raise ClaimDraftingDataIntegrityError(hit.source_chunk_id)
            ordered_chunks.append((source_chunk, hit.score))
        return ordered_chunks

    def _select_claims(
        self,
        task_id: UUID,
        *,
        claim_ids: list[UUID] | None,
        limit: int,
    ) -> list[Claim]:
        if claim_ids is None:
            return self.claim_repository.list_for_task(
                task_id,
                verification_status=CLAIM_VERIFICATION_STATUS_DRAFT,
                limit=limit,
            )

        selected = self.claim_repository.list_by_ids_for_task(task_id, claim_ids)
        selected_by_id = {item.id: item for item in selected}
        ordered_claims: list[Claim] = []
        seen_ids: set[UUID] = set()
        for claim_id in claim_ids:
            if claim_id in seen_ids:
                continue
            claim = selected_by_id.get(claim_id)
            if claim is None:
                raise ClaimNotFoundError(task_id, claim_id)
            ordered_claims.append(claim)
            seen_ids.add(claim_id)
            if len(ordered_claims) >= limit:
                break
        return ordered_claims

    def _summarize_claims(
        self,
        task_id: UUID,
        claims: list[Claim],
    ) -> list[ClaimSummaryEntry]:
        claim_evidence = self.claim_evidence_repository.list_for_task(task_id)
        claim_counts: dict[UUID, dict[str, int]] = {}
        for evidence in claim_evidence:
            counts = claim_counts.setdefault(
                evidence.claim_id,
                {
                    CLAIM_EVIDENCE_RELATION_SUPPORT: 0,
                    CLAIM_EVIDENCE_RELATION_CONTRADICT: 0,
                },
            )
            if evidence.relation_type in counts:
                counts[evidence.relation_type] += 1

        summaries: list[ClaimSummaryEntry] = []
        for claim in claims:
            verification_notes = claim.notes_json.get("verification", {})
            counts = claim_counts.get(
                claim.id,
                {
                    CLAIM_EVIDENCE_RELATION_SUPPORT: 0,
                    CLAIM_EVIDENCE_RELATION_CONTRADICT: 0,
                },
            )
            summaries.append(
                ClaimSummaryEntry(
                    claim=claim,
                    support_evidence_count=counts[CLAIM_EVIDENCE_RELATION_SUPPORT],
                    contradict_evidence_count=counts[CLAIM_EVIDENCE_RELATION_CONTRADICT],
                    rationale=verification_notes.get("rationale"),
                )
            )
        return summaries

    def _count_claim_evidence(self, claim_id: UUID) -> tuple[int, int]:
        claim_evidence = self.claim_evidence_repository.list_for_claim(claim_id)
        support_count = 0
        contradict_count = 0
        for evidence in claim_evidence:
            if evidence.relation_type == CLAIM_EVIDENCE_RELATION_SUPPORT:
                support_count += 1
            elif evidence.relation_type == CLAIM_EVIDENCE_RELATION_CONTRADICT:
                contradict_count += 1
        return support_count, contradict_count

    def _ensure_citation_span(
        self,
        *,
        source_chunk: SourceChunk,
        start_offset: int,
        end_offset: int,
        excerpt: str,
    ) -> tuple[CitationSpan, bool]:
        citation_span = self.citation_span_repository.get_for_chunk_offsets(
            source_chunk.id,
            start_offset=start_offset,
            end_offset=end_offset,
        )
        if citation_span is None:
            validate_citation_span(source_chunk.text, start_offset, end_offset, excerpt)
            citation_span = self.citation_span_repository.add(
                CitationSpan(
                    source_chunk_id=source_chunk.id,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    excerpt=excerpt,
                    normalized_excerpt_hash=normalized_excerpt_hash(excerpt),
                )
            )
            return citation_span, False

        validate_citation_span(
            source_chunk.text,
            citation_span.start_offset,
            citation_span.end_offset,
            citation_span.excerpt,
        )
        return citation_span, True

    def _ensure_claim_evidence(
        self,
        *,
        claim: Claim,
        citation_span: CitationSpan,
        relation_type: str,
        score: float | None,
    ) -> tuple[ClaimEvidence, bool]:
        claim_evidence = self.claim_evidence_repository.get_for_claim_citation_relation(
            claim.id,
            citation_span_id=citation_span.id,
            relation_type=relation_type,
        )
        if claim_evidence is None:
            claim_evidence = self.claim_evidence_repository.add(
                ClaimEvidence(
                    claim_id=claim.id,
                    citation_span_id=citation_span.id,
                    relation_type=relation_type,
                    score=score,
                )
            )
            return claim_evidence, False

        return claim_evidence, True

    def _get_task(self, task_id: UUID) -> ResearchTask:
        task = self.task_repository.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task


def create_claim_drafting_service(
    session: Session,
    *,
    index_backend: ChunkIndexBackend,
    max_candidates_per_request: int,
    verification_max_claims_per_request: int = 5,
    retrieval_max_results_per_request: int = 20,
    draft_allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
    verify_allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
) -> ClaimDraftingService:
    return ClaimDraftingService(
        session,
        task_repository=ResearchTaskRepository(session),
        source_chunk_repository=SourceChunkRepository(session),
        citation_span_repository=CitationSpanRepository(session),
        claim_repository=ClaimRepository(session),
        claim_evidence_repository=ClaimEvidenceRepository(session),
        index_backend=index_backend,
        max_candidates_per_request=max_candidates_per_request,
        verification_max_claims_per_request=verification_max_claims_per_request,
        retrieval_max_results_per_request=retrieval_max_results_per_request,
        draft_allowed_statuses=draft_allowed_statuses,
        verify_allowed_statuses=verify_allowed_statuses,
    )
