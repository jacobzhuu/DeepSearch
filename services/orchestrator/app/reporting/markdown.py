from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from services.orchestrator.app.research_quality import (
    answer_slot_coverage,
    answer_slots_for_query,
    build_slot_coverage_summary,
    slot_ids_for_claim_category,
)

ClaimStatus = Literal["draft", "supported", "mixed", "unsupported"]
EvidenceRelation = Literal["support", "contradict"]

_STATUS_PRIORITY: dict[str, int] = {
    "supported": 0,
    "mixed": 1,
    "unsupported": 2,
    "draft": 3,
}
_CATEGORY_PRIORITY: dict[str, int] = {
    "definition": 0,
    "mechanism": 1,
    "privacy": 2,
    "feature": 3,
    "deployment/self_hosting": 4,
    "other": 5,
}


@dataclass(frozen=True)
class ReportEvidenceItem:
    claim_evidence_id: UUID
    citation_span_id: UUID
    source_document_id: UUID
    source_chunk_id: UUID
    relation_type: EvidenceRelation
    score: float | None
    canonical_url: str
    domain: str
    chunk_no: int
    start_offset: int
    end_offset: int
    excerpt: str
    relation_detail: str | None = None
    support_level: str | None = None
    verifier_method: str | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportClaimItem:
    claim_id: UUID
    statement: str
    claim_type: str
    confidence: float | None
    verification_status: ClaimStatus
    rationale: str | None
    support_evidence: list[ReportEvidenceItem]
    contradict_evidence: list[ReportEvidenceItem]
    claim_quality_score: float | None = None
    query_answer_score: float | None = None
    claim_category: str | None = None
    slot_ids: tuple[str, ...] = ()
    verifier_method: str | None = None
    support_level: str | None = None


@dataclass(frozen=True)
class ReportSourceItem:
    source_document_id: UUID
    canonical_url: str
    domain: str
    title: str | None


@dataclass(frozen=True)
class RenderedMarkdownReport:
    title: str
    markdown: str
    supported_count: int
    mixed_count: int
    unsupported_count: int
    draft_count: int
    answer_relevant_count: int
    excluded_low_quality_count: int


