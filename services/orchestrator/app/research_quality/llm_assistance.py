from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from packages.db.models import Claim, SourceChunk
from services.orchestrator.app.llm import LLMError, LLMProvider, LLMRequest
from services.orchestrator.app.planning.types import PlannedSearchQuery, ResearchPlan
from services.orchestrator.app.research_quality.answer_slots import answer_slots_for_query

MAX_RAW_PREVIEW_CHARS = 500
MIN_CLAIM_REVIEW_ACCEPT_CONFIDENCE = 0.65


@dataclass(frozen=True)
class LLMAssistanceResult:
    status: str
    stage: str
    used: bool
    fallback_reason: str | None
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class QueryRewriteResult(LLMAssistanceResult):
    search_queries: list[PlannedSearchQuery]


@dataclass(frozen=True)
class EvidenceRerankResult(LLMAssistanceResult):
    source_chunk_ids: list[UUID]


@dataclass(frozen=True)
class ClaimReviewResult(LLMAssistanceResult):
    decisions: list[dict[str, Any]]


class _RewrittenQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_text: str = Field(min_length=1, max_length=220)
    rationale: str = Field(min_length=1, max_length=400)
    expected_source_type: Literal[
        "general_web",
        "official_docs",
        "official_about",
        "official_installation_admin",
        "official_or_reference",
        "official_repository",
        "github_readme_or_repo",
        "reference",
        "package_registry",
        "standards_specification",
        "academic",
        "official_product",
    ]
    priority: int = Field(ge=1, le=99)

    @field_validator("query_text", "rationale", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class _QueryRewritePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: list[_RewrittenQuery] = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)


class _EvidenceRerankItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_chunk_id: str
    answer_slot_ids: list[str] = Field(default_factory=list)
    relevance_score: float = Field(ge=0.0, le=1.0)
    evidence_strength_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=300)


class _EvidenceRerankPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rankings: list[_EvidenceRerankItem] = Field(default_factory=list)


