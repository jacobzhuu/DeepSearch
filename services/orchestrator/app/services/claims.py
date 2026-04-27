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
    MIN_DRAFT_CLAIM_QUALITY_SCORE,
    MIN_DRAFT_QUERY_ANSWER_SCORE,
    ClaimCandidateScore,
    SupportingSpan,
    build_verification_rationale,
    candidate_category_sort_key,
    classify_query_intent,
    compute_claim_confidence,
    draft_claim_statement,
    is_claimable_statement,
    iter_supporting_spans,
    normalize_claim_identity,
    normalized_excerpt_hash,
    resolve_verification_status,
    score_claim_statement,
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
class DraftClaimCandidate:
    source_chunk: SourceChunk
    supporting_span: SupportingSpan
    statement: str
    score: ClaimCandidateScore
    retrieval_score: float | None
    paragraph_key: tuple[UUID, int]
    rejected_rules: tuple[str, ...] = ()
    draft_mode: str = "strict"
    fallback_reason: str | None = None
    original_rejected_reason: str | None = None


@dataclass(frozen=True)
class DraftChunkSelection:
    chunks_seen: list[tuple[SourceChunk, float | None]]
    eligible_chunks: list[tuple[SourceChunk, float | None]]


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
    diagnostics: dict[str, object]


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
        chunk_selection = self._select_chunks_for_drafting(
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
        claims_by_identity = {
            normalize_claim_identity(claim.statement): claim
            for claim in self.claim_repository.list_for_task(task.id)
        }

        ranked_candidates, diagnostics = self._rank_claim_candidates(
            chunks_seen=chunk_selection.chunks_seen,
            query=effective_query,
            limit=effective_limit,
        )

        for selection_rank, candidate in enumerate(ranked_candidates, start=1):
            source_chunk = candidate.source_chunk
            supporting_span = candidate.supporting_span
            retrieval_score = candidate.retrieval_score
            statement = candidate.statement
            confidence = compute_claim_confidence(
                query=effective_query,
                statement=statement,
                retrieval_score=retrieval_score,
            )
            claim_notes = self._build_claim_notes(
                query=effective_query,
                source_chunk=source_chunk,
                retrieval_score=retrieval_score,
                candidate=candidate,
                selection_rank=selection_rank,
                relation_type=CLAIM_EVIDENCE_RELATION_SUPPORT,
            )

            claim_identity = normalize_claim_identity(statement)
            claim = claims_by_identity.get(
                claim_identity
            ) or self.claim_repository.get_for_task_statement(task.id, statement)
            reused_claim = claim is not None
            if claim is None:
                claim = self.claim_repository.add(
                    Claim(
                        task_id=task.id,
                        statement=statement,
                        claim_type=CLAIM_TYPE_FACT,
                        confidence=confidence,
                        verification_status=CLAIM_VERIFICATION_STATUS_DRAFT,
                        notes_json=claim_notes,
                    )
                )
                claims_by_identity[claim_identity] = claim
                created_claims += 1
            else:
                claim.notes_json = self._merge_claim_notes(claim.notes_json, claim_notes)
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
            diagnostics=diagnostics,
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
                    if not _source_chunk_eligible_for_claims(source_chunk):
                        continue
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
                if not _source_chunk_eligible_for_claims(source_chunk):
                    continue
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

    def _select_chunks_for_drafting(
        self,
        task_id: UUID,
        *,
        query: str,
        source_chunk_ids: list[UUID] | None,
        limit: int,
    ) -> DraftChunkSelection:
        if source_chunk_ids is not None:
            selected = self.source_chunk_repository.list_by_ids_for_task(task_id, source_chunk_ids)
            selected_by_id = {item.id: item for item in selected}
            chunks_seen: list[tuple[SourceChunk, float | None]] = []
            seen_ids: set[UUID] = set()
            for source_chunk_id in source_chunk_ids:
                if source_chunk_id in seen_ids:
                    continue
                source_chunk = selected_by_id.get(source_chunk_id)
                if source_chunk is None:
                    raise ClaimSourceChunkNotFoundError(task_id, source_chunk_id)
                chunks_seen.append((source_chunk, None))
                seen_ids.add(source_chunk_id)
                if len(chunks_seen) >= limit:
                    break
            return DraftChunkSelection(
                chunks_seen=chunks_seen,
                eligible_chunks=[
                    item for item in chunks_seen if _source_chunk_eligible_for_claims(item[0])
                ],
            )

        retrieval_hits = self.index_backend.retrieve_chunks(
            task_id=task_id,
            query=query,
            offset=0,
            limit=limit,
        ).hits
        if not retrieval_hits:
            return DraftChunkSelection(chunks_seen=[], eligible_chunks=[])

        chunks_seen = self._load_retrieved_chunks(
            task_id,
            retrieval_hits,
            include_ineligible=True,
        )
        return DraftChunkSelection(
            chunks_seen=chunks_seen,
            eligible_chunks=[
                item for item in chunks_seen if _source_chunk_eligible_for_claims(item[0])
            ],
        )

    def _rank_claim_candidates(
        self,
        *,
        chunks_seen: list[tuple[SourceChunk, float | None]],
        query: str,
        limit: int,
    ) -> tuple[list[DraftClaimCandidate], dict[str, object]]:
        candidates: list[DraftClaimCandidate] = []
        for source_chunk, retrieval_score in chunks_seen:
            content_quality_score = _chunk_content_quality_score(source_chunk)
            source_quality_score = _source_quality_score(source_chunk)
            for supporting_span in iter_supporting_spans(source_chunk.text):
                try:
                    statement = draft_claim_statement(supporting_span.excerpt)
                except ValueError:
                    continue
                score = score_claim_statement(
                    statement=statement,
                    query=query,
                    content_quality_score=content_quality_score,
                    source_quality_score=source_quality_score,
                )
                rejected_rules = _strict_rejected_rules(source_chunk, statement, query, score)
                candidates.append(
                    DraftClaimCandidate(
                        source_chunk=source_chunk,
                        supporting_span=supporting_span,
                        statement=statement,
                        score=score,
                        retrieval_score=retrieval_score,
                        paragraph_key=(
                            source_chunk.id,
                            _paragraph_index(source_chunk.text, supporting_span.start_offset),
                        ),
                        rejected_rules=tuple(rejected_rules),
                        original_rejected_reason=_first_rejection_reason(rejected_rules, score),
                    )
                )

        diagnostics = _build_claim_drafting_diagnostics(
            chunks_seen=chunks_seen,
            candidates=candidates,
        )
        if not candidates:
            return [], diagnostics

        strict_candidates = [candidate for candidate in candidates if not candidate.rejected_rules]
        if not strict_candidates:
            fallback_candidates = self._fallback_claim_candidates(
                candidates=candidates,
                query=query,
            )
            diagnostics = {
                **diagnostics,
                "fallback_attempted": True,
                "fallback_candidates_count": len(fallback_candidates),
            }
            if not fallback_candidates:
                return [], diagnostics
            ordered_fallback_candidates = sorted(
                fallback_candidates,
                key=lambda candidate: (
                    -candidate.score.final_score,
                    candidate_category_sort_key(candidate.score.claim_category),
                    -candidate.score.query_answer_score,
                    -candidate.score.claim_quality_score,
                    str(candidate.source_chunk.source_document_id),
                    candidate.source_chunk.chunk_no,
                    candidate.supporting_span.start_offset,
                ),
            )
            return (
                self._diversify_claim_candidates(
                    candidates=ordered_fallback_candidates,
                    query=query,
                    limit=limit,
                ),
                diagnostics,
            )

        ordered_candidates = sorted(
            strict_candidates,
            key=lambda candidate: (
                -candidate.score.final_score,
                candidate_category_sort_key(candidate.score.claim_category),
                -candidate.score.query_answer_score,
                -candidate.score.claim_quality_score,
                str(candidate.source_chunk.source_document_id),
                candidate.source_chunk.chunk_no,
                candidate.supporting_span.start_offset,
            ),
        )
        return (
            self._diversify_claim_candidates(
                candidates=ordered_candidates,
                query=query,
                limit=limit,
            ),
            diagnostics,
        )

    def _fallback_claim_candidates(
        self,
        *,
        candidates: list[DraftClaimCandidate],
        query: str,
    ) -> list[DraftClaimCandidate]:
        del query
        fallback_candidates: list[DraftClaimCandidate] = []
        for candidate in candidates:
            if not _fallback_candidate_allowed(candidate):
                continue
            fallback_candidates.append(
                DraftClaimCandidate(
                    source_chunk=candidate.source_chunk,
                    supporting_span=candidate.supporting_span,
                    statement=candidate.statement,
                    score=candidate.score,
                    retrieval_score=candidate.retrieval_score,
                    paragraph_key=candidate.paragraph_key,
                    rejected_rules=(),
                    draft_mode="fallback_relaxed",
                    fallback_reason="strict_filters_produced_no_claims",
                    original_rejected_reason=candidate.original_rejected_reason,
                )
            )
        return fallback_candidates

    def _diversify_claim_candidates(
        self,
        *,
        candidates: list[DraftClaimCandidate],
        query: str,
        limit: int,
    ) -> list[DraftClaimCandidate]:
        selected: list[DraftClaimCandidate] = []
        selected_keys: set[tuple[UUID, int, int]] = set()
        used_paragraphs: set[tuple[UUID, int]] = set()
        intent = classify_query_intent(query)

        def candidate_key(candidate: DraftClaimCandidate) -> tuple[UUID, int, int]:
            return (
                candidate.source_chunk.id,
                candidate.supporting_span.start_offset,
                candidate.supporting_span.end_offset,
            )

        def add_candidate(
            candidate: DraftClaimCandidate,
            *,
            enforce_paragraph_diversity: bool,
        ) -> bool:
            if len(selected) >= limit:
                return False
            key = candidate_key(candidate)
            if key in selected_keys:
                return False
            if enforce_paragraph_diversity and candidate.paragraph_key in used_paragraphs:
                return False
            selected.append(candidate)
            selected_keys.add(key)
            used_paragraphs.add(candidate.paragraph_key)
            return True

        for category in intent.expected_claim_types:
            for candidate in candidates:
                if candidate.score.claim_category == category and add_candidate(
                    candidate,
                    enforce_paragraph_diversity=True,
                ):
                    break

        for candidate in candidates:
            add_candidate(candidate, enforce_paragraph_diversity=True)

        if len(selected) < limit:
            for candidate in candidates:
                add_candidate(candidate, enforce_paragraph_diversity=False)

        return selected[:limit]

    def _build_claim_notes(
        self,
        *,
        query: str,
        source_chunk: SourceChunk,
        retrieval_score: float | None,
        candidate: DraftClaimCandidate,
        selection_rank: int,
        relation_type: str,
    ) -> dict[str, object]:
        return {
            "draft_query": query,
            "draft_method": "query_aware_sentence_ranker_v1",
            "draft_mode": candidate.draft_mode,
            "fallback_reason": candidate.fallback_reason,
            "original_rejected_reason": candidate.original_rejected_reason,
            "source_chunk_id": str(source_chunk.id),
            "source_document_id": str(source_chunk.source_document_id),
            "retrieval_score": retrieval_score,
            "relation_type": relation_type,
            "selection_rank": selection_rank,
            "paragraph_index": candidate.paragraph_key[1],
            **candidate.score.as_notes(),
        }

    def _merge_claim_notes(
        self,
        existing_notes: dict[str, object],
        new_notes: dict[str, object],
    ) -> dict[str, object]:
        existing_score = _numeric_note(existing_notes.get("claim_selection_score"))
        new_score = _numeric_note(new_notes.get("claim_selection_score"))
        if existing_score is not None and new_score is not None and existing_score >= new_score:
            return existing_notes
        return {**existing_notes, **new_notes}

    def _load_retrieved_chunks(
        self,
        task_id: UUID,
        retrieval_hits: list[IndexedChunkRecord],
        *,
        include_ineligible: bool = False,
    ) -> list[tuple[SourceChunk, float | None]]:
        source_chunk_ids = [hit.source_chunk_id for hit in retrieval_hits]
        selected = self.source_chunk_repository.list_by_ids_for_task(task_id, source_chunk_ids)
        selected_by_id = {item.id: item for item in selected}

        ordered_chunks: list[tuple[SourceChunk, float | None]] = []
        for hit in retrieval_hits:
            source_chunk = selected_by_id.get(hit.source_chunk_id)
            if source_chunk is None:
                raise ClaimDraftingDataIntegrityError(hit.source_chunk_id)
            if not include_ineligible and not _source_chunk_eligible_for_claims(source_chunk):
                continue
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


def _source_chunk_eligible_for_claims(source_chunk: SourceChunk) -> bool:
    metadata = source_chunk.metadata_json or {}
    if metadata.get("eligible_for_claims") is False:
        return False
    if metadata.get("should_generate_claims") is False:
        return False
    if metadata.get("is_reference_section") is True:
        return False
    if metadata.get("is_navigation_noise") is True:
        return False
    if metadata.get("reason") == "redirect_stub":
        return False
    quality_score = metadata.get("content_quality_score")
    if isinstance(quality_score, int | float) and quality_score < 0.3:
        return False
    source_score = source_chunk.source_document.final_source_score
    if source_score is not None and source_score < 0.2:
        return False
    return True


def _source_chunk_hard_excluded_for_claims(source_chunk: SourceChunk) -> bool:
    metadata = source_chunk.metadata_json or {}
    if metadata.get("is_reference_section") is True:
        return True
    if metadata.get("is_navigation_noise") is True:
        return True
    if metadata.get("reason") == "redirect_stub":
        return True
    source_score = source_chunk.source_document.final_source_score
    return source_score is not None and source_score < 0.2


def _strict_rejected_rules(
    source_chunk: SourceChunk,
    statement: str,
    query: str,
    score: ClaimCandidateScore,
) -> list[str]:
    rejected_rules: list[str] = []
    if not _source_chunk_eligible_for_claims(source_chunk):
        rejected_rules.append("chunk_ineligible")
    if score.rejected_reason is not None:
        rejected_rules.append(score.rejected_reason)
    if score.claim_quality_score < MIN_DRAFT_CLAIM_QUALITY_SCORE:
        rejected_rules.append("insufficient_claim_quality")
    if score.query_answer_score < MIN_DRAFT_QUERY_ANSWER_SCORE:
        rejected_rules.append("insufficient_answer_score")
    if not is_claimable_statement(statement, query=query) and score.rejected_reason is None:
        rejected_rules.append("not_claimable_statement")
    return list(dict.fromkeys(rejected_rules))


def _fallback_candidate_allowed(candidate: DraftClaimCandidate) -> bool:
    statement = " ".join(candidate.statement.split())
    if len(statement) < 40 or len(statement) > 300:
        return False
    if _source_chunk_hard_excluded_for_claims(candidate.source_chunk):
        return False
    if candidate.score.rejected_reason is not None:
        return False
    if candidate.score.claim_category in {"setup", "community", "slogan", "reference"}:
        return False
    if candidate.score.claim_category not in {"definition", "mechanism", "privacy", "feature"}:
        return False
    if (
        candidate.score.query_answer_score < MIN_DRAFT_QUERY_ANSWER_SCORE
        and candidate.score.query_relevance_score < 0.45
    ):
        return False
    return True


def _first_rejection_reason(
    rejected_rules: list[str],
    score: ClaimCandidateScore,
) -> str | None:
    if score.rejected_reason is not None:
        return score.rejected_reason
    return rejected_rules[0] if rejected_rules else None


def _build_claim_drafting_diagnostics(
    *,
    chunks_seen: list[tuple[SourceChunk, float | None]],
    candidates: list[DraftClaimCandidate],
) -> dict[str, object]:
    rejected_candidates = [candidate for candidate in candidates if candidate.rejected_rules]
    distribution: dict[str, int] = {}
    for candidate in rejected_candidates:
        for rule in candidate.rejected_rules:
            distribution[rule] = distribution.get(rule, 0) + 1

    return {
        "total_chunks_seen": len(chunks_seen),
        "eligible_chunks_seen": sum(
            1 for source_chunk, _ in chunks_seen if _source_chunk_eligible_for_claims(source_chunk)
        ),
        "candidate_sentences_count": len(candidates),
        "rejected_candidates_count": len(rejected_candidates),
        "top_rejected_candidates": [
            _candidate_diagnostic(candidate)
            for candidate in sorted(
                rejected_candidates,
                key=lambda item: (-item.score.final_score, item.source_chunk.chunk_no),
            )[:10]
        ],
        "rejection_reason_distribution": dict(sorted(distribution.items())),
        "chunks": [_chunk_diagnostic(source_chunk) for source_chunk, _ in chunks_seen],
        "fallback_attempted": False,
        "fallback_candidates_count": 0,
    }


def _candidate_diagnostic(candidate: DraftClaimCandidate) -> dict[str, object]:
    rejected_reason = _first_rejection_reason(list(candidate.rejected_rules), candidate.score)
    return {
        "candidate_text": candidate.statement,
        "source_chunk_id": str(candidate.source_chunk.id),
        "claim_category": candidate.score.claim_category,
        "claim_quality_score": candidate.score.claim_quality_score,
        "query_answer_score": candidate.score.query_answer_score,
        "query_relevance_score": candidate.score.query_relevance_score,
        "claim_selection_score": candidate.score.final_score,
        "rejected_reason": rejected_reason,
        "rejected_rules": list(candidate.rejected_rules),
    }


def _chunk_diagnostic(source_chunk: SourceChunk) -> dict[str, object]:
    metadata = source_chunk.metadata_json or {}
    return {
        "source_chunk_id": str(source_chunk.id),
        "source_document_id": str(source_chunk.source_document_id),
        "chunk_no": source_chunk.chunk_no,
        "token_count": source_chunk.token_count,
        "eligible_for_claims": _source_chunk_eligible_for_claims(source_chunk),
        "content_quality_score": metadata.get("content_quality_score"),
        "query_relevance_score": metadata.get("query_relevance_score"),
        "text_preview": " ".join(source_chunk.text.split())[:240],
    }


def _chunk_content_quality_score(source_chunk: SourceChunk) -> float | None:
    metadata = source_chunk.metadata_json or {}
    quality_score = metadata.get("content_quality_score")
    if isinstance(quality_score, int | float):
        return float(quality_score)
    return None


def _source_quality_score(source_chunk: SourceChunk) -> float | None:
    source_score = source_chunk.source_document.final_source_score
    if isinstance(source_score, int | float):
        return float(source_score)
    return None


def _paragraph_index(text: str, start_offset: int) -> int:
    if start_offset <= 0:
        return 0
    prefix = text[:start_offset]
    return len([part for part in prefix.split("\n\n")[:-1]])


def _numeric_note(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None
