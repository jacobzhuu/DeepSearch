from __future__ import annotations

from uuid import UUID

from services.orchestrator.app.services.debug_pipeline import (
    COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY_STOP,
    GAP_SEARCH_SKIP_CATEGORY_NOT_ALLOWED,
    GAP_SEARCH_SKIP_DUPLICATE_IN_ROUND,
    GAP_SEARCH_SKIP_INVALID_CANDIDATE_ID,
    GAP_SEARCH_SKIP_MISSING_CANDIDATE_ID,
    GAP_SEARCH_SKIP_PRIORITY_TOO_LOW,
    _maybe_suppress_strategist_gap_continue_for_coverage_alignment,
    _select_gap_search_candidate_ids,
)


def _fallback_analysis_covered_required() -> dict:
    return {
        "round_no": 1,
        "max_rounds": 3,
        "triggered": False,
        "reason": "required_slots_covered",
        "required_slots_missing": [],
        "required_slots_weak": [],
        "supplemental_queries": [],
        "warnings": [],
    }


def test_maybe_suppress_optional_weak_only_stops_strategist_continue_search() -> None:
    fb = _fallback_analysis_covered_required()
    merged = {
        **fb,
        "triggered": True,
        "reason": "llm_research_strategy_continue_search",
        "strategy_decision": "continue_search",
        "strategy_status": "used",
        "supplemental_queries": [{"query_text": "extra privacy search"}],
        "fallback_gap_analysis": dict(fb),
    }
    strategy_payload = {
        "status": "used",
        "decision": "continue_search",
        "planned_queries": [{"query_text": "extra privacy search"}],
    }
    slots = [
        {"slot_id": "definition", "required": True, "status": "covered"},
        {"slot_id": "privacy", "required": False, "status": "weak"},
    ]
    cov = {
        "overall_status": "sufficient",
        "required_slots_missing": [],
        "required_slots_weak": [],
    }
    out = _maybe_suppress_strategist_gap_continue_for_coverage_alignment(
        merged,
        coverage_evaluation=cov,
        slot_coverage_summary=slots,
        strategy_payload=strategy_payload,
        research_loop_enabled=True,
        research_loop_strategist_shadow_mode=False,
    )
    assert out["triggered"] is False
    assert out["reason"] == COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY_STOP
    assert out["loop_stop_reason"] == COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY_STOP
    assert out["supplemental_queries"] == []
    assert out["coverage_alignment"]["stop_reason"] == COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY_STOP


def test_maybe_suppress_not_applied_when_required_slot_weak() -> None:
    fb = _fallback_analysis_covered_required()
    merged = {
        **fb,
        "triggered": True,
        "reason": "llm_research_strategy_continue_search",
        "strategy_decision": "continue_search",
        "supplemental_queries": [{"query_text": "q"}],
        "fallback_gap_analysis": dict(fb),
    }
    strategy_payload = {
        "status": "used",
        "decision": "continue_search",
        "planned_queries": [{"query_text": "q"}],
    }
    cov = {
        "overall_status": "sufficient",
        "required_slots_missing": [],
        "required_slots_weak": [],
    }
    slots = [
        {"slot_id": "definition", "required": True, "status": "weak"},
    ]
    out = _maybe_suppress_strategist_gap_continue_for_coverage_alignment(
        merged,
        coverage_evaluation=cov,
        slot_coverage_summary=slots,
        strategy_payload=strategy_payload,
        research_loop_enabled=True,
        research_loop_strategist_shadow_mode=False,
    )
    assert out == merged


