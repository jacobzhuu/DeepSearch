from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any
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
    CLAIM_EVIDENCE_RELATION_CANDIDATE_SUPPORT,
    CLAIM_EVIDENCE_RELATION_CONTRADICT,
    CLAIM_EVIDENCE_RELATION_SUPPORT,
    CLAIM_EVIDENCE_RELATION_WEAK_SUPPORT,
    CLAIM_TYPE_FACT,
    CLAIM_VERIFICATION_STATUS_DRAFT,
    MIN_DRAFT_QUERY_ANSWER_SCORE,
    VERIFIER_METHOD_LEXICAL_HEURISTIC_V2,
    ClaimCandidateScore,
    SupportingSpan,
    VerificationSpanMatch,
    build_verification_rationale,
    candidate_category_sort_key,
    classify_query_intent,
    compute_claim_confidence,
    deployment_evidence_statement,
    deployment_slot_ids_for_claim_text,
    deployment_slot_ids_for_evidence,
    draft_claim_statement,
    is_answer_relevant_score,
    is_claimable_statement,
    is_deployment_evidence_statement,
    iter_deployment_evidence_spans,
    iter_supporting_spans,
    normalize_claim_identity,
    normalized_excerpt_hash,
    resolve_verification_status,
    rewrite_claim_self_contained,
    score_claim_statement,
    select_verification_span,
    validate_citation_span,
)
from services.orchestrator.app.claims.verification import (
    VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE,
    _query_asks_technical_explanation_for_readme_verification,
    try_repository_readme_normalized_composite_verification,
)
from services.orchestrator.app.indexing import ChunkIndexBackend, IndexedChunkRecord
from services.orchestrator.app.research_quality import (
    EvidenceCandidate,
    answer_slots_for_query,
    classify_source_intent,
    evidence_candidate_id,
    slot_ids_for_candidate_category,
    technical_slot_ids_for_text,
)
from services.orchestrator.app.research_quality.candidate_target_slots import (
    ROLES_ELIGIBLE_FOR_PLANNER_TARGET_SLOT_MERGE,
    load_candidate_target_slots_by_source_document,
    merge_technical_lexical_and_planner_slots,
    weak_optional_slots_without_planner_propagation,
)
from services.orchestrator.app.services.acquisition import _query_asks_technical_explanation
from services.orchestrator.app.services.research_tasks import (
    PHASE2_ACTIVE_STATUS,
    TaskNotFoundError,
)

logger = get_logger(__name__)


def _limitations_official_planner_target_slot_id(
    *,
    technical_explanation: bool,
    document_target_slots: dict[UUID, frozenset[str]] | None,
    source_document_id: UUID,
    source_role: str | None,
) -> str | None:
    """Only when planner merged ``limitations`` applies scoring alignment (official roles only)."""
    if not technical_explanation or document_target_slots is None:
        return None
    if (source_role or "").strip() not in ROLES_ELIGIBLE_FOR_PLANNER_TARGET_SLOT_MERGE:
        return None
    if "limitations" not in document_target_slots.get(source_document_id, frozenset()):
        return None
    return "limitations"


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
    evidence_candidate_id: str


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
    evidence_kind: str = "sentence"
    evidence_slot_ids: tuple[str, ...] = ()
    lexical_evidence_slot_ids: tuple[str, ...] = ()
    candidate_target_slot_ids: tuple[str, ...] = ()
    normalized_from_readme: bool = False
    cleaned_github_readme: bool = False


@dataclass(frozen=True)
class DraftChunkSelection:
    chunks_seen: list[tuple[SourceChunk, float | None]]
    eligible_chunks: list[tuple[SourceChunk, float | None]]


def _append_target_slot_diagnostics(
    diagnostics: dict[str, Any],
    diversified: list[DraftClaimCandidate],
    *,
    document_target_slots: dict[UUID, frozenset[str]] | None,
    target_slot_load_meta: dict[str, int] | None,
    query: str,
    technical_explanation: bool,
) -> None:
    if not technical_explanation:
        diagnostics.update(
            {
                "candidate_target_slots_seen_count": 0,
                "candidate_urls_joined_for_target_slots": 0,
                "candidate_target_slots_applied_count": 0,
                "claims_with_candidate_target_slots_count": 0,
                "claim_slots_from_lexical_count": 0,
                "claim_slots_from_candidate_target_count": 0,
                "limitations_claims_from_candidate_target_slots_count": 0,
                "weak_slots_without_candidate_target_claims": [],
            }
        )
        return
    meta = target_slot_load_meta or {}
    diagnostics["candidate_target_slots_seen_count"] = int(meta.get("documents_with_slots", 0))
    diagnostics["candidate_urls_joined_for_target_slots"] = int(
        meta.get("candidate_urls_joined", 0)
    )
    diagnostics["candidate_target_slots_applied_count"] = sum(
        len(c.candidate_target_slot_ids) for c in diversified
    )
    diagnostics["claims_with_candidate_target_slots_count"] = sum(
        1 for c in diversified if c.candidate_target_slot_ids
    )
    diagnostics["claim_slots_from_lexical_count"] = sum(
        (
            len(c.lexical_evidence_slot_ids)
            if c.lexical_evidence_slot_ids
            else len(c.evidence_slot_ids)
        )
        for c in diversified
    )
    diagnostics["claim_slots_from_candidate_target_count"] = sum(
        len(c.candidate_target_slot_ids) for c in diversified
    )
    diagnostics["limitations_claims_from_candidate_target_slots_count"] = sum(
        1 for c in diversified if "limitations" in c.candidate_target_slot_ids
    )
    diagnostics["weak_slots_without_candidate_target_claims"] = (
        weak_optional_slots_without_planner_propagation(query=query, diversified=diversified)
    )


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
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class GithubReadmeCleanResult:
    text: str
    line_spans: tuple[tuple[int, int], ...]
    applied: bool
    removed_line_count: int
    kept_line_count: int


@dataclass(frozen=True)
class ClaimSummaryEntry:
    claim: Claim
    support_evidence_count: int
    weak_support_evidence_count: int
    contradict_evidence_count: int
    rationale: str | None


@dataclass(frozen=True)
class VerifiedClaimEntry:
    claim: Claim
    support_evidence_count: int
    weak_support_evidence_count: int
    contradict_evidence_count: int
    rationale: str


@dataclass(frozen=True)
class VerificationEvidenceCandidate:
    source_chunk: SourceChunk
    matched_span: VerificationSpanMatch
    retrieval_score: float | None
    rank_score: float
    diversity_adjusted_score: float
    reuse_penalty: float
    chunk_reuse_count_before: int
    span_reuse_count_before: int
    content_reuse_count_before: int
    source_quality_score: float
    content_quality_score: float
    information_density_score: float
    content_hash: str | None
    chunk_text_hash: str
    span_text_hash: str
    readme_composite_metadata: dict[str, Any] | None = None


@dataclass
class EvidenceReuseTracker:
    chunk_counts: dict[str, int]
    span_counts: dict[str, int]
    content_counts: dict[str, int]


_README_COMPOSITE_ALLOWED_SOURCE_INTENTS: frozenset[str] = frozenset(
    {"official_repository_readme", "github_readme_or_repo"}
)
_README_COMPOSITE_DIAGNOSTIC_KEY = "repository_normalized_readme_composite_diagnostic"


def _readme_set_diag(track: dict[str, Any], composite_diag: dict[str, Any]) -> None:
    track[_README_COMPOSITE_DIAGNOSTIC_KEY] = composite_diag


def _init_readme_verification_tracker() -> dict[str, Any]:
    return {
        "repository_normalized_verification_attempt_count": 0,
        "repository_normalized_verification_supported_count": 0,
        "repository_normalized_verification_rejection_reason_distribution": Counter(),
        "repository_normalized_support_method_distribution": Counter(),
        _README_COMPOSITE_DIAGNOSTIC_KEY: None,
    }


def _finalize_readme_verification_tracker(track: dict[str, Any]) -> dict[str, Any]:
    reject = track.get("repository_normalized_verification_rejection_reason_distribution")
    methods = track.get("repository_normalized_support_method_distribution")
    reject_dict: dict[str, int] = {}
    methods_dict: dict[str, int] = {}
    if isinstance(reject, Counter):
        reject_dict = dict(sorted(reject.items(), key=lambda item: item[0]))
    if isinstance(methods, Counter):
        methods_dict = dict(sorted(methods.items(), key=lambda item: item[0]))
    return {
        "repository_normalized_verification_attempt_count": int(
            track.get("repository_normalized_verification_attempt_count", 0)
        ),
        "repository_normalized_verification_supported_count": int(
            track.get("repository_normalized_verification_supported_count", 0)
        ),
        "repository_normalized_verification_rejection_reason_distribution": reject_dict,
        "repository_normalized_support_method_distribution": methods_dict,
        "repository_normalized_readme_composite_diagnostic": track.get(
            _README_COMPOSITE_DIAGNOSTIC_KEY
        ),
    }


def _claim_eligible_for_readme_repository_normalized_composite(
    claim: Claim,
    task: ResearchTask,
    source_chunk: SourceChunk,
) -> bool:
    del task  # reserved for future tightening; eligibility is claim+chunk local
    notes = claim.notes_json or {}
    if notes.get("normalized_from_readme") is not True:
        return False
    if str(notes.get("source_role") or "").strip() != "official_repository":
        return False
    intent = str(notes.get("source_intent") or "").strip()
    if intent not in _README_COMPOSITE_ALLOWED_SOURCE_INTENTS:
        return False
    raw_scid = notes.get("source_chunk_id")
    if not isinstance(raw_scid, str):
        return False
    try:
        if UUID(raw_scid) != source_chunk.id:
            return False
    except ValueError:
        return False
    domain = (source_chunk.source_document.domain or "").lower().rstrip(".")
    if domain != "raw.githubusercontent.com":
        return False
    url = (source_chunk.source_document.canonical_url or "").lower()
    if not (url.endswith("/readme.md") or url.endswith("/readme.markdown")):
        return False
    return True


