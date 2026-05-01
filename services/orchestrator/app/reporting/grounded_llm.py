from __future__ import annotations

import json
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any
from uuid import UUID

from services.orchestrator.app.llm import LLMProvider, LLMRequest
from services.orchestrator.app.reporting.language import (
    is_chinese_report_language,
    normalize_report_language,
)
from services.orchestrator.app.reporting.markdown import (
    RenderedMarkdownReport,
    ReportClaimItem,
    ReportEvidenceItem,
    ReportSourceItem,
    build_report_title,
)


class GroundedLLMReportValidationError(ValueError):
    pass


@dataclass(frozen=True)
class GroundedLLMReport:
    rendered: RenderedMarkdownReport
    metadata: dict[str, object]


@dataclass(frozen=True)
class _GroundedItem:
    text: str
    claim_ids: tuple[str, ...]
    claim_evidence_ids: tuple[str, ...]
    citation_span_ids: tuple[str, ...]


def render_grounded_llm_report(
    *,
    task_id: UUID,
    research_question: str,
    revision_no: int,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
    report_language: str,
    answer_relevant_claim_count: int,
    excluded_low_quality_claim_count: int,
    llm_provider: LLMProvider,
    llm_model: str,
    max_output_tokens: int,
) -> GroundedLLMReport:
    normalized_language = normalize_report_language(report_language)
    grounded_claims = _grounded_claims(claims)
    if not grounded_claims:
        raise GroundedLLMReportValidationError(
            "grounded LLM report writer requires at least one verified claim with evidence"
        )

    bundle = _build_grounding_bundle(
        task_id=task_id,
        research_question=research_question,
        revision_no=revision_no,
        claims=grounded_claims,
        sources=sources,
        report_language=normalized_language,
    )
    response = llm_provider.generate(
        LLMRequest(
            system_prompt=_system_prompt(normalized_language),
            user_prompt=json.dumps(bundle, ensure_ascii=False, sort_keys=True),
            model=llm_model,
            max_output_tokens=max_output_tokens,
            temperature=0.0,
            metadata={
                "task_id": str(task_id),
                "query": research_question,
                "purpose": "grounded_report_writer",
                "report_language": normalized_language,
            },
        )
    )
    payload = _parse_json_object(response.text)
    rendered = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question=research_question,
        revision_no=revision_no,
        claims=grounded_claims,
        sources=sources,
        report_language=normalized_language,
        answer_relevant_claim_count=answer_relevant_claim_count,
        excluded_low_quality_claim_count=excluded_low_quality_claim_count,
    )
    metadata: dict[str, object] = {
        "mode": "llm_grounded",
        "status": "used",
        "provider": response.provider,
        "model": response.model,
        "raw_response_id": response.raw_response_id,
        "finish_reason": response.finish_reason,
        "usage": response.usage or {},
        "input_claim_count": len(grounded_claims),
        "input_claim_evidence_count": sum(
            len(claim.support_evidence) + len(claim.contradict_evidence)
            for claim in grounded_claims
        ),
    }
    return GroundedLLMReport(rendered=rendered, metadata=metadata)


def _system_prompt(report_language: str) -> str:
    language_name = (
        "Simplified Chinese" if is_chinese_report_language(report_language) else "English"
    )
    return (
        "You are a grounded research report writer for an OSINT research ledger.\n"
        f"Write all report prose and section headings in {language_name}.\n"
        "You may only use facts explicitly present in the provided verified claims "
        "and citation excerpts.\n"
        "Do not introduce new facts, dates, numbers, names, comparisons, causes, or conclusions.\n"
        "Every factual item must cite existing claim_ids and claim_evidence_ids from the input.\n"
        "Mixed or unsupported claims may only be discussed as uncertainty, not as settled facts.\n"
        "Return valid JSON only. Do not return Markdown and do not wrap JSON in prose.\n"
        'Required JSON shape: {"title": string, "executive_summary": [item], '
        '"sections": [{"heading": string, "items": [item]}], "uncertainties": [item], '
        '"unresolved": [string]}. Each item is {"text": string, "claim_ids": [string], '
        '"claim_evidence_ids": [string], "citation_span_ids": [string]}.'
    )


