from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
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
    contradicted_count: int
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
    report_language: str = DEFAULT_REPORT_LANGUAGE,
    answer_relevant_claim_count: int | None = None,
    excluded_low_quality_claim_count: int = 0,
    include_ledger_debug_appendix: bool = False,
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
    lines.extend(
        [
            "",
            f"## {labels['answer']}",
            "",
        ]
    )

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
        for claim in unresolved_claims:
            lines.append(f"- {_normalize_inline(claim.statement)}")
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
            "deterministic_generation": (
                "生成该 artifact 时没有执行新的搜索、抓取、解析、索引、验证器或 LLM 报告写作逻辑。"
            ),
            "evidence_sources": "带证据链接的来源文档：{count} 个；域名：{domains}。",
            "evidence_table": "证据表",
            "evidence_table_header": "| Claim 类别 | 支持细节 | Claim | 证据域名 | 来源 |",
            "excluded_claims": "已排除低质量或偏离问题的 claim：{count}。",
            "excerpt": "摘录",
            "executive_summary": "执行摘要",
            "generated": (
                "_由已持久化证据在 revision `{revision_no}` 生成。"
                "生成时间：{generation_time} (北京时间)_"
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
            "uncertainty_counts": (
                "当前仍有不确定性：{mixed} 条 mixed、{contradicted} 条 contradicted、"
                "{unsupported} 条 unsupported、{draft} 条 draft。"
            ),
            "unresolved": "未解决问题 / 低覆盖区域",
            "weak_missing_slots": "缺失或较弱的必需答案槽位：{slots}。",
            "weak_supported_claims": ("{count} 条 claim 只有弱词法支持，已从主要答案章节中排除。"),
        }
    return {
        "additional": "additional",
        "answer": "Answer",
        "answer_relevant": "Answer-relevant claims included: {count}.",
        "answer_slot_count": "Answer slot coverage: {covered}/{total}.",
        "answer_slot_coverage": "Answer Slot Coverage",
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
        "deterministic_generation": (
            "No new search, fetch, parse, index, verifier, or LLM report-writing logic was "
            "executed while generating this artifact."
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
        "generated": (
            "_Generated from persisted evidence at revision `{revision_no}`. "
            "Generated at: {generation_time} (Beijing Time)_"
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
        "uncertainty_counts": (
            "Current uncertainty remains: {mixed} mixed, {contradicted} contradicted, "
            "{unsupported} unsupported, {draft} draft."
        ),
        "unresolved": "Unresolved / Low Coverage Areas",
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