@dataclass(frozen=True)
class ClaimVerificationBatchResult:
    task: ResearchTask
    verified_claims: int
    created_citation_spans: int
    reused_citation_spans: int
    created_claim_evidence: int
    reused_claim_evidence: int
    entries: list[VerifiedClaimEntry]
    readme_normalized_verification: dict[str, Any] = field(default_factory=dict)


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

        effective_query = (query or task.query).strip()
        effective_limit = self.max_candidates_per_request
        if classify_query_intent(effective_query).intent_name == "deployment":
            effective_limit = max(
                effective_limit,
                _deployment_claim_limit_for_query(effective_query),
            )
        if limit is not None:
            effective_limit = min(limit, effective_limit)

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
            task_id=task.id,
        )

        for selection_rank, candidate in enumerate(ranked_candidates, start=1):
            source_chunk = candidate.source_chunk
            supporting_span = candidate.supporting_span
            retrieval_score = candidate.retrieval_score
            statement = candidate.statement
            evidence_candidate = _evidence_candidate_payload(candidate, query=effective_query)
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
                relation_type=CLAIM_EVIDENCE_RELATION_CANDIDATE_SUPPORT,
                evidence_candidate=evidence_candidate,
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
                relation_type=CLAIM_EVIDENCE_RELATION_CANDIDATE_SUPPORT,
                score=confidence,
            )
            if reused_evidence:
                reused_claim_evidence += 1
            else:
                created_claim_evidence += 1

            claim.notes_json = self._merge_claim_notes(
                claim.notes_json,
                _claim_lineage_notes(
                    evidence_candidate=evidence_candidate,
                    citation_span_id=str(citation_span.id),
                    claim_evidence_id=str(claim_evidence.id),
                ),
            )

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
                    evidence_candidate_id=str(evidence_candidate["evidence_candidate_id"]),
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
        if classify_query_intent(task.query).intent_name == "deployment":
            effective_limit = max(effective_limit, _deployment_claim_limit_for_query(task.query))
        if limit is not None:
            effective_limit = min(limit, effective_limit)

        claims = self._select_claims(task.id, claim_ids=claim_ids, limit=effective_limit)
        created_citation_spans = 0
        reused_citation_spans = 0
        created_claim_evidence = 0
        reused_claim_evidence = 0
        entries: list[VerifiedClaimEntry] = []
        reuse_tracker = EvidenceReuseTracker(
            chunk_counts={},
            span_counts={},
            content_counts={},
        )

        readme_batch_tracker = _init_readme_verification_tracker()
        for claim in claims:
            readme_claim_tracker = _init_readme_verification_tracker()
            verification_candidates: list[VerificationEvidenceCandidate] = []
            seen_verification_spans: set[tuple[UUID, int, int]] = set()
            retrieval_hits = self.index_backend.retrieve_chunks(
                task_id=task.id,
                query=claim.statement,
                offset=0,
                limit=self.retrieval_max_results_per_request,
            ).hits
            if retrieval_hits:
                for source_chunk, retrieval_score in self._load_retrieved_chunks(
                    task.id,
                    retrieval_hits,
                ):
                    if not _source_chunk_eligible_for_claims(source_chunk):
                        continue
                    matched_span = select_verification_span(source_chunk.text, claim.statement)
                    if matched_span is None:
                        continue
                    seen_verification_spans.add(
                        (
                            source_chunk.id,
                            matched_span.start_offset,
                            matched_span.end_offset,
                        )
                    )
                    verification_candidates.append(
                        _verification_evidence_candidate(
                            source_chunk=source_chunk,
                            matched_span=matched_span,
                            retrieval_score=retrieval_score,
                        )
                    )
            verification_candidates.extend(
                self._candidate_support_verification_candidates(
                    task=task,
                    claim=claim,
                    seen_spans=seen_verification_spans,
                    readme_batch_tracker=readme_batch_tracker,
                    readme_claim_tracker=readme_claim_tracker,
                )
            )

            selected_candidates = _select_verification_evidence(
                verification_candidates,
                reuse_tracker=reuse_tracker,
            )
            evidence_relation_details: list[dict[str, object]] = []
            for selection_rank, candidate in enumerate(selected_candidates, start=1):
                source_chunk = candidate.source_chunk
                matched_span = candidate.matched_span
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

                claim_evidence, reused_evidence = self._ensure_claim_evidence(
                    claim=claim,
                    citation_span=citation_span,
                    relation_type=matched_span.relation_type,
                    score=candidate.rank_score,
                )
                row: dict[str, object] = {
                        "claim_evidence_id": str(claim_evidence.id),
                        "claim_evidence_reused": reused_evidence,
                        "claim_id": str(claim.id),
                        "citation_span_id": str(citation_span.id),
                        "source_chunk_id": str(source_chunk.id),
                        "source_document_id": str(source_chunk.source_document_id),
                        "source_domain": source_chunk.source_document.domain,
                        "source_url": source_chunk.source_document.canonical_url,
                        "selection_rank": selection_rank,
                        "evidence_rank_score": candidate.rank_score,
                        "diversity_adjusted_score": candidate.diversity_adjusted_score,
                        "reuse_penalty": candidate.reuse_penalty,
                        "chunk_reuse_count_before": candidate.chunk_reuse_count_before,
                        "span_reuse_count_before": candidate.span_reuse_count_before,
                        "content_reuse_count_before": candidate.content_reuse_count_before,
                        "source_quality_score": candidate.source_quality_score,
                        "content_quality_score": candidate.content_quality_score,
                        "information_density_score": candidate.information_density_score,
                        "retrieval_score": candidate.retrieval_score,
                        "content_hash": candidate.content_hash,
                        "chunk_text_hash": candidate.chunk_text_hash,
                        "span_text_hash": candidate.span_text_hash,
                        **matched_span.to_metadata(),
                    }
                meta = candidate.readme_composite_metadata
                if meta:
                    for key in (
                        "repository_normalized_support_method",
                        "repository_normalized_support_token_hits",
                        "repository_normalized_support_missing_terms",
                        "repository_normalized_support_rejection",
                    ):
                        if key in meta and meta[key] is not None:
                            row[key] = meta[key]
                evidence_relation_details.append(row)
                _record_candidate_reuse(reuse_tracker, candidate)
                if reused_evidence:
                    reused_claim_evidence += 1
                else:
                    created_claim_evidence += 1

            support_count, weak_support_count, contradict_count = self._count_claim_evidence(
                claim.id,
                relation_details=evidence_relation_details,
            )
            verification_status = resolve_verification_status(
                support_count=support_count,
                contradict_count=contradict_count,
                weak_support_count=weak_support_count,
            )
            rationale = build_verification_rationale(
                support_count=support_count,
                contradict_count=contradict_count,
                weak_support_count=weak_support_count,
            )
            claim_readme_diag = _finalize_readme_verification_tracker(readme_claim_tracker)
            any_composite = any(
                candidate.readme_composite_metadata for candidate in selected_candidates
            )
            verifier_primary = (
                VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE
                if any_composite
                else VERIFIER_METHOD_LEXICAL_HEURISTIC_V2
            )
            claim.verification_status = verification_status
            claim.notes_json = {
                **claim.notes_json,
                "verification": {
                    "method": verifier_primary,
                    "verifier_method": verifier_primary,
                    "verification_query": claim.statement,
                    "support_evidence_count": support_count,
                    "strong_support_evidence_count": support_count,
                    "weak_support_evidence_count": weak_support_count,
                    "contradict_evidence_count": contradict_count,
                    "insufficient_evidence_count": (
                        1
                        if support_count == 0 and weak_support_count == 0 and contradict_count == 0
                        else 0
                    ),
                    "candidate_evidence_count": len(verification_candidates),
                    "selected_evidence_count": len(selected_candidates),
                    "dropped_evidence_count": max(
                        0,
                        len(verification_candidates) - len(selected_candidates),
                    ),
                    "evidence_relations": evidence_relation_details,
                    "evidence_diversity": _evidence_diversity_summary(
                        evidence_relation_details,
                    ),
                    "rationale": rationale,
                    "readme_repository_normalized": {
                        "repository_normalized_verification_attempt_count": claim_readme_diag[
                            "repository_normalized_verification_attempt_count"
                        ],
                        "repository_normalized_verification_supported_count": claim_readme_diag[
                            "repository_normalized_verification_supported_count"
                        ],
                        "repository_normalized_verification_rejection_reason_distribution": (
                            claim_readme_diag[
                                "repository_normalized_verification_rejection_reason_distribution"
                            ]
                        ),
                        "repository_normalized_support_method_distribution": claim_readme_diag[
                            "repository_normalized_support_method_distribution"
                        ],
                    },
                },
            }
            entries.append(
                VerifiedClaimEntry(
                    claim=claim,
                    support_evidence_count=support_count,
                    weak_support_evidence_count=weak_support_count,
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
            readme_normalized_verification=_finalize_readme_verification_tracker(
                readme_batch_tracker
            ),
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
        task_id: UUID,
    ) -> tuple[list[DraftClaimCandidate], dict[str, Any]]:
        candidates: list[DraftClaimCandidate] = []
        deployment_query = classify_query_intent(query).intent_name == "deployment"
        technical_explanation = (
            _query_asks_technical_explanation(query) and not deployment_query
        )
        document_target_slots: dict[UUID, frozenset[str]] | None = None
        target_slot_load_meta: dict[str, int] = {}
        if technical_explanation:
            document_target_slots, target_slot_load_meta = (
                load_candidate_target_slots_by_source_document(self.session, task_id)
            )
        for source_chunk, retrieval_score in chunks_seen:
            content_quality_score = _chunk_content_quality_score(source_chunk)
            source_quality_score = _source_quality_score(source_chunk)
            page_title = source_chunk.source_document.title
            source_classification = classify_source_intent(
                canonical_url=source_chunk.source_document.canonical_url,
                domain=source_chunk.source_document.domain,
                title=source_chunk.source_document.title,
                query=query,
            )
            for supporting_span in iter_supporting_spans(source_chunk.text):
                try:
                    statement = draft_claim_statement(supporting_span.excerpt)
                except ValueError:
                    continue

                # Add self-contained rewriting
                statement = rewrite_claim_self_contained(
                    statement, page_title=page_title, query=query
                )

                limitations_slot = _limitations_official_planner_target_slot_id(
                    technical_explanation=technical_explanation,
                    document_target_slots=document_target_slots,
                    source_document_id=source_chunk.source_document_id,
                    source_role=source_classification.source_role,
                )
                score = score_claim_statement(
                    statement=statement,
                    query=query,
                    content_quality_score=content_quality_score,
                    source_quality_score=source_quality_score,
                    domain=source_chunk.source_document.domain,
                    source_url=source_chunk.source_document.canonical_url,
                    page_title=page_title,
                    target_slot_id=limitations_slot,
                )
                evidence_slot_ids: tuple[str, ...] = ()
                lexical_evidence_slot_ids: tuple[str, ...] = ()
                planner_only_slots: tuple[str, ...] = ()
                if deployment_query:
                    evidence_slot_ids = deployment_slot_ids_for_claim_text(
                        statement,
                        supporting_span.excerpt,
                    )
                    lexical_evidence_slot_ids = evidence_slot_ids
                else:
                    lexical_evidence_slot_ids = technical_slot_ids_for_text(
                        text=f"{statement}\n{supporting_span.excerpt}",
                        category=score.claim_category,
                        query=query,
                        source_intent=source_classification.source_intent,
                    )
                    if technical_explanation and document_target_slots is not None:
                        evidence_slot_ids, planner_only_slots = (
                            merge_technical_lexical_and_planner_slots(
                                lexical_slots=lexical_evidence_slot_ids,
                                source_document_id=source_chunk.source_document_id,
                                source_role=source_classification.source_role,
                                document_target_slots=document_target_slots,
                            )
                        )
                    else:
                        evidence_slot_ids = lexical_evidence_slot_ids
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
                        evidence_slot_ids=evidence_slot_ids,
                        lexical_evidence_slot_ids=lexical_evidence_slot_ids,
                        candidate_target_slot_ids=planner_only_slots,
                    )
                )
            if (
                not deployment_query
                and _should_normalize_repository_readme_candidates(
                    query=query,
                    source_chunk=source_chunk,
                    source_role=source_classification.source_role,
                )
            ):
                readme_clean_result = _github_readme_clean_result_for_claims(
                    query=query,
                    source_chunk=source_chunk,
                    source_role=source_classification.source_role,
                )
                candidates.extend(
                    _repository_readme_normalized_candidates(
                        source_chunk=source_chunk,
                        query=query,
                        page_title=page_title,
                        content_quality_score=content_quality_score,
                        source_quality_score=source_quality_score,
                        source_intent=source_classification.source_intent,
                        source_role=source_classification.source_role,
                        clean_result=readme_clean_result,
                        document_target_slots=document_target_slots
                        if technical_explanation
                        else None,
                    )
                )
            if not deployment_query:
                continue
            for supporting_span in iter_deployment_evidence_spans(source_chunk.text):
                statement = deployment_evidence_statement(supporting_span.excerpt)
                score = score_claim_statement(
                    statement=statement,
                    query=query,
                    content_quality_score=max(content_quality_score or 0.0, 0.58),
                    source_quality_score=source_quality_score,
                    domain=source_chunk.source_document.domain,
                    source_url=source_chunk.source_document.canonical_url,
                )
                rejected_rules = _strict_rejected_rules(source_chunk, statement, query, score)
                dep_slots = deployment_slot_ids_for_evidence(
                    statement,
                    supporting_span.excerpt,
                )
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
                        evidence_kind="deployment_code_or_config",
                        evidence_slot_ids=dep_slots,
                        lexical_evidence_slot_ids=dep_slots,
                        candidate_target_slot_ids=(),
                    )
                )

        full_readme_extra: list[DraftClaimCandidate] = []
        full_readme_diag: dict[str, int] = {
            "raw_readme_full_document_group_count": 0,
            "raw_readme_full_document_normalized_candidate_count": 0,
        }
        if not deployment_query:
            full_readme_extra, full_readme_diag = _raw_readme_full_document_normalized_candidates(
                source_chunk_repository=self.source_chunk_repository,
                chunks_seen=chunks_seen,
                query=query,
                existing_candidates=candidates,
                document_target_slots=document_target_slots
                if technical_explanation
                else None,
            )
            candidates.extend(full_readme_extra)

        diagnostics = _build_claim_drafting_diagnostics(
            chunks_seen=chunks_seen,
            candidates=candidates,
            query=query,
        )
        diagnostics.update({k: int(v) for k, v in full_readme_diag.items()})
        if not candidates:
            _append_target_slot_diagnostics(
                diagnostics,
                [],
                document_target_slots=document_target_slots
                if technical_explanation
                else None,
                target_slot_load_meta=target_slot_load_meta if technical_explanation else None,
                query=query,
                technical_explanation=technical_explanation,
            )
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
                _append_target_slot_diagnostics(
                    diagnostics,
                    [],
                    document_target_slots=document_target_slots
                    if technical_explanation
                    else None,
                    target_slot_load_meta=target_slot_load_meta
                    if technical_explanation
                    else None,
                    query=query,
                    technical_explanation=technical_explanation,
                )
                return [], diagnostics
            ordered_fallback_candidates = sorted(
                fallback_candidates,
                key=_candidate_selection_sort_key,
            )
            diversified, near_duplicate_removed = self._diversify_claim_candidates(
                candidates=ordered_fallback_candidates,
                query=query,
                limit=limit,
            )
            diagnostics = {
                **diagnostics,
                "accepted_claims_by_category": _accepted_candidates_by_category(diversified),
                "category_coverage_missing": _category_coverage_missing(query, diversified),
                "near_duplicate_claims_removed": near_duplicate_removed,
                "repository_normalized_claim_count": sum(
                    1 for candidate in diversified if candidate.normalized_from_readme
                ),
                "repository_normalized_supported_claim_count": 0,
                "accepted_evidence_candidate_ids": [
                    _candidate_evidence_id(candidate) for candidate in diversified
                ],
            }
            _append_target_slot_diagnostics(
                diagnostics,
                diversified,
                document_target_slots=document_target_slots
                if technical_explanation
                else None,
                target_slot_load_meta=target_slot_load_meta if technical_explanation else None,
                query=query,
                technical_explanation=technical_explanation,
            )
            return (
                diversified,
                diagnostics,
            )

        ordered_candidates = sorted(
            strict_candidates,
            key=_candidate_selection_sort_key,
        )
        diversified, near_duplicate_removed = self._diversify_claim_candidates(
            candidates=ordered_candidates,
            query=query,
            limit=limit,
        )
        diagnostics = {
            **diagnostics,
            "accepted_claims_by_category": _accepted_candidates_by_category(diversified),
            "category_coverage_missing": _category_coverage_missing(query, diversified),
            "near_duplicate_claims_removed": near_duplicate_removed,
            "repository_normalized_claim_count": sum(
                1 for candidate in diversified if candidate.normalized_from_readme
            ),
            "repository_normalized_supported_claim_count": 0,
            "accepted_evidence_candidate_ids": [
                _candidate_evidence_id(candidate) for candidate in diversified
            ],
        }
        _append_target_slot_diagnostics(
            diagnostics,
            diversified,
            document_target_slots=document_target_slots if technical_explanation else None,
            target_slot_load_meta=target_slot_load_meta if technical_explanation else None,
            query=query,
            technical_explanation=technical_explanation,
        )
        return (diversified, diagnostics)

    def _fallback_claim_candidates(
        self,
        *,
        candidates: list[DraftClaimCandidate],
        query: str,
    ) -> list[DraftClaimCandidate]:
        fallback_candidates: list[DraftClaimCandidate] = []
        for candidate in candidates:
            if not _fallback_candidate_allowed(candidate, query=query):
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
                    evidence_slot_ids=candidate.evidence_slot_ids,
                    lexical_evidence_slot_ids=candidate.lexical_evidence_slot_ids,
                    candidate_target_slot_ids=candidate.candidate_target_slot_ids,
                    normalized_from_readme=candidate.normalized_from_readme,
                    cleaned_github_readme=candidate.cleaned_github_readme,
                )
            )
        return fallback_candidates

    def _diversify_claim_candidates(
        self,
        *,
        candidates: list[DraftClaimCandidate],
        query: str,
        limit: int,
    ) -> tuple[list[DraftClaimCandidate], int]:
        selected: list[DraftClaimCandidate] = []
        selected_keys: set[tuple[UUID, int, int]] = set()
        selected_semantic_identities: dict[str, str] = {}
        used_paragraphs: set[tuple[UUID, int]] = set()
        near_duplicate_removed = [0]
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
            semantic_key = _semantic_duplicate_key(candidate)
            claim_identity = normalize_claim_identity(candidate.statement)
            existing_identity = selected_semantic_identities.get(semantic_key)
            if existing_identity is not None and existing_identity != claim_identity:
                near_duplicate_removed[0] += 1
                return False
            if enforce_paragraph_diversity and candidate.paragraph_key in used_paragraphs:
                return False
            selected.append(candidate)
            selected_keys.add(key)
            selected_semantic_identities.setdefault(semantic_key, claim_identity)
            used_paragraphs.add(candidate.paragraph_key)
            return True

        if intent.intent_name == "deployment":
            deployment_slot_order = _deployment_slot_order_for_query(query)
            covered_slots: set[str] = set()
            candidate_positions = {
                id(candidate): index for index, candidate in enumerate(candidates)
            }
            while len(selected) < limit:
                remaining_slots = set(deployment_slot_order) - covered_slots
                if not remaining_slots:
                    break
                progress = False
                slot_candidates = sorted(
                    (
                        candidate
                        for candidate in candidates
                        if candidate_key(candidate) not in selected_keys
                        and set(candidate.evidence_slot_ids) & remaining_slots
                    ),
                    key=lambda candidate: _deployment_slot_candidate_sort_key(
                        candidate,
                        remaining_slots=remaining_slots,
                        slot_order=deployment_slot_order,
                        candidate_position=candidate_positions[id(candidate)],
                    ),
                )
                for candidate in slot_candidates:
                    if add_candidate(candidate, enforce_paragraph_diversity=False):
                        covered_slots.update(candidate.evidence_slot_ids)
                        progress = True
                        break
                if not progress:
                    break

            for marker_group in _deployment_required_marker_groups_for_query(query):
                if len(selected) >= limit:
                    break
                if any(
                    _candidate_matches_deployment_marker_group(candidate, marker_group)
                    for candidate in selected
                ):
                    continue
                marker_candidates = sorted(
                    (
                        candidate
                        for candidate in candidates
                        if candidate_key(candidate) not in selected_keys
                        and _candidate_matches_deployment_marker_group(candidate, marker_group)
                    ),
                    key=lambda candidate: _deployment_marker_candidate_sort_key(
                        candidate,
                        marker_group=marker_group,
                        candidate_position=candidate_positions[id(candidate)],
                    ),
                )
                for candidate in marker_candidates:
                    if add_candidate(candidate, enforce_paragraph_diversity=False):
                        covered_slots.update(candidate.evidence_slot_ids)
                        break

        technical_slot_order = _technical_slot_order_for_query(query)
        if technical_slot_order:
            candidate_positions = {
                id(candidate): index for index, candidate in enumerate(candidates)
            }
            normalized_repo_candidates = sorted(
                (
                    candidate
                    for candidate in candidates
                    if not candidate.rejected_rules
                    and candidate.normalized_from_readme
                    and _candidate_source_role(candidate, query=query) == "official_repository"
                ),
                key=lambda candidate: _technical_source_role_candidate_sort_key(
                    candidate,
                    candidate_position=candidate_positions[id(candidate)],
                ),
            )
            for candidate in normalized_repo_candidates[:2]:
                add_candidate(candidate, enforce_paragraph_diversity=False)
            for role in _technical_source_role_order_for_query(query):
                if len(selected) >= limit:
                    break
                selected_role_present = any(
                    _candidate_source_role(candidate, query=query) == role for candidate in selected
                )
                if selected_role_present:
                    continue
                role_candidates = sorted(
                    (
                        candidate
                        for candidate in candidates
                        if candidate_key(candidate) not in selected_keys
                        and _candidate_source_role(candidate, query=query) == role
                        and (
                            candidate.score.answer_relevant
                            or (
                                role == "official_repository"
                                and _repository_candidate_allowed_for_slot_backfill(candidate)
                            )
                        )
                    ),
                    key=lambda candidate: _technical_source_role_candidate_sort_key(
                        candidate,
                        candidate_position=candidate_positions[id(candidate)],
                    ),
                )
                for candidate in role_candidates:
                    if add_candidate(candidate, enforce_paragraph_diversity=False):
                        break

            covered_slots = {
                slot_id
                for candidate in selected
                for slot_id in candidate.evidence_slot_ids
                if slot_id in technical_slot_order
            }
            while len(selected) < limit:
                remaining_slots = set(technical_slot_order) - covered_slots
                if not remaining_slots:
                    break
                slot_candidates = sorted(
                    (
                        candidate
                        for candidate in candidates
                        if candidate_key(candidate) not in selected_keys
                        and set(candidate.evidence_slot_ids) & remaining_slots
                        and candidate.score.answer_relevant
                    ),
                    key=lambda candidate: _technical_slot_candidate_sort_key(
                        candidate,
                        remaining_slots=remaining_slots,
                        slot_order=technical_slot_order,
                        candidate_position=candidate_positions[id(candidate)],
                    ),
                )
                if not slot_candidates:
                    break
                if add_candidate(slot_candidates[0], enforce_paragraph_diversity=False):
                    covered_slots.update(slot_candidates[0].evidence_slot_ids)
                else:
                    break

        if _query_needs_source_balanced_candidates(query):
            covered_source_documents = {
                candidate.source_chunk.source_document_id for candidate in selected
            }
            for candidate in candidates:
                if len(selected) >= limit:
                    break
                if candidate.source_chunk.source_document_id in covered_source_documents:
                    continue
                if not candidate.score.answer_relevant:
                    continue
                if add_candidate(candidate, enforce_paragraph_diversity=True):
                    covered_source_documents.add(candidate.source_chunk.source_document_id)

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

        return selected[:limit], near_duplicate_removed[0]

    def _build_claim_notes(
        self,
        *,
        query: str,
        source_chunk: SourceChunk,
        retrieval_score: float | None,
        candidate: DraftClaimCandidate,
        selection_rank: int,
        relation_type: str,
        evidence_candidate: dict[str, Any],
    ) -> dict[str, Any]:
        slot_ids_value = evidence_candidate.get("slot_ids")
        slot_ids = list(slot_ids_value) if isinstance(slot_ids_value, list | tuple) else []
        evidence_metadata = evidence_candidate.get("metadata")
        source_role = evidence_candidate.get("source_role")
        if source_role is None and isinstance(evidence_metadata, dict):
            source_role = evidence_metadata.get("source_role")
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
            "slot_ids": slot_ids,
            "source_intent": evidence_candidate.get("source_intent"),
            "source_role": source_role,
            "evidence_candidate_id": evidence_candidate.get("evidence_candidate_id"),
            "evidence_kind": candidate.evidence_kind,
            "normalized_from_readme": candidate.normalized_from_readme,
            "cleaned_github_readme": candidate.cleaned_github_readme,
            "evidence_quality_score": evidence_candidate.get("quality_score"),
            "evidence_salience_score": evidence_candidate.get("salience_score"),
            "evidence_rejection_reasons": evidence_candidate.get("rejection_reasons", []),
            "evidence_candidate": evidence_candidate,
            **candidate.score.as_notes(),
        }

    def _merge_claim_notes(
        self,
        existing_notes: dict[str, Any],
        new_notes: dict[str, Any],
    ) -> dict[str, Any]:
        existing_score = _numeric_note(existing_notes.get("claim_selection_score"))
        new_score = _numeric_note(new_notes.get("claim_selection_score"))
        if existing_score is not None and new_score is not None and existing_score >= new_score:
            return existing_notes
        return {**existing_notes, **new_notes}

    def _candidate_support_verification_candidates(
        self,
        *,
        task: ResearchTask,
        claim: Claim,
        seen_spans: set[tuple[UUID, int, int]],
        readme_batch_tracker: dict[str, Any],
        readme_claim_tracker: dict[str, Any],
    ) -> list[VerificationEvidenceCandidate]:
        notes = claim.notes_json or {}
        slot_ids = tuple(item for item in notes.get("slot_ids", []) if isinstance(item, str))
        deployment_claim = (
            classify_query_intent(task.query).intent_name == "deployment"
            and notes.get("evidence_kind") == "deployment_code_or_config"
        ) or any(slot_id.startswith("deployment_") for slot_id in slot_ids)

        candidates: list[VerificationEvidenceCandidate] = []
        for evidence in self.claim_evidence_repository.list_for_claim(claim.id):
            if evidence.relation_type != CLAIM_EVIDENCE_RELATION_CANDIDATE_SUPPORT:
                continue
            citation_span = evidence.citation_span
            source_chunk = citation_span.source_chunk
            if source_chunk.source_document.task_id != task.id:
                continue
            if not _source_chunk_eligible_for_claims(source_chunk):
                continue
            lexical_span = select_verification_span(source_chunk.text, claim.statement)
            matched_span = lexical_span
            readme_meta: dict[str, Any] | None = None
            if (
                matched_span is not None
                and not deployment_claim
                and matched_span.relation_type != CLAIM_EVIDENCE_RELATION_SUPPORT
                and _claim_eligible_for_readme_repository_normalized_composite(
                    claim, task, source_chunk
                )
                and _query_asks_technical_explanation_for_readme_verification(task.query)
            ):
                matched_span = None

            if matched_span is None and not deployment_claim:
                if (
                    _claim_eligible_for_readme_repository_normalized_composite(
                        claim, task, source_chunk
                    )
                    and _query_asks_technical_explanation_for_readme_verification(task.query)
                ):
                    readme_batch_tracker["repository_normalized_verification_attempt_count"] += 1
                    readme_claim_tracker["repository_normalized_verification_attempt_count"] += 1
                    composite_span, composite_diag = (
                        try_repository_readme_normalized_composite_verification(
                            source_text=source_chunk.text,
                            statement=claim.statement,
                            draft_excerpt=citation_span.excerpt,
                            start_offset=citation_span.start_offset,
                            end_offset=citation_span.end_offset,
                            query=task.query,
                        )
                    )
                    if composite_span is not None:
                        matched_span = composite_span
                        readme_meta = composite_diag
                        readme_batch_tracker[
                            "repository_normalized_verification_supported_count"
                        ] += 1
                        readme_claim_tracker[
                            "repository_normalized_verification_supported_count"
                        ] += 1
                        method = composite_diag.get("repository_normalized_support_method")
                        if isinstance(method, str):
                            batch_methods = readme_batch_tracker[
                                "repository_normalized_support_method_distribution"
                            ]
                            claim_methods = readme_claim_tracker[
                                "repository_normalized_support_method_distribution"
                            ]
                            batch_methods[method] += 1
                            claim_methods[method] += 1
                        _readme_set_diag(readme_batch_tracker, composite_diag)
                        _readme_set_diag(readme_claim_tracker, composite_diag)
                    else:
                        _readme_set_diag(readme_batch_tracker, composite_diag)
                        _readme_set_diag(readme_claim_tracker, composite_diag)
                        rej = composite_diag.get("repository_normalized_support_rejection")
                        if isinstance(rej, str):
                            readme_batch_tracker[
                                "repository_normalized_verification_rejection_reason_distribution"
                            ][rej] += 1
                            readme_claim_tracker[
                                "repository_normalized_verification_rejection_reason_distribution"
                            ][rej] += 1
            if matched_span is None:
                continue
            if (
                not deployment_claim
                and matched_span.relation_type != CLAIM_EVIDENCE_RELATION_SUPPORT
            ):
                continue
            span_key = (
                source_chunk.id,
                matched_span.start_offset,
                matched_span.end_offset,
            )
            if span_key in seen_spans:
                continue
            seen_spans.add(span_key)
            candidates.append(
                _verification_evidence_candidate(
                    source_chunk=source_chunk,
                    matched_span=matched_span,
                    retrieval_score=evidence.score,
                    readme_composite_metadata=readme_meta,
                )
            )
        return candidates

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
            claims = self.claim_repository.list_for_task(
                task_id,
                verification_status=CLAIM_VERIFICATION_STATUS_DRAFT,
                limit=limit,
            )
            return [claim for claim in claims if not _llm_claim_review_rejected(claim)]

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
            if _llm_claim_review_rejected(claim):
                continue
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
                    CLAIM_EVIDENCE_RELATION_WEAK_SUPPORT: 0,
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
                    CLAIM_EVIDENCE_RELATION_WEAK_SUPPORT: 0,
                    CLAIM_EVIDENCE_RELATION_CONTRADICT: 0,
                },
            )
            summaries.append(
                ClaimSummaryEntry(
                    claim=claim,
                    support_evidence_count=counts[CLAIM_EVIDENCE_RELATION_SUPPORT],
                    weak_support_evidence_count=counts[CLAIM_EVIDENCE_RELATION_WEAK_SUPPORT],
                    contradict_evidence_count=counts[CLAIM_EVIDENCE_RELATION_CONTRADICT],
                    rationale=verification_notes.get("rationale"),
                )
            )
        return summaries

    def _count_claim_evidence(
        self,
        claim_id: UUID,
        *,
        relation_details: list[dict[str, object]] | None = None,
    ) -> tuple[int, int, int]:
        if relation_details is not None:
            support_count = sum(
                1
                for detail in relation_details
                if detail.get("relation_type") == CLAIM_EVIDENCE_RELATION_SUPPORT
            )
            weak_support_count = sum(
                1
                for detail in relation_details
                if detail.get("relation_type") == CLAIM_EVIDENCE_RELATION_WEAK_SUPPORT
            )
            contradict_count = sum(
                1
                for detail in relation_details
                if detail.get("relation_type") == CLAIM_EVIDENCE_RELATION_CONTRADICT
            )
            return support_count, weak_support_count, contradict_count

        claim_evidence = self.claim_evidence_repository.list_for_claim(claim_id)
        support_count = 0
        weak_support_count = 0
        contradict_count = 0
        for evidence in claim_evidence:
            if evidence.relation_type == CLAIM_EVIDENCE_RELATION_SUPPORT:
                support_count += 1
            elif evidence.relation_type == CLAIM_EVIDENCE_RELATION_WEAK_SUPPORT:
                weak_support_count += 1
            elif evidence.relation_type == CLAIM_EVIDENCE_RELATION_CONTRADICT:
                contradict_count += 1
        return support_count, weak_support_count, contradict_count

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

        if score is not None and (claim_evidence.score is None or score > claim_evidence.score):
            claim_evidence.score = score
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


