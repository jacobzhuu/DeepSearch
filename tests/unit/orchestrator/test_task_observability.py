from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from packages.db.models import Claim, ResearchTask, TaskEvent
from services.orchestrator.app.api.routes.research_tasks import _derive_observability
from services.orchestrator.app.research_quality import build_slot_coverage_summary
from services.orchestrator.app.services.debug_pipeline import (
    _claim_slot_summary_row,
    _target_slot_ids_from_candidate_or_search_query,
)
from services.orchestrator.app.services.research_tasks import TaskSnapshot


def test_reporting_stage_does_not_overwrite_pipeline_evidence_yield() -> None:
    task_id = uuid4()
    task = ResearchTask(
        id=task_id,
        query="介绍一下gemini的近期讯息",
        status="COMPLETED",
        constraints_json={"report_language": "zh-CN"},
    )
    drafting_summary = {
        "total_candidates": 82,
        "accepted_candidates": 5,
        "rejected_candidates": 12,
        "unselected_candidates": 65,
    }
    report_summary = {
        "total_candidates": 5,
        "accepted_candidates": 5,
        "rejected_candidates": 0,
        "unselected_candidates": 0,
    }
    drafting_source_yield = [
        {"canonical_url": "https://example.com/full-pipeline", "contribution_level": "low"}
    ]
    report_source_yield = [
        {"canonical_url": "https://example.com/report-only", "contribution_level": "high"}
    ]
    events = [
        TaskEvent(
            id=uuid4(),
            task_id=task_id,
            event_type="pipeline.stage_completed",
            sequence_no=1,
            payload_json={
                "stage": "DRAFTING_CLAIMS",
                "result": {
                    "evidence_yield_summary": drafting_summary,
                    "source_yield_summary": drafting_source_yield,
                },
            },
            created_at=datetime(2026, 5, 8, 13, 22, 41, tzinfo=UTC),
        ),
        TaskEvent(
            id=uuid4(),
            task_id=task_id,
            event_type="pipeline.stage_completed",
            sequence_no=2,
            payload_json={
                "stage": "REPORTING",
                "result": {
                    "evidence_yield_summary": report_summary,
                    "source_yield_summary": report_source_yield,
                    "verification_summary": {"strong_supported_claim_count": 5},
                },
            },
            created_at=datetime(2026, 5, 8, 13, 23, 16, tzinfo=UTC),
        ),
    ]

    observability = _derive_observability(TaskSnapshot(task=task, events=events))

    assert observability is not None
    assert observability.evidence_yield_summary == drafting_summary
    assert observability.source_yield_summary == drafting_source_yield
    assert observability.verification_summary == {"strong_supported_claim_count": 5}


def test_observability_treats_old_score_rejections_as_unselected() -> None:
    task_id = uuid4()
    task = ResearchTask(
        id=task_id,
        query="介绍一下gemini的近期讯息",
        status="DRAFTING_CLAIMS",
        constraints_json={},
    )
    accepted_candidate = {
        "evidence_candidate_id": "ec-accepted",
        "source_document_id": "source-1",
        "slot_ids": ["overview"],
        "rejection_reasons": [],
    }
    old_score_rejected_candidate = {
        "evidence_candidate_id": "ec-score",
        "source_document_id": "source-1",
        "slot_ids": ["details"],
        "rejection_reasons": ["insufficient_answer_score", "not_answer_relevant"],
    }
    hard_rejected_candidate = {
        "evidence_candidate_id": "ec-hard",
        "source_document_id": "source-1",
        "slot_ids": ["details"],
        "rejection_reasons": ["reference_or_citation", "insufficient_answer_score"],
    }
    events = [
        TaskEvent(
            id=uuid4(),
            task_id=task_id,
            event_type="pipeline.stage_completed",
            sequence_no=1,
            payload_json={
                "stage": "DRAFTING_CLAIMS",
                "result": {
                    "diagnostics": {
                        "evidence_candidates": [
                            accepted_candidate,
                            old_score_rejected_candidate,
                            hard_rejected_candidate,
                        ],
                        "accepted_evidence_candidate_ids": ["ec-accepted"],
                    },
                    "evidence_yield_summary": {
                        "total_candidates": 3,
                        "accepted_candidates": 1,
                        "rejected_candidates": 2,
                    },
                },
            },
            created_at=datetime(2026, 5, 8, 13, 22, 41, tzinfo=UTC),
        )
    ]

    observability = _derive_observability(TaskSnapshot(task=task, events=events))

    assert observability is not None
    assert observability.evidence_yield_summary["total_candidates"] == 3
    assert observability.evidence_yield_summary["accepted_candidates"] == 1
    assert observability.evidence_yield_summary["rejected_candidates"] == 1
    assert observability.evidence_yield_summary["unselected_candidates"] == 1
    assert observability.evidence_yield_summary["top_rejection_reasons"] == [
        {"reason": "reference_or_citation", "count": 1}
    ]


