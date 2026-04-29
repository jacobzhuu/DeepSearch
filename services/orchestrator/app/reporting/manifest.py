from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import UUID

from services.orchestrator.app.reporting.markdown import (
    ReportClaimItem,
    ReportEvidenceItem,
    ReportSourceItem,
)
from services.orchestrator.app.research_quality import answer_slot_coverage

REPORT_MANIFEST_VERSION = 1


def compute_report_content_hash(markdown: str) -> str:
    digest = sha256(markdown.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_report_manifest(
    *,
    task_id: UUID,
    revision_no: int,
    query: str,
    report_title: str,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
    slot_coverage_summary: list[dict[str, Any]] | None = None,
    evidence_yield_summary: dict[str, Any] | None = None,
    source_yield_summary: list[dict[str, Any]] | None = None,
    verification_summary: dict[str, Any] | None = None,
    dropped_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ordered_claims = sorted(claims, key=lambda item: (item.verification_status, str(item.claim_id)))
    ordered_sources = sorted(sources, key=lambda item: (item.domain, item.canonical_url))

    return {
        "manifest_version": REPORT_MANIFEST_VERSION,
        "task_id": str(task_id),
        "revision_no": revision_no,
        "query": query,
        "report_title": report_title,
        "answer_slot_coverage": answer_slot_coverage(
            query,
            {
                claim.claim_category
                for claim in ordered_claims
                if claim.verification_status == "supported" and claim.claim_category is not None
            },
        ),
        "slot_coverage_summary": slot_coverage_summary or [],
        "evidence_yield_summary": evidence_yield_summary or {},
        "source_yield_summary": source_yield_summary or [],
        "dropped_sources": dropped_sources or [],
        "verification_summary": verification_summary or {},
        "claim_counts": {
            "supported": sum(
                1 for item in ordered_claims if item.verification_status == "supported"
            ),
            "mixed": sum(1 for item in ordered_claims if item.verification_status == "mixed"),
            "unsupported": sum(
                1 for item in ordered_claims if item.verification_status == "unsupported"
            ),
            "draft": sum(1 for item in ordered_claims if item.verification_status == "draft"),
        },
        "claim_snapshot": [
            {
                "claim_id": str(claim.claim_id),
                "statement": claim.statement,
                "claim_type": claim.claim_type,
                "verification_status": claim.verification_status,
                "confidence": claim.confidence,
                "claim_quality_score": claim.claim_quality_score,
                "query_answer_score": claim.query_answer_score,
                "claim_category": claim.claim_category,
                "slot_ids": list(claim.slot_ids),
                "verifier_method": claim.verifier_method,
                "support_level": claim.support_level,
                "support_evidence": [_serialize_evidence(item) for item in claim.support_evidence],
                "contradict_evidence": [
                    _serialize_evidence(item) for item in claim.contradict_evidence
                ],
            }
            for claim in ordered_claims
        ],
        "source_snapshot": [
            {
                "source_document_id": str(source.source_document_id),
                "canonical_url": source.canonical_url,
                "domain": source.domain,
                "title": source.title,
            }
            for source in ordered_sources
        ],
    }


def _serialize_evidence(evidence: ReportEvidenceItem) -> dict[str, object]:
    return {
        "claim_evidence_id": str(evidence.claim_evidence_id),
        "citation_span_id": str(evidence.citation_span_id),
        "source_document_id": str(evidence.source_document_id),
        "source_chunk_id": str(evidence.source_chunk_id),
        "relation_type": evidence.relation_type,
        "score": evidence.score,
        "canonical_url": evidence.canonical_url,
        "domain": evidence.domain,
        "chunk_no": evidence.chunk_no,
        "start_offset": evidence.start_offset,
        "end_offset": evidence.end_offset,
        "excerpt": evidence.excerpt,
        "relation_detail": evidence.relation_detail,
        "support_level": evidence.support_level,
        "verifier_method": evidence.verifier_method,
        "reasons": list(evidence.reasons),
    }