def _verification_evidence_candidate(
    *,
    source_chunk: SourceChunk,
    matched_span: VerificationSpanMatch,
    retrieval_score: float | None,
    readme_composite_metadata: dict[str, Any] | None = None,
) -> VerificationEvidenceCandidate:
    source_quality_score = _source_quality_score(source_chunk)
    content_quality_score = _chunk_content_quality_score(source_chunk)
    information_density_score = _chunk_information_density_score(source_chunk)
    retrieval_component = min(max(retrieval_score or 0.0, 0.0), 5.0) / 5.0
    rank_score = (
        (matched_span.score * 0.46)
        + ((source_quality_score or 0.5) * 0.2)
        + ((content_quality_score or 0.5) * 0.14)
        + ((information_density_score or 0.5) * 0.1)
        + (retrieval_component * 0.1)
    )
    rounded_rank_score = round(min(0.99, max(0.0, rank_score)), 4)
    return VerificationEvidenceCandidate(
        source_chunk=source_chunk,
        matched_span=matched_span,
        retrieval_score=retrieval_score,
        rank_score=rounded_rank_score,
        diversity_adjusted_score=rounded_rank_score,
        reuse_penalty=0.0,
        chunk_reuse_count_before=0,
        span_reuse_count_before=0,
        content_reuse_count_before=0,
        source_quality_score=round(source_quality_score or 0.5, 4),
        content_quality_score=round(content_quality_score or 0.5, 4),
        information_density_score=round(information_density_score or 0.5, 4),
        content_hash=_source_content_hash(source_chunk),
        chunk_text_hash=_source_chunk_text_hash(source_chunk),
        span_text_hash=normalized_excerpt_hash(matched_span.excerpt),
        readme_composite_metadata=readme_composite_metadata,
    )


