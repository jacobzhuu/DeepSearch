from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

ClaimStatus = Literal["draft", "supported", "mixed", "unsupported"]
EvidenceRelation = Literal["support", "contradict"]

_STATUS_PRIORITY: dict[str, int] = {
    "supported": 0,
    "mixed": 1,
    "unsupported": 2,
    "draft": 3,
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
    ordered_claims = sorted(
        claims,
        key=lambda item: (_STATUS_PRIORITY.get(item.verification_status, 99), str(item.claim_id)),
    )
    supported_claims = [item for item in ordered_claims if item.verification_status == "supported"]
    mixed_claims = [item for item in ordered_claims if item.verification_status == "mixed"]
    unsupported_claims = [
        item for item in ordered_claims if item.verification_status == "unsupported"
    ]
    draft_claims = [item for item in ordered_claims if item.verification_status == "draft"]
    if answer_relevant_claim_count is None:
        answer_relevant_claim_count = len(ordered_claims)

    title = build_report_title(research_question)
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

    if supported_claims:
        for claim in supported_claims:
            lines.append(f"- {_normalize_inline(claim.statement)}")
    else:
        lines.append("- No supported claims are currently available in the persisted ledger.")
    if answer_relevant_claim_count < 2:
        lines.append(
            "- Warning: Low answer coverage:"
            f" only {answer_relevant_claim_count} answer-relevant claims were generated."
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
            "## Method And Source Scope",
            "",
            (
                "- This report is synthesized strictly from persisted task, claim, citation,"
                " evidence, and verification records."
            ),
            (
                "- No new search, fetch, parse, index, or verifier logic was executed while"
                " generating this artifact."
            ),
            (
                "- Claim counts:"
                f" {len(supported_claims)} supported,"
                f" {len(mixed_claims)} mixed,"
                f" {len(unsupported_claims)} unsupported,"
                f" {len(draft_claims)} draft."
            ),
            f"- Answer-relevant claims included: {answer_relevant_claim_count}.",
            ("- Excluded low-quality or off-query claims:" f" {excluded_low_quality_claim_count}."),
            (
                "- Evidence-linked source documents:"
                f" {len(sources)} across domains: {', '.join(_format_domains(sources)) or 'none'}."
            ),
            "",
            "## Key Conclusions",
            "",
        ]
    )

    if supported_claims:
        for claim in supported_claims:
            lines.append(f"- [SUPPORTED] {_normalize_inline(claim.statement)}")
    else:
        lines.append("- No settled evidence-backed conclusions are currently available.")

    lines.extend(["", "## Conclusion Details And Evidence", ""])
    if ordered_claims:
        for index, claim in enumerate(ordered_claims, start=1):
            lines.extend(_render_claim_section(index, claim))
    else:
        lines.append("No persisted claims are available for this task.")

    lines.extend(["", "## Conflicts / Uncertainty", ""])
    if mixed_claims:
        lines.append("### Mixed Claims")
        lines.append("")
        for claim in mixed_claims:
            lines.append(
                f"- {_normalize_inline(claim.statement)}"
                f" ({claim.rationale or 'Mixed evidence is present.'})"
            )
        lines.append("")
    if unsupported_claims:
        lines.append("### Unsupported Claims")
        lines.append("")
        for claim in unsupported_claims:
            lines.append(
                f"- {_normalize_inline(claim.statement)}"
                f" ({claim.rationale or 'No support evidence is currently recorded.'})"
            )
        lines.append("")
    if draft_claims:
        lines.append("### Draft Claims")
        lines.append("")
        for claim in draft_claims:
            lines.append(
                f"- {_normalize_inline(claim.statement)}"
                " (This claim remains in draft and has not been verified.)"
            )
        lines.append("")
    if not mixed_claims and not unsupported_claims and not draft_claims:
        lines.append("No mixed, unsupported, or draft claims are currently recorded.")
        lines.append("")

    lines.extend(["## Unresolved Questions", ""])
    unresolved_claims = mixed_claims + unsupported_claims + draft_claims
    if unresolved_claims:
        for claim in unresolved_claims:
            lines.append(f"- {_normalize_inline(claim.statement)}")
    elif not ordered_claims:
        lines.append("- The ledger currently contains no claims to synthesize.")
    else:
        lines.append(
            "- No additional unresolved questions were inferred beyond the current"
            " verified claim set."
        )

    lines.extend(["", "## Appendix: Source List", ""])
    if sources:
        for index, source in enumerate(
            sorted(sources, key=lambda item: (item.domain, item.canonical_url)), start=1
        ):
            lines.append(
                f"{index}. `{source.source_document_id}`"
                f" | {source.domain}"
                f" | {_normalize_inline(source.title or '(untitled)')}"
                f" | {source.canonical_url}"
            )
    else:
        lines.append("No evidence-linked source documents are currently available.")

    lines.extend(["", "## Appendix: Claim To Citation Spans Mapping", ""])
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
            f"{evidence.relation_type} | citation `{evidence.citation_span_id}`"
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
            f" | {evidence.canonical_url}"
        ),
        f"    > {_normalize_inline(evidence.excerpt)}",
    ]


def _format_domains(sources: list[ReportSourceItem]) -> list[str]:
    return sorted({source.domain for source in sources})


def _format_confidence(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _normalize_inline(value: str) -> str:
    return " ".join(value.split())
