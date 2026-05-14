from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from services.orchestrator.app.research_quality import (
    answer_slots_for_query,
    build_slot_coverage_summary,
)


class GroundedLLMReportValidationError(ValueError):
    pass


_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_EVIDENCE_ANCHOR_NUM = re.compile(r"^e(\d+)$", re.IGNORECASE)


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
    support_type: str = "direct_evidence"


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
    include_ledger_debug_appendix: bool = False,
    original_user_question: str | None = None,
    research_plan: dict[str, Any] | None = None,
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
        original_user_question=original_user_question,
        research_plan=research_plan,
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
    rendered, llm_sidecar = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question=research_question,
        revision_no=revision_no,
        claims=grounded_claims,
        sources=sources,
        report_language=normalized_language,
        answer_relevant_claim_count=answer_relevant_claim_count,
        excluded_low_quality_claim_count=excluded_low_quality_claim_count,
        include_ledger_debug_appendix=include_ledger_debug_appendix,
    )
    metadata: dict[str, object] = {
        "mode": "llm_grounded",
        "status": "used",
        "provider": response.provider,
        "model": response.model,
        "raw_response_id": response.raw_response_id,
        "finish_reason": response.finish_reason,
        "usage": response.usage or {},
        "report_language": normalized_language,
        "input_claim_count": len(grounded_claims),
        "input_claim_evidence_count": sum(
            len(claim.support_evidence) + len(claim.contradict_evidence)
            for claim in grounded_claims
        ),
        "include_ledger_debug_appendix": include_ledger_debug_appendix,
        **llm_sidecar,
    }
    return GroundedLLMReport(rendered=rendered, metadata=metadata)


