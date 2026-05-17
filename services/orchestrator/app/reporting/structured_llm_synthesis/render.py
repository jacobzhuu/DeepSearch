from __future__ import annotations

import re
from dataclasses import replace

from services.orchestrator.app.reporting.language import is_chinese_report_language
from services.orchestrator.app.reporting.markdown import (
    RenderedMarkdownReport,
    ReportClaimItem,
    ReportEvidenceItem,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.evidence_index import (
    build_claim_evidence_index,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.schema import (
    EvidenceBackedText,
    StructuredSynthesisBundle,
    StructuredSynthesisStageFlags,
)


def _max_footnote_index(markdown: str) -> int:
    nums = [int(x) for x in re.findall(r"\[\^(\d+)\]", markdown)]
    return max(nums) if nums else 0


class _FootnoteSink:
    def __init__(self, first_index: int) -> None:
        self._url_to_idx: dict[str, int] = {}
        self._next = first_index

    def ref(self, url: str) -> str:
        u = (url or "").strip()
        if not u:
            return ""
        if u not in self._url_to_idx:
            self._url_to_idx[u] = self._next
            self._next += 1
        return f"[^{self._url_to_idx[u]}]"

    def definition_lines(self) -> list[str]:
        lines: list[str] = []
        for url, idx in sorted(self._url_to_idx.items(), key=lambda kv: kv[1]):
            host = url.split("//", 1)[-1].split("/", 1)[0]
            lines.append(f"[^{idx}]: **{host}** <{url}>")
        return lines


def _escape_cell(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")


def _format_ebt(
    field: EvidenceBackedText,
    *,
    sink: _FootnoteSink,
    index: dict[str, ReportEvidenceItem],
) -> str:
    text = _escape_cell(field.text)
    if not field.evidence_ids:
        return text
    ev = index.get(field.evidence_ids[0])
    url = getattr(ev, "canonical_url", "") or ""
    if url:
        return f"{text} {sink.ref(url)}"
    return text


def render_validated_bundle_markdown(
    bundle: StructuredSynthesisBundle,
    *,
    claims: list[ReportClaimItem],
    base_markdown: str,
    report_language: str,
    flags: StructuredSynthesisStageFlags,
) -> str:
    zh = is_chinese_report_language(report_language)
    title = "LLM 辅助结构化综合（证据绑定，已校验）" if zh else "LLM-assisted structured synthesis (validated)"
    lines: list[str] = ["", f"## {title}", ""]
    sink = _FootnoteSink(_max_footnote_index(base_markdown) + 1)
    index = build_claim_evidence_index(claims)

    if flags.structure and bundle.archetype_judge is not None:
        j = bundle.archetype_judge
        lines.append("### " + ("结构判定" if zh else "Archetype judge"))
        lines.append(
            f"- **archetype**: `{j.report_archetype}` · **confidence**: `{j.confidence:.2f}`"
        )
        if j.reason:
            lines.append(f"- **reason**: {_escape_cell(j.reason)}")
        if j.risks:
            lines.append("- **risks**:")
            for r in j.risks[:12]:
                lines.append(f"  - {_escape_cell(r)}")
        lines.append("")

    if flags.method_cards and bundle.method_cards:
        lines.append("### " + ("方法/材料卡片（LLM 结构化）" if zh else "Method / material cards (LLM)"))
        for i, card in enumerate(bundle.method_cards, start=1):
            name = _format_ebt(card.method_name, sink=sink, index=index)
            lines.append(f"#### {i}. {name}")
            rows = (
                ("问题", card.problem),
                ("动机", card.motivation),
                ("核心方法", card.core_method),
                ("架构/算法", card.architecture_or_algorithm),
                ("目标/损失", card.objective_or_loss),
                ("数据/任务", card.datasets_or_tasks),
                ("指标/结果", card.metrics_or_results),
                ("局限", card.limitations),
            )
            for label, fld in rows:
                if not fld.text.strip() and fld.text == "":
                    continue
                lines.append(f"- **{label}**: {_format_ebt(fld, sink=sink, index=index)}")
            if card.insight.text.strip():
                ins = card.insight
                tag = "综合判断（推断）" if zh else "Judgment (inference)"
                ev_urls = " ".join(
                    sink.ref(getattr(index[eid], "canonical_url", "") or "")
                    for eid in ins.evidence_ids[:4]
                    if eid in index
                )
                lines.append(
                    f"- **{tag}**: {_escape_cell(ins.text)} "
                    f"（强度 `{ins.inference_strength}`；{_escape_cell(ins.caveat)}） {ev_urls}"
                )
            lines.append("")

    if flags.comparison_table and bundle.comparison_table and bundle.comparison_table.dimensions:
        tbl = bundle.comparison_table
        lines.append("### " + ("对比维度表（LLM 规划，已校验）" if zh else "Comparison dimensions (LLM, validated)"))
        ents = tbl.entities
        if ents:
            header = "| " + ("维度" if zh else "Dimension") + " | " + " | ".join(_escape_cell(e) for e in ents) + " |"
            sep = "| " + " | ".join(["---"] * (len(ents) + 1)) + " |"
            lines.extend([header, sep])
            for dim in tbl.dimensions:
                row_cells: list[str] = []
                for ent in ents:
                    cell = dim.cells.get(ent)
                    if cell is None:
                        row_cells.append(_escape_cell(""))
                        continue
                    txt = _escape_cell(cell.text)
                    suffix = ""
                    if cell.evidence_ids:
                        ev0 = index.get(cell.evidence_ids[0])
                        url = getattr(ev0, "canonical_url", "") if ev0 is not None else ""
                        if url:
                            suffix = " " + sink.ref(url)
                    row_cells.append(txt + suffix)
                lines.append("| " + _escape_cell(dim.name) + " | " + " | ".join(row_cells) + " |")
            lines.append("")

    if flags.insights and bundle.insights and bundle.insights.insights:
        lines.append("### " + ("证据约束下的综合洞察" if zh else "Evidence-bound insights"))
        for ins in bundle.insights.insights:
            label = {
                "synthesis": "综合归纳",
                "inference": "可能表明",
                "caveated_projection": "仍需注意",
            }.get(ins.type, ins.type)
            ev_urls = " ".join(
                sink.ref(getattr(index[eid], "canonical_url", "") or "")
                for eid in ins.evidence_ids[:6]
                if eid in index
            )
            lines.append(
                f"- **{label}**（`{ins.type}`）: {_escape_cell(ins.text)} — {_escape_cell(ins.caveat)} {ev_urls}"
            )
        lines.append("")

    defs = sink.definition_lines()
    if defs:
        lines.append("**" + ("结构化综合脚注" if zh else "Structured synthesis footnotes") + "**")
        lines.extend(defs)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def append_to_rendered_markdown(
    rendered: RenderedMarkdownReport,
    *,
    fragment: str,
) -> RenderedMarkdownReport:
    merged = rendered.markdown.rstrip() + "\n\n" + fragment.strip() + "\n"
    return replace(rendered, markdown=merged)
