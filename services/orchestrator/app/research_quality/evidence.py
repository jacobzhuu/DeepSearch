from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from services.orchestrator.app.research_quality.answer_slots import (
    answer_slots_for_query,
    slot_ids_for_claim_category,
)

DROPPED_SOURCE_REASONS: tuple[str, ...] = (
    "not_selected_low_priority",
    "blocked_by_policy",
    "fetch_failed",
    "unsupported_content_type",
    "parse_failed",
    "low_chunk_quality",
    "no_evidence_candidates",
    "evidence_rejected",
    "duplicate_or_near_duplicate",
    "off_intent",
    "unknown",
)

CONTRIBUTION_LEVELS: tuple[str, ...] = ("high", "medium", "low", "none")

QUALITY_DIAGNOSTIC_FIELDS: tuple[str, ...] = (
    "selected_sources",
    "attempted_sources",
    "dropped_sources",
    "source_yield_summary",
    "evidence_yield_summary",
    "slot_coverage_summary",
    "verification_summary",
)

EVIDENCE_LINEAGE_FIELDS: tuple[str, ...] = (
    "slot_ids",
    "source_document_id",
    "source_chunk_id",
    "citation_span_id",
    "claim_evidence_id",
    "source_intent",
    "evidence_candidate_id",
    "evidence_quality_score",
    "evidence_salience_score",
    "evidence_rejection_reasons",
    "evidence_candidate",
)


@dataclass(frozen=True)
class EvidenceCandidate:
    evidence_candidate_id: str
    source_document_id: str | None
    source_chunk_id: str
    citation_span_id: str | None
    slot_ids: tuple[str, ...]
    source_intent: str
    excerpt: str
    start_offset: int
    end_offset: int
    salience_score: float
    quality_score: float
    extraction_strategy: str | None
    rejection_reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "evidence_candidate_id": self.evidence_candidate_id,
            "source_document_id": self.source_document_id,
            "source_chunk_id": self.source_chunk_id,
            "citation_span_id": self.citation_span_id,
            "slot_ids": list(self.slot_ids),
            "source_intent": self.source_intent,
            "excerpt": self.excerpt,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "salience_score": self.salience_score,
            "quality_score": self.quality_score,
            "extraction_strategy": self.extraction_strategy,
            "rejection_reasons": list(self.rejection_reasons),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> EvidenceCandidate:
        return cls(
            evidence_candidate_id=_required_str(payload, "evidence_candidate_id"),
            source_document_id=_optional_str(payload.get("source_document_id")),
            source_chunk_id=_required_str(payload, "source_chunk_id"),
            citation_span_id=_optional_str(payload.get("citation_span_id")),
            slot_ids=tuple(_string_list(payload.get("slot_ids"))),
            source_intent=_required_str(payload, "source_intent"),
            excerpt=_required_str(payload, "excerpt"),
            start_offset=int(payload.get("start_offset", 0)),
            end_offset=int(payload.get("end_offset", 0)),
            salience_score=_float_value(payload.get("salience_score")),
            quality_score=_float_value(payload.get("quality_score")),
            extraction_strategy=_optional_str(payload.get("extraction_strategy")),
            rejection_reasons=tuple(_string_list(payload.get("rejection_reasons"))),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        )


@dataclass(frozen=True)
class EvidenceYieldSummary:
    total_candidates: int
    accepted_candidates: int
    rejected_candidates: int
    by_slot: dict[str, dict[str, int]]
    by_source: dict[str, dict[str, int]]
    by_query: dict[str, Any]
    top_rejection_reasons: list[dict[str, Any]]

    def to_payload(self) -> dict[str, Any]:
        return {
            "total_candidates": self.total_candidates,
            "accepted_candidates": self.accepted_candidates,
            "rejected_candidates": self.rejected_candidates,
            "by_slot": self.by_slot,
            "by_source": self.by_source,
            "by_query": self.by_query,
            "top_rejection_reasons": self.top_rejection_reasons,
        }


@dataclass(frozen=True)
class SourceYieldSummary:
    source_document_id: str | None
    url: str
    source_intent: str
    attempted: bool
    fetched: bool
    parsed: bool
    indexed: bool
    candidate_count: int
    accepted_evidence_count: int
    claim_count: int
    rejected_count: int
    dropped_reasons: tuple[str, ...]
    contribution_level: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_document_id": self.source_document_id,
            "url": self.url,
            "canonical_url": self.url,
            "source_intent": self.source_intent,
            "attempted": self.attempted,
            "fetched": self.fetched,
            "parsed": self.parsed,
            "indexed": self.indexed,
            "candidate_count": self.candidate_count,
            "accepted_evidence_count": self.accepted_evidence_count,
            "claim_count": self.claim_count,
            "rejected_count": self.rejected_count,
            "dropped_reasons": list(self.dropped_reasons),
            "contribution_level": self.contribution_level,
        }


@dataclass(frozen=True)
class SlotCoverageSummary:
    slot_id: str
    required: bool
    evidence_candidate_count: int
    accepted_evidence_count: int
    supported_claim_count: int
    weak_supported_claim_count: int
    unsupported_claim_count: int
    source_count: int
    status: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "required": self.required,
            "evidence_candidate_count": self.evidence_candidate_count,
            "accepted_evidence_count": self.accepted_evidence_count,
            "supported_claim_count": self.supported_claim_count,
            "weak_supported_claim_count": self.weak_supported_claim_count,
            "unsupported_claim_count": self.unsupported_claim_count,
            "source_count": self.source_count,
            "status": self.status,
        }