def _select_verification_evidence(
    candidates: list[VerificationEvidenceCandidate],
    *,
    reuse_tracker: EvidenceReuseTracker | None = None,
) -> list[VerificationEvidenceCandidate]:
    if not candidates:
        return []
    strong_support = _select_diverse_relation_evidence(
        candidates,
        relation_type=CLAIM_EVIDENCE_RELATION_SUPPORT,
        limit=1,
        reuse_tracker=reuse_tracker,
    )
    contradict = _select_diverse_relation_evidence(
        candidates,
        relation_type=CLAIM_EVIDENCE_RELATION_CONTRADICT,
        limit=1,
        reuse_tracker=reuse_tracker,
    )
    weak_limit = 1 if not strong_support or contradict else 0
    weak_support = _select_diverse_relation_evidence(
        candidates,
        relation_type=CLAIM_EVIDENCE_RELATION_WEAK_SUPPORT,
        limit=weak_limit,
        used_keys=_candidate_diversity_keys(strong_support + contradict),
        reuse_tracker=reuse_tracker,
    )
    selected = strong_support + contradict + weak_support
    return sorted(
        selected,
        key=lambda item: (
            _verification_relation_sort_key(item.matched_span.relation_type),
            -item.diversity_adjusted_score,
            -item.rank_score,
            item.source_chunk.source_document.domain,
            item.source_chunk.chunk_no,
            item.matched_span.start_offset,
        ),
    )


def _select_diverse_relation_evidence(
    candidates: list[VerificationEvidenceCandidate],
    *,
    relation_type: str,
    limit: int,
    used_keys: set[tuple[str, str | None]] | None = None,
    reuse_tracker: EvidenceReuseTracker | None = None,
) -> list[VerificationEvidenceCandidate]:
    if limit <= 0:
        return []
    selected: list[VerificationEvidenceCandidate] = []
    selected_keys = set(used_keys or set())
    relation_candidates = sorted(
        (
            _candidate_with_reuse_penalty(candidate, reuse_tracker)
            for candidate in candidates
            if candidate.matched_span.relation_type == relation_type
        ),
        key=lambda item: (
            -item.diversity_adjusted_score,
            -item.rank_score,
            -item.source_quality_score,
            -item.content_quality_score,
            item.source_chunk.source_document.domain,
            item.source_chunk.chunk_no,
            item.matched_span.start_offset,
        ),
    )
    for candidate in relation_candidates:
        diversity_key = (
            candidate.source_chunk.source_document.domain,
            candidate.content_hash,
        )
        if diversity_key in selected_keys:
            continue
        selected.append(candidate)
        selected_keys.add(diversity_key)
        if len(selected) >= limit:
            break
    if len(selected) >= limit:
        return selected
    for candidate in relation_candidates:
        if candidate in selected:
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def _candidate_diversity_keys(
    candidates: list[VerificationEvidenceCandidate],
) -> set[tuple[str, str | None]]:
    return {
        (
            candidate.source_chunk.source_document.domain,
            candidate.content_hash,
        )
        for candidate in candidates
    }


def _candidate_with_reuse_penalty(
    candidate: VerificationEvidenceCandidate,
    reuse_tracker: EvidenceReuseTracker | None,
) -> VerificationEvidenceCandidate:
    if reuse_tracker is None:
        return candidate
    chunk_key = _candidate_chunk_key(candidate)
    span_key = _candidate_span_key(candidate)
    content_key = _candidate_content_key(candidate)
    chunk_reuse_count = reuse_tracker.chunk_counts.get(chunk_key, 0)
    span_reuse_count = reuse_tracker.span_counts.get(span_key, 0)
    content_reuse_count = reuse_tracker.content_counts.get(content_key, 0)
    penalty = min(
        0.14,
        min(0.08, chunk_reuse_count * 0.04)
        + min(0.10, span_reuse_count * 0.05)
        + min(0.06, content_reuse_count * 0.03),
    )
    adjusted_score = round(max(0.0, candidate.rank_score - penalty), 4)
    return replace(
        candidate,
        diversity_adjusted_score=adjusted_score,
        reuse_penalty=round(penalty, 4),
        chunk_reuse_count_before=chunk_reuse_count,
        span_reuse_count_before=span_reuse_count,
        content_reuse_count_before=content_reuse_count,
    )


def _record_candidate_reuse(
    reuse_tracker: EvidenceReuseTracker,
    candidate: VerificationEvidenceCandidate,
) -> None:
    chunk_key = _candidate_chunk_key(candidate)
    span_key = _candidate_span_key(candidate)
    content_key = _candidate_content_key(candidate)
    reuse_tracker.chunk_counts[chunk_key] = reuse_tracker.chunk_counts.get(chunk_key, 0) + 1
    reuse_tracker.span_counts[span_key] = reuse_tracker.span_counts.get(span_key, 0) + 1
    reuse_tracker.content_counts[content_key] = reuse_tracker.content_counts.get(content_key, 0) + 1


def _candidate_chunk_key(candidate: VerificationEvidenceCandidate) -> str:
    return str(candidate.source_chunk.id)


def _candidate_span_key(candidate: VerificationEvidenceCandidate) -> str:
    return (
        f"{candidate.source_chunk.id}:"
        f"{candidate.matched_span.start_offset}:"
        f"{candidate.matched_span.end_offset}:"
        f"{candidate.span_text_hash}"
    )


def _candidate_content_key(candidate: VerificationEvidenceCandidate) -> str:
    return candidate.span_text_hash or candidate.chunk_text_hash


def _evidence_diversity_summary(
    evidence_relation_details: list[dict[str, object]],
) -> dict[str, object]:
    source_document_ids = _unique_strings(evidence_relation_details, "source_document_id")
    source_chunk_ids = _unique_strings(evidence_relation_details, "source_chunk_id")
    citation_span_ids = _unique_strings(evidence_relation_details, "citation_span_id")
    span_hashes = _unique_strings(evidence_relation_details, "span_text_hash")
    reuse_penalties = [
        value
        for value in (
            _numeric_note(item.get("reuse_penalty")) for item in evidence_relation_details
        )
        if value is not None
    ]
    return {
        "evidence_count": len(evidence_relation_details),
        "unique_source_count": len(source_document_ids),
        "unique_chunk_count": len(source_chunk_ids),
        "unique_span_count": len(citation_span_ids),
        "unique_span_hash_count": len(span_hashes),
        "max_reuse_penalty": round(max(reuse_penalties), 4) if reuse_penalties else 0.0,
        "mean_reuse_penalty": (
            round(sum(reuse_penalties) / len(reuse_penalties), 4) if reuse_penalties else 0.0
        ),
    }


def _unique_strings(rows: list[dict[str, object]], key: str) -> set[str]:
    values: set[str] = set()
    for row in rows:
        value = row.get(key)
        if isinstance(value, str) and value:
            values.add(value)
    return values


def _verification_relation_sort_key(relation_type: str) -> int:
    if relation_type == CLAIM_EVIDENCE_RELATION_SUPPORT:
        return 0
    if relation_type == CLAIM_EVIDENCE_RELATION_CONTRADICT:
        return 1
    if relation_type == CLAIM_EVIDENCE_RELATION_WEAK_SUPPORT:
        return 2
    return 3


def _source_chunk_eligible_for_claims(source_chunk: SourceChunk) -> bool:
    metadata = source_chunk.metadata_json or {}
    quality_reasons = metadata.get("quality_reasons")
    deployment_code_or_config = (
        isinstance(quality_reasons, list) and "deployment_code_or_config" in quality_reasons
    )
    if metadata.get("eligible_for_claims") is False:
        return False
    if metadata.get("should_generate_claims") is False:
        return False
    if metadata.get("is_reference_section") is True:
        return False
    if metadata.get("is_navigation_noise") is True:
        return False
    if metadata.get("is_diagram_or_config_section") is True and not deployment_code_or_config:
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
    quality_reasons = metadata.get("quality_reasons")
    deployment_code_or_config = (
        isinstance(quality_reasons, list) and "deployment_code_or_config" in quality_reasons
    )
    if metadata.get("is_reference_section") is True:
        return True
    if metadata.get("is_navigation_noise") is True:
        return True
    if metadata.get("is_diagram_or_config_section") is True and not deployment_code_or_config:
        return True
    if metadata.get("reason") == "redirect_stub":
        return True
    source_score = source_chunk.source_document.final_source_score
    return source_score is not None and source_score < 0.2