class _ClaimReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    decision: Literal["accept", "downrank", "reject", "duplicate", "vague", "split_needed"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    covered_slot_ids: list[str] = Field(default_factory=list)


class _ClaimReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[_ClaimReviewItem] = Field(default_factory=list)


class LLMQueryRewriterService:
    def __init__(
        self,
        *,
        enabled: bool,
        provider: LLMProvider | None,
        model: str,
        max_queries: int,
        max_output_tokens: int,
        input_max_chars: int,
    ) -> None:
        self.enabled = enabled
        self.provider = provider
        self.model = model
        self.max_queries = max(1, max_queries)
        self.max_output_tokens = max(200, max_output_tokens)
        self.input_max_chars = max(2_000, input_max_chars)

    def rewrite(
        self,
        *,
        query: str,
        plan: ResearchPlan,
        constraints: dict[str, Any],
    ) -> QueryRewriteResult:
        stage = "query_rewriter"
        if not self.enabled:
            return QueryRewriteResult("disabled", stage, False, "disabled", {}, [])
        if self.provider is None:
            return QueryRewriteResult(
                "fallback",
                stage,
                False,
                "provider_unavailable",
                {"reason": "LLM query rewriter provider not configured."},
                [],
            )

        request_payload = {
            "query": query,
            "intent": plan.intent,
            "normalized_question": plan.normalized_question,
            "answer_slots": plan.answer_slots,
            "source_preferences": plan.source_preferences,
            "existing_queries": [item.to_payload() for item in plan.search_queries],
            "constraints": _safe_constraints(constraints),
            "rules": [
                "Return query rewrites only, not source URLs and not answer facts.",
                (
                    "Prioritize official docs, reference docs, upstream GitHub, package "
                    "registries, standards/specifications, academic sources, and official "
                    "product pages."
                ),
                (
                    "Avoid social profiles, job pages, SEO mirrors, and generic low-value "
                    "blogs when better official sources are likely."
                ),
            ],
        }
        try:
            response_text = self.provider.generate(
                LLMRequest(
                    system_prompt=(
                        "You rewrite search queries for an evidence-first research pipeline. "
                        "Return JSON only with keys queries and notes. Do not invent source URLs."
                    ),
                    user_prompt=_bounded_json(request_payload, self.input_max_chars),
                    model=self.model,
                    max_output_tokens=self.max_output_tokens,
                    temperature=0.0,
                    metadata={"purpose": stage, "query": query},
                )
            ).text
            parsed = _parse_json_object(response_text)
            parsed = _normalize_query_rewrite_payload(parsed)
            payload = _QueryRewritePayload.model_validate(parsed)
        except (LLMError, JSONDecodeError, ValidationError, ValueError) as error:
            return QueryRewriteResult(
                "fallback",
                stage,
                False,
                type(error).__name__,
                _failure_diagnostics(error, raw_text=locals().get("response_text")),
                [],
            )

        planned_queries = [
            PlannedSearchQuery(
                query_text=item.query_text,
                rationale=item.rationale,
                expected_source_type=item.expected_source_type,
                priority=item.priority,
                query_source="llm_query_rewriter",
                metadata={"llm_stage": stage},
            )
            for item in payload.queries[: self.max_queries]
        ]
        return QueryRewriteResult(
            "used",
            stage,
            True,
            None,
            {
                "query_count": len(planned_queries),
                "added_query_count": len(planned_queries),
                "notes": payload.notes[:5],
                "input_hash": _sha256(_bounded_json(request_payload, self.input_max_chars)),
            },
            planned_queries,
        )


class LLMEvidenceRerankerService:
    def __init__(
        self,
        *,
        enabled: bool,
        provider: LLMProvider | None,
        model: str,
        max_chunks: int,
        max_output_tokens: int,
        input_max_chars: int,
    ) -> None:
        self.enabled = enabled
        self.provider = provider
        self.model = model
        self.max_chunks = max(1, max_chunks)
        self.max_output_tokens = max(200, max_output_tokens)
        self.input_max_chars = max(2_000, input_max_chars)

    def rerank(
        self,
        *,
        query: str,
        chunks: list[SourceChunk],
        answer_slots: list[dict[str, Any]],
    ) -> EvidenceRerankResult:
        stage = "evidence_reranker"
        fallback_ids = [chunk.id for chunk in chunks]
        if not self.enabled:
            return EvidenceRerankResult("disabled", stage, False, "disabled", {}, fallback_ids)
        if self.provider is None:
            return EvidenceRerankResult(
                "fallback",
                stage,
                False,
                "provider_unavailable",
                {"reason": "LLM evidence reranker provider not configured."},
                fallback_ids,
            )

        bounded_chunks = chunks[: self.max_chunks]
        allowed_ids = {str(chunk.id): chunk.id for chunk in bounded_chunks}
        request_payload = {
            "query": query,
            "answer_slots": answer_slots,
            "chunks": [
                {
                    "source_chunk_id": str(chunk.id),
                    "domain": chunk.source_document.domain,
                    "canonical_url": chunk.source_document.canonical_url,
                    "chunk_no": chunk.chunk_no,
                    "text_preview": chunk.text[:1200],
                    "metadata": _compact_metadata(chunk.metadata_json or {}),
                }
                for chunk in bounded_chunks
            ],
            "rules": [
                "Rank only the supplied source_chunk_id values.",
                "Do not create facts.",
                "Prefer chunks that directly support answer slots with concrete evidence.",
            ],
        }
        try:
            response_text = self.provider.generate(
                LLMRequest(
                    system_prompt=(
                        "You rank already-fetched evidence chunks. Return JSON only with "
                        "a rankings array. Do not add new chunks or facts."
                    ),
                    user_prompt=_bounded_json(request_payload, self.input_max_chars),
                    model=self.model,
                    max_output_tokens=self.max_output_tokens,
                    temperature=0.0,
                    metadata={"purpose": stage, "query": query},
                )
            ).text
            parsed = _parse_json_object(response_text)
            parsed = _normalize_evidence_rerank_payload(parsed)
            payload = _EvidenceRerankPayload.model_validate(parsed)
        except (LLMError, JSONDecodeError, ValidationError, ValueError) as error:
            return EvidenceRerankResult(
                "fallback",
                stage,
                False,
                type(error).__name__,
                _failure_diagnostics(error, raw_text=locals().get("response_text")),
                fallback_ids,
            )

        valid_slot_ids = {
            str(slot.get("slot_id"))
            for slot in answer_slots
            if isinstance(slot, dict) and isinstance(slot.get("slot_id"), str)
        }
        ranked_ids: list[UUID] = []
        decisions: list[dict[str, Any]] = []
        invalid_chunk_ids: list[str] = []
        for item in sorted(
            payload.rankings,
            key=lambda row: (
                -(row.relevance_score + row.evidence_strength_score),
                row.source_chunk_id,
            ),
        ):
            chunk_id = allowed_ids.get(item.source_chunk_id)
            if chunk_id is None:
                invalid_chunk_ids.append(item.source_chunk_id)
                continue
            if chunk_id in ranked_ids:
                continue
            ranked_ids.append(chunk_id)
            decision = item.model_dump()
            if valid_slot_ids:
                decision["answer_slot_ids"] = [
                    slot_id
                    for slot_id in decision.get("answer_slot_ids", [])
                    if slot_id in valid_slot_ids
                ]
            decisions.append(decision)
        for chunk in bounded_chunks:
            if chunk.id not in ranked_ids:
                ranked_ids.append(chunk.id)
        if not ranked_ids:
            return EvidenceRerankResult(
                "fallback",
                stage,
                False,
                "no_valid_rankings",
                {"invalid_output": "LLM returned no valid input chunk ids."},
                fallback_ids,
            )
        quality = _evidence_rerank_quality(decisions, invalid_chunk_ids=invalid_chunk_ids)
        if quality["low_quality"]:
            return EvidenceRerankResult(
                "low_quality_rerank",
                stage,
                False,
                str(quality["quality_fallback_reason"]),
                {
                    "ranked_chunk_count": len(fallback_ids),
                    "reranked_chunk_count": len(decisions),
                    "input_chunk_count": len(bounded_chunks),
                    "candidate_chunk_count": len(bounded_chunks),
                    "decisions": decisions[:20],
                    **quality,
                },
                fallback_ids,
            )
        return EvidenceRerankResult(
            "used",
            stage,
            True,
            None,
            {
                "ranked_chunk_count": len(ranked_ids),
                "reranked_chunk_count": len(ranked_ids),
                "input_chunk_count": len(bounded_chunks),
                "candidate_chunk_count": len(bounded_chunks),
                "decisions": decisions[:20],
                **quality,
            },
            ranked_ids,
        )


class LLMClaimReviewService:
    def __init__(
        self,
        *,
        enabled: bool,
        provider: LLMProvider | None,
        model: str,
        max_claims: int,
        max_output_tokens: int,
        input_max_chars: int,
    ) -> None:
        self.enabled = enabled
        self.provider = provider
        self.model = model
        self.max_claims = max(1, max_claims)
        self.max_output_tokens = max(200, max_output_tokens)
        self.input_max_chars = max(2_000, input_max_chars)

    def review(self, *, query: str, claims: list[Claim]) -> ClaimReviewResult:
        stage = "claim_reviewer"
        if not self.enabled:
            return ClaimReviewResult("disabled", stage, False, "disabled", {}, [])
        if self.provider is None:
            return ClaimReviewResult(
                "fallback",
                stage,
                False,
                "provider_unavailable",
                {"reason": "LLM claim reviewer provider not configured."},
                [],
            )
        bounded_claims = claims[: self.max_claims]
        allowed_ids = {str(claim.id) for claim in bounded_claims}
        request_payload = {
            "query": query,
            "answer_slots": [slot.to_payload() for slot in answer_slots_for_query(query)],
            "claims": [
                {
                    "claim_id": str(claim.id),
                    "statement": claim.statement,
                    "verification_status": claim.verification_status,
                    "notes": _compact_metadata(claim.notes_json or {}),
                }
                for claim in bounded_claims
            ],
            "rules": [
                "Review only supplied claim_id values.",
                "Do not create new claims.",
                "Evaluate main-entity relevance, user-question relevance, answer-slot coverage, "
                "adjacent-entity or ecosystem drift, usefulness for the main report, evidence "
                "support, and whether the claim is too generic or vague.",
                "Reject or downrank vague, duplicate, unsupported-looking, adjacent, ecosystem, "
                "off-topic, or off-slot claims.",
                "An accept decision must include concrete reasons and covered_slot_ids.",
                "Do not mark unsupported claims as supported.",
            ],
        }
        try:
            response_text = self.provider.generate(
                LLMRequest(
                    system_prompt=(
                        "You review candidate claims for an evidence-first research ledger. "
                        "Return JSON only with a decisions array. Do not create claims."
                    ),
                    user_prompt=_bounded_json(request_payload, self.input_max_chars),
                    model=self.model,
                    max_output_tokens=self.max_output_tokens,
                    temperature=0.0,
                    metadata={"purpose": stage, "query": query},
                )
            ).text
            parsed = _parse_json_object(response_text)
            parsed = _normalize_claim_review_payload(parsed)
            payload = _ClaimReviewPayload.model_validate(parsed)
        except (LLMError, JSONDecodeError, ValidationError, ValueError) as error:
            return ClaimReviewResult(
                "fallback",
                stage,
                False,
                type(error).__name__,
                _failure_diagnostics(error, raw_text=locals().get("response_text")),
                [],
            )

        valid_slot_ids = {slot.slot_id for slot in answer_slots_for_query(query)}
        decisions = [
            _normalize_claim_review_decision_quality(
                item.model_dump(),
                valid_slot_ids=valid_slot_ids,
            )
            for item in payload.decisions
            if item.claim_id in allowed_ids
        ]
        low_quality_decision_count = sum(
            1 for decision in decisions if decision.get("quality_flags")
        )
        if decisions and low_quality_decision_count == len(decisions):
            return ClaimReviewResult(
                "low_quality_review",
                stage,
                False,
                "all_review_decisions_failed_quality_validation",
                {
                    "reviewed_claim_count": len(decisions),
                    "input_claim_count": len(bounded_claims),
                    "decision_counts": _decision_counts(decisions),
                    "low_quality_decision_count": low_quality_decision_count,
                },
                decisions,
            )
        return ClaimReviewResult(
            "used",
            stage,
            True,
            None,
            {
                "reviewed_claim_count": len(decisions),
                "input_claim_count": len(bounded_claims),
                "decision_counts": _decision_counts(decisions),
                "low_quality_decision_count": low_quality_decision_count,
            },
            decisions,
        )


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        payload = json.loads(stripped)
    except JSONDecodeError:
        payload = json.loads(_extract_json_object(stripped))
    if not isinstance(payload, dict):
        raise ValueError("LLM assistance output must be a JSON object")
    return payload


def _extract_json_object(text: str) -> str:
    fenced_start = text.find("```")
    if fenced_start >= 0:
        fence_body_start = text.find("\n", fenced_start)
        fence_search_start = fence_body_start + 1 if fence_body_start >= 0 else fenced_start + 3
        fenced_end = text.find("```", fence_search_start)
        if fence_body_start >= 0 and fenced_end > fence_body_start:
            candidate = text[fence_body_start + 1 : fenced_end].strip()
            if candidate:
                return candidate
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    raise JSONDecodeError("LLM assistance response did not contain a JSON object", text, 0)


def _normalize_query_rewrite_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_queries = payload.get("queries")
    if raw_queries is None:
        raw_queries = payload.get("search_queries")
    if isinstance(raw_queries, dict):
        raw_queries = list(raw_queries.values())
    normalized_queries: list[dict[str, Any]] = []
    if isinstance(raw_queries, list):
        for index, item in enumerate(raw_queries, start=1):
            if isinstance(item, str):
                item = {"query_text": item}
            if not isinstance(item, dict):
                continue
            query_text = _first_string(item, "query_text", "query", "search_query", "text")
            if not query_text:
                continue
            rationale = _first_string(item, "rationale", "reason", "why", "notes") or (
                "LLM query rewrite suggestion."
            )
            source_type = _normalize_source_type(
                _first_string(item, "expected_source_type", "source_type", "source", "type")
            )
            priority = _coerce_int(item.get("priority"), default=index, minimum=1, maximum=99)
            normalized_queries.append(
                {
                    "query_text": query_text[:220],
                    "rationale": rationale[:400],
                    "expected_source_type": source_type,
                    "priority": priority,
                }
            )
    notes = payload.get("notes")
    if isinstance(notes, str):
        normalized_notes = [notes]
    elif isinstance(notes, list):
        normalized_notes = [item for item in notes if isinstance(item, str)]
    else:
        normalized_notes = []
    return {"queries": normalized_queries, "notes": normalized_notes}


def _normalize_evidence_rerank_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_rankings = payload.get("rankings")
    if raw_rankings is None:
        raw_rankings = (
            payload.get("ranked_chunks") or payload.get("chunks") or payload.get("results")
        )
    if raw_rankings is None and isinstance(payload.get("source_chunk_ids"), list):
        raw_rankings = [{"source_chunk_id": item} for item in payload["source_chunk_ids"]]
    normalized_rankings: list[dict[str, Any]] = []
    if isinstance(raw_rankings, list):
        for item in raw_rankings:
            if isinstance(item, str):
                item = {"source_chunk_id": item}
            if not isinstance(item, dict):
                continue
            source_chunk_id = _first_string(item, "source_chunk_id", "chunk_id", "id")
            if not source_chunk_id:
                continue
            score = _coerce_score(item.get("score"), default=None)
            relevance = _coerce_score(
                item.get("relevance_score", item.get("relevance")), default=score or 0.5
            )
            strength = _coerce_score(
                item.get(
                    "evidence_strength_score",
                    item.get("evidence_strength", item.get("support_score")),
                ),
                default=score or relevance,
            )
            slot_ids = item.get("answer_slot_ids", item.get("slot_ids", item.get("slots", [])))
            normalized_rankings.append(
                {
                    "source_chunk_id": source_chunk_id,
                    "answer_slot_ids": _string_list(slot_ids),
                    "relevance_score": relevance,
                    "evidence_strength_score": strength,
                    "rationale": (
                        _first_string(item, "rationale", "reason", "why", "explanation") or ""
                    )[:300],
                }
            )
    return {"rankings": normalized_rankings}


def _normalize_claim_review_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_decisions = payload.get("decisions")
    if raw_decisions is None:
        raw_decisions = payload.get("reviews") or payload.get("claims") or payload.get("results")
    normalized_decisions: list[dict[str, Any]] = []
    if isinstance(raw_decisions, list):
        for item in raw_decisions:
            if not isinstance(item, dict):
                continue
            claim_id = _first_string(item, "claim_id", "id")
            if not claim_id:
                continue
            decision = _normalize_claim_decision(
                _first_string(item, "decision", "action", "recommendation", "status")
            )
            reasons = item.get("reasons", item.get("reason", item.get("rationale", [])))
            slot_ids = item.get(
                "covered_slot_ids",
                item.get("slot_ids", item.get("answer_slot_ids", item.get("slots", []))),
            )
            normalized_decisions.append(
                {
                    "claim_id": claim_id,
                    "decision": decision,
                    "confidence": _coerce_score(item.get("confidence"), default=0.5),
                    "reasons": _string_list(reasons),
                    "covered_slot_ids": _string_list(slot_ids),
                }
            )
    return {"decisions": normalized_decisions}


def _evidence_rerank_quality(
    decisions: list[dict[str, Any]],
    *,
    invalid_chunk_ids: list[str],
) -> dict[str, Any]:
    scores: list[float] = []
    for decision in decisions:
        for key in ("relevance_score", "evidence_strength_score"):
            value = decision.get(key)
            if isinstance(value, int | float):
                scores.append(float(value))
    has_answer_slot_ids = any(
        bool(_string_list(decision.get("answer_slot_ids"))) for decision in decisions
    )
    has_rationales = any(
        isinstance(decision.get("rationale"), str) and bool(decision["rationale"].strip())
        for decision in decisions
    )
    score_distribution = {
        "count": len(scores),
        "min": round(min(scores), 4) if scores else None,
        "max": round(max(scores), 4) if scores else None,
        "avg": round(sum(scores) / len(scores), 4) if scores else None,
    }
    flat_scores = bool(scores) and (max(scores) - min(scores)) < 0.05
    low_quality_reasons: list[str] = []
    if decisions and not has_answer_slot_ids:
        low_quality_reasons.append("missing_answer_slot_ids")
    if decisions and not has_rationales:
        low_quality_reasons.append("missing_rationales")
    if flat_scores and (not has_answer_slot_ids or not has_rationales):
        low_quality_reasons.append("flat_score_only_output")
    if invalid_chunk_ids:
        low_quality_reasons.append("unknown_chunk_ids_ignored")
    low_quality = any(
        reason
        in {
            "missing_answer_slot_ids",
            "missing_rationales",
            "flat_score_only_output",
        }
        for reason in low_quality_reasons
    )
    return {
        "output_quality": "low_quality" if low_quality else "complete",
        "low_quality": low_quality,
        "low_quality_reasons": low_quality_reasons,
        "quality_fallback_reason": (
            "_and_".join(low_quality_reasons) if low_quality_reasons else None
        ),
        "produced_answer_slot_ids": has_answer_slot_ids,
        "produced_rationales": has_rationales,
        "score_distribution": score_distribution,
        "flat_score_distribution": flat_scores,
        "invalid_chunk_id_count": len(invalid_chunk_ids),
    }


def _normalize_claim_review_decision_quality(
    decision: dict[str, Any],
    *,
    valid_slot_ids: set[str],
) -> dict[str, Any]:
    normalized = dict(decision)
    reasons = _string_list(normalized.get("reasons"))
    covered_slot_ids = [
        slot_id
        for slot_id in _string_list(normalized.get("covered_slot_ids"))
        if not valid_slot_ids or slot_id in valid_slot_ids
    ]
    confidence = _coerce_score(normalized.get("confidence"), default=0.0)
    original_decision = str(normalized.get("decision") or "")
    quality_flags: list[str] = []
    if not reasons:
        quality_flags.append("missing_reasons")
    if original_decision in {"accept", "downrank"} and not covered_slot_ids:
        quality_flags.append("missing_slot_coverage")
    if confidence < MIN_CLAIM_REVIEW_ACCEPT_CONFIDENCE:
        quality_flags.append("low_confidence")

    if original_decision == "accept" and quality_flags:
        normalized["decision"] = "downrank"
        normalized["original_decision"] = original_decision
        reasons = [
            *reasons,
            (
                "LLM accept decision was downranked because the structured review "
                f"was incomplete or low-confidence: {', '.join(quality_flags)}."
            ),
        ]
    elif quality_flags:
        reasons = [
            *reasons,
            f"Review quality flags: {', '.join(quality_flags)}.",
        ]

    normalized["confidence"] = confidence
    normalized["reasons"] = reasons
    normalized["covered_slot_ids"] = covered_slot_ids
    normalized["quality_flags"] = quality_flags
    return normalized


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _string_list(value: object) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _coerce_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        coerced = int(value) if value is not None else default
    except (TypeError, ValueError):
        coerced = default
    return max(minimum, min(maximum, coerced))


def _coerce_score(value: object, *, default: float | None) -> float:
    try:
        score = (
            float(value) if value is not None else float(default if default is not None else 0.0)
        )
    except (TypeError, ValueError):
        score = float(default if default is not None else 0.0)
    if score > 1.0 and score <= 100.0:
        score = score / 100.0
    return round(max(0.0, min(1.0, score)), 4)


def _normalize_source_type(value: str | None) -> str:
    normalized = (value or "general_web").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "official_reference": "official_or_reference",
        "official_reference_docs": "official_or_reference",
        "reference_docs": "reference",
        "docs": "official_docs",
        "documentation": "official_docs",
        "official_documentation": "official_docs",
        "api_docs": "official_docs",
        "repo": "official_repository",
        "repository": "official_repository",
        "github": "github_readme_or_repo",
        "github_repo": "github_readme_or_repo",
        "github_repository": "github_readme_or_repo",
        "product": "official_product",
        "product_page": "official_product",
        "academic_paper": "academic",
        "standard": "standards_specification",
        "spec": "standards_specification",
    }
    normalized = aliases.get(normalized, normalized)
    allowed = {
        "general_web",
        "official_docs",
        "official_about",
        "official_installation_admin",
        "official_or_reference",
        "official_repository",
        "github_readme_or_repo",
        "reference",
        "package_registry",
        "standards_specification",
        "academic",
        "official_product",
    }
    return normalized if normalized in allowed else "general_web"


