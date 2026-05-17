from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from services.orchestrator.app.query_intent_signals import (
    extract_comparison_entities,
    query_has_lexical_recency_or_update_markers,
)
from services.orchestrator.app.reporting.markdown import ReportClaimItem, ReportEvidenceItem
from services.orchestrator.app.reporting.structured_llm_synthesis.competitive import (
    statement_has_competitive_negative_tone,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.evidence_index import (
    build_claim_evidence_index,
    claims_for_evidence_ids,
    evidence_ids_core_eligible,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.schema import (
    ArchetypeJudgePayload,
    ComparisonCell,
    ComparisonDimension,
    ComparisonTablePayload,
    EvidenceBackedText,
    InsightRow,
    InsightsPayload,
    MethodCardPayload,
    MethodInsightPayload,
    StructuredSynthesisBundle,
    StructuredSynthesisStageFlags,
)

_INSUFFICIENT_ZH = "当前证据不足"
_YEAR_RE = re.compile(r"\b20\d{2}\b")


def _sanitize_evidence_backed_text(
    field: EvidenceBackedText,
    *,
    index: dict[str, ReportEvidenceItem],
    claims: list[ReportClaimItem],
    factual_core: bool,
    warnings: list[str],
) -> EvidenceBackedText:
    text = (field.text or "").strip()
    ids = [eid for eid in field.evidence_ids if eid in index]
    if ids != field.evidence_ids:
        warnings.append("dropped_unknown_evidence_ids")

    if not text or text == _INSUFFICIENT_ZH:
        return EvidenceBackedText(text=_INSUFFICIENT_ZH if factual_core else text, evidence_ids=ids)

    if not ids:
        warnings.append("factual_without_evidence_ids")
        return EvidenceBackedText(text=_INSUFFICIENT_ZH, evidence_ids=[])

    if factual_core and not evidence_ids_core_eligible(ids, index=index):
        warnings.append("non_core_evidence_for_factual_field")
        return EvidenceBackedText(text=_INSUFFICIENT_ZH, evidence_ids=[])

    if _YEAR_RE.search(text) and not evidence_ids_core_eligible(ids, index=index):
        warnings.append("year_like_text_without_core_evidence")
        return EvidenceBackedText(text=_INSUFFICIENT_ZH, evidence_ids=[])

    linked = claims_for_evidence_ids(ids, claims=claims)
    if linked and any(statement_has_competitive_negative_tone(c.statement) for c in linked):
        if not text.startswith("【竞争性说法】"):
            text = f"【竞争性说法】{text}"
            warnings.append("prefixed_competitive_framing")

    return EvidenceBackedText(text=text, evidence_ids=ids)


def _sanitize_method_insight(
    insight: MethodInsightPayload,
    *,
    index: dict[str, ReportEvidenceItem],
    claims: list[ReportClaimItem],
    warnings: list[str],
) -> MethodInsightPayload:
    text = (insight.text or "").strip()
    if not text:
        return MethodInsightPayload()
    ids = [eid for eid in insight.evidence_ids if eid in index]
    caveat = (insight.caveat or "").strip()
    if len(ids) < 2:
        warnings.append("method_insight_insufficient_evidence_ids")
        return MethodInsightPayload()
    if not caveat:
        warnings.append("method_insight_missing_caveat")
        return MethodInsightPayload()
    if not evidence_ids_core_eligible(ids, index=index):
        warnings.append("method_insight_non_core_evidence")
        return MethodInsightPayload()
    linked = claims_for_evidence_ids(ids, claims=claims)
    if linked and any(statement_has_competitive_negative_tone(c.statement) for c in linked):
        caveat = f"{caveat}（证据链可能含竞争性表述，需谨慎解读。）"
    return MethodInsightPayload(
        text=text,
        evidence_ids=ids,
        inference_strength=insight.inference_strength,
        caveat=caveat,
    )


def _sanitize_method_card(
    card: MethodCardPayload,
    *,
    index: dict[str, ReportEvidenceItem],
    claims: list[ReportClaimItem],
    warnings: list[str],
) -> MethodCardPayload:
    factual_names = (
        "method_name",
        "paper_title",
        "problem",
        "motivation",
        "core_method",
        "architecture_or_algorithm",
        "objective_or_loss",
        "datasets_or_tasks",
        "metrics_or_results",
        "limitations",
    )
    data = card.model_dump()
    for name in factual_names:
        data[name] = _sanitize_evidence_backed_text(
            EvidenceBackedText.model_validate(data[name]),
            index=index,
            claims=claims,
            factual_core=True,
            warnings=warnings,
        ).model_dump()
    data["insight"] = _sanitize_method_insight(
        MethodInsightPayload.model_validate(data["insight"]),
        index=index,
        claims=claims,
        warnings=warnings,
    ).model_dump()
    return MethodCardPayload.model_validate(data)


def _sanitize_comparison_table(
    table: ComparisonTablePayload,
    *,
    index: dict[str, ReportEvidenceItem],
    claims: list[ReportClaimItem],
    research_question: str,
    warnings: list[str],
) -> ComparisonTablePayload:
    allowed_entities = set(extract_comparison_entities(research_question, max_entities=8))
    for ent in list(table.entities):
        if ent in allowed_entities:
            continue
        if any(ent.lower() in (c.statement or "").lower() for c in claims):
            allowed_entities.add(ent)
            continue
        warnings.append(f"dropped_entity_not_evidence_backed:{ent}")
    entities = [e for e in table.entities if e in allowed_entities]
    dims_in = table.dimensions[:8]
    dims_out: list[ComparisonDimension] = []
    for dim in dims_in:
        cells: dict[str, ComparisonCell] = {}
        for ent, cell in dim.cells.items():
            if ent not in entities:
                continue
            ids = [eid for eid in cell.evidence_ids if eid in index]
            text = (cell.text or "").strip()
            competitive = bool(cell.competitive_framing)
            if not ids:
                cells[ent] = ComparisonCell(text=_INSUFFICIENT_ZH, evidence_ids=[], competitive_framing=False)
                warnings.append("comparison_cell_no_evidence")
                continue
            if not competitive and not evidence_ids_core_eligible(ids, index=index):
                cells[ent] = ComparisonCell(text=_INSUFFICIENT_ZH, evidence_ids=[], competitive_framing=False)
                warnings.append("comparison_cell_non_core_evidence")
                continue
            if not text:
                text = _INSUFFICIENT_ZH
            linked = claims_for_evidence_ids(ids, claims=claims)
            if linked and any(statement_has_competitive_negative_tone(c.statement) for c in linked):
                competitive = True
                if "竞争性" not in text:
                    text = f"【竞争性说法】{text}"
                    warnings.append("comparison_cell_competitive_tone")
            cells[ent] = ComparisonCell(
                text=text,
                evidence_ids=ids,
                competitive_framing=competitive,
            )
        dims_out.append(ComparisonDimension(name=dim.name, why_relevant=dim.why_relevant, cells=cells))
    return ComparisonTablePayload(entities=entities, dimensions=dims_out)


def _sanitize_insights(
    rows: list[InsightRow], *, index: dict[str, ReportEvidenceItem], warnings: list[str]
) -> list[InsightRow]:
    out: list[InsightRow] = []
    for row in rows:
        text = (row.text or "").strip()
        if not text:
            continue
        ids = [eid for eid in row.evidence_ids if eid in index]
        caveat = (row.caveat or "").strip()
        if row.type in {"inference", "caveated_projection"}:
            if len(ids) < 2:
                warnings.append("insight_inference_short_evidence")
                continue
            if not caveat:
                warnings.append("insight_inference_missing_caveat")
                continue
        else:
            if len(ids) < 1:
                warnings.append("insight_synthesis_no_evidence")
                continue
            if len(ids) < 2 and not (
                caveat and ("单来源" in caveat or "single-source" in caveat.lower())
            ):
                warnings.append("insight_synthesis_short_evidence")
                continue
        if not evidence_ids_core_eligible(ids, index=index):
            warnings.append("insight_non_core_evidence")
            continue
        out.append(InsightRow(text=text, type=row.type, evidence_ids=ids, caveat=caveat or ""))
    return out


def validate_and_sanitize_bundle(
    raw: dict[str, Any],
    *,
    claims: list[ReportClaimItem],
    research_question: str,
    deterministic_archetype: str,
    confidence_threshold: float,
    flags: StructuredSynthesisStageFlags,
) -> tuple[StructuredSynthesisBundle | None, list[str]]:
    warnings: list[str] = []
    if query_has_lexical_recency_or_update_markers(research_question):
        return None, ["recency_lexical_skips_structured_synthesis"]

    raw_work = dict(raw)
    if not flags.structure:
        raw_work.pop("archetype_judge", None)

    try:
        bundle = StructuredSynthesisBundle.model_validate(raw_work)
    except ValidationError as exc:
        return None, [f"schema_validation_error:{exc}"]

    index = build_claim_evidence_index(claims)

    judge_out: ArchetypeJudgePayload | None = bundle.archetype_judge
    if flags.structure:
        if judge_out is None:
            return None, ["missing_archetype_judge"]
        if judge_out.confidence < confidence_threshold:
            return None, ["archetype_confidence_below_threshold"]
        allowed_arch = {
            "research_survey",
            "technical_comparison",
            "news_update",
            "general",
        }
        if judge_out.report_archetype not in allowed_arch:
            return None, ["invalid_archetype_enum"]
        if judge_out.report_archetype != deterministic_archetype:
            warnings.append(
                f"archetype_judge_mismatch_llm={judge_out.report_archetype}"
                f"_deterministic={deterministic_archetype}"
            )
    else:
        judge_out = None

    method_cards: list[MethodCardPayload] = []
    if flags.method_cards and deterministic_archetype == "research_survey":
        method_cards = [_sanitize_method_card(c, index=index, claims=claims, warnings=warnings) for c in bundle.method_cards]

    comparison_out: ComparisonTablePayload | None = None
    if flags.comparison_table and deterministic_archetype == "technical_comparison":
        if bundle.comparison_table is not None:
            comparison_out = _sanitize_comparison_table(
                bundle.comparison_table,
                index=index,
                claims=claims,
                research_question=research_question,
                warnings=warnings,
            )

    insights_out = None
    if flags.insights and bundle.insights is not None:
        rows = _sanitize_insights(list(bundle.insights.insights), index=index, warnings=warnings)
        insights_out = InsightsPayload(insights=rows)

    return (
        StructuredSynthesisBundle(
            archetype_judge=judge_out,
            method_cards=method_cards,
            comparison_table=comparison_out,
            insights=insights_out,
        ),
        warnings,
    )


def bundle_has_renderable_content(bundle: StructuredSynthesisBundle, flags: StructuredSynthesisStageFlags) -> bool:
    if flags.structure and bundle.archetype_judge is not None:
        return True
    if flags.method_cards and bundle.method_cards:
        return True
    if flags.comparison_table and bundle.comparison_table and bundle.comparison_table.dimensions:
        return True
    if flags.insights and bundle.insights and bundle.insights.insights:
        return True
    return False
