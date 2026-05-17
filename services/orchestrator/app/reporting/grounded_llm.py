from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from json import JSONDecodeError
from typing import Any
from uuid import UUID

from urllib.parse import urlsplit

from services.orchestrator.app.llm import LLMProvider, LLMRequest
from services.orchestrator.app.reporting.language import (
    is_chinese_report_language,
    normalize_report_language,
)
from services.orchestrator.app.reporting.evidence_suitability import github_readme_logical_group_key
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
from services.orchestrator.app.query_intent_signals import (
    detect_report_archetype,
    query_is_news_or_recency_update,
    query_requests_explanation_comparison_template,
)
from services.orchestrator.app.reporting.survey_cards import build_method_survey_cards
from services.orchestrator.app.research_quality.source_intent import source_intent_report_core_eligible


class GroundedLLMReportValidationError(ValueError):
    pass


_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_EVIDENCE_ANCHOR_NUM = re.compile(r"^e(\d+)$", re.IGNORECASE)
_TIME_WINDOW_HINT_RE = re.compile(
    r"(近\s*\d+\s*(天|周|月|年)|最近\s*\d+|过去\s*\d+\s*(天|周|月)|今年(以来)?|近30天|"
    r"last\s+\d+\s+days|past\s+\d+\s+days|past\s+month|this\s+year)",
    re.IGNORECASE,
)
_LOW_SIGNAL_APPENDIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bis a (director|scientist|engineer|manager|advocate)\b", re.I),
    re.compile(r"\bdeveloper advocate\b", re.I),
    re.compile(r"\b(product marketing manager|senior deep learning scientist)\b", re.I),
    re.compile(r"\brelated posts\b", re.I),
    re.compile(r"\bsubscribe\b", re.I),
    re.compile(r"\bshare on\b", re.I),
    re.compile(r"\bcopyright\b", re.I),
    re.compile(r"\ball rights reserved\b", re.I),
    re.compile(r"\bnewsletter\b", re.I),
    re.compile(r"\bcookie policy\b", re.I),
    re.compile(r"\bfollow us\b", re.I),
)


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
    report_archetype: str | None = None,
) -> GroundedLLMReport:
    normalized_language = normalize_report_language(report_language)
    grounded_claims = _grounded_claims(claims)
    if not grounded_claims:
        raise GroundedLLMReportValidationError(
            "grounded LLM report writer requires at least one verified claim with evidence"
        )

    plan_intent_hint: str | None = None
    plan_report_arch: str | None = None
    if isinstance(research_plan, dict):
        raw_pi = research_plan.get("intent")
        if isinstance(raw_pi, str) and raw_pi.strip():
            plan_intent_hint = raw_pi.strip()
        raw_ra = research_plan.get("report_archetype")
        if isinstance(raw_ra, str) and raw_ra.strip():
            plan_report_arch = raw_ra.strip()
    domain_list = [s.domain for s in sources if s.domain]
    effective_archetype = report_archetype or plan_report_arch or detect_report_archetype(
        research_question,
        plan_intent=plan_intent_hint,
        source_domains=domain_list,
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
        report_archetype=effective_archetype,
    )
    response = llm_provider.generate(
        LLMRequest(
            system_prompt=_system_prompt(
                normalized_language,
                research_question=research_question,
                research_plan=research_plan,
                report_archetype=effective_archetype,
            ),
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
        plan_intent=plan_intent_hint,
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
        "report_archetype": effective_archetype,
        **llm_sidecar,
    }
    return GroundedLLMReport(rendered=rendered, metadata=metadata)


def _zh_tech_compare_section_heading_hints(research_question: str) -> str:
    """Concrete section-title semantics tied to named entities in the user question (Chinese)."""
    tokens = _framework_tokens_from_question(research_question)
    if len(tokens) < 2:
        return ""
    primary = tokens[0]
    others = tokens[1:]
    others_join = " / ".join(others)
    others_list = "、".join(others)
    return (
        "【章节标题锚点】以下名称来自研究问题中的英文专名，请直接在 heading 中使用（可微调语序，"
        "但不得改成新闻/更新体例）：\n"
        f"  • 「{primary} 是什么」\n"
        f"  • 「{primary} 如何工作（机制与执行模型）」\n"
        f"  • 「{others_list} 的核心机制」\n"
        f"  • 「{primary} / {others_join} 核心差异」（本节必须包含 Markdown 对比表）\n"
        "  • 「适用场景与选型建议」\n"
        "  • 「证据不足与不确定性」\n"
    )


def _system_prompt(
    report_language: str,
    *,
    research_question: str = "",
    research_plan: dict[str, Any] | None = None,
    report_archetype: str = "general",
) -> str:
    plan_intent = None
    if isinstance(research_plan, dict):
        raw_intent = research_plan.get("intent")
        if isinstance(raw_intent, str):
            plan_intent = raw_intent
    rq = research_question or ""
    tech_compare = query_requests_explanation_comparison_template(rq, plan_intent=plan_intent)
    news_like = query_is_news_or_recency_update(rq, plan_intent=plan_intent)
    survey_like = report_archetype == "research_survey"

    common_zh = (
        "你是面向读者的深度调查/技术研究报告主笔；读者阅读的是最终 Markdown，"
        "而不是内部检索系统。\n"
        "Use Simplified Chinese for all user-visible report prose.\n"
        "【事实边界】输入 JSON 中的 verified_claims 及其 excerpt "
        "是唯一可当作事实写进正文的内容；"
        "planner_research_plan、answer_slots、slot_coverage_summary 仅帮助你理解调查范围，"
        "绝不能当作事实来源写入正文。\n"
        "【综合写作】禁止机械复述内部记录条目；要用自然段落给出判断→依据→影响/对比→限制/不确定性的顺序；"
        "不得引入摘录之外的新事实。\n"
        "【读者友好】正文中禁止出现内部字段名、内部角色名、调试口吻或模板套话。\n"
        "【脚注】每个段落仅在关键判断句末集中引用脚注；避免一句末尾堆叠过多脚注。\n"
        "【摘要】executive_summary 共 2–4 条短段落：概括证据支持的结论边界；"
        "不得与后文某一节逐字重复同一 claim 文本；若与正文重复，请改写为更高层概括。\n"
        "【跨框架/竞品表述】若证据来自某一框架官方站点/README，却评价另一框架的性能、复杂度或缺点，"
        "只能写入 uncertainties，support_type 用 inference/background，并标记 competitive_implication=true；"
        "不得写入 executive_summary 或 sections 的正文。\n"
        "【对比表】若研究问题要求多方案对比，在 sections 中安排一节，使用 Markdown 管道表格；"
        "表头为「维度」及各方案名称（英文专名列名与问题中出现的顺序一致，例如 LangGraph、AutoGen、CrewAI）；"
        "每个非空单元格尽量在句末附带脚注引用；"
        "证据不足的单元格写「当前证据不足」。\n"
        "【来源边界】source_intent 为 github_topic、search_or_topic_aggregate、搜索聚合页或 SEO 聚合页的材料只能作为背景线索，"
        "不得单独支撑客观核心结论；若仅有此类来源，应在 uncertainties 说明证据不足。\n"
        "unresolved 为短句列表，用读者语言描述尚缺材料；禁止写工程内部词。\n"
        "可选字段 question_alignment、coverage_notes、related_planner_subquestions、related_answer_slots "
        "请留空或省略。\n"
        "每个带事实的段落必须携带有效的 claim_ids、claim_evidence_ids、citation_span_ids；"
        "JSON 键名仅限机器解析，不得出现在 text 字段。\n"
        "返回有效 JSON 对象，不要使用 Markdown 代码围栏或解释性前后文。\n"
        "JSON 结构：\n"
        "{\n"
        '  "title": string,\n'
        '  "executive_summary": [item],\n'
        '  "sections": [{"heading": string, "items": [item]}],\n'
        '  "uncertainties": [item],\n'
        '  "unresolved": [string]\n'
        "}\n"
        'item 为 {"text": string, "claim_ids": [string], "claim_evidence_ids": [string], '
        '"citation_span_ids": [string], '
        '"support_type": "direct_evidence" | "inference" | "background" | "unsupported", '
        '"competitive_implication": boolean (可选)}。\n'
        "规则：executive_summary 与 sections[].items 必须使用 support_type=direct_evidence，"
        "且不得将 competitive_implication 设为 true；"
        "竞争性判断只能放在 uncertainties。\n"
    )

    common_en = (
        "You write reader-facing investigative or technical research reports; the Markdown is "
        "for human readers, not for internal ledger operators.\n"
        "Factual content may come ONLY from verified_claims and their excerpts in the bundle. "
        "planner_research_plan, answer_slots, and slot_coverage_summary explain scope but are "
        "NOT factual sources.\n"
        "Synthesize with judgment, evidence-backed support, limits, and uncertainty. "
        "Do not paste internal field names into prose.\n"
        "Footnotes: cluster citations at key sentences.\n"
        "executive_summary: 2–4 short paragraphs; do not repeat the exact same claim wording "
        "later in a section; paraphrase at a higher level if needed.\n"
        "Cross-vendor criticism: if evidence comes from one vendor’s README/docs but the claim "
        "targets another named product negatively, put it only in uncertainties with "
        "inference/background and competitive_implication=true; never in executive_summary or "
        "sections items.\n"
        "Comparison tables: for multi-option comparison questions, include one Markdown pipe "
        "table section; header row starts with a dimension column then each named option in the "
        "same order as the research question (e.g. LangGraph, AutoGen, CrewAI); cite footnotes "
        "in cells; use “insufficient evidence in bundle” when needed.\n"
        "github_topic / search_or_topic_aggregate / search-aggregator / SEO-aggregator sources are discovery-only and must "
        "not be the sole anchor for objective core conclusions.\n"
        "unresolved is plain language about missing material; never mention slots or claim counts.\n"
        "Optional keys question_alignment, coverage_notes, related_planner_subquestions, "
        "related_answer_slots: omit or empty.\n"
        "Every factual paragraph must include valid claim_ids, claim_evidence_ids, and "
        "citation_span_ids.\n"
        "Return valid JSON only. No Markdown fences or surrounding prose.\n"
        "JSON shape:\n"
        "{\n"
        '  "title": string,\n'
        '  "executive_summary": [item],\n'
        '  "sections": [{"heading": string, "items": [item]}],\n'
        '  "uncertainties": [item],\n'
        '  "unresolved": [string]\n'
        "}\n"
        'Item: {"text": string, "claim_ids": [string], "claim_evidence_ids": [string], '
        '"citation_span_ids": [string], '
        '"support_type": "direct_evidence" | "inference" | "background" | "unsupported", '
        '"competitive_implication": boolean (optional)}.\n'
        "Rules: executive_summary and sections[].items must use support_type=direct_evidence and "
        "must not set competitive_implication to true; competitive framing belongs in uncertainties.\n"
    )

    if is_chinese_report_language(report_language):
        if survey_like:
            return common_zh + (
                "【体例】研究综述（research_survey）：写作顺序为先脉络再方法；禁止逐句复述 excerpt；"
                "禁止虚构论文题目、作者、会议、年份或实验数值。\n"
                "【方法卡片】若 bundle 提供 method_survey_cards：每个方法/材料簇对应一节「方法深读」，"
                "标题使用卡片 display_name / paper_title（若为空则用来源域名），不得硬编码示例专名。\n"
                "【结构】sections 至少 7 节，heading 必须覆盖下列语义（可微调措辞，不得使用新闻四段式标题）：\n"
                "  1. 绪论（问题与材料边界）\n"
                "  2. 研究脉络（主题演进与代表材料，仅事实句进正文）\n"
                "  3. 方法深读（按 method_survey_cards 分节；每节先机制/定义事实，再写应用边界）\n"
                "  4. 应用与实践要点\n"
                "  5. 横向对比（必须包含 Markdown 管道对比表；列名来自卡片/研究问题中的对象名；"
                "证据不足写「当前证据不足」）\n"
                "  6. 综合判断与展望（超出摘录的组织性归纳须放在 uncertainties，"
                "support_type=inference/background，并在 prose 明确写出「综合判断」「推断」「可能」等读者标签）\n"
                "  7. 附录（材料范围、未覆盖子问题、脚注说明；不写内部字段名）\n"
                "uncertainties：mixed/unsupported/contradicted、跨来源竞争性表述、以及证据缺口。\n"
                "禁止使用「核心发现/更新主线」「主要更新内容」「影响分析」「重大变更与风险」等新闻章节标题。\n"
            )
        if tech_compare:
            return common_zh + _zh_tech_compare_section_heading_hints(rq) + (
                "【结构】sections 至少 6 节；heading 必须覆盖下列语义（可微调措辞但不得改成新闻/更新体例）：\n"
                "  1. 摘要（若与 executive_summary 重复，请让 sections 标题避免再写「摘要」；"
                "可用「阅读指引」替代）\n"
                "  2. 主体对象是什么\n"
                "  3. 主体对象如何工作（机制与执行模型）\n"
                "  4. 对比对象的核心机制（对官方文档可核验部分）\n"
                "  5. 核心差异对比（含 Markdown 对比表一节）\n"
                "  6. 适用场景与选型建议\n"
                "  7. 证据不足与不确定性\n"
                "uncertainties 用于 mixed/unsupported/contradicted、竞争性观点、以及证据缺口。\n"
                "禁止使用「核心发现/更新主线」「主要更新内容」「影响分析」「重大变更与风险」等新闻章节标题。\n"
            )
        if news_like:
            return common_zh + (
                "【时间口径】若研究问题包含“近30天/今年/最近”等窗口：摘要或第一节必须交代证据时间边界。\n"
                "【结构】sections 必须恰好 4 节，且 heading 必须逐字使用下列标题：\n"
                "  1. 一、核心发现/更新主线\n"
                "  2. 二、主要更新内容\n"
                "  3. 三、影响分析\n"
                "  4. 四、重大变更与风险\n"
                "uncertainties 写在「五、证据不足与待确认事项」语义下。\n"
            )
        return common_zh + (
            "【结构】sections 共 3–5 节；按研究问题自拟中文标题（不要用新闻更新四段式），"
            "例如：结论要点、机制与实现、对比与边界（如适用）、风险与限制、证据范围说明。\n"
            "uncertainties 用于弱证据、矛盾或证据缺口。\n"
        )

    if survey_like:
        return common_en + (
            "Template: research_survey. Lead with problem framing and thematic thread before methods. "
            "Do not parrot excerpts; do not invent paper titles, venues, years, or numeric results.\n"
            "If method_survey_cards are present in the bundle, allocate one section per card for "
            "method deep dives; headings must use each card’s display_name / paper_title when "
            "available (otherwise the source domain).\n"
            "Structure: at least seven sections whose headings cover: introduction and evidence "
            "boundary; research thread; per-card method deep dives; applications; a mandatory "
            "Markdown comparison table (use “insufficient evidence in bundle” for empty cells); "
            "synthesis and outlook (organizational synthesis beyond excerpts belongs in uncertainties "
            "with support_type inference/background and explicit reader-facing labels like "
            "“inference” / “may”); appendix for scope notes.\n"
            "uncertainties: mixed/unsupported/contradicted, competitive cross-vendor claims, and gaps.\n"
            "Do NOT use the four-part news/update headline template.\n"
        )
    if tech_compare:
        return common_en + (
            "Structure: provide at least six sections whose headings cover these intents "
            "(wording may vary slightly, but must NOT use news/update templates): "
            "what the primary subject is; how it works; counterpart core mechanisms; "
            "key differences (include a Markdown comparison table section); fit-for-purpose guidance; "
            "insufficient evidence and uncertainty.\n"
            "uncertainties: mixed/unsupported/contradicted, competitive claims, and gaps.\n"
            "Do NOT use headings like “Core findings / update narrative”, “Main updates”, "
            "or “Major changes and risks”.\n"
        )
    if news_like:
        return common_en + (
            "Recency: if the research question encodes a time window, state the evidence time "
            "boundary in the executive summary or first section.\n"
            "Structure: sections MUST contain exactly four objects with these exact headings:\n"
            "  1. I. Core findings / narrative\n"
            "  2. II. Main updates\n"
            "  3. III. Impact analysis\n"
            "  4. IV. Major changes and risks\n"
            "uncertainties belong semantically under “V. Insufficient evidence and open questions”.\n"
        )
    return common_en + (
        "Structure: use 3–5 sections with headings tailored to the research question (not the "
        "four-part news/update template unless the question is time-sensitive news).\n"
        "uncertainties: weak, contradictory, or missing-evidence material.\n"
        "Target length remains roughly 3000–5000 words when the bundle supports it.\n"
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
    report_archetype: str = "general",
) -> dict[str, object]:
    slot_coverage_summary = _slot_coverage_rows(claims, research_question)
    bundle: dict[str, object] = {
        "task_id": str(task_id),
        "revision_no": revision_no,
        "research_question": research_question,
        "original_user_question": original_user_question or research_question,
        "planner_research_plan": research_plan or {},
        "report_language": report_language,
        "report_archetype": report_archetype,
        "rules": [
            "Use only verified_claims and their evidence excerpts for factual content.",
            "Use supported claims as settled findings only when support_level is not weak.",
            "Use mixed or unsupported claims only in uncertainty sections.",
            "Every item must include claim_ids and claim_evidence_ids from this bundle.",
            "support_type is machine metadata only; reader prose must never echo those labels.",
            "planner_research_plan provides structure and goals, but is NOT a factual source.",
            "If material is missing for part of the scope, describe the gap in plain language "
            "for readers (no slot or planner jargon).",
            "Avoid aggressive competitive claims unless evidence-backed; use cautious wording.",
            *(
                [
                    "When the research question encodes a recency window "
                    "(last N days / this year / 最近): "
                    "state the evidence time boundary in the executive summary or first section; "
                    "do not claim all material was published inside the window unless excerpt "
                    "timestamps support it—prefer explicit uncertainty when dates are missing.",
                ]
                if _research_question_requests_recency(research_question)
                else []
            ),
            "Impact analysis must separate vendor claims from independent benchmarks; if only "
            "official language exists, hedge with 'may suggest' and state what third-party or "
            "quantitative evidence is still missing.",
        ],
        "temporal_constraints": {
            "recency_detected": _research_question_requests_recency(research_question),
        },
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
    if report_archetype == "research_survey":
        bundle["method_survey_cards"] = [c.to_payload() for c in build_method_survey_cards(claims, sources)]
    return bundle


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


def _research_question_requests_recency(text: str) -> bool:
    return bool(_TIME_WINDOW_HINT_RE.search(text or ""))


def _is_low_signal_appendix_excerpt(text: str) -> bool:
    stripped = (text or "").strip()
    if len(stripped) < 12:
        return True
    for pattern in _LOW_SIGNAL_APPENDIX_PATTERNS:
        if pattern.search(stripped):
            return True
    low = stripped.lower()
    if low.startswith("http") and len(stripped) < 48:
        return True
    return False


def _filter_appendix_excerpts(excerpts: list[str]) -> list[str]:
    kept: list[str] = []
    for excerpt in excerpts:
        if _is_low_signal_appendix_excerpt(excerpt):
            continue
        if excerpt not in kept:
            kept.append(excerpt)
    return kept


def _collect_referenced_evidence_ids(
    executive_items: list[_GroundedItem],
    sections: list[tuple[str, list[_GroundedItem]]],
    uncertainty_items: list[_GroundedItem],
) -> set[str]:
    referenced: set[str] = set()
    for item in executive_items:
        referenced.update(item.claim_evidence_ids)
    for _, sec_items in sections:
        for item in sec_items:
            referenced.update(item.claim_evidence_ids)
    for item in uncertainty_items:
        referenced.update(item.claim_evidence_ids)
    return referenced


_CITE_STRIP_RE = re.compile(r"\s*\[\^e\d+\]\s*$", re.IGNORECASE)


def _normalize_item_text_for_dedupe(text: str) -> str:
    stripped = (text or "").strip()
    stripped = _CITE_STRIP_RE.sub("", stripped).lower()
    stripped = re.sub(r"[\s\u3000]+", " ", stripped)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", stripped)


def _item_text_dedupe_key(item: _GroundedItem) -> str:
    if item.claim_ids:
        return "c:" + ",".join(item.claim_ids)
    return "t:" + _normalize_item_text_for_dedupe(item.text)


def _dedupe_sections_cross_section(
    sections: list[tuple[str, list[_GroundedItem]]],
) -> list[tuple[str, list[_GroundedItem]]]:
    """Remove later section items that duplicate an earlier section (claim_id or normalized text)."""
    seen: set[str] = set()
    out: list[tuple[str, list[_GroundedItem]]] = []
    for heading, sec_items in sections:
        kept: list[_GroundedItem] = []
        for item in sec_items:
            key = _item_text_dedupe_key(item)
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)
        out.append((heading, kept))
    return out


def _dedupe_executive_against_sections(
    executive_items: list[_GroundedItem],
    sections: list[tuple[str, list[_GroundedItem]]],
) -> tuple[list[_GroundedItem], list[tuple[str, list[_GroundedItem]]]]:
    seen: set[str] = {_item_text_dedupe_key(it) for it in executive_items}
    new_sections: list[tuple[str, list[_GroundedItem]]] = []
    for heading, sec_items in sections:
        kept: list[_GroundedItem] = []
        for item in sec_items:
            key = _item_text_dedupe_key(item)
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)
        new_sections.append((heading, kept))
    return executive_items, new_sections