def _normalize_claim_decision(value: str | None) -> str:
    normalized = (value or "downrank").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "keep": "accept",
        "approve": "accept",
        "approved": "accept",
        "accepted": "accept",
        "good": "accept",
        "valid": "accept",
        "lower_priority": "downrank",
        "needs_evidence": "downrank",
        "unsupported": "reject",
        "off_topic": "reject",
        "off_slot": "reject",
        "remove": "reject",
        "duplicative": "duplicate",
        "too_vague": "vague",
        "needs_split": "split_needed",
        "split": "split_needed",
        "rewrite": "split_needed",
        "revise": "split_needed",
    }
    normalized = aliases.get(normalized, normalized)
    allowed = {"accept", "downrank", "reject", "duplicate", "vague", "split_needed"}
    return normalized if normalized in allowed else "downrank"


def _bounded_json(payload: dict[str, Any], max_chars: int) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _safe_constraints(constraints: dict[str, Any]) -> dict[str, Any]:
    safe_keys = {
        "domains_allow",
        "domains_deny",
        "language",
        "report_language",
        "max_urls",
        "source_engines",
    }
    return {key: value for key, value in constraints.items() if key in safe_keys}


def _compact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "claim_category",
        "answer_role",
        "answer_relevant",
        "source_quality_score",
        "content_quality_score",
        "retrieval_diagnostics",
        "source_intent",
        "slot_ids",
        "evidence_kind",
    }
    return {key: value for key, value in metadata.items() if key in allowed}


def _failure_diagnostics(error: BaseException, *, raw_text: str | None = None) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "error_type": type(error).__name__,
        "message": str(error)[:300],
    }
    if raw_text is not None:
        diagnostics["raw_output_preview"] = raw_text[:MAX_RAW_PREVIEW_CHARS]
        diagnostics["raw_output_hash"] = _sha256(raw_text)
    if isinstance(error, LLMError):
        diagnostics["llm_error"] = error.to_payload()
    if isinstance(error, ValidationError):
        diagnostics["validation_errors"] = [
            {
                "path": ".".join(str(part) for part in item.get("loc", ())),
                "message": str(item.get("msg", ""))[:200],
                "type": str(item.get("type", "")),
            }
            for item in error.errors()[:8]
        ]
    return diagnostics


def _decision_counts(decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for decision in decisions:
        label = str(decision.get("decision") or "unknown")
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def _sha256(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