def evidence_candidate_id(
    *,
    source_chunk_id: str,
    start_offset: int,
    end_offset: int,
    excerpt: str,
) -> str:
    normalized_excerpt = " ".join(excerpt.split()).lower()
    raw = f"{source_chunk_id}:{start_offset}:{end_offset}:{normalized_excerpt}"
    return f"ec_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def slot_ids_for_candidate_category(category: str, *, query: str | None) -> tuple[str, ...]:
    return tuple(slot_ids_for_claim_category(category, query=query))


def summarize_evidence_yield(
    candidates: list[dict[str, Any]],
    *,
    accepted_candidate_ids: set[str] | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    accepted_ids = accepted_candidate_ids or set()
    by_slot: dict[str, Counter[str]] = {}
    by_source: dict[str, Counter[str]] = {}
    rejection_counter: Counter[str] = Counter()

    accepted_count = 0
    rejected_count = 0
    for item in candidates:
        candidate_id = item.get("evidence_candidate_id")
        is_accepted = isinstance(candidate_id, str) and candidate_id in accepted_ids
        rejection_reasons = _string_list(item.get("rejection_reasons"))
        if is_accepted:
            accepted_count += 1
        elif rejection_reasons:
            rejected_count += 1
        for reason in rejection_reasons:
            rejection_counter[reason] += 1

        slot_ids = _string_list(item.get("slot_ids"))
        if not slot_ids:
            slot_ids = ["unassigned"]
        source_id = _optional_str(item.get("source_document_id")) or "unknown"
        for slot_id in slot_ids:
            slot_counts = by_slot.setdefault(slot_id, Counter())
            slot_counts["total"] += 1
            slot_counts["accepted" if is_accepted else "rejected"] += 1
        source_counts = by_source.setdefault(source_id, Counter())
        source_counts["total"] += 1
        source_counts["accepted" if is_accepted else "rejected"] += 1

    summary = EvidenceYieldSummary(
        total_candidates=len(candidates),
        accepted_candidates=accepted_count,
        rejected_candidates=rejected_count,
        by_slot={key: dict(value) for key, value in sorted(by_slot.items())},
        by_source={key: dict(value) for key, value in sorted(by_source.items())},
        by_query={"query": query} if query else {},
        top_rejection_reasons=[
            {"reason": reason, "count": count} for reason, count in rejection_counter.most_common(8)
        ],
    )
    return summary.to_payload()


def build_slot_coverage_summary(
    query: str | None,
    *,
    evidence_candidates: list[dict[str, Any]] | None = None,
    claim_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    candidate_rows = evidence_candidates or []
    claims = claim_rows or []
    rows: list[dict[str, Any]] = []

    for slot in answer_slots_for_query(query):
        slot_id = slot.slot_id
        slot_candidate_rows = [
            item for item in candidate_rows if slot_id in _string_list(item.get("slot_ids"))
        ]
        slot_claim_rows = [item for item in claims if slot_id in _string_list(item.get("slot_ids"))]
        accepted_evidence_count = sum(
            1 for item in slot_candidate_rows if not _string_list(item.get("rejection_reasons"))
        )
        supported_count = sum(
            1
            for item in slot_claim_rows
            if item.get("verification_status") == "supported"
            and item.get("support_level") != "weak"
        )
        weak_count = sum(
            1
            for item in slot_claim_rows
            if item.get("verification_status") == "supported"
            and item.get("support_level") == "weak"
        )
        unsupported_count = sum(
            1
            for item in slot_claim_rows
            if item.get("verification_status") in {"unsupported", "mixed", "draft"}
        )
        source_ids = {
            _optional_str(item.get("source_document_id"))
            for item in [*slot_candidate_rows, *slot_claim_rows]
        }
        source_ids.discard(None)
        status = _slot_status(
            supported_claim_count=supported_count,
            weak_supported_claim_count=weak_count,
            accepted_evidence_count=accepted_evidence_count,
            required=slot.required,
        )
        rows.append(
            SlotCoverageSummary(
                slot_id=slot_id,
                required=slot.required,
                evidence_candidate_count=len(slot_candidate_rows),
                accepted_evidence_count=accepted_evidence_count,
                supported_claim_count=supported_count,
                weak_supported_claim_count=weak_count,
                unsupported_claim_count=unsupported_count,
                source_count=len(source_ids),
                status=status,
            ).to_payload()
            | {
                "label": slot.label,
                "expected_claim_categories": list(slot.expected_claim_categories),
            }
        )
    return rows


def contribution_level_for_counts(
    *,
    accepted_evidence_count: int,
    claim_count: int,
    candidate_count: int,
) -> str:
    if claim_count >= 3 or accepted_evidence_count >= 3:
        return "high"
    if claim_count >= 1 or accepted_evidence_count >= 1:
        return "medium"
    if candidate_count > 0:
        return "low"
    return "none"


def normalize_dropped_reasons(reasons: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for reason in reasons:
        if reason in DROPPED_SOURCE_REASONS:
            normalized.append(reason)
        elif reason:
            normalized.append("unknown")
    return tuple(dict.fromkeys(normalized))


def _slot_status(
    *,
    supported_claim_count: int,
    weak_supported_claim_count: int,
    accepted_evidence_count: int,
    required: bool,
) -> str:
    if supported_claim_count > 0:
        return "covered"
    if weak_supported_claim_count > 0 or accepted_evidence_count > 0:
        return "weak"
    return "missing" if required else "weak"


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"missing required string field {key!r}")


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _float_value(value: object) -> float:
    if isinstance(value, int | float):
        return round(float(value), 4)
    return 0.0