def _build_grounding_bundle(
    *,
    task_id: UUID,
    research_question: str,
    revision_no: int,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
    report_language: str,
) -> dict[str, object]:
    return {
        "task_id": str(task_id),
        "revision_no": revision_no,
        "research_question": research_question,
        "report_language": report_language,
        "rules": [
            "Use only verified_claims and their evidence excerpts.",
            "Use supported claims as settled findings only when support_level is not weak.",
            "Use mixed or unsupported claims only in uncertainty sections.",
            "Every item must include claim_ids and claim_evidence_ids from this bundle.",
        ],
        "verified_claims": [_serialize_claim(claim) for claim in claims],
        "sources": [
            {
                "source_document_id": str(source.source_document_id),
                "domain": source.domain,
                "title": source.title,
                "canonical_url": source.canonical_url,
            }
            for source in sources
        ],
    }


def _serialize_claim(claim: ReportClaimItem) -> dict[str, object]:
    return {
        "claim_id": str(claim.claim_id),
        "statement": claim.statement,
        "verification_status": claim.verification_status,
        "claim_category": claim.claim_category,
        "support_level": claim.support_level,
        "confidence": claim.confidence,
        "rationale": claim.rationale,
        "support_evidence": [_serialize_evidence(item) for item in claim.support_evidence],
        "contradict_evidence": [_serialize_evidence(item) for item in claim.contradict_evidence],
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
        "reasons": list(evidence.reasons),
    }


def _render_validated_llm_payload(
    payload: dict[str, Any],
    *,
    task_id: UUID,
    research_question: str,
    revision_no: int,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
    report_language: str,
    answer_relevant_claim_count: int,
    excluded_low_quality_claim_count: int,
) -> RenderedMarkdownReport:
    labels = _labels(report_language)
    evidence_by_id, citation_by_evidence_id, claim_by_id, evidence_claim_id = _allowed_ids(claims)

    executive_items = _validated_items(
        payload.get("executive_summary"),
        claim_by_id=claim_by_id,
        evidence_by_id=evidence_by_id,
        citation_by_evidence_id=citation_by_evidence_id,
        evidence_claim_id=evidence_claim_id,
        allowed_statuses={"supported"},
        allow_weak_support=False,
    )
    sections = _validated_sections(
        payload.get("sections"),
        claim_by_id=claim_by_id,
        evidence_by_id=evidence_by_id,
        citation_by_evidence_id=citation_by_evidence_id,
        evidence_claim_id=evidence_claim_id,
    )
    uncertainty_items = _validated_items(
        payload.get("uncertainties"),
        claim_by_id=claim_by_id,
        evidence_by_id=evidence_by_id,
        citation_by_evidence_id=citation_by_evidence_id,
        evidence_claim_id=evidence_claim_id,
        allowed_statuses={"mixed", "unsupported", "contradicted", "supported"},
        allow_weak_support=True,
    )
    if not executive_items and not any(items for _, items in sections) and not uncertainty_items:
        raise GroundedLLMReportValidationError("LLM report JSON contained no grounded items")

    title = _string_or_none(payload.get("title")) or build_report_title(
        research_question,
        report_language=report_language,
    )
    lines = [
        f"# {title}",
        "",
        labels["generated"].format(task_id=task_id, revision_no=revision_no),
        "",
        f"## {labels['research_question']}",
        "",
        research_question,
        "",
        f"## {labels['executive_summary']}",
        "",
    ]
    if executive_items:
        lines.extend(_render_grounded_items(executive_items, labels=labels))
    else:
        lines.append(f"- {labels['no_supported_items']}")

    lines.extend(["", f"## {labels['key_findings']}", ""])
    if sections:
        for heading, items in sections:
            lines.extend([f"### {heading}", ""])
            lines.extend(_render_grounded_items(items, labels=labels))
            lines.append("")
    else:
        lines.append(f"- {labels['no_section_items']}")

    lines.extend(["", f"## {labels['uncertainty']}", ""])
    if uncertainty_items:
        lines.extend(_render_grounded_items(uncertainty_items, labels=labels))
    else:
        lines.append(f"- {labels['no_uncertainty_items']}")

    unresolved = [
        item.strip()
        for item in payload.get("unresolved", [])
        if isinstance(item, str) and item.strip()
    ]
    lines.extend(["", f"## {labels['unresolved']}", ""])
    if unresolved:
        for item in unresolved[:8]:
            lines.append(f"- {item}")
    else:
        lines.append(f"- {labels['no_unresolved']}")

    source_domains = sorted({source.domain for source in sources})
    lines.extend(["", f"## {labels['source_scope']}", ""])
    lines.append(f"- {labels['source_scope_strict']}")
    lines.append(f"- {labels['llm_generation']}")
    lines.append(
        "- "
        + labels["claim_counts"].format(
            supported=sum(1 for claim in claims if claim.verification_status == "supported"),
            mixed=sum(1 for claim in claims if claim.verification_status == "mixed"),
            contradicted=sum(1 for claim in claims if claim.verification_status == "contradicted"),
            unsupported=sum(1 for claim in claims if claim.verification_status == "unsupported"),
        )
    )
    lines.append(
        "- "
        + labels["evidence_sources"].format(
            count=len(sources),
            domains=", ".join(source_domains) or labels["none"],
        )
    )
    lines.append("- " + labels["answer_relevant"].format(count=answer_relevant_claim_count))
    lines.append("- " + labels["excluded_claims"].format(count=excluded_low_quality_claim_count))

    lines.extend(["", f"## {labels['claim_mapping']}", ""])
    lines.extend(_render_claim_mapping(claims, labels=labels))

    markdown = "\n".join(lines).strip() + "\n"
    return RenderedMarkdownReport(
        title=title,
        markdown=markdown,
        supported_count=sum(1 for claim in claims if claim.verification_status == "supported"),
        mixed_count=sum(1 for claim in claims if claim.verification_status == "mixed"),
        contradicted_count=sum(
            1 for claim in claims if claim.verification_status == "contradicted"
        ),
        unsupported_count=sum(1 for claim in claims if claim.verification_status == "unsupported"),
        draft_count=0,
        answer_relevant_count=answer_relevant_claim_count,
        excluded_low_quality_count=excluded_low_quality_claim_count,
    )