def _filter_core_only_evidence_ids(
    evidence_ids: tuple[str, ...],
    *,
    evidence_by_id: dict[str, ReportEvidenceItem],
) -> tuple[str, ...]:
    filtered = tuple(
        eid
        for eid in evidence_ids
        if eid in evidence_by_id
        and source_intent_report_core_eligible(evidence_by_id[eid].source_intent)
    )
    return filtered if filtered else evidence_ids


def _github_repo_brand_from_url(url: str) -> str | None:
    parsed = urlsplit(url)
    host = (parsed.netloc or "").lower().rstrip(".")
    parts = [p for p in (parsed.path or "").strip("/").split("/") if p]
    if host == "github.com" and len(parts) >= 2:
        return parts[1].lower()
    if host == "raw.githubusercontent.com" and len(parts) >= 2:
        return parts[1].lower()
    return None


def _compact_brand_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


_COMPETITIVE_NEGATIVE_MARKERS: tuple[str, ...] = (
    "worse",
    "slower",
    "too much",
    "boilerplate",
    "more complex",
    "disadvantage",
    "advantages over",
    "better than",
    "outperform",
    "superior to",
    "compared to",
    "performance advantages",
    "complex state",
    "缺点",
    "性能",
    "样板",
    "复杂",
    "不如",
    "劣势",
)


