from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from packages.db.models import CandidateUrl, FetchJob, ResearchRun, SearchQuery
from packages.db.repositories import (
    CandidateUrlRepository,
    FetchJobRepository,
    ResearchRunRepository,
    SearchQueryRepository,
)
from services.orchestrator.app.research_quality import analyze_required_slot_gaps
from services.orchestrator.app.services.acquisition import FETCH_MODE_HTTP, FETCH_STATUS_FAILED
from services.orchestrator.app.services.debug_pipeline import (
    _select_gap_search_candidate_ids,
    _select_supplemental_candidates,
)
from services.orchestrator.app.services.research_tasks import create_research_task_service


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


def test_gap_analyzer_targets_owned_langgraph_sources_for_missing_overview_slots() -> None:
    result = analyze_required_slot_gaps(
        "What is LangGraph and how does it work?",
        slot_coverage_summary=[
            {
                "slot_id": "definition",
                "label": "What it is",
                "required": True,
                "status": "missing",
                "expected_claim_categories": ["definition"],
            },
            {
                "slot_id": "mechanism",
                "label": "How it works",
                "required": True,
                "status": "missing",
                "expected_claim_categories": ["mechanism"],
            },
        ],
        round_no=1,
        max_rounds=2,
        max_queries_per_round=4,
    )

    assert [query.query_text for query in result.supplemental_queries] == [
        "LangGraph site:docs.langchain.com",
        "LangGraph site:reference.langchain.com",
        "LangGraph github langchain-ai langgraph",
        "LangGraph docs langchain",
    ]
    assert all(
        set(query.slot_ids) == {"definition", "mechanism"} for query in result.supplemental_queries
    )


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


def test_gap_round_candidate_selection_skips_low_value_new_results_for_fallback() -> None:
    selected, skipped = _select_gap_search_candidate_ids(
        {
            "selected_sources": [
                {
                    "candidate_url_id": "11111111-1111-4111-8111-111111111111",
                    "canonical_url": "https://hk.linkedin.com/in/example",
                    "source_category": "generic_article",
                    "fetch_priority_score": 20,
                },
                {
                    "candidate_url_id": "22222222-2222-4222-8222-222222222222",
                    "canonical_url": "https://reference.langchain.com/python/langgraph",
                    "source_category": "official_docs_reference",
                    "fetch_priority_score": 10,
                },
                {
                    "candidate_url_id": "33333333-3333-4333-8333-333333333333",
                    "canonical_url": "https://docs.langchain.com/langsmith/data-storage-and-privacy",
                    "source_category": "official_docs_reference",
                    "fetch_priority_score": 35,
                },
            ]
        },
        limit=2,
    )

    assert [str(item) for item in selected] == ["22222222-2222-4222-8222-222222222222"]
    assert [item["skip_reason"] for item in skipped] == [
        "gap_category_not_allowed",
        "gap_priority_too_low",
    ]


def test_gap_round_supplemental_fallback_selects_unattempted_owned_sources(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is LangGraph and how does it work?",
        constraints={},
    )
    run = ResearchRunRepository(db_session).add(
        ResearchRun(
            task_id=task.id,
            round_no=1,
            current_state="PLANNED",
            checkpoint_json={"task_revision_no": 1},
        )
    )
    search_query = SearchQueryRepository(db_session).add(
        SearchQuery(
            task_id=task.id,
            run_id=run.id,
            query_text=task.query,
            provider="searxng",
            round_no=1,
            issued_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
            raw_response_json={"task_revision_no": 1},
        )
    )
    mirror_candidate = _add_gap_candidate(
        db_session,
        task_id=task.id,
        search_query_id=search_query.id,
        canonical_url="https://github.langchain.ac.cn/langgraph/",
        domain="github.langchain.ac.cn",
        title="LangGraph docs mirror",
        rank=1,
    )
    docs_candidate = _add_gap_candidate(
        db_session,
        task_id=task.id,
        search_query_id=search_query.id,
        canonical_url="https://docs.langchain.com/oss/python/langgraph/overview",
        domain="docs.langchain.com",
        title="LangGraph overview - Docs by LangChain",
        rank=2,
    )
    reference_candidate = _add_gap_candidate(
        db_session,
        task_id=task.id,
        search_query_id=search_query.id,
        canonical_url="https://reference.langchain.com/python/langgraph",
        domain="reference.langchain.com",
        title="langgraph - LangChain Reference Docs",
        rank=3,
    )
    _add_gap_candidate(
        db_session,
        task_id=task.id,
        search_query_id=search_query.id,
        canonical_url="https://www.freelancer.hk/job-search/langgraph/",
        domain="www.freelancer.hk",
        title="LangGraph jobs",
        rank=4,
    )
    FetchJobRepository(db_session).add(
        FetchJob(
            task_id=task.id,
            candidate_url_id=mirror_candidate.id,
            mode=FETCH_MODE_HTTP,
            status=FETCH_STATUS_FAILED,
        )
    )
    db_session.commit()

    selected, skipped = _select_supplemental_candidates(
        db_session,
        task.id,
        query=task.query,
        limit=2,
        high_value_only=True,
    )

    assert [candidate.id for candidate in selected] == [
        docs_candidate.id,
        reference_candidate.id,
    ]
    skipped_by_url = {item["canonical_url"]: item["skip_reason"] for item in skipped}
    assert skipped_by_url["https://github.langchain.ac.cn/langgraph/"] == "already_attempted"
    assert (
        skipped_by_url["https://www.freelancer.hk/job-search/langgraph/"]
        == "low_priority_for_overview_supplemental_acquisition"
    )


def _add_gap_candidate(
    db_session: Session,
    *,
    task_id: object,
    search_query_id: object,
    canonical_url: str,
    domain: str,
    title: str,
    rank: int,
) -> CandidateUrl:
    candidate = CandidateUrlRepository(db_session).add(
        CandidateUrl(
            task_id=task_id,
            search_query_id=search_query_id,
            original_url=canonical_url,
            canonical_url=canonical_url,
            domain=domain,
            title=title,
            rank=rank,
            selected=False,
            metadata_json={},
        )
    )
    db_session.flush()
    return candidate