def test_maybe_suppress_not_applied_when_required_slot_missing() -> None:
    fb = {
        **_fallback_analysis_covered_required(),
        "required_slots_missing": [{"slot_id": "definition", "status": "missing"}],
    }
    merged = {
        **fb,
        "triggered": True,
        "reason": "llm_research_strategy_continue_search",
        "strategy_decision": "continue_search",
        "supplemental_queries": [{"query_text": "q"}],
        "fallback_gap_analysis": dict(fb),
    }
    strategy_payload = {
        "status": "used",
        "decision": "continue_search",
        "planned_queries": [{"query_text": "q"}],
    }
    cov = {
        "overall_status": "insufficient",
        "required_slots_missing": ["definition"],
        "required_slots_weak": [],
    }
    slots = [
        {"slot_id": "definition", "required": True, "status": "missing"},
    ]
    out = _maybe_suppress_strategist_gap_continue_for_coverage_alignment(
        merged,
        coverage_evaluation=cov,
        slot_coverage_summary=slots,
        strategy_payload=strategy_payload,
        research_loop_enabled=True,
        research_loop_strategist_shadow_mode=False,
    )
    assert out == merged


def test_maybe_suppress_not_applied_when_coverage_not_sufficient() -> None:
    fb = _fallback_analysis_covered_required()
    merged = {
        **fb,
        "triggered": True,
        "reason": "llm_research_strategy_continue_search",
        "strategy_decision": "continue_search",
        "supplemental_queries": [{"query_text": "q"}],
        "fallback_gap_analysis": dict(fb),
    }
    strategy_payload = {
        "status": "used",
        "decision": "continue_search",
        "planned_queries": [{"query_text": "q"}],
    }
    cov = {
        "overall_status": "insufficient",
        "required_slots_missing": [],
        "required_slots_weak": [],
    }
    slots = [{"slot_id": "definition", "required": True, "status": "covered"}]
    out = _maybe_suppress_strategist_gap_continue_for_coverage_alignment(
        merged,
        coverage_evaluation=cov,
        slot_coverage_summary=slots,
        strategy_payload=strategy_payload,
        research_loop_enabled=True,
        research_loop_strategist_shadow_mode=False,
    )
    assert out == merged


def test_select_gap_search_skips_standards_or_academic_category() -> None:
    cid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    selected, skipped = _select_gap_search_candidate_ids(
        {
            "selected_sources": [
                {
                    "candidate_url_id": cid,
                    "source_category": "standards_or_academic",
                    "fetch_priority_score": 15,
                }
            ]
        },
        limit=3,
    )
    assert selected == []
    assert len(skipped) == 1
    assert skipped[0]["skip_reason"] == GAP_SEARCH_SKIP_CATEGORY_NOT_ALLOWED


def test_select_gap_search_priority_too_low_when_category_allowed() -> None:
    cid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    selected, skipped = _select_gap_search_candidate_ids(
        {
            "selected_sources": [
                {
                    "candidate_url_id": cid,
                    "source_category": "official_docs_reference",
                    "fetch_priority_score": 35,
                }
            ]
        },
        limit=3,
    )
    assert selected == []
    assert skipped[0]["skip_reason"] == GAP_SEARCH_SKIP_PRIORITY_TOO_LOW


def test_select_gap_search_missing_and_invalid_ids() -> None:
    _, skipped_a = _select_gap_search_candidate_ids(
        {
            "selected_sources": [
                {"candidate_url_id": "", "source_category": "official_docs_reference"},
            ],
        },
        limit=3,
    )
    assert skipped_a[0]["skip_reason"] == GAP_SEARCH_SKIP_MISSING_CANDIDATE_ID

    _, skipped_b = _select_gap_search_candidate_ids(
        {
            "selected_sources": [
                {"candidate_url_id": "not-a-uuid", "source_category": "official_docs_reference"}
            ]
        },
        limit=3,
    )
    assert skipped_b[0]["skip_reason"] == GAP_SEARCH_SKIP_INVALID_CANDIDATE_ID


def test_select_gap_search_duplicate_in_round() -> None:
    cid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    selected, skipped = _select_gap_search_candidate_ids(
        {
            "selected_sources": [
                {
                    "candidate_url_id": cid,
                    "source_category": "official_docs_reference",
                    "fetch_priority_score": 10,
                },
                {
                    "candidate_url_id": cid,
                    "source_category": "official_docs_reference",
                    "fetch_priority_score": 10,
                },
            ]
        },
        limit=3,
    )
    assert selected == [UUID(cid)]
    assert len(skipped) == 1
    assert skipped[0]["skip_reason"] == GAP_SEARCH_SKIP_DUPLICATE_IN_ROUND