def _statement_has_competitive_negative_tone(statement: str) -> bool:
    lower = (statement or "").lower()
    return any(marker in lower for marker in _COMPETITIVE_NEGATIVE_MARKERS)


def _framework_tokens_from_question(question: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\b[A-Z][A-Za-z0-9.-]{1,40}\b", question)))


_TECH_COMPARE_ROW_LABELS_ZH: tuple[str, ...] = (
    "核心抽象",
    "编排方式",
    "状态管理",
    "多智能体协作",
    "长流程/可恢复",
    "适合场景",
    "优势",
    "代价或限制",
)
_TECH_COMPARE_ROW_LABELS_EN: tuple[str, ...] = (
    "Core abstractions",
    "Orchestration",
    "State management",
    "Multi-agent collaboration",
    "Long-running / resume",
    "Fit-for-purpose",
    "Strengths",
    "Costs / limits",
)


def _norm_table_cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _short_claim_cell_snippet(statement: str, *, max_chars: int = 72) -> str:
    one_line = _norm_inline((statement or "").strip())
    if len(one_line) <= max_chars:
        return one_line
    return one_line[: max_chars - 1].rstrip() + "…"


def _pick_claim_evidence_for_entity_column(
    claims: list[ReportClaimItem],
    entity: str,
) -> tuple[ReportClaimItem, ReportEvidenceItem] | None:
    needle = entity.lower()
    for claim in claims:
        if needle not in (claim.statement or "").lower():
            continue
        preferred = [
            ev
            for ev in claim.support_evidence
            if source_intent_report_core_eligible(ev.source_intent)
        ]
        if preferred:
            return claim, preferred[0]
        if claim.support_evidence:
            return claim, claim.support_evidence[0]
    return None


@dataclass(frozen=True)
class _TechComparisonTableSpec:
    heading: str
    dim_labels: tuple[str, ...]
    entities: tuple[str, ...]
    column_snippet: tuple[str | None, ...]
    column_eid: tuple[str | None, ...]
    flat_eid_order: tuple[str, ...]


def _maybe_build_tech_comparison_table_spec(
    *,
    claims: list[ReportClaimItem],
    research_question: str,
    report_language: str,
    plan_intent: str | None,
) -> _TechComparisonTableSpec | None:
    if not query_requests_explanation_comparison_template(
        research_question, plan_intent=plan_intent
    ):
        return None
    entities = tuple(_framework_tokens_from_question(research_question))[:5]
    if len(entities) < 2:
        return None
    zh = is_chinese_report_language(report_language)
    dim_labels = _TECH_COMPARE_ROW_LABELS_ZH if zh else _TECH_COMPARE_ROW_LABELS_EN
    snippets: list[str | None] = []
    eids: list[str | None] = []
    flat_order: list[str] = []
    seen_flat: set[str] = set()
    for ent in entities:
        picked = _pick_claim_evidence_for_entity_column(claims, ent)
        if picked is None:
            snippets.append(None)
            eids.append(None)
            continue
        claim, evidence = picked
        eid = str(evidence.claim_evidence_id)
        snippets.append(_short_claim_cell_snippet(claim.statement))
        eids.append(eid)
        if eid not in seen_flat:
            seen_flat.add(eid)
            flat_order.append(eid)
    heading = (
        "结构化技术对比（证据绑定）"
        if zh
        else "Structured technical comparison (evidence-bound)"
    )
    return _TechComparisonTableSpec(
        heading=heading,
        dim_labels=dim_labels,
        entities=entities,
        column_snippet=tuple(snippets),
        column_eid=tuple(eids),
        flat_eid_order=tuple(flat_order),
    )


def _format_tech_comparison_table_markdown(
    spec: _TechComparisonTableSpec,
    *,
    anchor_by_evidence_id: dict[str, str],
    report_language: str,
) -> str:
    zh = is_chinese_report_language(report_language)
    empty = "当前证据不足" if zh else "Insufficient evidence in bundle"
    see_above = "（见上）" if zh else "(see above)"
    dim_col = "维度" if zh else "Dimension"
    header = "| " + dim_col + " | " + " | ".join(spec.entities) + " |"
    sep = "| " + " | ".join(["---"] * (len(spec.entities) + 1)) + " |"
    lines = [header, sep]
    for row_idx, dim in enumerate(spec.dim_labels):
        cells: list[str] = []
        for col_idx, _ent in enumerate(spec.entities):
            eid = spec.column_eid[col_idx]
            if not eid:
                cells.append(empty)
                continue
            anchor = anchor_by_evidence_id.get(eid)
            if not anchor:
                cells.append(empty)
                continue
            sn = spec.column_snippet[col_idx] or ""
            if row_idx == 0:
                body = f"{_norm_table_cell(sn)} [^{anchor}]"
            else:
                body = f"{see_above} [^{anchor}]"
            cells.append(_norm_table_cell(body))
        lines.append("| " + _norm_table_cell(dim) + " | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _is_competitive_cross_vendor_framing(
    claim: ReportClaimItem,
    evidence: ReportEvidenceItem,
    research_question: str,
) -> bool:
    repo_brand = _github_repo_brand_from_url(evidence.canonical_url)
    if not repo_brand:
        return False
    if not _statement_has_competitive_negative_tone(claim.statement):
        return False
    repo_c = _compact_brand_token(repo_brand)
    haystack = _compact_brand_token(claim.statement or "")
    statement_lower = (claim.statement or "").lower()
    for token in _framework_tokens_from_question(research_question):
        token_c = _compact_brand_token(token)
        if not token_c or token_c == repo_c:
            continue
        if token.lower() in statement_lower or token_c in haystack:
            return True
    return False


def _item_has_competitive_cross_vendor_framing(
    item: _GroundedItem,
    *,
    claim_by_id: dict[str, ReportClaimItem],
    evidence_by_id: dict[str, ReportEvidenceItem],
    research_question: str,
) -> bool:
    for cid in item.claim_ids:
        claim = claim_by_id.get(cid)
        if claim is None:
            continue
        for eid in item.claim_evidence_ids:
            evidence = evidence_by_id.get(eid)
            if evidence is None:
                continue
            if _is_competitive_cross_vendor_framing(claim, evidence, research_question):
                return True
    return False


def _migrate_competitive_core_items(
    executive_items: list[_GroundedItem],
    sections: list[tuple[str, list[_GroundedItem]]],
    uncertainty_items: list[_GroundedItem],
    *,
    claim_by_id: dict[str, ReportClaimItem],
    evidence_by_id: dict[str, ReportEvidenceItem],
    research_question: str,
) -> tuple[list[_GroundedItem], list[tuple[str, list[_GroundedItem]]], list[_GroundedItem]]:
    exec_out: list[_GroundedItem] = []
    moved: list[_GroundedItem] = []
    for item in executive_items:
        if _item_has_competitive_cross_vendor_framing(
            item,
            claim_by_id=claim_by_id,
            evidence_by_id=evidence_by_id,
            research_question=research_question,
        ):
            moved.append(
                _GroundedItem(
                    text=item.text,
                    claim_ids=item.claim_ids,
                    claim_evidence_ids=item.claim_evidence_ids,
                    citation_span_ids=item.citation_span_ids,
                    support_type="inference",
                )
            )
        else:
            exec_out.append(item)
    sec_out: list[tuple[str, list[_GroundedItem]]] = []
    for heading, sec_items in sections:
        kept: list[_GroundedItem] = []
        for item in sec_items:
            if _item_has_competitive_cross_vendor_framing(
                item,
                claim_by_id=claim_by_id,
                evidence_by_id=evidence_by_id,
                research_question=research_question,
            ):
                moved.append(
                    _GroundedItem(
                        text=item.text,
                        claim_ids=item.claim_ids,
                        claim_evidence_ids=item.claim_evidence_ids,
                        citation_span_ids=item.citation_span_ids,
                        support_type="inference",
                    )
                )
            else:
                kept.append(item)
        sec_out.append((heading, kept))
    return exec_out, sec_out, [*uncertainty_items, *moved]


def _display_anchor_by_evidence_id(
    *,
    executive_items: list[_GroundedItem],
    sections: list[tuple[str, list[_GroundedItem]]],
    uncertainty_items: list[_GroundedItem],
    stable_anchor_by_eid: dict[str, str],
    table_eid_order: tuple[str, ...] | None = None,
) -> dict[str, str]:
    sequence: list[str] = []
    seen: set[str] = set()

    def absorb(items: list[_GroundedItem]) -> None:
        for item in items:
            for eid in item.claim_evidence_ids:
                old = stable_anchor_by_eid.get(eid)
                if old and old not in seen:
                    sequence.append(old)
                    seen.add(old)

    absorb(executive_items)
    for _, sec_items in sections:
        absorb(sec_items)
    if table_eid_order:
        for eid in table_eid_order:
            old = stable_anchor_by_eid.get(eid)
            if old and old not in seen:
                sequence.append(old)
                seen.add(old)
    absorb(uncertainty_items)
    old_to_new = {old: f"e{i}" for i, old in enumerate(sequence, start=1)}
    display: dict[str, str] = {}
    for eid, old in stable_anchor_by_eid.items():
        mapped = old_to_new.get(old)
        if mapped:
            display[eid] = mapped
    return display


def _fallback_executive_from_sections(
    sections: list[tuple[str, list[_GroundedItem]]],
    *,
    max_items: int = 3,
) -> list[_GroundedItem]:
    picked: list[_GroundedItem] = []
    for _heading, sec_items in sections:
        for item in sec_items:
            if item.support_type != "direct_evidence":
                continue
            picked.append(item)
            if len(picked) >= max_items:
                return picked
    return picked


def _norm_inline(value: str) -> str:
    return " ".join(value.split())


def _evidence_anchor_sort_key(anchor: str) -> int:
    match = _EVIDENCE_ANCHOR_NUM.match(anchor.strip())
    if match:
        return int(match.group(1))
    return 10**9


def _build_evidence_anchor_map(claims: list[ReportClaimItem]) -> dict[str, str]:
    """Assign one footnote anchor per canonical URL to reduce inline citation clutter."""
    url_to_anchor: dict[str, str] = {}
    anchors: dict[str, str] = {}
    counter = 1
    for claim in claims:
        for evidence in claim.support_evidence + claim.contradict_evidence:
            eid = str(evidence.claim_evidence_id)
            url_key = (
                github_readme_logical_group_key(evidence.canonical_url)
                or evidence.canonical_url.strip().lower()
                or eid
            )
            if isinstance(url_key, str):
                url_key = url_key.lower()
            if url_key not in url_to_anchor:
                url_to_anchor[url_key] = f"e{counter}"
                counter += 1
            anchors[eid] = url_to_anchor[url_key]
    return anchors


def _footnote_citation_suffix(
    evidence_ids: tuple[str, ...],
    anchor_by_evidence_id: dict[str, str],
    *,
    max_anchors: int = 4,
) -> str:
    parts: list[tuple[int, str]] = []
    seen_anchor: set[str] = set()
    for eid in evidence_ids:
        anchor = anchor_by_evidence_id.get(eid)
        if anchor and anchor not in seen_anchor:
            seen_anchor.add(anchor)
            parts.append((_evidence_anchor_sort_key(anchor), f"[^{anchor}]"))
    parts.sort(key=lambda item: item[0])
    if len(parts) > max_anchors:
        parts = parts[:max_anchors]
    return "".join(fragment for _, fragment in parts)


def _render_evidence_footnote_section(
    claims: list[ReportClaimItem],
    anchor_by_evidence_id: dict[str, str],
    labels: dict[str, Any],
    *,
    report_language: str,
    include_trace: bool,
) -> list[str]:
    if not anchor_by_evidence_id:
        return []
    zh = is_chinese_report_language(report_language)
    evidence_rows: dict[str, tuple[ReportClaimItem, ReportEvidenceItem]] = {}
    for claim in claims:
        for evidence in claim.support_evidence + claim.contradict_evidence:
            eid = str(evidence.claim_evidence_id)
            if eid in anchor_by_evidence_id:
                evidence_rows[eid] = (claim, evidence)
    lines = ["", f"## {labels['evidence_appendix']}", ""]
    ordered_anchors = sorted(
        set(anchor_by_evidence_id.values()),
        key=lambda anchor: _evidence_anchor_sort_key(anchor),
    )
    for anchor in ordered_anchors:
        eids = [eid for eid, a in anchor_by_evidence_id.items() if a == anchor]
        excerpts: list[str] = []
        topic = ""
        url = ""
        domain = ""
        for eid in sorted(eids):
            row = evidence_rows.get(eid)
            if row is None:
                continue
            claim, evidence = row
            if not topic:
                topic = _norm_inline((claim.statement or "")[:220])
            if not url:
                url = evidence.canonical_url.strip()
                domain = _norm_inline(evidence.domain)
            ex = _norm_inline(evidence.excerpt)
            if not ex:
                continue
            if claim.normalized_from_readme and len(ex.split()) <= 14:
                continue
            if ex and ex not in excerpts:
                excerpts.append(ex)
        if not url:
            continue
        filtered_excerpts = _filter_appendix_excerpts(excerpts)
        if not filtered_excerpts:
            if zh:
                excerpt_join = (
                    "（正文引用的相关摘录主要为导航、作者简介或模板性内容，已在附录中省略具体引文；"
                    "结论仍以正文可核验段落为准。）"
                )
            else:
                excerpt_join = (
                    "(Navigation/bio boilerplate tied to this footnote was omitted; rely on the "
                    "main body for factual claims.)"
                )
        elif zh:
            excerpt_join = "；".join(f"「{ex}」" for ex in filtered_excerpts[:5])
        else:
            excerpt_join = "; ".join(f'"{ex}"' for ex in filtered_excerpts[:5])
        defn = (
            f"[^{anchor}]: **{labels['appendix_source']}** [{domain}](<{url}>) · "
            f"**{labels['appendix_topic']}** {topic or labels['appendix_topic_unknown']} · "
            f"**{labels['excerpt']}** {excerpt_join}"
        )
        lines.append(defn)
        if include_trace:
            trace_bits = ", ".join(f"`{eid}`" for eid in eids[:16])
            lines.append(f"    *{labels['appendix_trace']}:* {trace_bits}")
        lines.append("")
    return lines


def _support_type_sidecar(
    *,
    executive_items: list[_GroundedItem],
    sections: list[tuple[str, list[_GroundedItem]]],
    uncertainty_items: list[_GroundedItem],
) -> dict[str, object]:
    counts: dict[str, int] = {}

    def feed(items: list[_GroundedItem]) -> None:
        for grounded in items:
            counts[grounded.support_type] = counts.get(grounded.support_type, 0) + 1

    feed(executive_items)
    for _, sec in sections:
        feed(sec)
    feed(uncertainty_items)
    return {"grounded_report_support_type_counts": counts}


def _render_ops_debug_appendix(
    *,
    payload: dict[str, Any],
    task_id: UUID,
    revision_no: int,
    generation_time: str,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
    labels: dict[str, Any],
    executive_items: list[_GroundedItem],
    sections: list[tuple[str, list[_GroundedItem]]],
    uncertainty_items: list[_GroundedItem],
    answer_relevant_claim_count: int,
    excluded_low_quality_claim_count: int,
) -> list[str]:
    """Operator-facing trace: alignment, counts, ids — not for general readers."""
    lines: list[str] = ["", f"## {labels['debug_appendix_title']}", ""]
    lines.append(
        labels["debug_run_line"].format(
            task_id=task_id, revision_no=revision_no, generation_time=generation_time
        )
    )
    lines.append("")

    alignment = payload.get("question_alignment")
    if isinstance(alignment, dict):
        lines.append(f"### {labels['question_alignment']}")
        lines.append("")
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
        lines.append("")

    coverage_raw = payload.get("coverage_notes")
    coverage_notes_clean = [
        note.strip()
        for note in (coverage_raw if isinstance(coverage_raw, list) else [])
        if isinstance(note, str) and note.strip()
    ]
    if coverage_notes_clean:
        lines.append(f"### {labels['coverage_notes_label']}")
        lines.append("")
        for note in coverage_notes_clean:
            lines.append(f"- {note}")
        lines.append("")

    lines.append(f"### {labels['source_scope']}")
    lines.append("")
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
    source_domains = sorted({source.domain for source in sources})
    lines.append(
        "- "
        + labels["evidence_sources"].format(
            count=len(sources),
            domains=", ".join(source_domains) or labels["none"],
        )
    )
    lines.append("- " + labels["answer_relevant"].format(count=answer_relevant_claim_count))
    lines.append("- " + labels["excluded_claims"].format(count=excluded_low_quality_claim_count))
    lines.append("")
    lines.extend([f"### {labels['claim_mapping']}", ""])
    lines.extend(_render_claim_mapping(claims, labels=labels))
    return lines


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
    plan_intent: str | None = None,
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
    executive_items, sections, uncertainty_items = _migrate_competitive_core_items(
        executive_items,
        sections,
        uncertainty_items,
        claim_by_id=claim_by_id,
        evidence_by_id=evidence_by_id,
        research_question=research_question,
    )
    executive_items, sections = _dedupe_executive_against_sections(executive_items, sections)
    sections = _dedupe_sections_cross_section(sections)
    if not executive_items and not any(items for _, items in sections) and not uncertainty_items:
        raise GroundedLLMReportValidationError("LLM report JSON contained no grounded items")

    unresolved = [
        item.strip()
        for item in payload.get("unresolved", [])
        if isinstance(item, str) and item.strip()
    ]
    unresolved.extend(_coverage_gap_unresolved_items(claims, research_question, labels=labels))
    unresolved = list(dict.fromkeys(unresolved))

    title = _string_or_none(payload.get("title")) or build_report_title(
        research_question,
        report_language=report_language,
    )
    beijing_tz = timezone(timedelta(hours=8))
    generation_time = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# {title}",
        "",
        labels["reader_provenance"].format(generation_time=generation_time),
        "",
        f"> **{labels['inquiry_blockquote']}** {research_question}",
        "",
    ]
    executive_for_body = list(executive_items)
    if not executive_for_body:
        executive_for_body = _fallback_executive_from_sections(sections)
    exec_render = executive_items if executive_items else executive_for_body
    comparison_table_spec = _maybe_build_tech_comparison_table_spec(
        claims=claims,
        research_question=research_question,
        report_language=report_language,
        plan_intent=plan_intent,
    )
    display_anchor_by_eid = _display_anchor_by_evidence_id(
        executive_items=exec_render,
        sections=sections,
        uncertainty_items=uncertainty_items,
        stable_anchor_by_eid=anchor_by_evidence_id,
        table_eid_order=comparison_table_spec.flat_eid_order if comparison_table_spec else None,
    )

    if executive_items:
        lines.extend([f"## {labels['summary_heading']}", ""])
        lines.extend(
            _render_grounded_items(
                executive_items,
                labels=labels,
                anchor_by_evidence_id=display_anchor_by_eid,
                as_paragraphs=True,
            )
        )
        lines.append("")
    elif executive_for_body:
        lines.extend([f"## {labels['summary_heading']}", ""])
        lines.extend(
            _render_grounded_items(
                executive_for_body,
                labels=labels,
                anchor_by_evidence_id=display_anchor_by_eid,
                as_paragraphs=True,
            )
        )
        lines.append("")

    if sections:
        for heading, items in sections:
            lines.extend([f"## {heading}", ""])
            lines.extend(
                _render_grounded_items(
                    items,
                    labels=labels,
                    anchor_by_evidence_id=display_anchor_by_eid,
                    as_paragraphs=True,
                )
            )
            lines.append("")

    if comparison_table_spec:
        table_md = _format_tech_comparison_table_markdown(
            comparison_table_spec,
            anchor_by_evidence_id=display_anchor_by_eid,
            report_language=report_language,
        )
        lines.extend(
            [
                "",
                f"## {comparison_table_spec.heading}",
                "",
                table_md,
                "",
            ]
        )

    deployment_slot_lines = _render_deployment_slot_sections(
        claims,
        research_question=research_question,
        labels=labels,
    )
    if deployment_slot_lines:
        lines.extend(["", f"## {labels['deployment_evidence_reader']}", ""])
        lines.extend(deployment_slot_lines)

    lines.extend(["", f"## {labels['section_five_heading']}", ""])
    if uncertainty_items:
        lines.extend(
            _render_grounded_items(
                uncertainty_items,
                labels=labels,
                anchor_by_evidence_id=display_anchor_by_eid,
                as_paragraphs=True,
            )
        )
        if unresolved:
            lines.append("")

    if unresolved:
        lines.append(f"**{labels['unresolved_inline_title']}**")
        lines.append("")
        for item in unresolved[:10]:
            lines.append(f"- {item}")
        lines.append("")
    elif not uncertainty_items:
        lines.append(labels["no_open_questions"])
        lines.append("")

    referenced_evidence_ids = _collect_referenced_evidence_ids(
        executive_items if executive_items else executive_for_body,
        sections,
        uncertainty_items,
    )
    if comparison_table_spec:
        referenced_evidence_ids = set(referenced_evidence_ids)
        referenced_evidence_ids.update(comparison_table_spec.flat_eid_order)
    appendix_anchor_by_evidence_id = {
        eid: display_anchor_by_eid[eid]
        for eid in referenced_evidence_ids
        if eid in display_anchor_by_eid
    }
    if not appendix_anchor_by_evidence_id:
        appendix_anchor_by_evidence_id = (
            dict(display_anchor_by_eid) if display_anchor_by_eid else dict(anchor_by_evidence_id)
        )

    lines.extend(
        _render_evidence_footnote_section(
            claims,
            appendix_anchor_by_evidence_id,
            labels=labels,
            report_language=report_language,
            include_trace=include_ledger_debug_appendix,
        )
    )

    if include_ledger_debug_appendix:
        lines.extend(
            _render_ops_debug_appendix(
                payload=payload,
                task_id=task_id,
                revision_no=revision_no,
                generation_time=generation_time,
                claims=claims,
                sources=sources,
                labels=labels,
                executive_items=executive_items,
                sections=sections,
                uncertainty_items=uncertainty_items,
                answer_relevant_claim_count=answer_relevant_claim_count,
                excluded_low_quality_claim_count=excluded_low_quality_claim_count,
            )
        )

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
            restrict_support_types=True,
        )
        if items:
            result.append((heading, items))
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
        if restrict_support_types:
            evidence_ids = _filter_core_only_evidence_ids(
                evidence_ids,
                evidence_by_id=evidence_by_id,
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
    sections: list[tuple[str, list[_GroundedItem]]],
    uncertainty_items: list[_GroundedItem],
) -> bool:
    for grounded in executive_items:
        if grounded.support_type in {"inference", "background"}:
            return True
    for _, sec_items in sections:
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
    as_paragraphs: bool,
) -> list[str]:
    lines: list[str] = []
    for item in items:
        suffix = _footnote_citation_suffix(item.claim_evidence_ids, anchor_by_evidence_id)
        block = f"{item.text}{suffix}"
        if as_paragraphs:
            lines.extend([block, ""])
        else:
            lines.append(f"- {block}")
    if as_paragraphs and lines and lines[-1] == "":
        lines.pop()
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
            "answer_relevant": "与问题直接相关的已核验陈述条数：{count}。",
            "appendix_source": "来源",
            "appendix_topic": "对应主题",
            "appendix_topic_unknown": "（材料中未单独命名主题）",
            "appendix_trace": "内部追溯键",
            "chunk": "chunk",
            "citation": "citation",
            "citations": "citations",
            "claim": "内部陈述",
            "claim_counts": (
                "内部 ledger 计数：supported {supported}、mixed {mixed}、"
                "contradicted {contradicted}、unsupported {unsupported}。"
            ),
            "claim_evidence": "claim_evidence",
            "claim_mapping": "内部陈述—摘录—引用映射",
            "claims": "claims",
            "coverage_notes_label": "覆盖与缺口备注（内部）",
            "debug_appendix_title": "附录：编排与追溯（调试）",
            "debug_run_line": (
                "_任务 `{task_id}` · 内部修订 `{revision_no}` · 生成时间 {generation_time} "
                "（北京时间）_"
            ),
            "deployment_coverage_gap": (
                "关于「{slot}」，本次收集的材料里缺少足够清晰、可复核的说明，"
                "因此无法在报告中给出可靠结论。"
            ),
            "deployment_evidence": "部署证据覆盖",
            "deployment_evidence_reader": "部署与配置要点",
            "deployment_slot_gap": "「{slot}」在现有材料中仍缺少可直接引用的配置或命令说明。",
            "evidence_appendix": "附录：来源与证据",
            "evidence_sources": "带摘录链接的来源文档：{count} 个；涉及域名：{domains}。",
            "evidence_footnotes": "附录：来源与证据",
            "excluded_claims": "未纳入正文统计的弱相关或低质量陈述：{count} 条。",
            "excerpt": "摘录",
            "footnote_trace": "追溯键",
            "executive_summary": "摘要",
            "generated": (
                "_由 grounded LLM report writer 基于已持久化证据在 revision `{revision_no}` 生成。"
                "生成时间：{generation_time} (北京时间)_"
            ),
            "inquiry_blockquote": "调查问题",
            "key_findings": "关键结论",
            "llm_generation": (
                "合成阶段仅消费已核验的陈述、证据记录与引用摘录；"
                "输出条目均通过内部标识校验。"
            ),
            "no_citation_spans": "未记录引用片段。",
            "no_mappings": "当前没有可用的内部映射表。",
            "no_open_questions": "当前没有需要单独强调的证据矛盾或待定事项。",
            "no_section_items": "现有已核验证据不足以生成该分节。",
            "no_supported_items": "现有已核验证据不足以生成独立摘要。",
            "no_uncertainty_items": "没有额外混合或弱支持材料需要单独展示。",
            "no_unresolved": "未从已核验材料中推断出额外的未决问题。",
            "none": "无",
            "offsets": "offsets",
            "original_question_label": "原始问题",
            "partially_answered_label": "部分回答部分",
            "placeholder_section": "主要分析",
            "planner_intent_label": "规划意图",
            "question_alignment": "问题对齐与覆盖（内部）",
            "reader_provenance": (
                "_本报告正文仅综合文末「附录：来源与证据」中的可核验摘录；"
                "生成时间：{generation_time}（北京时间）。_"
            ),
            "related_subs": "关联子问题",
            "research_question": "研究问题",
            "section_five_heading": "五、证据不足与待确认事项",
            "source": "source",
            "source_scope": "来源范围与限制",
            "source_scope_mixed": (
                "正文主体仅使用与摘录紧耦合的表述；本节以下条目可含谨慎推断。"
                "全文仍由已持久化材料经内部校验拼装，未引入未归档的外部事实。"
            ),
            "source_scope_strict": (
                "本报告仅由已持久化材料与可核验摘录综合，不使用外部事实。"
            ),
            "summary_heading": "摘要",
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
            "unresolved_inline_title": "尚需材料支撑或未能展开的要点",
        }
    return {
        "answered_label": "Answered parts",
        "answer_relevant": "Answer-relevant verified statements included: {count}.",
        "appendix_source": "Source",
        "appendix_topic": "Topic",
        "appendix_topic_unknown": "(topic not separately named in the bundle)",
        "appendix_trace": "Internal trace ids",
        "chunk": "chunk",
        "citation": "citation",
        "citations": "citations",
        "claim": "Internal statement",
        "claim_counts": (
            "Internal ledger counts: {supported} supported, {mixed} mixed, "
            "{contradicted} contradicted, {unsupported} unsupported."
        ),
        "claim_evidence": "claim_evidence",
        "claim_mapping": "Internal statement–excerpt–reference mapping",
        "claims": "claims",
        "coverage_notes_label": "Coverage notes (internal)",
        "debug_appendix_title": "Appendix: Orchestration trace (debug)",
        "debug_run_line": (
            "_Task `{task_id}` · internal revision `{revision_no}` · generated {generation_time} "
            "(Beijing time)_"
        ),
        "deployment_coverage_gap": (
            "For “{slot}”, the collected material lacks clear, checkable detail, "
            "so the report cannot state a reliable conclusion."
        ),
        "deployment_evidence": "Deployment Evidence Coverage",
        "deployment_evidence_reader": "Deployment and configuration notes",
        "deployment_slot_gap": (
            "“{slot}” still lacks directly quotable configuration or command evidence "
            "in the current material."
        ),
        "evidence_appendix": "Appendix: Sources and evidence",
        "evidence_sources": "Source documents with excerpt links: {count}; domains: {domains}.",
        "evidence_footnotes": "Appendix: Sources and evidence",
        "excluded_claims": (
            "Weakly related or low-quality statements not counted in the body: {count}."
        ),
        "excerpt": "Excerpt",
        "footnote_trace": "Trace key",
        "executive_summary": "Executive summary",
        "generated": (
            "_Generated by grounded LLM report writer from persisted evidence at revision "
            "`{revision_no}`. Generated at: {generation_time} (Beijing Time)_"
        ),
        "inquiry_blockquote": "Research question",
        "key_findings": "Key Findings",
        "llm_generation": (
            "Synthesis consumed only verified statements, evidence records, and excerpt spans; "
            "each rendered block passed internal id validation."
        ),
        "no_citation_spans": "No citation spans recorded.",
        "no_mappings": "No internal mapping table is available.",
        "no_open_questions": (
            "No contradictions or open items need a separate callout here."
        ),
        "no_section_items": (
            "No verifiable section items; upstream material may be thin."
        ),
        "no_supported_items": (
            "This edition does not include a separate executive summary; rely on section "
            "narratives and appendix footnotes."
        ),
        "no_uncertainty_items": "No additional mixed or weak-support material needs display.",
        "no_unresolved": "No additional unresolved questions were inferred from the verified set.",
        "none": "none",
        "offsets": "offsets",
        "original_question_label": "Original user question",
        "partially_answered_label": "Partially answered parts",
        "placeholder_section": "Main analysis",
        "planner_intent_label": "Planner intent",
        "question_alignment": "Question alignment and coverage (internal)",
        "reader_provenance": (
            "_The narrative is grounded only in excerpts listed under “Appendix: Sources and "
            "evidence”. Generated: {generation_time} (Beijing time)._"
        ),
        "related_subs": "Related subquestions",
        "research_question": "Research Question",
        "section_five_heading": "V. Insufficient evidence and open questions",
        "source": "source",
        "source_scope": "Source scope and limitations",
        "source_scope_mixed": (
            "Main body wording stays tightly tied to excerpts; items below may include "
            "cautious interpretation. The narrative is still built only from persisted, "
            "validated material."
        ),
        "source_scope_strict": (
            "This report is synthesized strictly from persisted material and checkable excerpts, "
            "with no external facts."
        ),
        "summary_heading": "Executive summary",
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
        "unresolved_inline_title": "Points that still need material or could not be expanded",
    }
