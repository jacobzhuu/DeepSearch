from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from services.orchestrator.app.reporting.language import (
    DEFAULT_REPORT_LANGUAGE,
    is_chinese_report_language,
    normalize_report_language,
)
from services.orchestrator.app.research_quality import (
    answer_slot_coverage,
    answer_slots_for_query,
    build_slot_coverage_summary,
    slot_ids_for_claim_category,
)
from services.orchestrator.app.query_intent_signals import (
    detect_report_archetype,
    extract_comparison_entities,
    query_asks_comparison,
)
from services.orchestrator.app.reporting.survey_cards import build_method_survey_cards
from services.orchestrator.app.research_quality.source_intent import source_intent_report_core_eligible

ClaimStatus = Literal["draft", "supported", "mixed", "unsupported", "contradicted"]
EvidenceRelation = Literal["candidate_support", "support", "weak_support", "contradict"]

_STATUS_PRIORITY: dict[str, int] = {
    "supported": 0,
    "mixed": 1,
    "contradicted": 2,
    "unsupported": 3,
    "draft": 4,
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
    source_role: str | None = None
    source_intent: str | None = None
    relation_detail: str | None = None
    support_level: str | None = None
    verifier_method: str | None = None
    citation_precision: str | None = None
    citation_precision_reason: str | None = None
    reuse_penalty: float | None = None
    chunk_reuse_count_before: int | None = None
    span_reuse_count_before: int | None = None
    content_reuse_count_before: int | None = None
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
    evidence_kind: str | None = None
    deployment_evidence_excerpt: str | None = None
    verifier_method: str | None = None
    support_level: str | None = None
    normalized_from_readme: bool = False


@dataclass(frozen=True)
class ReportSourceItem:
    source_document_id: UUID
    canonical_url: str
    domain: str
    title: str | None
    source_role: str | None = None
    source_intent: str | None = None


@dataclass(frozen=True)
class RenderedMarkdownReport:
    title: str
    markdown: str
    supported_count: int
    mixed_count: int
    unsupported_count: int
    contradicted_count: int
    draft_count: int
    answer_relevant_count: int
    excluded_low_quality_count: int
    synthesis_plan: dict[str, object] | None = None
    critic_result: dict[str, object] | None = None
    redundancy_clusters: list[dict[str, object]] | None = None


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
    report_language: str = DEFAULT_REPORT_LANGUAGE,
    answer_relevant_claim_count: int | None = None,
    excluded_low_quality_claim_count: int = 0,
    include_ledger_debug_appendix: bool = False,
    report_archetype: str | None = None,
    plan_intent: str | None = None,
) -> RenderedMarkdownReport:
    normalized_language = normalize_report_language(report_language)
    labels = _report_labels(normalized_language)
    ordered_claims = sorted(claims, key=_claim_sort_key)
    supported_claims = [item for item in ordered_claims if item.verification_status == "supported"]
    weak_supported_claims = [
        item for item in supported_claims if _claim_support_level(item) == "weak"
    ]
    strong_supported_claims = [
        item for item in supported_claims if _claim_support_level(item) != "weak"
    ]
    mixed_claims = [item for item in ordered_claims if item.verification_status == "mixed"]
    contradicted_claims = [
        item for item in ordered_claims if item.verification_status == "contradicted"
    ]
    unsupported_claims = [
        item for item in ordered_claims if item.verification_status == "unsupported"
    ]
    draft_claims = [item for item in ordered_claims if item.verification_status == "draft"]
    if answer_relevant_claim_count is None:
        answer_relevant_claim_count = len(ordered_claims)

    title = build_report_title(research_question, report_language=normalized_language)
    supported_by_category = _claims_by_category(strong_supported_claims)
    covered_categories = set(supported_by_category)
    slot_coverage = answer_slot_coverage(research_question, covered_categories)
    slot_coverage_summary = build_slot_coverage_summary(
        research_question,
        evidence_candidates=[],
        claim_rows=_claim_rows_for_slot_summary(ordered_claims, query=research_question),
    )
    slot_coverage_for_counts = _effective_slot_coverage_rows(
        slot_coverage,
        slot_coverage_summary,
    )
    missing_required_slots = [
        slot for slot in slot_coverage_for_counts if slot["required"] and not slot["covered"]
    ]
    missing_core_categories = _missing_core_categories_for_query(
        research_question,
        supported_by_category,
    )
    source_domain_list = [s.domain for s in sources if s.domain]
    effective_archetype = report_archetype or detect_report_archetype(
        research_question,
        plan_intent=plan_intent,
        source_domains=source_domain_list,
    )
    source_domains = _format_domains(sources)
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
        _normalize_inline(research_question),
        "",
        f"## {labels['executive_summary']}",
        "",
    ]

    if strong_supported_claims:
        for claim in strong_supported_claims[:6]:
            lines.append(f"- {_normalize_inline(claim.statement)}")
    else:
        lines.append(f"- {labels['no_strong_claims']}")
    exec_dedupe_keys = {
        _markdown_body_dedupe_key(c.statement) for c in strong_supported_claims[:6]
    }
    if weak_supported_claims:
        lines.append(
            "- " + labels["weak_supported_claims"].format(count=len(weak_supported_claims))
        )
    if answer_relevant_claim_count < 2 or missing_core_categories:
        lines.append(
            "- "
            + labels["coverage_limited"].format(
                categories="/".join(missing_core_categories) or labels["additional"]
            )
        )
    if mixed_claims or contradicted_claims or unsupported_claims or draft_claims:
        lines.append(
            "- "
            + labels["uncertainty_counts"].format(
                mixed=len(mixed_claims),
                contradicted=len(contradicted_claims),
                unsupported=len(unsupported_claims),
                draft=len(draft_claims),
            )
        )
    if effective_archetype == "research_survey":
        lines.extend(
            _render_research_survey_synthesis_sections(
                research_question=research_question,
                claims=strong_supported_claims,
                sources=sources,
                slot_coverage_summary=slot_coverage_summary,
                report_language=normalized_language,
                labels=labels,
                dedupe_statement_keys=exec_dedupe_keys,
            )
        )
    else:
        lines.extend(
            _render_synthesis_sections(
                research_question=research_question,
                claims=strong_supported_claims,
                sources=sources,
                slot_coverage_summary=slot_coverage_summary,
                report_language=normalized_language,
                labels=labels,
                dedupe_statement_keys=exec_dedupe_keys,
            )
        )
    lines.extend(
        [
            "",
            f"## {labels['answer']}",
            "",
        ]
    )

    if effective_archetype == "research_survey":
        lines.extend(
            _render_survey_answer_sections(
                research_question=research_question,
                claims=strong_supported_claims,
                sources=sources,
                report_language=normalized_language,
                labels=labels,
            )
        )
    else:
        for slot in answer_slots_for_query(research_question):
            slot_label = _slot_label(slot.label, report_language=normalized_language)
            lines.extend([f"### {slot_label}", ""])
            section_claims = [
                claim
                for claim in strong_supported_claims
                if _claim_matches_slot(claim, slot_id=slot.slot_id, query=research_question)
            ]
            if section_claims:
                for claim in section_claims:
                    lines.extend(_render_claim_answer_lines(claim, bullet_prefix="- "))
            else:
                lines.append("- " + labels["slot_coverage_limited"].format(slot=slot_label.lower()))
            lines.append("")

    lines.extend([f"## {labels['answer_slot_coverage']}", ""])
    if slot_coverage_summary:
        lines.extend(
            [
                labels["slot_coverage_header"],
                "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for coverage_slot in slot_coverage_summary:
            lines.append(
                "| "
                f"{_escape_table_cell(str(coverage_slot.get('slot_id', 'unknown')))} | "
                f"{_escape_table_cell(str(coverage_slot.get('status', 'unknown')))} | "
                f"{coverage_slot.get('evidence_candidate_count', 0)} | "
                f"{coverage_slot.get('accepted_evidence_count', 0)} | "
                f"{coverage_slot.get('supported_claim_count', 0)} | "
                f"{coverage_slot.get('weak_supported_claim_count', 0)} | "
                f"{coverage_slot.get('source_count', 0)} |"
            )
    else:
        lines.append(labels["no_slot_coverage"])

    lines.extend(["", f"## {labels['evidence_table']}", ""])
    evidence_rows = _evidence_rows(supported_claims)
    if evidence_rows:
        lines.extend(
            [
                labels["evidence_table_header"],
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
        lines.append(labels["no_evidence_rows"])

    lines.extend(["", f"## {labels['source_scope']}", ""])
    lines.append(f"- {labels['source_scope_strict']}")
    lines.append(f"- {labels['deterministic_generation']}")
    lines.append(
        "- "
        + labels["claim_counts"].format(
            strong=len(strong_supported_claims),
            weak=len(weak_supported_claims),
            mixed=len(mixed_claims),
            contradicted=len(contradicted_claims),
            unsupported=len(unsupported_claims),
            draft=len(draft_claims),
        )
    )
    lines.append("- " + labels["answer_relevant"].format(count=answer_relevant_claim_count))
    lines.append("- " + labels["excluded_claims"].format(count=excluded_low_quality_claim_count))
    lines.append(
        "- "
        + labels["answer_slot_count"].format(
            covered=sum(1 for slot in slot_coverage_for_counts if slot["covered"]),
            total=len(slot_coverage_for_counts),
        )
    )
    lines.append(
        "- "
        + labels["evidence_sources"].format(
            count=len(sources),
            domains=", ".join(source_domains) or labels["none"],
        )
    )
    if len(source_domains) == 1:
        lines.append(f"- {labels['one_domain_warning']}")

    lines.extend(["", f"## {labels['unresolved']}", ""])
    unresolved_claims = (
        weak_supported_claims
        + mixed_claims
        + contradicted_claims
        + unsupported_claims
        + draft_claims
    )
    weak_or_missing_slots = [
        slot
        for slot in slot_coverage_summary
        if slot.get("required") is True and slot.get("status") in {"missing", "weak"}
    ]
    if weak_or_missing_slots:
        weak_missing_labels = ", ".join(
            str(slot.get("label") or slot.get("slot_id")) for slot in weak_or_missing_slots
        )
        lines.append("- " + labels["weak_missing_slots"].format(slots=weak_missing_labels))
    if missing_required_slots:
        lines.append(
            "- "
            + labels["missing_required_slots"].format(
                slots=", ".join(str(slot["label"]) for slot in missing_required_slots)
            )
        )
    if missing_core_categories:
        lines.append(
            "- "
            + labels["missing_answer_coverage"].format(
                categories=", ".join(missing_core_categories)
            )
        )
    if unresolved_claims:
        lines.append("- " + labels["unresolved_claim_count"].format(count=len(unresolved_claims)))
        for claim in unresolved_claims[:3]:
            lines.append(f"- {_normalize_inline(claim.statement)}")
        if len(unresolved_claims) > 3:
            lines.append(
                "- " + labels["unresolved_claims_omitted"].format(count=len(unresolved_claims) - 3)
            )
    elif not ordered_claims:
        lines.append(f"- {labels['no_claims']}")
    elif not missing_core_categories:
        lines.append(f"- {labels['no_extra_unresolved']}")

    if include_ledger_debug_appendix:
        lines.extend(["", f"## {labels['claim_mapping']}", ""])
        if ordered_claims:
            for claim in ordered_claims:
                lines.extend(_render_claim_mapping(claim, report_language=normalized_language))
        else:
            lines.append(labels["no_mappings"])

    markdown = "\n".join(lines).strip() + "\n"
    return RenderedMarkdownReport(
        title=title,
        markdown=markdown,
        supported_count=len(supported_claims),
        mixed_count=len(mixed_claims),
        unsupported_count=len(unsupported_claims),
        contradicted_count=len(contradicted_claims),
        draft_count=len(draft_claims),
        answer_relevant_count=answer_relevant_claim_count,
        excluded_low_quality_count=excluded_low_quality_claim_count,
        synthesis_plan=None,
        critic_result=None,
        redundancy_clusters=None,
    )


def build_report_title(
    research_question: str,
    *,
    report_language: str = DEFAULT_REPORT_LANGUAGE,
) -> str:
    normalized = _normalize_inline(research_question)
    if is_chinese_report_language(report_language):
        if not normalized:
            return "研究报告"
        return f"研究报告：{normalized}"
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


def _missing_core_categories_for_query(
    research_question: str,
    supported_by_category: dict[str, list[ReportClaimItem]],
) -> list[str]:
    lower = research_question.lower()
    if any(
        term in lower
        for term in ("deploy", "deployment", "docker", "compose", "container", "install")
    ):
        return [
            category
            for category in ("deployment/self_hosting",)
            if not supported_by_category.get(category)
        ]
    return [
        category
        for category in ("definition", "mechanism", "privacy", "feature")
        if not supported_by_category.get(category)
    ]


def _evidence_rows(
    claims: list[ReportClaimItem],
) -> list[tuple[ReportClaimItem, ReportEvidenceItem]]:
    rows: list[tuple[ReportClaimItem, ReportEvidenceItem]] = []
    for claim in claims:
        for evidence in claim.support_evidence[:2]:
            rows.append((claim, evidence))
    return rows


def _markdown_body_dedupe_key(statement: str) -> str:
    base = _normalize_inline(statement or "").lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", base)


_INSUFFICIENT_ZH = "当前证据不足"
_INSUFFICIENT_EN = "Insufficient evidence in the current bundle"


def _short_url_host(url: str) -> str:
    trimmed = url.strip()
    if not trimmed:
        return "source"
    return trimmed.split("//", 1)[-1].split("/", 1)[0]


class _MarkdownFootnoteSink:
    """Assigns stable consecutive [^n] markers to canonical URLs in a table section."""

    __slots__ = ("_by_url",)

    def __init__(self) -> None:
        self._by_url: dict[str, int] = {}

    def note_for_url(self, url: str) -> str:
        u = (url or "").strip()
        if not u:
            return ""
        if u not in self._by_url:
            self._by_url[u] = len(self._by_url) + 1
        return f"[^{self._by_url[u]}]"

    def definition_lines(self, *, zh: bool) -> list[str]:
        if not self._by_url:
            return []
        head = "**证据脚注**" if zh else "**Evidence footnotes**"
        lines: list[str] = ["", head, ""]
        for url, idx in sorted(self._by_url.items(), key=lambda kv: kv[1]):
            host = _short_url_host(url)
            lines.append(f"[^{idx}]: **{host}** <{url}>")
        lines.append("")
        return lines


def _pick_evidence_for_cell_claim(claim: ReportClaimItem) -> ReportEvidenceItem | None:
    if not claim.support_evidence:
        return None
    preferred = [
        ev
        for ev in claim.support_evidence
        if source_intent_report_core_eligible(ev.source_intent)
    ]
    return preferred[0] if preferred else claim.support_evidence[0]


def _score_statement_keywords(statement: str, keywords: tuple[str, ...]) -> int:
    low = (statement or "").lower()
    return sum(1 for kw in keywords if kw.lower() in low)


def _claim_matches_entity(statement: str, entity: str) -> bool:
    return entity.lower() in (statement or "").lower()


_TECH_COMPARISON_DIMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("核心定位", "Positioning", ("定位", "角色", "目标", "面向", "position", "purpose", "role")),
    ("核心抽象", "Core abstractions", ("抽象", "图", "graph", "状态机", "primitive", "abstraction")),
    ("工作机制", "Mechanism", ("机制", "流程", "执行", "路由", "pipeline", "how", "work")),
    (
        "状态/数据管理",
        "State / data",
        ("状态", "持久", "checkpoint", "存储", "数据", "内存", "transaction", "cache"),
    ),
    ("适用场景", "Scenarios", ("场景", "适用", "用例", "应用", "fit", "when")),
    ("优势", "Strengths", ("优势", "更好", "性能", "优点", "faster", "stronger", "benefit")),
    ("代价", "Tradeoffs", ("代价", "限制", "缺点", "成本", "开销", "latency", "limitation")),
)

_SURVEY_CARD_DIMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("研究问题", "Research problem", ("问题", "任务", "动机", "目标", "gap", "motivat", "address")),
    ("数据/图类型", "Data / graph", ("数据", "数据集", "图", "输入", "dataset", "topology", "benchmark")),
    ("关键技术", "Key techniques", ("技术", "模块", "算法", "架构", "encoder", "layer", "network")),
    ("优化目标", "Objectives", ("优化", "损失", "目标", "objective", "loss", "training")),
    ("实验指标", "Metrics", ("指标", "实验", "准确率", "f1", "bleu", "rouge", "metric")),
    ("优势", "Strengths", ("优势", "更好", "提升", "improve", "outperform", "精度")),
    ("局限", "Limits", ("局限", "限制", "不足", "limitation", "failure", "缺点")),
    ("适用场景", "Applicability", ("场景", "适用", "应用", "用例", "setting", "domain")),
)


def _render_comparison_table_block(
    *,
    research_question: str,
    claims: list[ReportClaimItem],
    report_language: str,
) -> list[str]:
    entities = extract_comparison_entities(research_question, max_entities=5)
    if len(entities) < 2:
        return []
    zh = is_chinese_report_language(report_language)
    ins = _INSUFFICIENT_ZH if zh else _INSUFFICIENT_EN
    see_above = "见上" if zh else "See above"
    title = "技术对象横向对比表" if zh else "Technical comparison table"
    dim_col = "维度" if zh else "Dimension"
    header = f"| {dim_col} | " + " | ".join(_escape_table_cell(e) for e in entities) + " |"
    sep = "| " + " | ".join(["---"] * (len(entities) + 1)) + " |"
    sink = _MarkdownFootnoteSink()
    used_claim_ids: set[str] = set()
    prev_by_entity: dict[str, str] = {e: "" for e in entities}

    def render_cell(entity: str, dim_keywords: tuple[str, ...]) -> str:
        best: tuple[int, int, str, ReportClaimItem] | None = None
        for claim in claims:
            if claim.verification_status != "supported":
                continue
            stmt = claim.statement or ""
            if not _claim_matches_entity(stmt, entity):
                continue
            if not claim.support_evidence:
                continue
            kw = _score_statement_keywords(stmt, dim_keywords)
            used_penalty = 1 if str(claim.claim_id) in used_claim_ids else 0
            key = (kw, -used_penalty, str(claim.claim_id))
            best_key = None if best is None else (best[0], best[1], best[2])
            if best is None or key > best_key:
                best = (key[0], key[1], str(claim.claim_id), claim)
        chosen: ReportClaimItem | None = None
        if best is not None and best[0] > 0:
            chosen = best[3]
        if chosen is None:
            for claim in claims:
                if claim.verification_status != "supported":
                    continue
                stmt = claim.statement or ""
                if not _claim_matches_entity(stmt, entity):
                    continue
                if not claim.support_evidence:
                    continue
                if str(claim.claim_id) in used_claim_ids:
                    continue
                chosen = claim
                break
        if chosen is None:
            prev = prev_by_entity.get(entity, "")
            if prev and prev not in {ins, see_above}:
                text = see_above
                prev_by_entity[entity] = text
                return _escape_table_cell(text)
            prev_by_entity[entity] = ins
            return _escape_table_cell(ins)
        stmt = _normalize_inline((chosen.statement or "")[:200])
        ev = _pick_evidence_for_cell_claim(chosen)
        url = (ev.canonical_url if ev else "") or ""
        frag = stmt
        if url:
            frag = f"{stmt} {sink.note_for_url(url)}"
        used_claim_ids.add(str(chosen.claim_id))
        prev_by_entity[entity] = frag
        return _escape_table_cell(frag)

    rows = [header, sep]
    for zh_label, en_label, kws in _TECH_COMPARISON_DIMS:
        label = zh_label if zh else en_label
        rows.append(
            "| " + _escape_table_cell(label) + " | " + " | ".join(render_cell(e, kws) for e in entities) + " |"
        )
    out = ["", f"## {title}", "", "\n".join(rows), ""]
    out.extend(sink.definition_lines(zh=zh))
    return out


def _render_survey_comparison_table_from_cards(
    cards: list[Any],
    *,
    claims_by_id: dict[str, ReportClaimItem],
    report_language: str,
) -> list[str]:
    zh = is_chinese_report_language(report_language)
    ins = _INSUFFICIENT_ZH if zh else _INSUFFICIENT_EN
    see_above = "见上" if zh else "See above"
    if len(cards) < 2:
        return []
    dim_col = "维度" if zh else "Dimension"
    title = "横向对比表（方法卡片）" if zh else "Cross-method comparison table (cards)"
    slice_cards = cards[:6]
    names: list[str] = []
    for c in slice_cards:
        raw_name = _normalize_inline(c.display_name)[:72] or c.primary_domain or ins
        names.append(_escape_table_cell(raw_name))
    header = f"| {dim_col} | " + " | ".join(names) + " |"
    sep = "| " + " | ".join(["---"] * (len(names) + 1)) + " |"
    sink = _MarkdownFootnoteSink()
    used_by_card: dict[str, set[str]] = {str(c.card_key): set() for c in slice_cards}
    prev_text_by_card: dict[str, str] = {str(c.card_key): "" for c in slice_cards}

    def render_cell(card: Any, dim_kws: tuple[str, ...]) -> str:
        ckey = str(card.card_key)
        used = used_by_card[ckey]
        prev = prev_text_by_card.get(ckey, "")
        ranked: list[tuple[int, int, ReportClaimItem]] = []
        for cid in card.claim_ids:
            cl = claims_by_id.get(cid)
            if (
                cl is None
                or cl.verification_status != "supported"
                or not cl.support_evidence
            ):
                continue
            stmt = cl.statement or ""
            kw_score = _score_statement_keywords(stmt, dim_kws)
            if kw_score <= 0:
                continue
            used_penalty = 1 if str(cl.claim_id) in used else 0
            ranked.append((kw_score, -used_penalty, cl))
        ranked.sort(
            key=lambda item: (item[0], item[1], str(item[2].claim_id)),
            reverse=True,
        )
        chosen: ReportClaimItem | None = ranked[0][2] if ranked else None
        if chosen is None:
            for cid in card.claim_ids:
                if cid in used:
                    continue
                cl = claims_by_id.get(cid)
                if (
                    cl is None
                    or cl.verification_status != "supported"
                    or not cl.support_evidence
                ):
                    continue
                chosen = cl
                break
        if chosen is None:
            if prev and prev not in {ins, see_above}:
                prev_text_by_card[ckey] = see_above
                return _escape_table_cell(see_above)
            prev_text_by_card[ckey] = ins
            return _escape_table_cell(ins)
        stmt = _normalize_inline((chosen.statement or "")[:220])
        ev = _pick_evidence_for_cell_claim(chosen)
        url = (ev.canonical_url if ev else "") or ""
        frag = f"{stmt} {sink.note_for_url(url)}" if url else stmt
        used.add(str(chosen.claim_id))
        prev_text_by_card[ckey] = frag
        return _escape_table_cell(frag)

    out_lines: list[str] = ["", f"## {title}", "", header, sep]
    for zh_label, en_label, kws in _SURVEY_CARD_DIMS:
        label = zh_label if zh else en_label
        cells = [render_cell(c, kws) for c in slice_cards]
        out_lines.append("| " + _escape_table_cell(label) + " | " + " | ".join(cells) + " |")
    out_lines.append("")
    out_lines.extend(sink.definition_lines(zh=zh))
    return out_lines


def _render_research_survey_synthesis_sections(
    *,
    research_question: str,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
    slot_coverage_summary: list[dict[str, object]],
    report_language: str,
    labels: dict[str, str],
    dedupe_statement_keys: set[str] | None = None,
) -> list[str]:
    if not claims:
        return []
    zh = is_chinese_report_language(report_language)
    ins = _INSUFFICIENT_ZH if zh else _INSUFFICIENT_EN
    dedupe_statement_keys = dedupe_statement_keys or set()
    cards = build_method_survey_cards(claims, sources)
    claims_by_id = {str(c.claim_id): c for c in claims}
    domains = _format_domains(sources)
    h_intro = "绪论（材料边界）" if zh else "Introduction (evidence boundary)"
    h_thread = "研究脉络与覆盖范围" if zh else "Research thread and coverage"
    h_apps = "应用场景与实践要点" if zh else "Applications and practice notes"
    h_judge = "综合判断与展望（仅基于已验证结论的组织性归纳）" if zh else "Synthesis and outlook (organization only)"
    h_appendix = "附录：材料组织说明" if zh else "Appendix: how material is organized"

    intro_claims = [
        c
        for c in claims
        if (c.claim_category or "") in {"definition", "mechanism", "feature"}
    ][:5]
    if not intro_claims:
        intro_claims = claims[:5]
    lines: list[str] = ["", f"## {h_intro}", ""]
    intro_emitted = 0
    for claim in intro_claims:
        key = _markdown_body_dedupe_key(claim.statement)
        if key in dedupe_statement_keys:
            continue
        dedupe_statement_keys.add(key)
        lines.append(
            "- "
            + _normalize_inline(claim.statement)
            + " — "
            + _claim_evidence_summary(claim, labels=labels)
        )
        intro_emitted += 1
    if intro_emitted == 0:
        lines.append(f"- {ins}")

    lines.extend(["", f"## {h_thread}", ""])
    lines.append(
        "- "
        + labels["background_paragraph"].format(
            question=_normalize_inline(research_question),
            claim_count=len(claims),
            source_count=len(sources),
            domains=", ".join(domains) or labels["none"],
        )
    )
    weak_slots = [
        str(slot.get("label") or slot.get("slot_id"))
        for slot in slot_coverage_summary
        if slot.get("required") is True and slot.get("status") in {"missing", "weak"}
    ]
    if weak_slots:
        slot_note = "；".join(weak_slots[:6]) if zh else "; ".join(weak_slots[:6])
        lines.append(
            "- "
            + (
                f"材料仍偏弱的主题维度包括：{slot_note}（仅作范围提示，不当作事实结论）。"
                if zh
                else f"Weaker or missing thematic areas include: {slot_note} (scope note only)."
            )
        )

    for card in cards:
        title = f"方法深读：{_normalize_inline(card.display_name)}" if zh else f"Method deep dive: {_normalize_inline(card.display_name)}"
        lines.extend(["", f"## {title}", ""])
        emitted = 0
        for cid in card.claim_ids:
            claim = claims_by_id.get(cid)
            if not claim:
                continue
            key = _markdown_body_dedupe_key(claim.statement)
            if key in dedupe_statement_keys:
                continue
            dedupe_statement_keys.add(key)
            lines.extend(_render_finding_paragraph(emitted + 1, claim, labels=labels))
            emitted += 1
        if emitted == 0:
            lines.append(f"- {ins}")

    lines.extend(["", f"## {h_apps}", ""])
    app_claims = [
        c
        for c in claims
        if (c.claim_category or "")
        in {"deployment/self_hosting", "feature", "privacy", "mechanism"}
    ][:6]
    if not app_claims:
        lines.append(f"- {ins}")
    else:
        for i, claim in enumerate(app_claims, start=1):
            lines.extend(
                _render_supporting_paragraph(
                    i,
                    claim,
                    labels=labels,
                )
            )

    lines.extend(
        _render_survey_comparison_table_from_cards(
            cards,
            claims_by_id=claims_by_id,
            report_language=report_language,
        )
    )

    lines.extend(["", f"## {h_judge}", ""])
    judge_pool = [c for c in claims if (c.claim_category or "") == "mechanism"][:4] or claims[:4]
    lines.append(
        "- "
        + (
            "（综合判断）以下条目是对已列强支持结论的并列整理，用于帮助读者把握分歧点与证据边界；"
            "不包含摘录之外的新事实。"
            if zh
            else "(Synthesis) The bullets below only reorganize already listed supported claims; "
            "they do not add facts beyond excerpts."
        )
    )
    for idx, claim in enumerate(judge_pool, start=1):
        lines.append(f"- **{idx}.** {_normalize_inline(claim.statement)}")

    lines.extend(["", f"## {h_appendix}", ""])
    lines.append(f"- {labels['source_scope_strict']}")
    lines.append(f"- {labels['deterministic_generation']}")
    lines.append(
        "- "
        + (
            "方法卡片按「首个支持证据对应的来源文档」聚类；若同一来源下证据不足，表格与深读节会标注「当前证据不足」。"
            if zh
            else "Method cards cluster claims by the first supporting source document; "
            "when material is missing, sections and tables state insufficient evidence."
        )
    )
    return lines


def _render_survey_answer_sections(
    *,
    research_question: str,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
    report_language: str,
    labels: dict[str, str],
) -> list[str]:
    zh = is_chinese_report_language(report_language)
    ins = _INSUFFICIENT_ZH if zh else _INSUFFICIENT_EN
    cards = build_method_survey_cards(claims, sources)
    by_id = {str(c.claim_id): c for c in claims}
    covered: set[str] = set()
    lines: list[str] = []
    overview = "研究结论（按方法卡片组织）" if zh else "Findings (organized by method cards)"
    lines.extend(["", f"### {overview}", ""])
    if not cards:
        lines.append(f"- {ins}")
    else:
        for card in cards:
            lines.append(f"#### {_normalize_inline(card.display_name)}")
            if card.paper_title:
                lines.append(
                    "- "
                    + (
                        f"材料题名（如可得）：{_normalize_inline(card.paper_title)}"
                        if zh
                        else f"Material title (when available): {_normalize_inline(card.paper_title)}"
                    )
                )
            lines.append(
                "- "
                + (
                    f"证据锚点（claim_evidence_id）：{', '.join(card.evidence_ids[:12])}"
                    + (" …" if len(card.evidence_ids) > 12 else "")
                    if card.evidence_ids
                    else f"{ins}"
                )
            )
            for cid in card.claim_ids:
                claim = by_id.get(cid)
                if not claim:
                    continue
                lines.extend(_render_claim_answer_lines(claim, bullet_prefix="- "))
                covered.add(cid)
            lines.append("")

    other_title = "其他要点（未归入单一来源簇）" if zh else "Other highlights (not in a single cluster)"
    lines.extend([f"### {other_title}", ""])
    others = [c for c in claims if str(c.claim_id) not in covered]
    if not others:
        lines.append(f"- {ins}")
    else:
        for claim in others[:12]:
            lines.extend(_render_claim_answer_lines(claim, bullet_prefix="- "))
        if len(others) > 12:
            lines.append(
                "- "
                + (
                    f"另有 {len(others) - 12} 条未展开（仍可在证据表中追溯）。"
                    if zh
                    else f"{len(others) - 12} additional claims omitted here but traceable in the table."
                )
            )
    lines.append("")
    return lines


def _render_synthesis_sections(
    *,
    research_question: str,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
    slot_coverage_summary: list[dict[str, object]],
    report_language: str,
    labels: dict[str, str],
    dedupe_statement_keys: set[str] | None = None,
) -> list[str]:
    if not claims:
        return []
    dedupe_statement_keys = dedupe_statement_keys or set()
    main_claims = claims[: min(6, len(claims))]
    supporting_claims = claims[min(6, len(claims)) : min(14, len(claims))]
    filtered_main = [
        c
        for c in main_claims
        if _markdown_body_dedupe_key(c.statement) not in dedupe_statement_keys
    ]
    slot_seen = set(dedupe_statement_keys)
    domains = _format_domains(sources)
    lines: list[str] = [
        "",
        f"## {labels['background_and_scope']}",
        "",
        labels["background_paragraph"].format(
            question=_normalize_inline(research_question),
            claim_count=len(claims),
            source_count=len(sources),
            domains=", ".join(domains) or labels["none"],
        ),
        "",
        f"## {labels['core_findings']}",
        "",
    ]
    for index, claim in enumerate(filtered_main, start=1):
        lines.extend(_render_finding_paragraph(index, claim, labels=labels))
    if not filtered_main:
        lines.append(f"- {labels['no_strong_claims']}")
    if supporting_claims:
        lines.extend(["", f"## {labels['supporting_findings']}", ""])
        for index, claim in enumerate(supporting_claims, start=1):
            key = _markdown_body_dedupe_key(claim.statement)
            if key in slot_seen:
                continue
            slot_seen.add(key)
            lines.extend(_render_supporting_paragraph(index, claim, labels=labels))

    if query_asks_comparison(research_question):
        lines.extend(
            _render_comparison_table_block(
                research_question=research_question,
                claims=claims,
                report_language=report_language,
            )
        )

    lines.extend(["", f"## {labels['mechanism_analysis']}", ""])
    grouped = _claims_by_slot(claims, query=research_question)
    for slot in answer_slots_for_query(research_question):
        slot_claims = grouped.get(slot.slot_id, [])
        if not slot_claims:
            continue
        slot_label = _slot_label(slot.label, report_language=report_language)
        lines.append(f"### {slot_label}")
        lines.append("")
        emitted = 0
        for claim in slot_claims[:2]:
            key = _markdown_body_dedupe_key(claim.statement)
            if key in slot_seen:
                continue
            slot_seen.add(key)
            lines.append(
                labels["mechanism_paragraph"].format(
                    claim=_normalize_inline(claim.statement),
                    evidence=_claim_evidence_summary(claim, labels=labels),
                )
            )
            lines.append("")
            emitted += 1
        if emitted == 0:
            lines.append("- " + labels["slot_coverage_limited"].format(slot=slot_label.lower()))
            lines.append("")

    covered_slots = [
        str(slot.get("label") or slot.get("slot_id"))
        for slot in slot_coverage_summary
        if slot.get("required") is True and slot.get("status") not in {"missing", "weak"}
    ]
    weak_slots = [
        str(slot.get("label") or slot.get("slot_id"))
        for slot in slot_coverage_summary
        if slot.get("required") is True and slot.get("status") in {"missing", "weak"}
    ]
    lines.extend(
        [
            f"## {labels['evidence_interpretation']}",
            "",
            labels["evidence_interpretation_paragraph"].format(
                claim_count=len(claims),
                source_count=len(sources),
                domains=", ".join(domains) or labels["none"],
                covered_slots=", ".join(covered_slots) or labels["none"],
                weak_slots=", ".join(weak_slots) or labels["none"],
            ),
        ]
    )
    return lines


def _render_finding_paragraph(
    index: int,
    claim: ReportClaimItem,
    *,
    labels: dict[str, str],
) -> list[str]:
    return [
        labels["finding_paragraph"].format(
            index=index,
            claim=_normalize_inline(claim.statement),
            category=_normalize_inline(claim.claim_category or "other"),
            evidence=_claim_evidence_summary(claim, labels=labels),
        ),
        "",
    ]


def _render_supporting_paragraph(
    index: int,
    claim: ReportClaimItem,
    *,
    labels: dict[str, str],
) -> list[str]:
    return [
        labels["supporting_paragraph"].format(
            index=index,
            claim=_normalize_inline(claim.statement),
            evidence=_claim_evidence_summary(claim, labels=labels),
        ),
        "",
    ]


def _claim_evidence_summary(
    claim: ReportClaimItem,
    *,
    labels: dict[str, str],
) -> str:
    domains = list(dict.fromkeys(evidence.domain for evidence in claim.support_evidence[:3]))
    excerpts = [
        _normalize_inline(evidence.excerpt)
        for evidence in claim.support_evidence[:2]
        if evidence.excerpt.strip()
    ]
    return labels["evidence_summary"].format(
        evidence_count=len(claim.support_evidence),
        domains=", ".join(domains) or labels["none"],
        excerpts="；".join(excerpts) if excerpts else labels["none"],
    )


def _claims_by_slot(
    claims: list[ReportClaimItem],
    *,
    query: str,
) -> dict[str, list[ReportClaimItem]]:
    grouped: dict[str, list[ReportClaimItem]] = {}
    for claim in claims:
        for slot_id in _claim_slot_ids(claim, query=query):
            grouped.setdefault(slot_id, []).append(claim)
    return grouped


def _effective_slot_coverage_rows(
    category_rows: list[dict[str, object]],
    slot_summary_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not any(str(row.get("slot_id", "")).startswith("deployment_") for row in slot_summary_rows):
        return category_rows
    rows_by_slot_id = {
        str(row.get("slot_id")): row
        for row in slot_summary_rows
        if isinstance(row.get("slot_id"), str)
    }
    effective_rows: list[dict[str, object]] = []
    for row in category_rows:
        slot_id = str(row.get("slot_id", ""))
        summary_row = rows_by_slot_id.get(slot_id)
        if summary_row is None:
            effective_rows.append(row)
            continue
        effective_rows.append(
            {
                **row,
                "covered": summary_row.get("status") == "covered",
                "matched_claim_categories": (
                    list(row.get("matched_claim_categories", []))
                    if isinstance(row.get("matched_claim_categories"), list)
                    else []
                ),
            }
        )
    return effective_rows


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


def _render_claim_mapping(
    claim: ReportClaimItem,
    *,
    report_language: str = DEFAULT_REPORT_LANGUAGE,
) -> list[str]:
    labels = _report_labels(report_language)
    lines = [
        (
            f"- {labels['claim_label']} `{claim.claim_id}` [{claim.verification_status.upper()}]:"
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
        lines.append(f"  - {labels['no_citation_spans']}")
        return lines
    for evidence in evidence_items:
        lines.append(
            "  - "
            f"{evidence.relation_type}"
            f"({evidence.relation_detail or evidence.support_level or 'n/a'})"
            f" | {labels['claim_evidence_id']} `{evidence.claim_evidence_id}`"
            f" | {labels['citation']} `{evidence.citation_span_id}`"
            f" | {labels['source']} `{evidence.source_document_id}`"
            f" | {labels['chunk']} `{evidence.source_chunk_id}` #{evidence.chunk_no}"
            f" | {labels['offsets']} `{evidence.start_offset}:{evidence.end_offset}`"
            f" | {evidence.canonical_url}"
            f' | {labels["excerpt"]}: "{_normalize_inline(evidence.excerpt)}"'
        )
    return lines


def _render_claim_answer_lines(
    claim: ReportClaimItem,
    *,
    bullet_prefix: str,
) -> list[str]:
    code_block = _deployment_code_block_for_claim(claim)
    if code_block is None:
        return [f"{bullet_prefix}{_normalize_inline(claim.statement)}"]
    intro = _deployment_statement_intro(claim.statement, code_block=code_block)
    trace = _deployment_claim_trace(claim)
    return [
        f"{bullet_prefix}{_normalize_inline(intro)} {trace}",
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
        return max(code_blocks, key=lambda item: len(_normalize_inline(item)))
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
    normalized_statement = _normalize_inline(statement)
    if code_block and len(normalized_statement) <= 320:
        return normalized_statement
    code_from_statement = _deployment_code_block_from_statement(statement)
    if (
        code_from_statement
        and code_block
        and _normalize_inline(code_from_statement) == _normalize_inline(code_block)
    ):
        return normalized_statement
    return statement.split(":", 1)[0].strip() + ":"


def _deployment_claim_trace(claim: ReportClaimItem) -> str:
    source_urls = list(dict.fromkeys(item.canonical_url for item in claim.support_evidence[:2]))
    if not source_urls:
        return ""
    return f"({_normalize_inline('; '.join(source_urls))})"


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


def _slot_label(label: str, *, report_language: str) -> str:
    if not is_chinese_report_language(report_language):
        return label
    mapping = {
        "What it is": "是什么",
        "How it works": "工作机制",
        "Privacy / tracking behavior": "隐私 / 追踪行为",
        "Key features / limitations": "关键功能 / 限制",
        "Deployment target": "部署目标",
        "Deployment steps": "部署步骤",
        "Configuration / operations": "配置 / 运维",
        "Evidence quality": "证据质量",
        "Prerequisites": "前置条件",
        "Docker run / Docker Compose": "Docker run / Docker Compose",
        "Volumes": "卷挂载",
        "Ports": "端口",
        "Configuration": "配置",
        "Security": "安全",
        "Troubleshooting": "故障排查",
        "Update / maintenance": "更新 / 维护",
    }
    return mapping.get(label, label)


def _report_labels(report_language: str) -> dict[str, str]:
    if is_chinese_report_language(report_language):
        return {
            "additional": "更多",
            "answer": "研究结论",
            "answer_relevant": "已纳入与问题相关的 claim：{count}。",
            "answer_slot_count": "答案槽位覆盖：{covered}/{total}。",
            "answer_slot_coverage": "答案槽位覆盖",
            "background_and_scope": "背景与问题框架",
            "background_paragraph": (
                "本报告围绕“{question}”展开，只综合已经写入研究账本并通过验证的证据。"
                "当前可用于主体论证的结论共有 {claim_count} 条，来自 {source_count} 个来源，"
                "覆盖域名包括 {domains}。因此，下面的分析不是开放式猜测，而是把已抓取、"
                "已解析、已绑定 citation span 的证据组织成可审计的回答。"
            ),
            "chunk": "chunk",
            "citation": "citation",
            "claim_counts": (
                "Claim 计数：{strong} 条强支持、{weak} 条弱支持、{mixed} 条混合、"
                "{contradicted} 条反驳、{unsupported} 条不支持、{draft} 条草稿。"
            ),
            "claim_evidence_id": "claim_evidence",
            "claim_label": "Claim",
            "claim_mapping": "附录：claim/evidence/citation 映射",
            "coverage_limited": "覆盖有限，因为未生成 {categories} 类 claim。",
            "core_findings": "核心发现",
            "deterministic_generation": (
                "生成该 artifact 时没有执行新的搜索、抓取、解析、索引、验证器或 LLM 报告写作逻辑。"
            ),
            "evidence_interpretation": "证据解释",
            "evidence_interpretation_paragraph": (
                "证据层面，主体结论使用 {claim_count} 条已支持 claim 和 {source_count} 个来源。"
                "来源域名为 {domains}。已形成较明确覆盖的槽位包括 {covered_slots}；"
                "仍较弱或缺失的槽位包括 {weak_slots}。这些弱项不被提升为事实结论，"
                "只在最后的限制说明中出现。"
            ),
            "evidence_summary": (
                "证据数 {evidence_count}；来源域名 {domains}；关键摘录：{excerpts}"
            ),
            "evidence_sources": "带证据链接的来源文档：{count} 个；域名：{domains}。",
            "evidence_table": "证据表",
            "evidence_table_header": "| Claim 类别 | 支持细节 | Claim | 证据域名 | 来源 |",
            "excluded_claims": "已排除低质量或偏离问题的 claim：{count}。",
            "excerpt": "摘录",
            "executive_summary": "执行摘要",
            "finding_paragraph": (
                "**发现 {index}（{category}）**：{claim}。这条结论进入主体报告，是因为它"
                "已经绑定到可追溯证据：{evidence}。报告只围绕这些已验证事实展开。"
            ),
            "generated": (
                "_由已持久化证据在 revision `{revision_no}` 生成。"
                "生成时间：{generation_time} (北京时间)_"
            ),
            "mechanism_analysis": "机制分析",
            "mechanism_paragraph": (
                "{claim} 该判断对应的证据基础为：{evidence}。从论证角度看，"
                "它补充了该槽位下的因果链、组成关系或适用边界。"
            ),
            "missing_answer_coverage": "缺失答案覆盖：{categories}。",
            "missing_required_slots": "缺失必需答案槽位：{slots}。",
            "no_citation_spans": "未记录 citation span。",
            "no_claims": "当前 ledger 中没有可综合的 claim。",
            "no_evidence_rows": "当前没有支持性证据行。",
            "no_extra_unresolved": "除当前已验证 claim 集外，未推断额外未解决问题。",
            "no_mappings": "当前没有 claim 到 citation 的映射。",
            "no_slot_coverage": "当前没有答案槽位覆盖摘要。",
            "no_strong_claims": "当前 persisted ledger 中没有强支持 claim。",
            "none": "无",
            "offsets": "offsets",
            "one_domain_warning": "警告：来源覆盖仅使用一个证据域名。",
            "research_question": "研究问题",
            "slot_coverage_header": (
                "| 槽位 | 状态 | 候选证据 | 已采纳证据 | 强支持 claim | 弱支持 claim | 来源数 |"
            ),
            "slot_coverage_limited": "覆盖有限，因为未生成强支持的 {slot} claim。",
            "source": "source",
            "source_scope": "来源范围与限制",
            "source_scope_strict": (
                "本报告严格由已持久化的 task、claim、citation、evidence 和 verification 记录综合。"
            ),
            "supporting_findings": "支撑性发现",
            "supporting_paragraph": (
                "**支撑 {index}**：{claim} 这条材料不单独决定总体结论，"
                "但它为主体判断提供背景、边界或交叉印证；证据为：{evidence}。"
            ),
            "uncertainty_counts": (
                "当前仍有不确定性：{mixed} 条 mixed、{contradicted} 条 contradicted、"
                "{unsupported} 条 unsupported、{draft} 条 draft。"
            ),
            "unresolved": "未解决问题 / 低覆盖区域",
            "unresolved_claim_count": (
                "未进入主体结论的不确定 claim 共 {count} 条；以下仅列少量代表性限制。"
            ),
            "unresolved_claims_omitted": (
                "另有 {count} 条不确定 claim 已从主体报告省略，仅保留在账本中供审计。"
            ),
            "weak_missing_slots": "缺失或较弱的必需答案槽位：{slots}。",
            "weak_supported_claims": ("{count} 条 claim 只有弱词法支持，已从主要答案章节中排除。"),
        }
    return {
        "additional": "additional",
        "answer": "Answer",
        "answer_relevant": "Answer-relevant claims included: {count}.",
        "answer_slot_count": "Answer slot coverage: {covered}/{total}.",
        "answer_slot_coverage": "Answer Slot Coverage",
        "background_and_scope": "Background and Scope",
        "background_paragraph": (
            "This report addresses “{question}” using only verified ledger evidence. "
            "The main synthesis has {claim_count} supported claims from {source_count} "
            "sources across these domains: {domains}. The analysis below organizes fetched, "
            "parsed, citation-bound evidence rather than adding unsupported outside facts."
        ),
        "chunk": "chunk",
        "citation": "citation",
        "claim_counts": (
            "Claim counts: {strong} strongly supported, {weak} weak-supported, "
            "{mixed} mixed, {contradicted} contradicted, {unsupported} unsupported, "
            "{draft} draft."
        ),
        "claim_evidence_id": "claim_evidence",
        "claim_label": "Claim",
        "claim_mapping": "Appendix: Claim Evidence Mapping",
        "coverage_limited": "Coverage is limited because no {categories} claims were generated.",
        "core_findings": "Core Findings",
        "deterministic_generation": (
            "No new search, fetch, parse, index, verifier, or LLM report-writing logic was "
            "executed while generating this artifact."
        ),
        "evidence_interpretation": "Evidence Interpretation",
        "evidence_interpretation_paragraph": (
            "The body relies on {claim_count} supported claims and {source_count} sources. "
            "The source domains are {domains}. Covered required slots include {covered_slots}; "
            "weak or missing slots include {weak_slots}. Weak areas are not promoted into "
            "settled findings and are kept in the final limitations section."
        ),
        "evidence_summary": (
            "{evidence_count} evidence item(s); domains: {domains}; key excerpt(s): {excerpts}"
        ),
        "evidence_sources": (
            "Evidence-linked source documents: {count} across domains: {domains}."
        ),
        "evidence_table": "Evidence Table",
        "evidence_table_header": (
            "| Claim category | Support detail | Claim | Evidence domain | Source |"
        ),
        "excluded_claims": "Excluded low-quality or off-query claims: {count}.",
        "excerpt": "excerpt",
        "executive_summary": "Executive Summary",
        "finding_paragraph": (
            "**Finding {index} ({category})**: {claim} This finding is part of the main "
            "answer because it is bound to traceable evidence: {evidence}."
        ),
        "generated": (
            "_Generated from persisted evidence at revision `{revision_no}`. "
            "Generated at: {generation_time} (Beijing Time)_"
        ),
        "mechanism_analysis": "Mechanism Analysis",
        "mechanism_paragraph": (
            "{claim} The evidence basis is: {evidence}. This adds causal, structural, "
            "or boundary detail for the answer slot."
        ),
        "missing_answer_coverage": "Missing answer coverage: {categories}.",
        "missing_required_slots": "Missing required answer slots: {slots}.",
        "no_citation_spans": "No citation spans recorded.",
        "no_claims": "The ledger currently contains no claims to synthesize.",
        "no_evidence_rows": "No support evidence rows are currently available.",
        "no_extra_unresolved": (
            "No additional unresolved questions were inferred beyond the current verified "
            "claim set."
        ),
        "no_mappings": "No claim-to-citation mappings are currently available.",
        "no_slot_coverage": "No answer-slot coverage summary is available.",
        "no_strong_claims": (
            "No strongly supported claims are currently available in the persisted ledger."
        ),
        "none": "none",
        "offsets": "offsets",
        "one_domain_warning": "Warning: source coverage uses only one evidence domain.",
        "research_question": "Research Question",
        "slot_coverage_header": (
            "| Slot | Status | Evidence candidates | Accepted evidence | "
            "Strong claims | Weak claims | Sources |"
        ),
        "slot_coverage_limited": (
            "Coverage is limited because no strongly supported {slot} claims were generated."
        ),
        "source": "source",
        "source_scope": "Source Scope and Limitations",
        "source_scope_strict": (
            "This report is synthesized strictly from persisted task, claim, citation, "
            "evidence, and verification records."
        ),
        "supporting_findings": "Supporting Findings",
        "supporting_paragraph": (
            "**Support {index}**: {claim} This item provides context, boundary detail, or "
            "cross-checking for the main argument; evidence: {evidence}."
        ),
        "uncertainty_counts": (
            "Current uncertainty remains: {mixed} mixed, {contradicted} contradicted, "
            "{unsupported} unsupported, {draft} draft."
        ),
        "unresolved": "Unresolved / Low Coverage Areas",
        "unresolved_claim_count": (
            "{count} uncertain claim(s) were not promoted into the main answer; only a few "
            "representative limitations are listed here."
        ),
        "unresolved_claims_omitted": (
            "{count} additional uncertain claim(s) were omitted from the report body and remain "
            "available in the audit ledger."
        ),
        "weak_missing_slots": "Missing or weak required answer slots: {slots}.",
        "weak_supported_claims": (
            "{count} claim(s) have weak lexical support only and are kept out of the main "
            "answer sections."
        ),
    }


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