_README_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_README_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.+?)\s*$")
_README_DASH_ITEM_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /&+_.-]{2,80})\s+[—-]\s+(.+?)\s*$")
_README_IGNORE_SECTION_MARKERS = (
    "license",
    "contributing",
    "contribution",
    "community",
    "support",
    "contributors",
    "acknowledg",
    "acknowledgement",
    "roadmap",
    "changelog",
    "release notes",
    "faq",
    "installation",
    "install",
    "requirements",
    "prerequisite",
    "security policy",
)
_README_IGNORE_BULLET_MARKERS = (
    "http://",
    "https://",
    "badge",
    "license",
    "contributing",
    "community",
    "star",
    "fork",
    "issue",
    "pull request",
    "discord",
    "slack",
    "join ",
    "follow ",
    "pip install",
    "npm install",
    "brew install",
    "git clone",
)
_README_NORMALIZED_MAX_CANDIDATES_PER_CHUNK = 2
_README_NORMALIZED_MAX_GROUPS_PER_FULL_RAW_README_DOC = 4
_GITHUB_README_UI_LINE_KEYS = frozenset(
    {
        "actions",
        "activity",
        "branches",
        "branchestags",
        "code",
        "contributors",
        "fork",
        "forks",
        "foldersandfiles",
        "history",
        "insights",
        "issues",
        "lastcommitdate",
        "lastcommitmessage",
        "latestcommit",
        "license",
        "name",
        "notifications",
        "openmoreactionsmenu",
        "packages",
        "projects",
        "public",
        "pullrequests",
        "readmemd",
        "releases",
        "repositoryfilesnavigation",
        "resources",
        "security",
        "star",
        "stars",
        "tags",
        "watch",
    }
)
_GITHUB_README_DROP_SECTION_HEADINGS = frozenset(
    {
        "about",
        "activity",
        "branches",
        "code of conduct",
        "community",
        "contributing",
        "contributing guide",
        "contributors",
        "forks",
        "license",
        "packages",
        "releases",
        "resources",
        "security",
        "security policy",
        "stars",
        "topics",
    }
)
_GITHUB_README_DROP_LINE_PREFIXES = (
    "contributing guide",
    "code of conduct",
    "discussions:",
    "forked from ",
    "open more actions menu",
    "you must be signed in",
)
_GITHUB_COUNTER_LINE_RE = re.compile(r"^\d+(?:[.,]\d+)?[kKmM]?$")
_GITHUB_COMMIT_COUNT_LINE_RE = re.compile(r"^\d[\d,]*\s+commits?$", re.IGNORECASE)
_GITHUB_TOC_LINK_LINE_RE = re.compile(
    r"^\s*(?:[-*+]|\d+\.)\s+\[[^\]]+\]\(#[^)]+\)\s*$"
)
_GITHUB_BADGE_LINE_RE = re.compile(
    r"(?:!\[[^\]]*\]\([^)]+(?:badge|shields|actions/workflows)[^)]+\)|"
    r"shields\.io|badge\.svg)",
    re.IGNORECASE,
)


def _should_normalize_repository_readme_candidates(
    *,
    query: str,
    source_chunk: SourceChunk,
    source_role: str,
) -> bool:
    if source_role != "official_repository":
        return False
    if not _technical_slot_order_for_query(query):
        return False
    if classify_query_intent(query).intent_name == "deployment":
        return False
    return _is_repository_readme_or_overview_url(
        canonical_url=source_chunk.source_document.canonical_url,
        domain=source_chunk.source_document.domain,
    )


def _github_readme_clean_result_for_claims(
    *,
    query: str,
    source_chunk: SourceChunk,
    source_role: str,
    readme_body_text: str | None = None,
) -> GithubReadmeCleanResult:
    body = readme_body_text if readme_body_text is not None else source_chunk.text
    if not _should_clean_github_readme_for_claims(
        query=query,
        source_chunk=source_chunk,
        source_role=source_role,
    ):
        return GithubReadmeCleanResult(
            text=body,
            line_spans=_line_spans_for_text(body),
            applied=False,
            removed_line_count=0,
            kept_line_count=len(body.splitlines()),
        )
    return _clean_github_readme_text(body)


def _should_clean_github_readme_for_claims(
    *,
    query: str,
    source_chunk: SourceChunk,
    source_role: str,
) -> bool:
    if source_role != "official_repository":
        return False
    if not _technical_slot_order_for_query(query):
        return False
    if classify_query_intent(query).intent_name == "deployment":
        return False
    return _is_repository_readme_or_overview_url(
        canonical_url=source_chunk.source_document.canonical_url,
        domain=source_chunk.source_document.domain,
    )


def _is_raw_github_readme_readme_document_chunk(source_chunk: SourceChunk) -> bool:
    """Narrow: raw GitHub README markdown only (not arbitrary raw paths)."""
    doc = source_chunk.source_document
    domain = (doc.domain or "").lower().rstrip(".")
    if domain != "raw.githubusercontent.com":
        return False
    url = (doc.canonical_url or "").lower()
    return url.endswith("/readme.md") or url.endswith("/readme.markdown")


def _readme_first_bullet_original_start_in_range(full_raw: str, o_st: int, o_en: int) -> int | None:
    segment = full_raw[o_st:o_en]
    pos = o_st
    for raw_line in segment.splitlines(keepends=True):
        stripped = raw_line.strip()
        if not stripped:
            pos += len(raw_line)
            continue
        is_bullet_line = _readme_bullet_text(stripped) is not None or _readme_dash_item_text(
            stripped
        ) is not None
        if is_bullet_line:
            return pos
        pos += len(raw_line)
    return None


def _readme_best_owner_chunk_local_span(
    ordered_chunks: list[SourceChunk],
    full_raw: str,
    global_start: int,
    global_end: int,
) -> tuple[SourceChunk, int, int] | None:
    """Pick the chunk with the largest character overlap for [global_start, global_end)."""
    best: tuple[int, SourceChunk, int, int] | None = None
    pos = 0
    for chunk in ordered_chunks:
        base = pos
        ceiling = base + len(chunk.text)
        lo = max(global_start, base)
        hi = min(global_end, ceiling)
        if hi > lo:
            overlap = hi - lo
            local_start = lo - base
            local_end = hi - base
            if best is None or overlap > best[0]:
                best = (overlap, chunk, local_start, local_end)
        pos = ceiling + 1
    if best is None:
        return None
    return best[1], best[2], best[3]


def _task_raw_readme_readme_document_ids(
    repository: SourceChunkRepository,
    task_id: UUID,
    *,
    cap: int = 2,
) -> set[UUID]:
    """Up to ``cap`` raw README ``source_document`` ids for a task (stable fetch order)."""
    ordered: list[UUID] = []
    seen: set[UUID] = set()
    for chunk in repository.list_for_task(task_id, limit=800):
        if not _is_raw_github_readme_readme_document_chunk(chunk):
            continue
        doc_id = chunk.source_document_id
        if doc_id in seen:
            continue
        seen.add(doc_id)
        ordered.append(doc_id)
        if len(ordered) >= cap:
            break
    return set(ordered)


def _raw_readme_full_document_normalized_candidates(
    *,
    source_chunk_repository: SourceChunkRepository,
    chunks_seen: list[tuple[SourceChunk, float | None]],
    query: str,
    existing_candidates: list[DraftClaimCandidate],
    document_target_slots: dict[UUID, frozenset[str]] | None = None,
) -> tuple[list[DraftClaimCandidate], dict[str, int]]:
    """
    Paragraph chunking often splits markdown ``##`` headings from their bullet lists. The
    per-chunk normalizer then produces zero groups. For raw ``README.md`` / ``README.markdown``
    on ``raw.githubusercontent.com`` only, join all eligible chunks for the document, extract
    heading/bullet groups once, and attach citations to the chunk that contains the bulk of the
    bullet lines (bounded; technical-explanation queries only).
    """
    diag = {
        "raw_readme_full_document_group_count": 0,
        "raw_readme_full_document_normalized_candidate_count": 0,
    }
    if classify_query_intent(query).intent_name == "deployment":
        return [], diag
    if not _technical_slot_order_for_query(query):
        return [], diag

    if not chunks_seen:
        return [], diag

    task_id = chunks_seen[0][0].source_document.task_id

    doc_ids: set[UUID] = {
        ch.source_document_id
        for ch, _ in chunks_seen
        if _is_raw_github_readme_readme_document_chunk(ch)
    }
    doc_ids |= _task_raw_readme_readme_document_ids(source_chunk_repository, task_id, cap=2)
    if not doc_ids:
        return [], diag

    existing_identities = {normalize_claim_identity(c.statement) for c in existing_candidates}
    out: list[DraftClaimCandidate] = []

    for doc_id in doc_ids:
        ordered = [
            c
            for c in source_chunk_repository.list_for_document(doc_id)
            if _source_chunk_eligible_for_claims(c)
        ]
        if len(ordered) < 2:
            continue
        head_chunk = ordered[0]
        classification = classify_source_intent(
            canonical_url=head_chunk.source_document.canonical_url,
            domain=head_chunk.source_document.domain,
            title=head_chunk.source_document.title,
            query=query,
        )
        if not _should_normalize_repository_readme_candidates(
            query=query,
            source_chunk=head_chunk,
            source_role=classification.source_role,
        ):
            continue

        full_raw = "\n".join(c.text for c in ordered)
        if len(full_raw) < 80:
            continue

        clean_result = _github_readme_clean_result_for_claims(
            query=query,
            source_chunk=head_chunk,
            source_role=classification.source_role,
            readme_body_text=full_raw,
        )
        groups = _repository_heading_bullet_groups(
            clean_result.text,
            original_text=full_raw,
            original_line_spans=clean_result.line_spans,
        )
        if not groups:
            continue

        capped = groups[:_README_NORMALIZED_MAX_GROUPS_PER_FULL_RAW_README_DOC]
        diag["raw_readme_full_document_group_count"] += len(capped)
        subject = _repository_subject_name(source_chunk=head_chunk, query=query)
        seen_local: set[str] = set()

        for heading, items, _ost, oen, _excerpt in capped:
            statement = _repository_readme_statement(subject=subject, heading=heading, items=items)
            if not statement:
                continue
            ident = normalize_claim_identity(statement)
            if ident in existing_identities or ident in seen_local:
                continue

            bullet_start = _readme_first_bullet_original_start_in_range(full_raw, _ost, oen)
            if bullet_start is None:
                bullet_start = _ost
            mapped = _readme_best_owner_chunk_local_span(ordered, full_raw, bullet_start, oen)
            if mapped is None:
                continue
            owner, ls, le = mapped
            if le <= ls or not owner.text[ls:le].strip():
                continue

            span_excerpt = owner.text[ls:le]
            supporting_span = SupportingSpan(start_offset=ls, end_offset=le, excerpt=span_excerpt)
            page_title = owner.source_document.title
            content_quality_score = _chunk_content_quality_score(owner)
            source_quality_score = _source_quality_score(owner)
            lim_slot = _limitations_official_planner_target_slot_id(
                technical_explanation=document_target_slots is not None,
                document_target_slots=document_target_slots,
                source_document_id=owner.source_document_id,
                source_role=classification.source_role,
            )
            score = score_claim_statement(
                statement=statement,
                query=query,
                content_quality_score=content_quality_score,
                source_quality_score=source_quality_score,
                domain=owner.source_document.domain,
                source_url=owner.source_document.canonical_url,
                page_title=page_title,
                target_slot_id=lim_slot,
            )
            evidence_slot_ids = technical_slot_ids_for_text(
                text=f"{statement}\n{span_excerpt}",
                category=score.claim_category,
                query=query,
                source_intent=classification.source_intent,
            )
            lexical_slots = evidence_slot_ids
            planner_only: tuple[str, ...] = ()
            if document_target_slots is not None:
                evidence_slot_ids, planner_only = merge_technical_lexical_and_planner_slots(
                    lexical_slots=lexical_slots,
                    source_document_id=owner.source_document_id,
                    source_role=classification.source_role,
                    document_target_slots=document_target_slots,
                )
            rejected_rules = _strict_rejected_rules(owner, statement, query, score)
            if score.rejected_reason == "reference_or_citation":
                rejected_rules = [
                    rule for rule in rejected_rules if rule != "reference_or_citation"
                ]
            out.append(
                DraftClaimCandidate(
                    source_chunk=owner,
                    supporting_span=supporting_span,
                    statement=statement,
                    score=score,
                    retrieval_score=None,
                    paragraph_key=(owner.id, _paragraph_index(owner.text, ls)),
                    rejected_rules=tuple(rejected_rules),
                    original_rejected_reason=_first_rejection_reason(rejected_rules, score),
                    evidence_slot_ids=evidence_slot_ids,
                    lexical_evidence_slot_ids=lexical_slots,
                    candidate_target_slot_ids=planner_only,
                    normalized_from_readme=True,
                    cleaned_github_readme=clean_result.applied,
                )
            )
            seen_local.add(ident)
            existing_identities.add(ident)

    diag["raw_readme_full_document_normalized_candidate_count"] = len(out)
    return out, diag