def test_source_yield_target_slots_use_search_query_raw_response_metadata() -> None:
    search_query = SimpleNamespace(
        raw_response_json={
            "expansion_metadata": {
                "target_slots": ["definition", " mechanism ", ""],
            },
        },
    )

    assert _target_slot_ids_from_candidate_or_search_query({}, search_query) == [
        "definition",
        "mechanism",
    ]


def test_source_yield_target_slots_prefer_candidate_metadata() -> None:
    search_query = SimpleNamespace(
        raw_response_json={
            "expansion_metadata": {"target_slots": ["definition"]},
        },
    )

    assert _target_slot_ids_from_candidate_or_search_query(
        {"target_slots": ["official_news"]},
        search_query,
    ) == ["official_news"]


def test_claim_slot_summary_uses_nested_evidence_candidate_slot_ids() -> None:
    query = "What is LangGraph and how does it work?"
    claim = Claim(
        task_id=uuid4(),
        statement="StateGraph is a builder class and cannot be used directly for execution.",
        claim_type="fact",
        confidence=0.9,
        verification_status="supported",
        notes_json={
            "report_eligible": True,
            "claim_category": "definition",
            "evidence_candidate": {
                "source_document_id": "source-stategraph",
                "slot_ids": ["limitations"],
            },
        },
    )

    row = _claim_slot_summary_row(claim, query=query)
    summary = build_slot_coverage_summary(query, claim_rows=[row] if row else [])
    limitations = next(item for item in summary if item["slot_id"] == "limitations")

    assert row is not None
    assert row["slot_ids"] == ["limitations"]
    assert row["source_document_id"] == "source-stategraph"
    assert limitations["supported_claim_count"] == 1
    assert limitations["status"] == "covered"


def test_gap_rounds_only_from_researching_more_stage_completed() -> None:
    """``pipeline.gap_analysis`` / ``research_strategy`` share ``stage=RESEARCHING_MORE`` but are not rounds."""
    task_id = uuid4()
    task = ResearchTask(
        id=task_id,
        query="q",
        status="COMPLETED",
        constraints_json={},
    )
    events = [
        TaskEvent(
            id=uuid4(),
            task_id=task_id,
            event_type="pipeline.gap_analysis",
            sequence_no=1,
            payload_json={
                "stage": "RESEARCHING_MORE",
                "result": {"gap_analysis": {"triggered": True, "round_no": 1}},
            },
            created_at=datetime(2026, 5, 8, 13, 22, 41, tzinfo=UTC),
        ),
        TaskEvent(
            id=uuid4(),
            task_id=task_id,
            event_type="pipeline.stage_completed",
            sequence_no=2,
            payload_json={
                "stage": "RESEARCHING_MORE",
                "result": {
                    "gap_analysis": {"round_no": 1},
                    "gap_round_diagnostics": {
                        "gap_round_outcome": "drafted",
                        "gap_round_index": 1,
                        "drafting_created_claims": 1,
                        "verification_supported_claims": 1,
                        "supplemental_search_failed": False,
                    },
                    "search": {"search_queries": [{"query_text": "extra"}]},
                },
            },
            created_at=datetime(2026, 5, 8, 13, 23, 16, tzinfo=UTC),
        ),
    ]
    obs = _derive_observability(TaskSnapshot(task=task, events=events))
    assert obs is not None
    assert len(obs.gap_rounds) == 1
    assert len(obs.research_rounds) == 1
    assert obs.research_rounds[0]["status"] == "produced"