def _system_prompt(report_language: str) -> str:
    if is_chinese_report_language(report_language):
        return (
            "你是一个基于 OSINT 研究账本的资深调查报告撰写专家。\n"
            "Use Simplified Chinese for all user-visible report prose.\n"
            "你的任务是生成一份高质量、详尽的研究报告，字数要求在 3000 到 5000 字之间。\n"
            "你必须先回答原始用户问题 (original_user_question)。\n"
            "章节顺序应尽可能参考 planner_research_plan.answer_outline 或 subquestions，"
            "并做必要的展开和深入分析。\n"
            "【严禁幻觉】verified_claims 和引用摘录是唯一的可靠事实来源。\n"
            "planner_research_plan 仅用于构建结构和预期，不能作为事实依据。\n"
            "如果某个规划的子问题缺乏已验证的 claim，请将其列为“覆盖缺口”或“未解决项”。\n"
            "不要机械地列出 claim。将相关的 claim 组合成连贯、逻辑严密、"
            "细节丰富的长段落和深度分析。\n"
            "每个章节应以简明扼要的结论或范围说明开始，随后进行详细的论述。\n"
            "每个事实段落必须携带有效的 claim_ids 和 claim_evidence_ids。\n"
            "弱支持/混合/不支持/反驳的 claim 不能作为既定事实，仅能用于讨论不确定性。\n"
            "返回有效的 JSON 对象，不要使用 Markdown Fences 或任何解释性文字。\n"
            "JSON 结构要求：\n"
            "{\n"
            '  "title": string,\n'
            '  "question_alignment": {\n'
            '    "original_user_question": string,\n'
            '    "planner_intent": string,\n'
            '    "answered_parts": [string],\n'
            '    "partially_answered_parts": [string],\n'
            '    "unanswered_parts": [string]\n'
            "  },\n"
            '  "executive_summary": [item],\n'
            '  "sections": [{\n'
            '    "heading": string,\n'
            '    "related_planner_subquestions": [string],\n'
            '    "related_answer_slots": [string],\n'
            '    "items": [item]\n'
            "  }],\n"
            '  "uncertainties": [item],\n'
            '  "unresolved": [string],\n'
            '  "coverage_notes": [string]\n'
            "}\n"
            '其中 item 是 {"text": string, "claim_ids": [string], '
            '"claim_evidence_ids": [string], "citation_span_ids": [string], '
            '"support_type": "direct_evidence" | "inference" | "background" | "unsupported", '
            '"competitive_implication": boolean (可选)}。\n'
            "要求：执行摘要与关键结论章节的 item 必须使用 support_type=direct_evidence，"
            "且不得将 competitive_implication 设为 true（该字段仅用于不确定性章节且须配合 inference/background）。\n"
            "涉及厂商对比或竞争格局的表述须克制，避免无证据的强弱结论；需要推断时请标为 inference 或 background，"
            "并放在 uncertainties。\n"
            "coverage_notes 用于简述与 planner 子问题/槽位的覆盖关系（不得引入 bundle 外事实）。"
        )

    return (
        "You are an expert grounded research report writer for an OSINT research ledger.\n"
        "Your task is to generate a high-quality, comprehensive research report between "
        "3000 and 5000 words in length.\n"
        "You must answer the original_user_question first.\n"
        "Section order should follow planner_research_plan.answer_outline or subquestions "
        "where possible, expanding and providing deep analysis for each point.\n"
        "verified_claims and evidence excerpts are the ONLY factual sources. DO NOT introduce "
        "external facts.\n"
        "planner_research_plan provides structure and coverage expectations, but cannot justify "
        "factual claims.\n"
        "If a planner subquestion lacks verified claims, write it as a coverage gap or "
        "unresolved item.\n"
        "Do not mechanically list claims. Group related claims into readable paragraphs.\n"
        "Each section starts with a concise takeaway or scope sentence.\n"
        "Every factual block must carry valid claim_ids and claim_evidence_ids.\n"
        "Weak/mixed/unsupported/contradicted claims cannot become established findings.\n"
        "Return valid JSON only. Do not return Markdown or wrap JSON in prose.\n"
        "Required JSON shape:\n"
        "{\n"
        '  "title": string,\n'
        '  "question_alignment": {\n'
        '    "original_user_question": string,\n'
        '    "planner_intent": string,\n'
        '    "answered_parts": [string],\n'
        '    "partially_answered_parts": [string],\n'
        '    "unanswered_parts": [string]\n'
        "  },\n"
        '  "executive_summary": [item],\n'
        '  "sections": [{\n'
        '    "heading": string,\n'
        '    "related_planner_subquestions": [string],\n'
        '    "related_answer_slots": [string],\n'
        '    "items": [item]\n'
        "  }],\n"
        '  "uncertainties": [item],\n'
        '  "unresolved": [string],\n'
        '  "coverage_notes": [string]\n'
        "}\n"
        'Item is {"text": string, "claim_ids": [string], '
        '"claim_evidence_ids": [string], "citation_span_ids": [string], '
        '"support_type": "direct_evidence" | "inference" | "background" | "unsupported", '
        '"competitive_implication": boolean (optional)}.\n'
        "Rules: executive_summary and sections[].items must use support_type=direct_evidence and must "
        "not set competitive_implication to true (use uncertainties with inference/background for "
        "cautious competitive framing).\n"
        "coverage_notes summarizes alignment with planner subquestions/slots without introducing "
        "facts outside the bundle."
    )