def _clean_github_readme_text(text: str) -> GithubReadmeCleanResult:
    raw_lines = text.splitlines(keepends=True)
    if not raw_lines:
        return GithubReadmeCleanResult(
            text="",
            line_spans=(),
            applied=True,
            removed_line_count=0,
            kept_line_count=0,
        )

    original_lines: list[tuple[str, int, int]] = []
    cursor = 0
    for raw_line in raw_lines:
        start = cursor
        end = cursor + len(raw_line)
        original_lines.append((raw_line, start, end))
        cursor = end

    body_start_index = _github_readme_body_start_index(original_lines)
    kept_lines: list[str] = []
    kept_spans: list[tuple[int, int]] = []
    removed_line_count = body_start_index
    seen_short_line_keys: set[str] = set()
    drop_section = False

    for raw_line, start, end in original_lines[body_start_index:]:
        stripped = raw_line.strip()
        if not stripped:
            if kept_lines and kept_lines[-1].strip():
                kept_lines.append(raw_line)
                kept_spans.append((start, end))
            continue

        if _github_readme_drop_section_heading(stripped):
            drop_section = True
            removed_line_count += 1
            continue
        if drop_section:
            if _readme_heading_text(stripped) is not None:
                drop_section = False
            else:
                removed_line_count += 1
                continue

        if _github_readme_noise_line(stripped, seen_short_line_keys=seen_short_line_keys):
            removed_line_count += 1
            continue

        line_key = _github_readme_line_key(stripped)
        if _looks_like_duplicate_nav_line(stripped) and line_key in seen_short_line_keys:
            removed_line_count += 1
            continue
        if _looks_like_duplicate_nav_line(stripped):
            seen_short_line_keys.add(line_key)

        kept_lines.append(raw_line)
        kept_spans.append((start, end))

    while kept_lines and not kept_lines[-1].strip():
        kept_lines.pop()
        kept_spans.pop()

    return GithubReadmeCleanResult(
        text="".join(kept_lines),
        line_spans=tuple(kept_spans),
        applied=True,
        removed_line_count=removed_line_count,
        kept_line_count=len(kept_lines),
    )


def _line_spans_for_text(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for raw_line in text.splitlines(keepends=True):
        start = cursor
        end = cursor + len(raw_line)
        spans.append((start, end))
        cursor = end
    return tuple(spans)


def _github_readme_body_start_index(lines: list[tuple[str, int, int]]) -> int:
    for index, (raw_line, _, _) in enumerate(lines):
        if raw_line.strip().lower() == "repository files navigation":
            return index + 1
    return 0


def _github_readme_noise_line(
    line: str,
    *,
    seen_short_line_keys: set[str],
) -> bool:
    lower = _clean_markdown_text(line).lower()
    key = _github_readme_line_key(line)
    if key in _GITHUB_README_UI_LINE_KEYS:
        return True
    if any(lower.startswith(prefix) for prefix in _GITHUB_README_DROP_LINE_PREFIXES):
        return True
    if _GITHUB_COUNTER_LINE_RE.match(line) or _GITHUB_COMMIT_COUNT_LINE_RE.match(line):
        return True
    if _GITHUB_BADGE_LINE_RE.search(line) or _GITHUB_TOC_LINK_LINE_RE.match(line):
        return True
    if key in seen_short_line_keys and _looks_like_duplicate_nav_line(line):
        return True
    return False


def _github_readme_drop_section_heading(line: str) -> bool:
    cleaned = _clean_markdown_text(line).lower().strip()
    if cleaned in _GITHUB_README_DROP_SECTION_HEADINGS:
        return True
    return any(
        cleaned.startswith(f"{heading} ") for heading in _GITHUB_README_DROP_SECTION_HEADINGS
    )


def _looks_like_duplicate_nav_line(line: str) -> bool:
    if len(line) > 80 or not line:
        return False
    if any(mark in line for mark in ".:;!?"):
        return False
    return bool(_github_readme_line_key(line))


def _github_readme_line_key(line: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean_markdown_text(line).lower())


def _is_repository_readme_or_overview_url(*, canonical_url: str, domain: str) -> bool:
    lower_url = canonical_url.lower()
    lower_domain = domain.lower()
    if lower_domain == "raw.githubusercontent.com":
        return lower_url.endswith("/readme.md")
    if lower_domain == "github.com":
        if "/issues" in lower_url or "/pull" in lower_url or "/discussions" in lower_url:
            return False
        path_parts = [part for part in lower_url.split("github.com/", 1)[-1].split("/") if part]
        if len(path_parts) == 2:
            return True
        if len(path_parts) >= 4 and path_parts[2] == "blob" and path_parts[-1] == "readme.md":
            return True
    return False


def _repository_readme_normalized_candidates(
    *,
    source_chunk: SourceChunk,
    query: str,
    page_title: str | None,
    content_quality_score: float | None,
    source_quality_score: float | None,
    source_intent: str,
    source_role: str,
    clean_result: GithubReadmeCleanResult | None = None,
    document_target_slots: dict[UUID, frozenset[str]] | None = None,
) -> list[DraftClaimCandidate]:
    candidate_text = clean_result.text if clean_result is not None else source_chunk.text
    groups = _repository_heading_bullet_groups(
        candidate_text,
        original_text=source_chunk.text,
        original_line_spans=clean_result.line_spans if clean_result is not None else (),
    )
    if not groups:
        return []
    subject = _repository_subject_name(source_chunk=source_chunk, query=query)
    candidates: list[DraftClaimCandidate] = []
    seen_statements: set[str] = set()
    for heading, items, start_offset, end_offset, excerpt in groups[
        : _README_NORMALIZED_MAX_CANDIDATES_PER_CHUNK
    ]:
        statement = _repository_readme_statement(subject=subject, heading=heading, items=items)
        if not statement:
            continue
        normalized_identity = normalize_claim_identity(statement)
        if normalized_identity in seen_statements:
            continue
        seen_statements.add(normalized_identity)
        supporting_span = SupportingSpan(
            start_offset=start_offset,
            end_offset=end_offset,
            excerpt=excerpt,
        )
        lim_slot = _limitations_official_planner_target_slot_id(
            technical_explanation=document_target_slots is not None,
            document_target_slots=document_target_slots,
            source_document_id=source_chunk.source_document_id,
            source_role=source_role,
        )
        score = score_claim_statement(
            statement=statement,
            query=query,
            content_quality_score=content_quality_score,
            source_quality_score=source_quality_score,
            domain=source_chunk.source_document.domain,
            source_url=source_chunk.source_document.canonical_url,
            page_title=page_title,
            target_slot_id=lim_slot,
        )
        evidence_slot_ids = technical_slot_ids_for_text(
            text=f"{statement}\n{excerpt}",
            category=score.claim_category,
            query=query,
            source_intent=source_intent,
        )
        lexical_slots = evidence_slot_ids
        planner_only: tuple[str, ...] = ()
        if document_target_slots is not None:
            evidence_slot_ids, planner_only = merge_technical_lexical_and_planner_slots(
                lexical_slots=lexical_slots,
                source_document_id=source_chunk.source_document_id,
                source_role=source_role,
                document_target_slots=document_target_slots,
            )
        rejected_rules = _strict_rejected_rules(source_chunk, statement, query, score)
        if score.rejected_reason == "reference_or_citation":
            rejected_rules = [rule for rule in rejected_rules if rule != "reference_or_citation"]
        candidates.append(
            DraftClaimCandidate(
                source_chunk=source_chunk,
                supporting_span=supporting_span,
                statement=statement,
                score=score,
                retrieval_score=None,
                paragraph_key=(source_chunk.id, _paragraph_index(source_chunk.text, start_offset)),
                rejected_rules=tuple(rejected_rules),
                original_rejected_reason=_first_rejection_reason(rejected_rules, score),
                evidence_slot_ids=evidence_slot_ids,
                lexical_evidence_slot_ids=lexical_slots,
                candidate_target_slot_ids=planner_only,
                normalized_from_readme=True,
                cleaned_github_readme=clean_result.applied if clean_result is not None else False,
            )
        )
    return candidates


def _repository_heading_bullet_groups(
    text: str,
    *,
    original_text: str | None = None,
    original_line_spans: tuple[tuple[int, int], ...] = (),
) -> list[tuple[str, list[str], int, int, str]]:
    lines: list[tuple[str, int, int, int, int]] = []
    cursor = 0
    for line_index, raw_line in enumerate(text.splitlines(keepends=True)):
        start = cursor
        end = cursor + len(raw_line)
        if original_line_spans and line_index < len(original_line_spans):
            original_start, original_end = original_line_spans[line_index]
        else:
            original_start, original_end = start, end
        lines.append((raw_line.rstrip("\r\n"), start, end, original_start, original_end))
        cursor = end
    groups: list[tuple[str, list[str], int, int, str]] = []
    index = 0
    while index < len(lines):
        line, heading_start, _, original_heading_start, _ = lines[index]
        heading = _readme_heading_text(line)
        if heading is None:
            index += 1
            continue
        heading_lower = heading.lower()
        if any(
            _readme_ignore_heading_marker_matches(marker, heading_lower)
            for marker in _README_IGNORE_SECTION_MARKERS
        ):
            index += 1
            continue
        cursor_index = index + 1
        while cursor_index < len(lines) and not lines[cursor_index][0].strip():
            cursor_index += 1
        bullet_items: list[str] = []
        bullet_end = heading_start
        original_bullet_end = original_heading_start
        while cursor_index < len(lines):
            bullet_line, _, bullet_line_end, _, original_bullet_line_end = lines[cursor_index]
            if not bullet_line.strip():
                if bullet_items:
                    cursor_index += 1
                    continue
                break
            bullet_value = _readme_bullet_text(bullet_line) or _readme_dash_item_text(bullet_line)
            if bullet_value is None:
                break
            cleaned_bullet = _clean_markdown_text(bullet_value)
            cleaned_lower = cleaned_bullet.lower()
            if cleaned_bullet and not any(
                marker in cleaned_lower for marker in _README_IGNORE_BULLET_MARKERS
            ):
                bullet_items.append(cleaned_bullet)
                bullet_end = bullet_line_end
                original_bullet_end = original_bullet_line_end
            cursor_index += 1
        if len(bullet_items) < 2:
            index += 1
            continue
        if original_text is not None and original_line_spans:
            excerpt = original_text[original_heading_start:original_bullet_end]
            start_offset = original_heading_start
            end_offset = original_bullet_end
        else:
            excerpt = text[heading_start:bullet_end]
            start_offset = heading_start
            end_offset = bullet_end
        if excerpt.strip():
            groups.append((heading, bullet_items[:4], start_offset, end_offset, excerpt))
        index = cursor_index
    return groups


def _readme_ignore_heading_marker_matches(marker: str, heading_lower: str) -> bool:
    if marker == "support":
        return re.search(r"\bsupport\b", heading_lower) is not None
    return marker in heading_lower


def _readme_heading_text(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    if _README_BULLET_RE.match(stripped):
        return None
    match = _README_HEADING_RE.match(line)
    if match:
        heading = match.group(1).strip()
    elif stripped.endswith(":"):
        heading = stripped[:-1].strip()
    else:
        heading = stripped
    heading = _clean_markdown_text(heading)
    if not heading:
        return None
    if len(heading.split()) > 10 and not stripped.endswith(":"):
        return None
    if len(heading.split()) > 24:
        return None
    if heading.endswith("."):
        return None
    return heading


def _readme_bullet_text(line: str) -> str | None:
    match = _README_BULLET_RE.match(line)
    if match is None:
        return None
    return match.group(1).strip()


def _readme_dash_item_text(line: str) -> str | None:
    match = _README_DASH_ITEM_RE.match(line)
    if match is None:
        return None
    left = _clean_markdown_text(match.group(1))
    right = _clean_markdown_text(match.group(2))
    if right and len(right.split()) <= 2:
        return f"{left} {right}".strip()
    return left


def _clean_markdown_text(value: str) -> str:
    cleaned = re.sub(r"`([^`]+)`", r"\1", value)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"[*_#>]", " ", cleaned)
    return " ".join(cleaned.split()).strip("-: ")


def _repository_subject_name(*, source_chunk: SourceChunk, query: str) -> str:
    intent = classify_query_intent(query)
    for subject in intent.subject_terms:
        lowered = subject.strip().lower()
        if not lowered:
            continue
        if lowered == "langgraph":
            return "LangGraph"
        return lowered.title()
    title = source_chunk.source_document.title or ""
    if "/" in title:
        repo = title.split("/")[-1].strip()
        if repo:
            if repo.lower() == "langgraph":
                return "LangGraph"
            return repo.replace("-", " ").title()
    return "The project"


def _repository_readme_statement(*, subject: str, heading: str, items: list[str]) -> str:
    heading_lower = heading.lower()
    item_phrase = _english_list_phrase(items)
    if not item_phrase:
        return ""
    if any(term in heading_lower for term in ("example", "use case", "tutorial", "quickstart")):
        return f"{subject} examples include use cases such as {item_phrase}."
    if any(term in heading_lower for term in ("workflow", "lifecycle", "how it works")):
        return f"{subject} workflows include lifecycle elements such as {item_phrase}."
    if any(term in heading_lower for term in ("core", "abstraction", "concept")):
        return f"{subject} provides core abstractions such as {item_phrase}."
    return f"{subject} provides key features such as {item_phrase} for agent workflows."


def _english_list_phrase(items: list[str]) -> str:
    normalized = [item.strip() for item in items if item.strip()]
    if not normalized:
        return ""
    if len(normalized) == 1:
        return normalized[0]
    if len(normalized) == 2:
        return f"{normalized[0]} and {normalized[1]}"
    return f"{', '.join(normalized[:-1])}, and {normalized[-1]}"


def _strict_rejected_rules(
    source_chunk: SourceChunk,
    statement: str,
    query: str,
    score: ClaimCandidateScore,
) -> list[str]:
    from services.orchestrator.app.claims.drafting import CandidateTriageStatus

    rejected_rules: list[str] = []
    deployment_evidence_statement = is_deployment_evidence_statement(statement)
    if not _source_chunk_eligible_for_claims(source_chunk) and not deployment_evidence_statement:
        rejected_rules.append("chunk_ineligible")
    if score.triage_status == CandidateTriageStatus.REJECT_FATAL:
        if score.rejected_reason is not None:
            rejected_rules.append(score.rejected_reason)
        else:
            rejected_rules.append("reject_fatal")
    if (
        not is_claimable_statement(statement, query=query)
        and score.triage_status != CandidateTriageStatus.REJECT_FATAL
    ):
        rejected_rules.append("not_claimable_statement")
    return list(dict.fromkeys(rejected_rules))


def _candidate_selection_sort_key(
    candidate: DraftClaimCandidate,
) -> tuple[int, int, int, float, float, float, float, float, str, int, int]:
    return (
        candidate_category_sort_key(candidate.score.answer_role),
        candidate_category_sort_key(candidate.score.claim_category),
        _candidate_tier_sort_priority(candidate.score.candidate_tier),
        -candidate.score.source_suitability_score,
        -candidate.score.source_quality_score,
        -candidate.score.query_answer_score,
        -candidate.score.claim_quality_score,
        -candidate.score.final_score,
        str(candidate.source_chunk.source_document_id),
        candidate.source_chunk.chunk_no,
        candidate.supporting_span.start_offset,
    )


def _candidate_tier_sort_priority(tier: str) -> int:
    return {
        "main_candidate": 0,
        "supporting_candidate": 1,
        "recall_candidate": 2,
        "rejected": 3,
    }.get(tier, 4)


def _deployment_slot_order_for_query(query: str) -> dict[str, int]:
    return {
        slot.slot_id: index
        for index, slot in enumerate(answer_slots_for_query(query))
        if slot.slot_id.startswith("deployment_")
    }


def _technical_slot_order_for_query(query: str) -> dict[str, int]:
    slots = {
        slot.slot_id: index
        for index, slot in enumerate(answer_slots_for_query(query))
        if slot.slot_id
        in {
            "definition",
            "motivation_problem",
            "core_abstractions",
            "architecture",
            "execution_model",
            "workflow_lifecycle",
            "key_features",
            "examples_use_cases",
            "limitations",
            "comparison_positioning",
            "official_sources",
        }
    }
    return slots if "core_abstractions" in slots and "execution_model" in slots else {}


def _technical_source_role_order_for_query(query: str) -> tuple[str, ...]:
    if not _technical_slot_order_for_query(query):
        return ()
    return ("official_docs", "official_reference", "official_repository")


def _deployment_claim_limit_for_query(query: str) -> int:
    deployment_slot_count = sum(
        1 for slot in answer_slots_for_query(query) if slot.slot_id.startswith("deployment_")
    )
    marker_group_count = len(_deployment_required_marker_groups_for_query(query))
    return max(deployment_slot_count + 8, marker_group_count + 4)


def _query_needs_source_balanced_candidates(query: str) -> bool:
    lower = query.lower()
    if any(term in lower for term in ("compare", "comparison", " versus ", " vs ")):
        return True
    if "how does" in lower or "how do" in lower:
        return True
    return False


def _llm_claim_review_rejected(claim: Claim) -> bool:
    notes = claim.notes_json or {}
    review = notes.get("llm_claim_review")
    if not isinstance(review, dict):
        return False
    decision = review.get("decision")
    confidence = review.get("confidence")
    confidence_value = float(confidence) if isinstance(confidence, int | float) else 0.0
    return decision in {"reject", "duplicate", "vague", "split_needed"} and confidence_value >= 0.65


def _deployment_required_marker_groups_for_query(query: str) -> tuple[tuple[str, ...], ...]:
    if classify_query_intent(query).intent_name != "deployment":
        return ()
    return (
        ("docker or podman", "docker/podman"),
        ("sudo usermod -ag docker", "sudo usermod -aG docker"),
        ("docker compose pull",),
        ("settings.yml",),
        (".env.example", ".env"),
        ("searxng_*", "searxng_"),
        ("reverse proxy",),
        ("limiter", "bot protection"),
        ("certificates", "update-ca-certificates"),
        ("docker run --name searxng",),
        ("docker container logs -f searxng",),
        ("docker container exec -it --user root searxng /bin/sh -l",),
    )


def _candidate_matches_deployment_marker_group(
    candidate: DraftClaimCandidate,
    marker_group: tuple[str, ...],
) -> bool:
    searchable = _deployment_marker_search_text(candidate)
    return any(marker.lower() in searchable for marker in marker_group)


def _deployment_marker_candidate_sort_key(
    candidate: DraftClaimCandidate,
    *,
    marker_group: tuple[str, ...],
    candidate_position: int,
) -> tuple[int, int, float, float, int]:
    exact_excerpt_match = any(
        marker.lower() in candidate.supporting_span.excerpt.lower() for marker in marker_group
    )
    return (
        0 if exact_excerpt_match else 1,
        0 if candidate.evidence_kind == "deployment_code_or_config" else 1,
        -candidate.score.source_quality_score,
        -candidate.score.final_score,
        candidate_position,
    )


def _deployment_marker_search_text(candidate: DraftClaimCandidate) -> str:
    return f"{candidate.statement}\n{candidate.supporting_span.excerpt}".lower()


def _deployment_slot_candidate_sort_key(
    candidate: DraftClaimCandidate,
    *,
    remaining_slots: set[str],
    slot_order: dict[str, int],
    candidate_position: int,
) -> tuple[int, int, int]:
    candidate_slots = [slot_id for slot_id in candidate.evidence_slot_ids if slot_id in slot_order]
    new_slots = [slot_id for slot_id in candidate_slots if slot_id in remaining_slots]
    if not new_slots:
        first_slot_order = len(slot_order)
    else:
        first_slot_order = min(slot_order.get(slot_id, len(slot_order)) for slot_id in new_slots)
    return (-len(set(new_slots)), first_slot_order, candidate_position)


def _technical_slot_candidate_sort_key(
    candidate: DraftClaimCandidate,
    *,
    remaining_slots: set[str],
    slot_order: dict[str, int],
    candidate_position: int,
) -> tuple[int, int, float, float, int]:
    candidate_slots = [slot_id for slot_id in candidate.evidence_slot_ids if slot_id in slot_order]
    new_slots = [slot_id for slot_id in candidate_slots if slot_id in remaining_slots]
    if not new_slots:
        first_slot_order = len(slot_order)
    else:
        first_slot_order = min(slot_order.get(slot_id, len(slot_order)) for slot_id in new_slots)
    return (
        -len(set(new_slots)),
        first_slot_order,
        -candidate.score.query_answer_score,
        -candidate.score.final_score,
        candidate_position,
    )


def _technical_source_role_candidate_sort_key(
    candidate: DraftClaimCandidate,
    *,
    candidate_position: int,
) -> tuple[int, float, float, int]:
    return (
        0 if candidate.score.answer_relevant else 1,
        -candidate.score.query_answer_score,
        -candidate.score.final_score,
        candidate_position,
    )


def _repository_candidate_allowed_for_slot_backfill(candidate: DraftClaimCandidate) -> bool:
    if candidate.score.rejected_reason and not (
        candidate.normalized_from_readme
        and candidate.score.rejected_reason == "reference_or_citation"
    ):
        return False
    if candidate.normalized_from_readme and not candidate.rejected_rules:
        return candidate.score.claim_quality_score >= 0.55 and (
            candidate.score.query_relevance_score >= 0.3
            or candidate.score.query_answer_score >= 0.35
        )
    allowed_slots = {
        "examples_use_cases",
        "workflow_lifecycle",
        "core_abstractions",
        "key_features",
    }
    if not any(slot_id in allowed_slots for slot_id in candidate.evidence_slot_ids):
        return False
    return candidate.score.query_relevance_score >= 0.45 and (
        candidate.score.query_answer_score >= 0.5 or candidate.score.claim_quality_score >= 0.7
    )


def _candidate_source_role(candidate: DraftClaimCandidate, *, query: str) -> str:
    classification = classify_source_intent(
        canonical_url=candidate.source_chunk.source_document.canonical_url,
        domain=candidate.source_chunk.source_document.domain,
        title=candidate.source_chunk.source_document.title,
        query=query,
    )
    return classification.source_role


def _fallback_candidate_allowed(
    candidate: DraftClaimCandidate,
    *,
    query: str | None = None,
) -> bool:
    from services.orchestrator.app.claims.drafting import CandidateTriageStatus

    statement = " ".join(candidate.statement.split())
    has_cjk = any("\u4e00" <= char <= "\u9fff" for char in statement)
    if not has_cjk and len(statement) < 40:
        return False
    if len(statement) > 300:
        return False
    if _source_chunk_hard_excluded_for_claims(candidate.source_chunk):
        return False
    if candidate.score.triage_status == CandidateTriageStatus.REJECT_FATAL:
        return False
    if candidate.score.claim_category in {
        "navigation",
        "setup",
        "community",
        "slogan",
        "reference",
    }:
        return False
    if not is_answer_relevant_score(candidate.score, query=query):
        return False
    if (
        candidate.score.query_answer_score < MIN_DRAFT_QUERY_ANSWER_SCORE
        and candidate.score.query_relevance_score < 0.45
    ):
        return False
    return True


def _accepted_candidates_by_category(candidates: list[DraftClaimCandidate]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        category = candidate.score.claim_category
        counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))


