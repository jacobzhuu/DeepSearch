from __future__ import annotations

from scripts.phase2_multiformat_benchmark import aggregate_results
from scripts.phase3_intelligence_benchmark import build_result


def test_phase2_benchmark_aggregate_exposes_multiformat_gaps() -> None:
    aggregate = aggregate_results(
        [
            {
                "completed": True,
                "source_formats": ["html", "pdf"],
                "has_rerank_diagnostics": True,
                "unsupported_or_failed_parse_count": 1,
            },
            {
                "completed": False,
                "source_formats": ["docx"],
                "has_rerank_diagnostics": False,
                "unsupported_or_failed_parse_count": 0,
            },
        ]
    )

    assert aggregate["completed_count"] == 1
    assert aggregate["has_pdf"] is True
    assert aggregate["has_office"] is True
    assert aggregate["has_rerank_diagnostics"] is True
    assert aggregate["unsupported_or_failed_parse_count"] == 1


def test_phase3_benchmark_counts_gaps_claims_and_source_judge() -> None:
    result = build_result(
        query="What is SearXNG?",
        task_id="task",
        status="COMPLETED",
        plan={"research_plan": {"intent": "definition"}, "planner_status": "created"},
        plan_readback={"research_plan": {"intent": "definition"}},
        detail={
            "progress": {
                "observability": {
                    "slot_coverage_summary": [
                        {"required": True, "status": "missing"},
                        {"required": True, "status": "covered"},
                    ],
                    "gap_rounds": [{"round": 1}],
                    "source_judgments": [{"fallback_status": "disabled"}],
                }
            }
        },
        claims={
            "claims": [
                {"verification_status": "unsupported"},
                {"verification_status": "mixed"},
            ]
        },
        report={"format": "markdown", "manifest": {"citation_coverage": 0.8}},
    )

    assert result["completed"] is True
    assert result["gap_count"] == 1
    assert result["query_iterations"] == 1
    assert result["source_judge_count"] == 1
    assert result["unsupported_claim_count"] == 1
    assert result["mixed_or_contradicted_claim_count"] == 1
    assert result["citation_coverage"] == 0.8