def _build_grounding_bundle(
    *,
    task_id: UUID,
    research_question: str,
    revision_no: int,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
    report_language: str,
    original_user_question: str | None = None,
    research_plan: dict[str, Any] | None = None,
) -> dict[str, object]:
    slot_coverage_summary = _slot_coverage_rows(claims, research_question)
    return {
        "task_id": str(task_id),
        "revision_no": revision_no,
        "research_question": research_question,
        "original_user_question": original_user_question or research_question,
        "planner_research_plan": research_plan or {},
        "report_language": report_language,
        "rules": [
            "Use only verified_claims and their evidence excerpts for factual content.",
            "Use supported claims as settled findings only when support_level is not weak.",
            "Use mixed or unsupported claims only in uncertainty sections.",
            "Every item must include claim_ids and claim_evidence_ids from this bundle.",
            "Label support_type: direct_evidence for paraphrases tightly tied to excerpts; "
            "inference/background only when the prose goes beyond the excerpt in uncertainties.",
            "planner_research_plan provides structure and goals, but is NOT a factual source.",
            "If a planner goal lacks verified claims, report it as a coverage gap.",
            "Avoid aggressive competitive claims unless evidence-backed; use cautious wording.",
        ],
        "answer_slots": [
            {
                **slot.to_payload(),
                "status": _slot_status_for_bundle(slot.slot_id, claims=claims),
            }
            for slot in answer_slots_for_query(research_question)
        ],
        "slot_coverage_summary": slot_coverage_summary,
        "claims_by_slot": _claims_by_slot(claims),
        "source_role_diversity": _source_role_distribution(sources=sources, claims=claims),
        "verified_claims": [_serialize_claim(claim) for claim in claims],
        "sources": [
            {
                "source_document_id": str(source.source_document_id),
                "domain": source.domain,
                "title": source.title,
                "canonical_url": source.canonical_url,
                "source_role": source.source_role,
                "source_intent": source.source_intent,
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
        "slot_ids": list(claim.slot_ids),
        "evidence_kind": claim.evidence_kind,
        "deployment_evidence_excerpt": claim.deployment_evidence_excerpt,
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
        "source_role": evidence.source_role,
        "source_intent": evidence.source_intent,
        "chunk_no": evidence.chunk_no,
        "start_offset": evidence.start_offset,
        "end_offset": evidence.end_offset,
        "excerpt": evidence.excerpt,
        "relation_detail": evidence.relation_detail,
        "support_level": evidence.support_level,
        "reasons": list(evidence.reasons),
    }


def _claims_by_slot(claims: list[ReportClaimItem]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for claim in claims:
        for slot_id in claim.slot_ids:
            grouped.setdefault(slot_id, []).append(str(claim.claim_id))
    return {slot_id: claim_ids for slot_id, claim_ids in sorted(grouped.items())}


def _source_role_distribution(
    *,
    sources: list[ReportSourceItem],
    claims: list[ReportClaimItem],
) -> dict[str, object]:
    source_roles: dict[str, int] = {}
    for source in sources:
        role = source.source_role or source.source_intent or "unknown"
        source_roles[role] = source_roles.get(role, 0) + 1

    evidence_roles: dict[str, int] = {}
    for claim in claims:
        for evidence in [*claim.support_evidence, *claim.contradict_evidence]:
            role = evidence.source_role or evidence.source_intent or "unknown"
            evidence_roles[role] = evidence_roles.get(role, 0) + 1

    return {
        "source_roles": dict(sorted(source_roles.items())),
        "evidence_roles": dict(sorted(evidence_roles.items())),
    }


def _norm_inline(value: str) -> str:
    return " ".join(value.split())


def _evidence_anchor_sort_key(anchor: str) -> int:
    match = _EVIDENCE_ANCHOR_NUM.match(anchor.strip())
    if match:
        return int(match.group(1))
    return 10**9


def _build_evidence_anchor_map(claims: list[ReportClaimItem]) -> dict[str, str]:
    anchors: dict[str, str] = {}
    counter = 1
    for claim in claims:
        for evidence in claim.support_evidence + claim.contradict_evidence:
            eid = str(evidence.claim_evidence_id)
            if eid not in anchors:
                anchors[eid] = f"e{counter}"
                counter += 1
    return anchors


def _footnote_citation_suffix(
    evidence_ids: tuple[str, ...],
    anchor_by_evidence_id: dict[str, str],
) -> str:
    parts: list[tuple[int, str]] = []
    seen: set[str] = set()
    for eid in evidence_ids:
        anchor = anchor_by_evidence_id.get(eid)
        if anchor and eid not in seen:
            seen.add(eid)
            parts.append((_evidence_anchor_sort_key(anchor), f"[^{anchor}]"))
    parts.sort(key=lambda item: item[0])
    return "".join(fragment for _, fragment in parts)


def _render_evidence_footnote_section(
    claims: list[ReportClaimItem],
    anchor_by_evidence_id: dict[str, str],
    labels: dict[str, Any],
) -> list[str]:
    if not anchor_by_evidence_id:
        return []
    evidence_rows: dict[str, tuple[ReportClaimItem, ReportEvidenceItem]] = {}
    for claim in claims:
        for evidence in claim.support_evidence + claim.contradict_evidence:
            eid = str(evidence.claim_evidence_id)
            if eid in anchor_by_evidence_id:
                evidence_rows[eid] = (claim, evidence)
    lines = ["", f"## {labels['evidence_footnotes']}", ""]
    ordered = sorted(
        anchor_by_evidence_id.items(),
        key=lambda item: _evidence_anchor_sort_key(item[1]),
    )
    for eid, anchor in ordered:
        row = evidence_rows.get(eid)
        if row is None:
            continue
        _, evidence = row
        url = evidence.canonical_url.strip()
        lines.append(
            f"[^{anchor}]: [{_norm_inline(evidence.domain)}](<{url}>) — "
            f"{labels['excerpt']}: \"{_norm_inline(evidence.excerpt)}\" — "
            f"{labels['footnote_trace']}: `{evidence.claim_evidence_id}`"
        )
        lines.append("")
    return lines


def _support_type_sidecar(
    *,
    executive_items: list[_GroundedItem],
    sections: list[tuple[str, list[_GroundedItem], tuple[str, ...]]],
    uncertainty_items: list[_GroundedItem],
) -> dict[str, object]:
    counts: dict[str, int] = {}

    def feed(items: list[_GroundedItem]) -> None:
        for grounded in items:
            counts[grounded.support_type] = counts.get(grounded.support_type, 0) + 1

    feed(executive_items)
    for _, sec, _ in sections:
        feed(sec)
    feed(uncertainty_items)
    return {"grounded_report_support_type_counts": counts}


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
    include_ledger_debug_appendix: bool,
) -> tuple[RenderedMarkdownReport, dict[str, object]]:
    labels = _labels(report_language)
    if is_chinese_report_language(report_language) and not _payload_contains_cjk(payload):
        raise GroundedLLMReportValidationError(
            "LLM report response did not follow the requested Chinese report language"
        )
    evidence_by_id, citation_by_evidence_id, claim_by_id, evidence_claim_id = _allowed_ids(claims)
    anchor_by_evidence_id = _build_evidence_anchor_map(claims)

    executive_items = _validated_items(
        payload.get("executive_summary"),
        claim_by_id=claim_by_id,
        evidence_by_id=evidence_by_id,
        citation_by_evidence_id=citation_by_evidence_id,
        evidence_claim_id=evidence_claim_id,
        allowed_statuses={"supported"},
        allow_weak_support=False,
        restrict_support_types=True,
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
        restrict_support_types=False,
    )
    if not executive_items and not any(items for _, items, _ in sections) and not uncertainty_items:
        raise GroundedLLMReportValidationError("LLM report JSON contained no grounded items")

    title = _string_or_none(payload.get("title")) or build_report_title(
        research_question,
        report_language=report_language,
    )
    beijing_tz = timezone(timedelta(hours=8))
    generation_time = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# {title}",
        "",
        labels["generated"].format(
            task_id=task_id, revision_no=revision_no, generation_time=generation_time
        ),
        "",
        f"## {labels['research_question']}",
        "",
        research_question,
        "",
    ]

    alignment = payload.get("question_alignment")
    has_alignment = isinstance(alignment, dict)
    coverage_raw = payload.get("coverage_notes")
    coverage_notes_clean = [
        note.strip()
        for note in (coverage_raw if isinstance(coverage_raw, list) else [])
        if isinstance(note, str) and note.strip()
    ]

    if has_alignment or coverage_notes_clean:
        lines.extend([f"## {labels['question_alignment']}", ""])
        if has_alignment:
            orig_q = _string_or_none(alignment.get("original_user_question"))
            if orig_q:
                lines.append(f"- **{labels['original_question_label']}**: {orig_q}")

            intent = _string_or_none(alignment.get("planner_intent"))
            if intent:
                lines.append(f"- **{labels['planner_intent_label']}**: {intent}")

            for key, label in [
                ("answered_parts", labels["answered_label"]),
                ("partially_answered_parts", labels["partially_answered_label"]),
                ("unanswered_parts", labels["unanswered_label"]),
            ]:
                parts = _string_list(alignment.get(key))
                if parts:
                    lines.append(f"- **{label}**:")
                    for part in parts:
                        lines.append(f"  - {part}")
        if coverage_notes_clean:
            lines.append(f"- **{labels['coverage_notes_label']}**:")
            for note in coverage_notes_clean:
                lines.append(f"  - {note}")
        lines.append("")

    lines.extend(
        [
            f"## {labels['executive_summary']}",
            "",
        ]
    )
    if executive_items:
        lines.extend(
            _render_grounded_items(
                executive_items, labels=labels, anchor_by_evidence_id=anchor_by_evidence_id
            )
        )
    else:
        lines.append(f"- {labels['no_supported_items']}")

    lines.extend(["", f"## {labels['key_findings']}", ""])
    if sections:
        for heading, items, planner_subs in sections:
            lines.extend([f"### {heading}", ""])

            if planner_subs:
                lines.append(f"_{labels['related_subs']}: {', '.join(planner_subs)}_")
                lines.append("")

            lines.extend(
                _render_grounded_items(
                    items, labels=labels, anchor_by_evidence_id=anchor_by_evidence_id
                )
            )
            lines.append("")
    else:
        lines.append(f"- {labels['no_section_items']}")

    deployment_slot_lines = _render_deployment_slot_sections(
        claims,
        research_question=research_question,
        labels=labels,
    )
    if deployment_slot_lines:
        lines.extend(["", f"## {labels['deployment_evidence']}", ""])
        lines.extend(deployment_slot_lines)

    lines.extend(["", f"## {labels['uncertainty']}", ""])
    if uncertainty_items:
        lines.extend(
            _render_grounded_items(
                uncertainty_items, labels=labels, anchor_by_evidence_id=anchor_by_evidence_id
            )
        )
    else:
        lines.append(f"- {labels['no_uncertainty_items']}")

    unresolved = [
        item.strip()
        for item in payload.get("unresolved", [])
        if isinstance(item, str) and item.strip()
    ]
    unresolved.extend(_coverage_gap_unresolved_items(claims, research_question, labels=labels))
    unresolved = list(dict.fromkeys(unresolved))
    lines.extend(["", f"## {labels['unresolved']}", ""])
    if unresolved:
        for item in unresolved[:8]:
            lines.append(f"- {item}")
    else:
        lines.append(f"- {labels['no_unresolved']}")

    source_domains = sorted({source.domain for source in sources})
    lines.extend(["", f"## {labels['source_scope']}", ""])
    if _render_has_inference_or_background(executive_items, sections, uncertainty_items):
        lines.append(f"- {labels['source_scope_mixed']}")
    else:
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

    if include_ledger_debug_appendix:
        lines.extend(["", f"## {labels['claim_mapping']}", ""])
        lines.extend(_render_claim_mapping(claims, labels=labels))

    lines.extend(_render_evidence_footnote_section(claims, anchor_by_evidence_id, labels=labels))

    markdown = "\n".join(lines).strip() + "\n"
    sidecar = _support_type_sidecar(
        executive_items=executive_items,
        sections=sections,
        uncertainty_items=uncertainty_items,
    )
    return (
        RenderedMarkdownReport(
            title=title,
            markdown=markdown,
            supported_count=sum(1 for claim in claims if claim.verification_status == "supported"),
            mixed_count=sum(1 for claim in claims if claim.verification_status == "mixed"),
            contradicted_count=sum(
                1 for claim in claims if claim.verification_status == "contradicted"
            ),
            unsupported_count=sum(
                1 for claim in claims if claim.verification_status == "unsupported"
            ),
            draft_count=0,
            answer_relevant_count=answer_relevant_claim_count,
            excluded_low_quality_count=excluded_low_quality_claim_count,
            synthesis_plan=None,
            critic_result=None,
            redundancy_clusters=None,
        ),
        sidecar,
    )


def _validated_sections(
    value: object,
    *,
    claim_by_id: dict[str, ReportClaimItem],
    evidence_by_id: dict[str, ReportEvidenceItem],
    citation_by_evidence_id: dict[str, str],
    evidence_claim_id: dict[str, str],
) -> list[tuple[str, list[_GroundedItem], tuple[str, ...]]]:
    if not isinstance(value, list):
        return []
    result: list[tuple[str, list[_GroundedItem], tuple[str, ...]]] = []
    for section in value:
        if not isinstance(section, dict):
            continue
        heading = _string_or_none(section.get("heading"))
        if heading is None:
            continue
        planner_subs = tuple(_string_list(section.get("related_planner_subquestions")))
        items = _validated_items(
            section.get("items"),
            claim_by_id=claim_by_id,
            evidence_by_id=evidence_by_id,
            citation_by_evidence_id=citation_by_evidence_id,
            evidence_claim_id=evidence_claim_id,
            allowed_statuses={"supported"},
            allow_weak_support=False,
            restrict_support_types=True,
        )
        if items:
            result.append((heading, items, planner_subs))
    return result


_VALID_SUPPORT_TYPES = frozenset({"direct_evidence", "inference", "background", "unsupported"})


def _normalize_support_type(raw: object) -> str:
    if isinstance(raw, str) and raw.strip():
        key = raw.strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {
            "direct": "direct_evidence",
            "evidence": "direct_evidence",
            "directevidence": "direct_evidence",
        }
        key = aliases.get(key, key)
        if key in _VALID_SUPPORT_TYPES:
            return key
    return "direct_evidence"


def _validated_items(
    value: object,
    *,
    claim_by_id: dict[str, ReportClaimItem],
    evidence_by_id: dict[str, ReportEvidenceItem],
    citation_by_evidence_id: dict[str, str],
    evidence_claim_id: dict[str, str],
    allowed_statuses: set[str],
    allow_weak_support: bool,
    restrict_support_types: bool,
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
        support_type = _normalize_support_type(item.get("support_type"))
        if restrict_support_types:
            if support_type != "direct_evidence":
                continue
            if item.get("competitive_implication") is True:
                continue
        result.append(
            _GroundedItem(
                text=text,
                claim_ids=claim_ids,
                claim_evidence_ids=evidence_ids,
                citation_span_ids=citation_ids,
                support_type=support_type,
            )
        )
    return result


def _render_has_inference_or_background(
    executive_items: list[_GroundedItem],
    sections: list[tuple[str, list[_GroundedItem], tuple[str, ...]]],
    uncertainty_items: list[_GroundedItem],
) -> bool:
    for grounded in executive_items:
        if grounded.support_type in {"inference", "background"}:
            return True
    for _, sec_items, _ in sections:
        for grounded in sec_items:
            if grounded.support_type in {"inference", "background"}:
                return True
    for grounded in uncertainty_items:
        if grounded.support_type in {"inference", "background"}:
            return True
    return False


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


def _render_deployment_slot_sections(
    claims: list[ReportClaimItem],
    *,
    research_question: str,
    labels: dict[str, Any],
) -> list[str]:
    slot_rows = _slot_coverage_rows(claims, research_question)
    if not any(str(row.get("slot_id", "")).startswith("deployment_") for row in slot_rows):
        return []

    lines: list[str] = []
    strong_claims = [
        claim
        for claim in claims
        if claim.verification_status == "supported" and claim.support_level != "weak"
    ]
    for slot in answer_slots_for_query(research_question):
        if not slot.slot_id.startswith("deployment_"):
            continue
        slot_label = _localized_slot_label(slot.label, labels=labels)
        lines.extend([f"### {slot_label}", ""])
        slot_claims = [claim for claim in strong_claims if slot.slot_id in set(claim.slot_ids)]
        if slot_claims:
            for claim in slot_claims:
                lines.extend(_render_deployment_claim_lines(claim, labels=labels))
        else:
            lines.append("- " + labels["deployment_slot_gap"].format(slot=slot_label))
        lines.append("")
    return lines


def _coverage_gap_unresolved_items(
    claims: list[ReportClaimItem],
    research_question: str,
    *,
    labels: dict[str, Any],
) -> list[str]:
    gaps: list[str] = []
    for row in _slot_coverage_rows(claims, research_question):
        slot_id = str(row.get("slot_id", ""))
        if not slot_id.startswith("deployment_"):
            continue
        if row.get("status") in {"missing", "weak"}:
            gaps.append(
                labels["deployment_coverage_gap"].format(
                    slot=_localized_slot_label(str(row.get("label") or slot_id), labels=labels),
                    status=row.get("status"),
                )
            )
    return gaps


def _slot_coverage_rows(
    claims: list[ReportClaimItem],
    research_question: str,
) -> list[dict[str, Any]]:
    return build_slot_coverage_summary(
        research_question,
        evidence_candidates=[],
        claim_rows=[
            {
                "claim_id": str(claim.claim_id),
                "verification_status": claim.verification_status,
                "slot_ids": list(claim.slot_ids),
                "source_document_id": (
                    str(claim.support_evidence[0].source_document_id)
                    if claim.support_evidence
                    else None
                ),
                "support_level": claim.support_level,
            }
            for claim in claims
        ],
    )


def _slot_status_for_bundle(slot_id: str, *, claims: list[ReportClaimItem]) -> str:
    for claim in claims:
        if (
            slot_id in set(claim.slot_ids)
            and claim.verification_status == "supported"
            and claim.support_level != "weak"
        ):
            return "covered"
    for claim in claims:
        if slot_id in set(claim.slot_ids) and claim.verification_status == "supported":
            return "weak"
    return "missing"


def _localized_slot_label(label: str, *, labels: dict[str, Any]) -> str:
    mapping = labels.get("slot_label_mapping")
    if isinstance(mapping, dict):
        value = mapping.get(label)
        if isinstance(value, str) and value:
            return value
    return label


def _render_deployment_claim_lines(
    claim: ReportClaimItem,
    *,
    labels: dict[str, Any],
) -> list[str]:
    source_urls = list(
        dict.fromkeys(evidence.canonical_url for evidence in claim.support_evidence[:2])
    )
    trace = f"({'; '.join(source_urls)})" if source_urls else ""
    code_block = _deployment_code_block_for_claim(claim)
    if code_block is None:
        return [f"- {claim.statement} {trace}"]
    return [
        f"- {_deployment_statement_intro(claim.statement, code_block=code_block)} {trace}",
        "",
        "```",
        code_block,
        "```",
    ]


def _deployment_code_block_for_claim(claim: ReportClaimItem) -> str | None:
    if claim.evidence_kind != "deployment_code_or_config":
        return None
    candidates = [
        _deployment_code_block_from_statement(claim.statement),
        claim.deployment_evidence_excerpt,
        *(evidence.excerpt for evidence in claim.support_evidence),
    ]
    code_blocks = [
        _strip_markdown_code_fence(candidate).strip()
        for candidate in candidates
        if candidate and candidate.strip()
    ]
    if code_blocks:
        return max(code_blocks, key=lambda item: len(re.sub(r"\s+", " ", item).strip()))
    return None


def _deployment_code_block_from_statement(statement: str) -> str | None:
    match = re.search(r":\s*`(.+)`\.$", statement, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def _strip_markdown_code_fence(value: str) -> str:
    lines = value.strip().splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith(("```", "~~~")):
        fence = lines[0].strip()[:3]
        if lines[-1].strip().startswith(fence):
            return "\n".join(lines[1:-1]).strip()
    return value.strip()


def _deployment_statement_intro(statement: str, *, code_block: str | None = None) -> str:
    normalized_statement = re.sub(r"\s+", " ", statement).strip()
    if code_block and len(normalized_statement) <= 320:
        return normalized_statement
    code_from_statement = _deployment_code_block_from_statement(statement)
    if (
        code_from_statement
        and code_block
        and re.sub(r"\s+", " ", code_from_statement).strip()
        == re.sub(r"\s+", " ", code_block).strip()
    ):
        return normalized_statement
    return statement.split(":", 1)[0].strip() + ":"


def _render_grounded_items(
    items: list[_GroundedItem],
    *,
    labels: dict[str, Any],
    anchor_by_evidence_id: dict[str, str],
) -> list[str]:
    lines: list[str] = []
    st_labels = labels.get("support_type_labels", {})
    if not isinstance(st_labels, dict):
        st_labels = {}
    for item in items:
        suffix = _footnote_citation_suffix(item.claim_evidence_ids, anchor_by_evidence_id)
        st_key = item.support_type if item.support_type in _VALID_SUPPORT_TYPES else "direct_evidence"
        st_human = st_labels.get(st_key) or st_key
        lines.append(f"- [{st_human}] {item.text}{suffix}")
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
                f' | {labels["excerpt"]}: "{evidence.excerpt}"'
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


def _payload_contains_cjk(payload: dict[str, Any]) -> bool:
    def iter_strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            strings: list[str] = []
            for nested_value in value.values():
                strings.extend(iter_strings(nested_value))
            return strings
        if isinstance(value, list):
            strings = []
            for item in value:
                strings.extend(iter_strings(item))
            return strings
        return []

    prose = " ".join(iter_strings(payload))
    return _CJK_PATTERN.search(prose) is not None


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


def _labels(report_language: str) -> dict[str, Any]:
    if is_chinese_report_language(report_language):
        return {
            "answered_label": "已回答部分",
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
            "coverage_notes_label": "覆盖与缺口备注",
            "deployment_coverage_gap": (
                "覆盖缺口：`{slot}` 当前为 `{status}`，没有可渲染的强支持证据。"
            ),
            "deployment_evidence": "部署证据覆盖",
            "deployment_slot_gap": "`{slot}` 暂无强支持命令或配置证据。",
            "evidence_sources": "带证据链接的来源文档：{count} 个；域名：{domains}。",
            "evidence_footnotes": "证据来源",
            "excluded_claims": "已排除低质量或偏离问题的 claim：{count}。",
            "excerpt": "摘录",
            "footnote_trace": "追溯键",
            "executive_summary": "执行摘要",
            "generated": (
                "_由 grounded LLM report writer 基于已持久化证据在 revision `{revision_no}` 生成。"
                "生成时间：{generation_time} (北京时间)_"
            ),
            "key_findings": "关键结论",
            "llm_generation": (
                "LLM 只接收已验证 claim、证据记录和 citation span 摘录；"
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
            "original_question_label": "原始问题",
            "partially_answered_label": "部分回答部分",
            "planner_intent_label": "规划意图",
            "question_alignment": "问题对齐与覆盖",
            "related_subs": "关联子问题",
            "research_question": "研究问题",
            "source": "source",
            "source_scope": "来源范围与限制",
            "source_scope_mixed": (
                "执行摘要与关键结论中的条目均标注为直接证据范围内表述；不确定性章节可含推断/背景类说明。"
                "全文仍由已持久化的 claim、evidence 与 citation span 经 id 校验生成，未引入未校验的外部事实。"
            ),
            "source_scope_strict": (
                "本报告严格由已持久化的 claim、evidence 和 citation span 综合，不使用外部事实。"
            ),
            "slot_label_mapping": {
                "Prerequisites": "前置条件",
                "Docker run / Docker Compose": "Docker run / Docker Compose",
                "Volumes": "卷挂载",
                "Ports": "端口",
                "Configuration": "配置",
                "Security": "安全",
                "Troubleshooting": "故障排查",
                "Update / maintenance": "更新 / 维护",
            },
            "support_type_labels": {
                "direct_evidence": "直接证据",
                "inference": "推断",
                "background": "背景",
                "unsupported": "无直接支持",
            },
            "unanswered_label": "未回答部分",
            "uncertainty": "冲突 / 不确定性",
            "unresolved": "未解决问题",
        }
    return {
        "answered_label": "Answered parts",
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
        "coverage_notes_label": "Coverage notes",
        "deployment_coverage_gap": (
            "Coverage gap: `{slot}` is currently `{status}` with no renderable strongly "
            "supported evidence."
        ),
        "deployment_evidence": "Deployment Evidence Coverage",
        "deployment_slot_gap": (
            "No strongly supported command or configuration evidence for `{slot}`."
        ),
        "evidence_sources": "Evidence-linked source documents: {count} across domains: {domains}.",
        "evidence_footnotes": "Evidence footnotes",
        "excluded_claims": "Excluded low-quality or off-query claims: {count}.",
        "excerpt": "excerpt",
        "footnote_trace": "Trace key",
        "executive_summary": "Executive Summary",
        "generated": (
            "_Generated by grounded LLM report writer from persisted evidence at revision "
            "`{revision_no}`. Generated at: {generation_time} (Beijing Time)_"
        ),
        "key_findings": "Key Findings",
        "llm_generation": (
            "The LLM received only verified claims, claim evidence records, and citation "
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
        "original_question_label": "Original user question",
        "partially_answered_label": "Partially answered parts",
        "planner_intent_label": "Planner intent",
        "question_alignment": "Question Alignment and Coverage",
        "related_subs": "Related subquestions",
        "research_question": "Research Question",
        "source": "source",
        "source_scope": "Source Scope and Limitations",
        "source_scope_mixed": (
            "Executive summary and key-finding bullets are restricted to direct_evidence; uncertainty "
            "sections may include inference or background interpretation. The narrative is still "
            "assembled only from persisted claims, evidence, and citation spans with id validation."
        ),
        "source_scope_strict": (
            "This report is synthesized strictly from persisted claims, evidence, and "
            "citation spans, with no external facts."
        ),
        "slot_label_mapping": {
            "Prerequisites": "Prerequisites",
            "Docker run / Docker Compose": "Docker run / Docker Compose",
            "Volumes": "Volumes",
            "Ports": "Ports",
            "Configuration": "Configuration",
            "Security": "Security",
            "Troubleshooting": "Troubleshooting",
            "Update / maintenance": "Update / maintenance",
        },
        "support_type_labels": {
            "direct_evidence": "Direct evidence",
            "inference": "Inference",
            "background": "Background",
            "unsupported": "Unsupported",
        },
        "unanswered_label": "Unanswered parts",
        "uncertainty": "Conflicts / Uncertainty",
        "unresolved": "Unresolved Questions",
    }