def _category_coverage_missing(query: str, candidates: list[DraftClaimCandidate]) -> list[str]:
    coverage = {candidate.score.claim_category for candidate in candidates}
    intent = classify_query_intent(query)
    expected = list(intent.expected_claim_types)
    if intent.intent_name == "definition_mechanism":
        expected = ["definition", "mechanism"]
    return [category for category in dict.fromkeys(expected) if category not in coverage]


def _semantic_duplicate_key(candidate: DraftClaimCandidate) -> str:
    statement = candidate.statement.lower()
    category = candidate.score.claim_category
    if (
        category == "feature"
        and "search engine" in statement
        and ("support" in statement or "supported" in statement)
    ):
        return "feature:search_engines_supported"
    if category == "definition" and "metasearch engine" in statement:
        return "definition:metasearch_engine"
    if (
        category == "mechanism"
        and "search engine" in statement
        and ("aggregat" in statement or "send" in statement or "return" in statement)
    ):
        return "mechanism:upstream_search_engines"
    if category == "privacy" and (
        "private data" in statement
        or "tracking" in statement
        or "profile" in statement
        or "stores little" in statement
    ):
        return "privacy:data_minimization"
    return f"{category}:{normalize_claim_identity(candidate.statement)}"


def _first_rejection_reason(
    rejected_rules: list[str],
    score: ClaimCandidateScore,
) -> str | None:
    from services.orchestrator.app.claims.drafting import CandidateTriageStatus

    if score.triage_status == CandidateTriageStatus.REJECT_FATAL:
        return score.rejected_reason
    return rejected_rules[0] if rejected_rules else None


def _limitations_slot_draft_diagnostics(
    candidates: list[DraftClaimCandidate],
    *,
    query: str,
) -> dict[str, Any]:
    lim_candidates = [
        c
        for c in candidates
        if "limitations" in (c.evidence_slot_ids or ())
        or "limitations" in (c.candidate_target_slot_ids or ())
    ]
    role_counts: Counter[str] = Counter()
    rej_counts: Counter[str] = Counter()
    tier_main = tier_supp = tier_recall = tier_rej = 0
    target_slot_hits = 0
    for c in lim_candidates:
        if "limitations" in (c.candidate_target_slot_ids or ()):
            target_slot_hits += 1
        src = classify_source_intent(
            canonical_url=c.source_chunk.source_document.canonical_url,
            domain=c.source_chunk.source_document.domain,
            title=c.source_chunk.source_document.title,
            query=query,
        )
        role_counts[src.source_role or "unknown"] += 1
        tier = c.score.candidate_tier
        if tier == "main_candidate":
            tier_main += 1
        elif tier == "supporting_candidate":
            tier_supp += 1
        elif tier == "recall_candidate":
            tier_recall += 1
        else:
            tier_rej += 1
        for rule in c.rejected_rules:
            rej_counts[rule] += 1
        if not c.rejected_rules and c.score.rejected_reason:
            rej_counts[str(c.score.rejected_reason)] += 1
    return {
        "limitations_candidate_count": len(lim_candidates),
        "limitations_main_candidate_count": tier_main,
        "limitations_supporting_candidate_count": tier_supp,
        "limitations_recall_candidate_count": tier_recall,
        "limitations_rejected_candidate_count": tier_rej,
        "limitations_rejection_reason_distribution": dict(sorted(rej_counts.items())),
        "limitations_candidate_source_role_distribution": dict(sorted(role_counts.items())),
        "limitations_candidate_target_slot_count": target_slot_hits,
        "limitations_supported_claim_count": 0,
    }