def _validated_sections(
    value: object,
    *,
    claim_by_id: dict[str, ReportClaimItem],
    evidence_by_id: dict[str, ReportEvidenceItem],
    citation_by_evidence_id: dict[str, str],
    evidence_claim_id: dict[str, str],
) -> list[tuple[str, list[_GroundedItem]]]:
    if not isinstance(value, list):
        return []
    result: list[tuple[str, list[_GroundedItem]]] = []
    for section in value:
        if not isinstance(section, dict):
            continue
        heading = _string_or_none(section.get("heading"))
        if heading is None:
            continue
        items = _validated_items(
            section.get("items"),
            claim_by_id=claim_by_id,
            evidence_by_id=evidence_by_id,
            citation_by_evidence_id=citation_by_evidence_id,
            evidence_claim_id=evidence_claim_id,
            allowed_statuses={"supported"},
            allow_weak_support=False,
        )
        if items:
            result.append((heading, items))
    return result


def _validated_items(
    value: object,
    *,
    claim_by_id: dict[str, ReportClaimItem],
    evidence_by_id: dict[str, ReportEvidenceItem],
    citation_by_evidence_id: dict[str, str],
    evidence_claim_id: dict[str, str],
    allowed_statuses: set[str],
    allow_weak_support: bool,
) -> list[_GroundedItem]:
    if not isinstance(value, list):
        return []
    result: list[_GroundedItem] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = _string_or_none(item.get("text"))
        if text is None:
            continue
        claim_ids = tuple(
            item_id
            for item_id in _string_list(item.get("claim_ids"))
            if item_id in claim_by_id
            and claim_by_id[item_id].verification_status in allowed_statuses
            and (allow_weak_support or claim_by_id[item_id].support_level != "weak")
        )
        if not claim_ids:
            continue
        claim_id_set = set(claim_ids)
        evidence_ids = tuple(
            item_id
            for item_id in _string_list(item.get("claim_evidence_ids", item.get("evidence_ids")))
            if item_id in evidence_by_id and evidence_claim_id.get(item_id) in claim_id_set
        )
        if not evidence_ids:
            continue
        citation_ids = tuple(
            item_id
            for item_id in _string_list(item.get("citation_span_ids"))
            if item_id in {citation_by_evidence_id[evidence_id] for evidence_id in evidence_ids}
        )
        if not citation_ids:
            citation_ids = tuple(
                citation_by_evidence_id[evidence_id] for evidence_id in evidence_ids
            )
        result.append(
            _GroundedItem(
                text=text,
                claim_ids=claim_ids,
                claim_evidence_ids=evidence_ids,
                citation_span_ids=citation_ids,
            )
        )
    return result


