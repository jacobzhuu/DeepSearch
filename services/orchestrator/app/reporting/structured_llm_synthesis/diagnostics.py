from __future__ import annotations

from collections import Counter

from services.orchestrator.app.reporting.structured_llm_synthesis.schema import (
    StructuredSynthesisBundle,
    StructuredSynthesisStageFlags,
)

_NON_CORE_WARNING_PREFIXES: frozenset[str] = frozenset(
    {
        "non_core_evidence_for_factual_field",
        "year_like_text_without_core_evidence",
        "comparison_cell_non_core_evidence",
        "insight_non_core_evidence",
        "method_insight_non_core_evidence",
    }
)

_COMPETITIVE_WARNING_PREFIXES: frozenset[str] = frozenset(
    {
        "prefixed_competitive_framing",
        "comparison_cell_competitive_tone",
    }
)

_DROPPED_FIELD_WARNING_PREFIXES: frozenset[str] = frozenset(
    {
        "factual_without_evidence_ids",
        "method_insight_insufficient_evidence_ids",
        "method_insight_missing_caveat",
        "insight_inference_short_evidence",
        "insight_inference_missing_caveat",
        "insight_synthesis_no_evidence",
        "insight_synthesis_short_evidence",
        "comparison_cell_no_evidence",
    }
)


def _warning_prefix(warning: str) -> str:
    return warning.split(":", 1)[0]


def warning_type_counts(warnings: list[str]) -> dict[str, int]:
    """Aggregate raw warning strings by stable prefix (before first ':')."""
    counts: Counter[str] = Counter(_warning_prefix(w) for w in warnings)
    return dict(sorted(counts.items()))


def aggregate_structured_synthesis_counts(warnings: list[str]) -> dict[str, int]:
    invalid_evidence = 0
    non_core = 0
    competitive = 0
    dropped_fields = 0
    for w in warnings:
        pfx = _warning_prefix(w)
        if pfx == "dropped_unknown_evidence_ids":
            invalid_evidence += 1
        if pfx in _NON_CORE_WARNING_PREFIXES:
            non_core += 1
        if pfx in _COMPETITIVE_WARNING_PREFIXES:
            competitive += 1
        if pfx in _DROPPED_FIELD_WARNING_PREFIXES or pfx.startswith("dropped_entity_not_evidence_backed"):
            dropped_fields += 1
    return {
        "invalid_evidence_ids": invalid_evidence,
        "non_core_evidence_rejections": non_core,
        "competitive_claims_marked": competitive,
        "dropped_fields_count": dropped_fields,
    }


def collect_sections_rendered(
    bundle: StructuredSynthesisBundle,
    flags: StructuredSynthesisStageFlags,
) -> list[str]:
    out: list[str] = []
    if flags.structure and bundle.archetype_judge is not None:
        out.append("archetype_judge")
    if flags.method_cards and bundle.method_cards:
        out.append("method_cards")
    if (
        flags.comparison_table
        and bundle.comparison_table is not None
        and bundle.comparison_table.dimensions
    ):
        out.append("comparison_table")
    if flags.insights and bundle.insights is not None and bundle.insights.insights:
        out.append("synthesis_insights")
    return out


def pack_structured_llm_synthesis_diagnostics(
    *,
    enabled: bool,
    attempted: bool,
    rendered: bool,
    skipped_reason: str,
    warnings: list[str],
    sections_rendered: list[str],
) -> dict[str, object]:
    metrics = aggregate_structured_synthesis_counts(warnings)
    return {
        "enabled": enabled,
        "attempted": attempted,
        "rendered": rendered,
        "skipped_reason": skipped_reason,
        "warnings_count": len(warnings),
        "dropped_fields_count": metrics["dropped_fields_count"],
        "invalid_evidence_ids": metrics["invalid_evidence_ids"],
        "non_core_evidence_rejections": metrics["non_core_evidence_rejections"],
        "competitive_claims_marked": metrics["competitive_claims_marked"],
        "sections_rendered": list(sections_rendered),
        "warning_type_counts": warning_type_counts(warnings),
    }