def _build_claim_drafting_diagnostics(
    *,
    chunks_seen: list[tuple[SourceChunk, float | None]],
    candidates: list[DraftClaimCandidate],
    query: str,
) -> dict[str, Any]:
    rejected_candidates = [candidate for candidate in candidates if candidate.rejected_rules]
    normalized_candidates = [
        candidate for candidate in candidates if candidate.normalized_from_readme
    ]
    distribution: dict[str, int] = {}
    for candidate in rejected_candidates:
        for rule in candidate.rejected_rules:
            distribution[rule] = distribution.get(rule, 0) + 1
    normalized_rejection_distribution: dict[str, int] = {}
    for candidate in normalized_candidates:
        for rule in candidate.rejected_rules:
            normalized_rejection_distribution[rule] = (
                normalized_rejection_distribution.get(rule, 0) + 1
            )
    official_repository_chunks_seen = 0
    github_readme_cleaner_applied_count = 0
    github_readme_cleaner_removed_line_count = 0
    github_readme_cleaner_kept_line_count = 0
    for source_chunk, _ in chunks_seen:
        source_classification = classify_source_intent(
            canonical_url=source_chunk.source_document.canonical_url,
            domain=source_chunk.source_document.domain,
            title=source_chunk.source_document.title,
            query=query,
        )
        if source_classification.source_role == "official_repository":
            official_repository_chunks_seen += 1
        if _should_clean_github_readme_for_claims(
            query=query,
            source_chunk=source_chunk,
            source_role=source_classification.source_role,
        ):
            clean_result = _github_readme_clean_result_for_claims(
                query=query,
                source_chunk=source_chunk,
                source_role=source_classification.source_role,
            )
            if clean_result.applied:
                github_readme_cleaner_applied_count += 1
                github_readme_cleaner_removed_line_count += clean_result.removed_line_count
                github_readme_cleaner_kept_line_count += clean_result.kept_line_count
    tier_counts: dict[str, int] = {
        "main_candidate": 0,
        "supporting_candidate": 0,
        "recall_candidate": 0,
        "rejected": 0,
    }
    soft_flag_counts: dict[str, int] = {}
    candidate_tiers_by_slot: dict[str, dict[str, int]] = {}
    reviewed_candidates_by_slot: dict[str, int] = {}
    for candidate in candidates:
        tier = candidate.score.candidate_tier
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        for flag in candidate.score.analysis_flags:
            soft_flag_counts[flag] = soft_flag_counts.get(flag, 0) + 1
        slot_ids = (
            candidate.evidence_slot_ids
            or slot_ids_for_candidate_category(candidate.score.claim_category, query=query)
            or ("unmapped",)
        )
        for slot_id in slot_ids:
            slot_counts = candidate_tiers_by_slot.setdefault(
                slot_id,
                {
                    "main_candidate": 0,
                    "supporting_candidate": 0,
                    "recall_candidate": 0,
                    "rejected": 0,
                },
            )
            slot_counts[tier] = slot_counts.get(tier, 0) + 1
            if not candidate.rejected_rules:
                reviewed_candidates_by_slot[slot_id] = (
                    reviewed_candidates_by_slot.get(slot_id, 0) + 1
                )
    answer_relevant_candidates = [
        candidate
        for candidate in candidates
        if is_answer_relevant_score(candidate.score, query=query)
    ]

    return {
        "total_chunks_seen": len(chunks_seen),
        "eligible_chunks_seen": sum(
            1 for source_chunk, _ in chunks_seen if _source_chunk_eligible_for_claims(source_chunk)
        ),
        "candidate_sentences_count": len(candidates),
        "answer_relevant_candidate_count": len(answer_relevant_candidates),
        "hard_rejected_garbage": sum(
            1 for candidate in candidates if candidate.score.triage_status.value == "reject_fatal"
        ),
        "soft_flag_short_text": soft_flag_counts.get("short_text", 0)
        + soft_flag_counts.get("very_short", 0),
        "soft_flag_missing_punctuation": soft_flag_counts.get("missing_punctuation", 0),
        "soft_flag_heading_like": soft_flag_counts.get("heading_like", 0),
        "main_candidate_count": tier_counts.get("main_candidate", 0),
        "supporting_candidate_count": tier_counts.get("supporting_candidate", 0),
        "recall_candidate_count": tier_counts.get("recall_candidate", 0),
        "score_rejected_count": tier_counts.get("rejected", 0),
        "candidate_tiers_by_slot": candidate_tiers_by_slot,
        "llm_reviewed_candidates_by_slot": reviewed_candidates_by_slot,
        "answer_candidate_count_by_category": _accepted_candidates_by_category(
            answer_relevant_candidates
        ),
        "evidence_candidates": [
            _evidence_candidate_payload(candidate, query=query) for candidate in candidates
        ],
        "rejected_candidates_count": len(rejected_candidates),
        "top_rejected_candidates": [
            _candidate_diagnostic(candidate, query=query)
            for candidate in sorted(
                rejected_candidates,
                key=lambda item: (-item.score.final_score, item.source_chunk.chunk_no),
            )[:10]
        ],
        "rejection_reason_distribution": dict(sorted(distribution.items())),
        "chunks": [_chunk_diagnostic(source_chunk) for source_chunk, _ in chunks_seen],
        "repository_normalized_candidate_count": len(normalized_candidates),
        "repository_normalized_claim_count": 0,
        "repository_normalized_supported_claim_count": 0,
        "repository_normalized_rejection_reason_distribution": dict(
            sorted(normalized_rejection_distribution.items())
        ),
        "official_repository_chunks_seen": official_repository_chunks_seen,
        "official_repository_chunks_cleaned": github_readme_cleaner_applied_count,
        "github_readme_cleaner_applied_count": github_readme_cleaner_applied_count,
        "github_readme_cleaner_removed_line_count": github_readme_cleaner_removed_line_count,
        "github_readme_cleaner_kept_line_count": github_readme_cleaner_kept_line_count,
        "github_readme_cleaner_candidate_count": sum(
            1 for candidate in candidates if candidate.cleaned_github_readme
        ),
        "official_repository_chunks_with_normalized_candidates": len(
            {candidate.source_chunk.id for candidate in normalized_candidates}
        ),
        "fallback_attempted": False,
        "fallback_candidates_count": 0,
        **_limitations_slot_draft_diagnostics(candidates, query=query),
    }


def _candidate_diagnostic(
    candidate: DraftClaimCandidate,
    *,
    query: str | None = None,
) -> dict[str, Any]:
    rejected_reason = _first_rejection_reason(list(candidate.rejected_rules), candidate.score)
    return {
        **_evidence_candidate_payload(candidate, query=query),
        "candidate_text": candidate.statement,
        "source_chunk_id": str(candidate.source_chunk.id),
        "claim_category": candidate.score.claim_category,
        "answer_role": candidate.score.answer_role,
        "answer_relevant": candidate.score.answer_relevant,
        "claim_quality_score": candidate.score.claim_quality_score,
        "query_answer_score": candidate.score.query_answer_score,
        "query_relevance_score": candidate.score.query_relevance_score,
        "source_suitability_score": candidate.score.source_suitability_score,
        "claim_selection_score": candidate.score.final_score,
        "candidate_tier": candidate.score.candidate_tier,
        "analysis_flags": list(candidate.score.analysis_flags),
        "rejected_reason": rejected_reason,
        "rejected_rules": list(candidate.rejected_rules),
    }


def _chunk_diagnostic(source_chunk: SourceChunk) -> dict[str, Any]:
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


def _evidence_candidate_payload(
    candidate: DraftClaimCandidate,
    *,
    query: str | None,
) -> dict[str, Any]:
    source_chunk = candidate.source_chunk
    source_document = source_chunk.source_document
    metadata = source_chunk.metadata_json or {}
    source_classification = classify_source_intent(
        canonical_url=source_document.canonical_url,
        domain=source_document.domain,
        title=source_document.title,
        query=query,
    )
    source_intent = source_classification.source_intent
    source_role = source_classification.source_role
    slot_ids = candidate.evidence_slot_ids
    if not slot_ids and classify_query_intent(query).intent_name != "deployment":
        slot_ids = technical_slot_ids_for_text(
            text=f"{candidate.statement}\n{candidate.supporting_span.excerpt}",
            category=candidate.score.claim_category,
            query=query,
            source_intent=source_intent,
        ) or slot_ids_for_candidate_category(candidate.score.claim_category, query=query)
    payload = EvidenceCandidate(
        evidence_candidate_id=_candidate_evidence_id(candidate),
        source_document_id=str(source_document.id),
        source_chunk_id=str(source_chunk.id),
        citation_span_id=None,
        slot_ids=slot_ids,
        source_intent=source_intent,
        excerpt=candidate.supporting_span.excerpt,
        start_offset=candidate.supporting_span.start_offset,
        end_offset=candidate.supporting_span.end_offset,
        salience_score=candidate.score.final_score,
        quality_score=candidate.score.claim_quality_score,
        extraction_strategy=(
            metadata.get("strategy") if isinstance(metadata.get("strategy"), str) else None
        ),
        rejection_reasons=tuple(candidate.rejected_rules),
        metadata={
            "claim_category": candidate.score.claim_category,
            "answer_role": candidate.score.answer_role,
            "answer_relevant": candidate.score.answer_relevant,
            "content_quality_score": candidate.score.content_quality_score,
            "query_relevance_score": candidate.score.query_relevance_score,
            "query_answer_score": candidate.score.query_answer_score,
            "source_quality_score": candidate.score.source_quality_score,
            "source_suitability_score": candidate.score.source_suitability_score,
            "claim_selection_score": candidate.score.final_score,
            "candidate_tier": candidate.score.candidate_tier,
            "analysis_flags": list(candidate.score.analysis_flags),
            "retrieval_score": candidate.retrieval_score,
            "draft_mode": candidate.draft_mode,
            "evidence_kind": candidate.evidence_kind,
            "normalized_from_readme": candidate.normalized_from_readme,
            "cleaned_github_readme": candidate.cleaned_github_readme,
            "fallback_reason": candidate.fallback_reason,
            "original_rejected_reason": candidate.original_rejected_reason,
            "source_url": source_document.canonical_url,
            "source_domain": source_document.domain,
            "source_role": source_role,
            "chunk_no": source_chunk.chunk_no,
        },
    ).to_payload()
    payload["source_role"] = source_role
    return payload


def _candidate_evidence_id(candidate: DraftClaimCandidate) -> str:
    return evidence_candidate_id(
        source_chunk_id=str(candidate.source_chunk.id),
        start_offset=candidate.supporting_span.start_offset,
        end_offset=candidate.supporting_span.end_offset,
        excerpt=candidate.supporting_span.excerpt,
    )


def _claim_lineage_notes(
    *,
    evidence_candidate: dict[str, Any],
    citation_span_id: str,
    claim_evidence_id: str,
) -> dict[str, Any]:
    candidate_with_links = {
        **evidence_candidate,
        "citation_span_id": citation_span_id,
        "claim_evidence_id": claim_evidence_id,
    }
    return {
        "citation_span_id": citation_span_id,
        "claim_evidence_id": claim_evidence_id,
        "evidence_candidate": candidate_with_links,
    }


def _chunk_content_quality_score(source_chunk: SourceChunk) -> float | None:
    metadata = source_chunk.metadata_json or {}
    quality_score = metadata.get("content_quality_score")
    if isinstance(quality_score, int | float):
        return float(quality_score)
    return None


def _chunk_information_density_score(source_chunk: SourceChunk) -> float | None:
    metadata = source_chunk.metadata_json or {}
    quality_score = metadata.get("information_density_score")
    if isinstance(quality_score, int | float):
        return float(quality_score)
    return None


def _source_quality_score(source_chunk: SourceChunk) -> float | None:
    source_score = source_chunk.source_document.final_source_score
    if isinstance(source_score, int | float):
        return float(source_score)
    return None


def _source_content_hash(source_chunk: SourceChunk) -> str | None:
    content_snapshot = source_chunk.source_document.content_snapshot
    if content_snapshot is None:
        return None
    content_hash = content_snapshot.content_hash
    return content_hash if isinstance(content_hash, str) and content_hash.strip() else None


def _source_chunk_text_hash(source_chunk: SourceChunk) -> str:
    normalized = " ".join(source_chunk.text.lower().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _paragraph_index(text: str, start_offset: int) -> int:
    if start_offset <= 0:
        return 0
    prefix = text[:start_offset]
    return len([part for part in prefix.split("\n\n")[:-1]])


def _numeric_note(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None