def _allowed_ids(
    claims: list[ReportClaimItem],
) -> tuple[
    dict[str, ReportEvidenceItem],
    dict[str, str],
    dict[str, ReportClaimItem],
    dict[str, str],
]:
    evidence_by_id: dict[str, ReportEvidenceItem] = {}
    citation_by_evidence_id: dict[str, str] = {}
    claim_by_id = {str(claim.claim_id): claim for claim in claims}
    evidence_claim_id: dict[str, str] = {}
    for claim in claims:
        claim_id = str(claim.claim_id)
        for evidence in claim.support_evidence + claim.contradict_evidence:
            evidence_id = str(evidence.claim_evidence_id)
            evidence_by_id[evidence_id] = evidence
            citation_by_evidence_id[evidence_id] = str(evidence.citation_span_id)
            evidence_claim_id[evidence_id] = claim_id
    return evidence_by_id, citation_by_evidence_id, claim_by_id, evidence_claim_id


def _render_grounded_items(items: list[_GroundedItem], *, labels: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for item in items:
        lines.append(
            f"- {item.text} "
            f"({labels['claims']}: {', '.join(f'`{claim_id}`' for claim_id in item.claim_ids)}; "
            f"{labels['claim_evidence']}: "
            f"{', '.join(f'`{evidence_id}`' for evidence_id in item.claim_evidence_ids)}; "
            f"{labels['citations']}: "
            f"{', '.join(f'`{citation_id}`' for citation_id in item.citation_span_ids)})"
        )
    return lines


def _render_claim_mapping(
    claims: list[ReportClaimItem],
    *,
    labels: dict[str, str],
) -> list[str]:
    lines: list[str] = []
    for claim in sorted(claims, key=lambda item: str(item.claim_id)):
        lines.append(
            f"- {labels['claim']} `{claim.claim_id}` [{claim.verification_status.upper()}]: "
            f"{claim.statement}"
        )
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
            lines.append(f"  - {labels['no_citation_spans']}")
            continue
        for evidence in evidence_items:
            lines.append(
                "  - "
                f"{evidence.relation_type}"
                f" | {labels['claim_evidence']} `{evidence.claim_evidence_id}`"
                f" | {labels['citation']} `{evidence.citation_span_id}`"
                f" | {labels['source']} `{evidence.source_document_id}`"
                f" | {labels['chunk']} `{evidence.source_chunk_id}` #{evidence.chunk_no}"
                f" | {labels['offsets']} `{evidence.start_offset}:{evidence.end_offset}`"
                f" | {evidence.canonical_url}"
                f" | {labels['excerpt']}: \"{evidence.excerpt}\""
            )
    return lines or [labels["no_mappings"]]


def _grounded_claims(claims: list[ReportClaimItem]) -> list[ReportClaimItem]:
    result: list[ReportClaimItem] = []
    for claim in claims:
        if claim.verification_status == "draft":
            continue
        if not claim.support_evidence and not claim.contradict_evidence:
            continue
        result.append(claim)
    return result


def _parse_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = _strip_code_fence(candidate)
    try:
        payload = json.loads(candidate)
    except JSONDecodeError as error:
        raise GroundedLLMReportValidationError("LLM report response was not valid JSON") from error
    if not isinstance(payload, dict):
        raise GroundedLLMReportValidationError("LLM report response JSON was not an object")
    return payload


def _strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _labels(report_language: str) -> dict[str, str]:
    if is_chinese_report_language(report_language):
        return {
            "answer_relevant": "已纳入与问题相关的 claim：{count}。",
            "chunk": "chunk",
            "citation": "citation",
            "citations": "citations",
            "claim": "Claim",
            "claim_counts": (
                "已验证 claim 计数：{supported} 条 supported、{mixed} 条 mixed、"
                "{contradicted} 条 contradicted、{unsupported} 条 unsupported。"
            ),
            "claim_evidence": "claim_evidence",
            "claim_mapping": "附录：claim/evidence/citation 映射",
            "claims": "claims",
            "evidence_sources": "带证据链接的来源文档：{count} 个；域名：{domains}。",
            "excluded_claims": "已排除低质量或偏离问题的 claim：{count}。",
            "excerpt": "摘录",
            "executive_summary": "执行摘要",
            "generated": (
                "_由 grounded LLM report writer 基于 research_task `{task_id}` "
                "revision `{revision_no}` 生成。_"
            ),
            "key_findings": "关键结论",
            "llm_generation": (
                "LLM 只接收已验证 claim、claim_evidence 和 citation span 摘录；"
                "所有输出条目均通过 id 校验。"
            ),
            "no_citation_spans": "未记录 citation span。",
            "no_mappings": "当前没有 claim 到 citation 的映射。",
            "no_section_items": "LLM 未返回可验证的关键结论条目。",
            "no_supported_items": "LLM 未返回可验证的强支持摘要条目。",
            "no_uncertainty_items": "没有额外混合或不支持 claim 需要展示。",
            "no_unresolved": "未从已验证 claim 集中推断额外未解决问题。",
            "none": "无",
            "offsets": "offsets",
            "research_question": "研究问题",
            "source": "source",
            "source_scope": "来源范围与限制",
            "source_scope_strict": (
                "本报告严格由已持久化的 claim、evidence 和 citation span 综合，" "不使用外部事实。"
            ),
            "uncertainty": "冲突 / 不确定性",
            "unresolved": "未解决问题",
        }
    return {
        "answer_relevant": "Answer-relevant claims included: {count}.",
        "chunk": "chunk",
        "citation": "citation",
        "citations": "citations",
        "claim": "Claim",
        "claim_counts": (
            "Verified claim counts: {supported} supported, {mixed} mixed, "
            "{contradicted} contradicted, {unsupported} unsupported."
        ),
        "claim_evidence": "claim_evidence",
        "claim_mapping": "Appendix: Claim Evidence Mapping",
        "claims": "claims",
        "evidence_sources": "Evidence-linked source documents: {count} across domains: {domains}.",
        "excluded_claims": "Excluded low-quality or off-query claims: {count}.",
        "excerpt": "excerpt",
        "executive_summary": "Executive Summary",
        "generated": (
            "_Generated by grounded LLM report writer from research task `{task_id}` "
            "at revision `{revision_no}`._"
        ),
        "key_findings": "Key Findings",
        "llm_generation": (
            "The LLM received only verified claims, claim_evidence rows, and citation "
            "span excerpts; every output item passed id validation."
        ),
        "no_citation_spans": "No citation spans recorded.",
        "no_mappings": "No claim-to-citation mappings are currently available.",
        "no_section_items": "The LLM returned no verifiable key finding items.",
        "no_supported_items": "The LLM returned no verifiable strongly supported summary items.",
        "no_uncertainty_items": "No additional mixed or unsupported claims need display.",
        "no_unresolved": (
            "No additional unresolved questions were inferred from the verified claim set."
        ),
        "none": "none",
        "offsets": "offsets",
        "research_question": "Research Question",
        "source": "source",
        "source_scope": "Source Scope and Limitations",
        "source_scope_strict": (
            "This report is synthesized strictly from persisted claims, evidence, and "
            "citation spans, with no external facts."
        ),
        "uncertainty": "Conflicts / Uncertainty",
        "unresolved": "Unresolved Questions",
    }