def extract_report_title(markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or "Research Report"
    return "Research Report"


def render_markdown_report(
    *,
    task_id: UUID,
    research_question: str,
    revision_no: int,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
    answer_relevant_claim_count: int | None = None,
    excluded_low_quality_claim_count: int = 0,
) -> RenderedMarkdownReport:
    ordered_claims = sorted(claims, key=_claim_sort_key)
    supported_claims = [item for item in ordered_claims if item.verification_status == "supported"]
    weak_supported_claims = [
        item for item in supported_claims if _claim_support_level(item) == "weak"
    ]
    strong_supported_claims = [
        item for item in supported_claims if _claim_support_level(item) != "weak"
    ]
    mixed_claims = [item for item in ordered_claims if item.verification_status == "mixed"]
    unsupported_claims = [
        item for item in ordered_claims if item.verification_status == "unsupported"
    ]
    draft_claims = [item for item in ordered_claims if item.verification_status == "draft"]
    if answer_relevant_claim_count is None:
        answer_relevant_claim_count = len(ordered_claims)

    title = build_report_title(research_question)
    supported_by_category = _claims_by_category(strong_supported_claims)
    covered_categories = set(supported_by_category)
    slot_coverage = answer_slot_coverage(research_question, covered_categories)
    slot_coverage_summary = build_slot_coverage_summary(
        research_question,
        evidence_candidates=[],
        claim_rows=_claim_rows_for_slot_summary(ordered_claims, query=research_question),
    )
    missing_required_slots = [
        slot for slot in slot_coverage if slot["required"] and not slot["covered"]
    ]
    missing_core_categories = [
        category
        for category in ("definition", "mechanism", "privacy", "feature")
        if not supported_by_category.get(category)
    ]
    source_domains = _format_domains(sources)
    lines = [
        f"# {title}",
        "",
        f"_Generated from research task `{task_id}` at revision `{revision_no}`._",
        "",
        "## Research Question",
        "",
        _normalize_inline(research_question),
        "",
        "## Executive Summary",
        "",
    ]

    if strong_supported_claims:
        for claim in strong_supported_claims[:6]:
            lines.append(f"- {_normalize_inline(claim.statement)}")
    else:
        lines.append(
            "- No strongly supported claims are currently available in the persisted ledger."
        )
    if weak_supported_claims:
        lines.append(
            f"- {len(weak_supported_claims)} claim(s) have weak lexical support only and "
            "are kept out of the main answer sections."
        )
    if answer_relevant_claim_count < 2 or missing_core_categories:
        lines.append(
            "- Coverage is limited because no "
            f"{'/'.join(missing_core_categories) or 'additional'} claims were generated."
        )
    if mixed_claims or unsupported_claims or draft_claims:
        lines.append(
            "- Current uncertainty remains:"
            f" {len(mixed_claims)} mixed,"
            f" {len(unsupported_claims)} unsupported,"
            f" {len(draft_claims)} draft."
        )
    lines.extend(
        [
            "",
            "## Answer",
            "",
        ]
    )

    for slot in answer_slots_for_query(research_question):
        lines.extend([f"### {slot.label}", ""])
        section_claims = [
            claim
            for claim in strong_supported_claims
            if _claim_matches_slot(claim, slot_id=slot.slot_id, query=research_question)
        ]
        if section_claims:
            for claim in section_claims:
                lines.append(f"- {_normalize_inline(claim.statement)}")
        else:
            lines.append(
                f"- Coverage is limited because no strongly supported {slot.label.lower()} "
                "claims were generated."
            )
        lines.append("")

    lines.extend(["## Answer Slot Coverage", ""])
    if slot_coverage_summary:
        lines.extend(
            [
                "| Slot | Status | Evidence candidates | Accepted evidence | "
                "Strong claims | Weak claims | Sources |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for slot in slot_coverage_summary:
            lines.append(
                "| "
                f"{_escape_table_cell(str(slot.get('slot_id', 'unknown')))} | "
                f"{_escape_table_cell(str(slot.get('status', 'unknown')))} | "
                f"{slot.get('evidence_candidate_count', 0)} | "
                f"{slot.get('accepted_evidence_count', 0)} | "
                f"{slot.get('supported_claim_count', 0)} | "
                f"{slot.get('weak_supported_claim_count', 0)} | "
                f"{slot.get('source_count', 0)} |"
            )
    else:
        lines.append("No answer-slot coverage summary is available.")

    lines.extend(["", "## Evidence Table", ""])
    evidence_rows = _evidence_rows(supported_claims)
    if evidence_rows:
        lines.extend(
            [
                "| Claim category | Support detail | Claim | Evidence domain | Source |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for claim, evidence in evidence_rows:
            lines.append(
                "| "
                f"{_normalize_inline(claim.claim_category or 'other')} | "
                f"{_escape_table_cell(_evidence_detail(evidence))} | "
                f"{_escape_table_cell(_normalize_inline(claim.statement))} | "
                f"{_escape_table_cell(evidence.domain)} | "
                f"{_escape_table_cell(evidence.canonical_url)} |"
            )
    else:
        lines.append("No support evidence rows are currently available.")

    lines.extend(["", "## Source Scope and Limitations", ""])
    lines.append(
        "- This report is synthesized strictly from persisted task, claim, citation, "
        "evidence, and verification records."
    )
    lines.append(
        "- No new search, fetch, parse, index, verifier, or LLM report-writing logic was "
        "executed while generating this artifact."
    )
    lines.append(
        "- Claim counts:"
        f" {len(strong_supported_claims)} strongly supported,"
        f" {len(weak_supported_claims)} weak-supported,"
        f" {len(mixed_claims)} mixed,"
        f" {len(unsupported_claims)} unsupported,"
        f" {len(draft_claims)} draft."
    )
    lines.append(f"- Answer-relevant claims included: {answer_relevant_claim_count}.")
    lines.append(f"- Excluded low-quality or off-query claims: {excluded_low_quality_claim_count}.")
    lines.append(
        "- Answer slot coverage:"
        f" {sum(1 for slot in slot_coverage if slot['covered'])}/{len(slot_coverage)}."
    )
    lines.append(
        "- Evidence-linked source documents:"
        f" {len(sources)} across domains: {', '.join(source_domains) or 'none'}."
    )
    if len(source_domains) == 1:
        lines.append("- Warning: source coverage uses only one evidence domain.")

    lines.extend(["", "## Unresolved / Low Coverage Areas", ""])
    unresolved_claims = weak_supported_claims + mixed_claims + unsupported_claims + draft_claims
    weak_or_missing_slots = [
        slot
        for slot in slot_coverage_summary
        if slot.get("required") is True and slot.get("status") in {"missing", "weak"}
    ]
    if weak_or_missing_slots:
        weak_missing_labels = ", ".join(
            str(slot.get("label") or slot.get("slot_id")) for slot in weak_or_missing_slots
        )
        lines.append("- Missing or weak required answer slots:" f" {weak_missing_labels}.")
    if missing_required_slots:
        lines.append(
            "- Missing required answer slots:"
            f" {', '.join(str(slot['label']) for slot in missing_required_slots)}."
        )
    if missing_core_categories:
        lines.append("- Missing answer coverage:" f" {', '.join(missing_core_categories)}.")
    if unresolved_claims:
        for claim in unresolved_claims:
            lines.append(f"- {_normalize_inline(claim.statement)}")
    elif not ordered_claims:
        lines.append("- The ledger currently contains no claims to synthesize.")
    elif not missing_core_categories:
        lines.append(
            "- No additional unresolved questions were inferred beyond the current"
            " verified claim set."
        )

    lines.extend(["", "## Appendix: Claim Evidence Mapping", ""])
    if ordered_claims:
        for claim in ordered_claims:
            lines.extend(_render_claim_mapping(claim))
    else:
        lines.append("No claim-to-citation mappings are currently available.")

    markdown = "\n".join(lines).strip() + "\n"
    return RenderedMarkdownReport(
        title=title,
        markdown=markdown,
        supported_count=len(supported_claims),
        mixed_count=len(mixed_claims),
        unsupported_count=len(unsupported_claims),
        draft_count=len(draft_claims),
        answer_relevant_count=answer_relevant_claim_count,
        excluded_low_quality_count=excluded_low_quality_claim_count,
    )


def build_report_title(research_question: str) -> str:
    normalized = _normalize_inline(research_question)
    if not normalized:
        return "Research Report"
    return f"Research Report: {normalized}"


def _claim_sort_key(claim: ReportClaimItem) -> tuple[int, int, str]:
    return (
        _CATEGORY_PRIORITY.get(claim.claim_category or "other", 99),
        _STATUS_PRIORITY.get(claim.verification_status, 99),
        str(claim.claim_id),
    )


def _claims_by_category(claims: list[ReportClaimItem]) -> dict[str, list[ReportClaimItem]]:
    grouped: dict[str, list[ReportClaimItem]] = {}
    for claim in claims:
        grouped.setdefault(claim.claim_category or "other", []).append(claim)
    for category, category_claims in grouped.items():
        grouped[category] = sorted(category_claims, key=_claim_sort_key)
    return grouped


def _evidence_rows(
    claims: list[ReportClaimItem],
) -> list[tuple[ReportClaimItem, ReportEvidenceItem]]:
    rows: list[tuple[ReportClaimItem, ReportEvidenceItem]] = []
    for claim in claims:
        for evidence in claim.support_evidence[:2]:
            rows.append((claim, evidence))
    return rows


def _render_claim_section(index: int, claim: ReportClaimItem) -> list[str]:
    lines = [
        (
            f"### Claim {index}: [{claim.verification_status.upper()}]"
            f" {_normalize_inline(claim.statement)}"
        ),
        "",
        f"- Claim id: `{claim.claim_id}`",
        f"- Claim type: `{claim.claim_type}`",
        f"- Confidence: {_format_confidence(claim.confidence)}",
        (
            "- Verification rationale:"
            f" {_normalize_inline(claim.rationale or 'No verification rationale recorded.')}"
        ),
        "- Support evidence:",
    ]
    if claim.support_evidence:
        for evidence in claim.support_evidence:
            lines.extend(_render_evidence_bullet(evidence))
    else:
        lines.append("  - None.")
    lines.append("- Contradict evidence:")
    if claim.contradict_evidence:
        for evidence in claim.contradict_evidence:
            lines.extend(_render_evidence_bullet(evidence))
    else:
        lines.append("  - None.")
    lines.append("")
    return lines


def _render_claim_mapping(claim: ReportClaimItem) -> list[str]:
    lines = [
        (
            f"- Claim `{claim.claim_id}` [{claim.verification_status.upper()}]:"
            f" {_normalize_inline(claim.statement)}"
        ),
    ]
    evidence_items = sorted(
        claim.support_evidence + claim.contradict_evidence,
        key=lambda item: (
            item.relation_type,
            str(item.source_document_id),
            item.start_offset,
            item.end_offset,
        ),
    )
    if not evidence_items:
        lines.append("  - No citation spans recorded.")
        return lines
    for evidence in evidence_items:
        lines.append(
            "  - "
            f"{evidence.relation_type}"
            f"({evidence.relation_detail or evidence.support_level or 'n/a'})"
            f" | citation `{evidence.citation_span_id}`"
            f" | source `{evidence.source_document_id}`"
            f" | chunk `{evidence.source_chunk_id}` #{evidence.chunk_no}"
            f" | offsets `{evidence.start_offset}:{evidence.end_offset}`"
            f" | {evidence.canonical_url}"
            f' | excerpt: "{_normalize_inline(evidence.excerpt)}"'
        )
    return lines


def _render_evidence_bullet(evidence: ReportEvidenceItem) -> list[str]:
    return [
        (
            "  - "
            f"[{evidence.relation_type.upper()}]"
            f" source `{evidence.source_document_id}`"
            f" chunk `{evidence.source_chunk_id}` #{evidence.chunk_no}"
            f" offsets `{evidence.start_offset}:{evidence.end_offset}`"
            f" score {_format_confidence(evidence.score)}"
            f" detail {evidence.relation_detail or evidence.support_level or 'n/a'}"
            f" | {evidence.canonical_url}"
        ),
        f"    > {_normalize_inline(evidence.excerpt)}",
    ]


def _claim_rows_for_slot_summary(
    claims: list[ReportClaimItem],
    *,
    query: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for claim in claims:
        rows.append(
            {
                "claim_id": str(claim.claim_id),
                "verification_status": claim.verification_status,
                "slot_ids": list(_claim_slot_ids(claim, query=query)),
                "source_document_id": (
                    str(claim.support_evidence[0].source_document_id)
                    if claim.support_evidence
                    else None
                ),
                "support_level": _claim_support_level(claim),
            }
        )
    return rows


def _claim_matches_slot(claim: ReportClaimItem, *, slot_id: str, query: str) -> bool:
    return slot_id in _claim_slot_ids(claim, query=query)


def _claim_slot_ids(claim: ReportClaimItem, *, query: str) -> tuple[str, ...]:
    if claim.slot_ids:
        return claim.slot_ids
    if claim.claim_category:
        return tuple(slot_ids_for_claim_category(claim.claim_category, query=query))
    return ()


def _claim_support_level(claim: ReportClaimItem) -> str:
    if claim.support_level:
        return claim.support_level
    support_levels = {item.support_level for item in claim.support_evidence if item.support_level}
    if support_levels == {"weak"}:
        return "weak"
    return "strong"


def _evidence_detail(evidence: ReportEvidenceItem) -> str:
    return evidence.relation_detail or evidence.support_level or "support"


def _format_domains(sources: list[ReportSourceItem]) -> list[str]:
    return sorted({source.domain for source in sources})


def _format_confidence(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _normalize_inline(value: str) -> str:
    return " ".join(value.split())


def _escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|")
