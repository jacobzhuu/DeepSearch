from __future__ import annotations

from services.orchestrator.app.research_quality.coverage_evaluator import (
    evaluate_research_coverage,
)


def test_coverage_evaluator_allows_stop_when_slots_and_sources_are_sufficient() -> None:
    result = evaluate_research_coverage(
        slot_coverage_summary=[
            {
                "slot_id": "definition",
                "required": True,
                "status": "covered",
                "supported_claim_count": 1,
                "source_count": 1,
            },
            {
                "slot_id": "mechanism",
                "required": True,
                "status": "covered",
                "supported_claim_count": 2,
                "source_count": 2,
            },
        ],
        source_yield_summary=[
            {
                "domain": "platform.openai.com",
                "source_intent": "official_docs_reference",
                "fetched": True,
            },
            {"domain": "docs.example.org", "source_intent": "reference", "parsed": True},
            {"domain": "tutorial.example", "source_intent": "generic_article", "parsed": True},
        ],
        required_slot_min_status="moderate",
        min_distinct_domains=3,
        min_authoritative_sources=1,
        min_source_roles=2,
    )

    assert result.can_stop is True
    assert result.stop_reason == "coverage_sufficient"
    assert result.required_slots_sufficient == 2
    assert result.source_roles >= 2


def test_coverage_evaluator_blocks_missing_required_slots() -> None:
    result = evaluate_research_coverage(
        slot_coverage_summary=[
            {"slot_id": "definition", "required": True, "status": "missing"},
            {"slot_id": "mechanism", "required": True, "status": "weak"},
        ],
        source_yield_summary=[],
        required_slot_min_status="moderate",
        min_distinct_domains=0,
        min_authoritative_sources=0,
        min_source_roles=0,
    )

    assert result.can_stop is False
    assert result.required_slots_missing == ("definition",)
    assert result.required_slots_weak == ("mechanism",)
    assert result.stop_reason == "required_slots_below_threshold"


def test_coverage_evaluator_reports_budget_exhausted_low_coverage_warning() -> None:
    result = evaluate_research_coverage(
        slot_coverage_summary=[
            {"slot_id": "definition", "required": True, "status": "weak"},
        ],
        source_yield_summary=[
            {"domain": "example.com", "source_intent": "reference", "fetched": True}
        ],
        budget_exhausted=True,
        allow_low_coverage_report=True,
    )

    assert result.can_stop is False
    assert result.overall_status == "budget_exhausted_partial"
    assert result.stop_reason == "coverage_partial_budget_exhausted"


def test_coverage_evaluator_reports_failed_no_evidence() -> None:
    result = evaluate_research_coverage(
        slot_coverage_summary=[
            {"slot_id": "definition", "required": True, "status": "missing"},
        ],
        source_yield_summary=[],
        budget_exhausted=True,
        allow_low_coverage_report=True,
    )

    assert result.can_stop is False
    assert result.overall_status == "insufficient"
    assert result.stop_reason == "coverage_failed_no_evidence"


def test_coverage_evaluator_blocks_insufficient_role_diversity() -> None:
    result = evaluate_research_coverage(
        slot_coverage_summary=[
            {"slot_id": "definition", "required": True, "status": "strong"},
        ],
        source_yield_summary=[
            {"domain": "a.com", "source_intent": "reference", "fetched": True},
            {"domain": "b.com", "source_intent": "reference", "fetched": True},
            {"domain": "c.com", "source_intent": "reference", "fetched": True},
        ],
        min_distinct_domains=3,
        min_source_roles=2,
    )

    assert result.can_stop is False
    assert "source_role_diversity_below_threshold" in result.warnings
    assert result.source_roles == 1
