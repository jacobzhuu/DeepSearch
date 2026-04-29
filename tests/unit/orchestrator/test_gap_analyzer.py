from __future__ import annotations

from services.orchestrator.app.research_quality import analyze_required_slot_gaps


def test_gap_analyzer_generates_supplemental_queries_for_missing_required_slots() -> None:
    result = analyze_required_slot_gaps(
        "How to deploy SearXNG with Docker?",
        slot_coverage_summary=[
            {
                "slot_id": "deployment_target",
                "label": "Deployment target",
                "required": True,
                "status": "covered",
                "expected_claim_categories": ["deployment/self_hosting"],
            },
            {
                "slot_id": "deployment_steps",
                "label": "Deployment steps",
                "required": True,
                "status": "missing",
                "expected_claim_categories": ["deployment/self_hosting", "feature"],
            },
            {
                "slot_id": "deployment_configuration",
                "label": "Configuration",
                "required": True,
                "status": "weak",
                "expected_claim_categories": ["deployment/self_hosting", "feature"],
            },
            {
                "slot_id": "deployment_limitations",
                "label": "Operational limitations",
                "required": False,
                "status": "missing",
            },
        ],
        round_no=1,
        max_rounds=2,
    )

    assert result.triggered is True
    assert result.reason == "missing_or_weak_required_slots"
    assert [slot["slot_id"] for slot in result.required_slots_missing] == ["deployment_steps"]
    assert [slot["slot_id"] for slot in result.required_slots_weak] == ["deployment_configuration"]
    assert [query.slot_ids for query in result.supplemental_queries] == [
        ("deployment_steps",),
        ("deployment_configuration",),
    ]
    assert "installation deployment steps" in result.supplemental_queries[0].query_text
    assert result.to_payload()["supplemental_queries"][0]["query_source"] == "gap_analyzer"


def test_gap_analyzer_stops_after_configured_round_limit() -> None:
    result = analyze_required_slot_gaps(
        "What is SearXNG?",
        slot_coverage_summary=[
            {
                "slot_id": "mechanism",
                "label": "How it works",
                "required": True,
                "status": "missing",
            }
        ],
        round_no=3,
        max_rounds=2,
    )

    assert result.triggered is False
    assert result.reason == "max_gap_rounds_reached"
    assert result.supplemental_queries == ()


def test_gap_analyzer_deduplicates_existing_queries() -> None:
    existing = {"What is SearXNG? how it works architecture official documentation"}
    result = analyze_required_slot_gaps(
        "What is SearXNG?",
        slot_coverage_summary=[
            {
                "slot_id": "mechanism",
                "label": "How it works",
                "required": True,
                "status": "missing",
                "expected_claim_categories": ["mechanism"],
            }
        ],
        round_no=1,
        max_rounds=2,
        existing_query_texts=existing,
    )

    assert result.triggered is True
    assert result.supplemental_queries[0].query_text != next(iter(existing))
    assert "technical overview" in result.supplemental_queries[0].query_text


def test_gap_analyzer_records_remaining_slots_when_round_limit_reached() -> None:
    result = analyze_required_slot_gaps(
        "What is SearXNG?",
        slot_coverage_summary=[
            {
                "slot_id": "mechanism",
                "label": "How it works",
                "required": True,
                "status": "missing",
                "expected_claim_categories": ["mechanism"],
            }
        ],
        round_no=3,
        max_rounds=2,
    )

    assert result.triggered is False
    assert result.reason == "max_gap_rounds_reached"
    assert [slot["slot_id"] for slot in result.required_slots_missing] == ["mechanism"]
    assert result.warnings
