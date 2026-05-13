from __future__ import annotations

from scripts.live_acceptance_framework import (
    COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY,
    EXPECTED_RUNNING_MODE,
    GAP_SKIP_CATEGORY_NOT_ALLOWED,
    GAP_SKIP_PRIORITY_TOO_LOW,
    NO_SELECTED_CANDIDATES,
    evaluate_deployment_acceptance,
    evaluate_langgraph_acceptance,
    get_profiles,
    print_gap_loop_summary_to_stdout,
    running_mode_has_required_capabilities,
    summarize_gap_loop_from_events,
    traceability_summary,
    write_artifacts,
)


def test_profiles_include_langgraph_and_deployment_acceptance() -> None:
    profiles = get_profiles()

    assert set(profiles) == {
        "langgraph-technical-explanation",
        "searxng-docker-deployment",
    }
    assert profiles["langgraph-technical-explanation"].query == (
        "What is LangGraph and how does it work?"
    )


def test_langgraph_profile_checks_technical_traceable_non_deployment_report() -> None:
    payloads = {
        "detail": {
            "status": "COMPLETED",
            "progress": {
                "observability": {
                    "running_mode": EXPECTED_RUNNING_MODE,
                    "planner_status": "success",
                    "plan_source": "llm_planner",
                }
            },
        },
        "run": {"running_mode": EXPECTED_RUNNING_MODE},
        "source_documents": {
            "source_documents": [
                {"canonical_url": "https://docs.langchain.com/oss/python/langgraph/overview"}
            ]
        },
        "source_chunks": {
            "source_chunks": [
                {
                    "text": (
                        "LangGraph is a framework for stateful graph workflows with nodes, "
                        "edges, routing, memory, streaming, and checkpointing."
                    ),
                    "metadata": {},
                }
            ]
        },
        "claims": {
            "claims": [
                {
                    "claim_id": "claim-1",
                    "statement": "LangGraph models agent workflows as state graphs.",
                    "verification_status": "supported",
                }
            ]
        },
        "claim_evidence": {
            "claim_evidence": [
                {
                    "claim_evidence_id": "evidence-1",
                    "claim_id": "claim-1",
                    "citation_span_id": "citation-1",
                    "relation_type": "support",
                    "excerpt": "LangGraph is a framework for stateful graph workflows.",
                }
            ]
        },
        "report": {
            "report_language": "en-US",
            "writer_mode": "llm_grounded",
            "llm_writer_status": "used",
            "markdown": "\n".join(
                [
                    "# Research Report: What is LangGraph and how does it work?",
                    "## What Is LangGraph?",
                    (
                        "LangGraph is explained as a technical framework for graph-based, "
                        "stateful workflow orchestration. It connects nodes and edges around "
                        "shared state so an application can route work between steps."
                    ),
                    "## How It Works",
                    (
                        "The workflow runs through graph nodes, state updates, conditional "
                        "edges, durable checkpointing, memory, streaming, and "
                        "human-in-the-loop control."
                    ),
                ]
            ),
        },
        "events": {"events": [{"event_type": "research_plan.created"}]},
        "search_queries": {"search_queries": []},
        "candidate_urls": {"candidate_urls": []},
    }

    result = evaluate_langgraph_acceptance("task-id", payloads["run"], payloads)

    assert result["passed"] is True
    assert result["checks"]["deployment_specific_checks_not_applied"] is True
    assert "term_coverage" not in result
    assert "group_coverage" not in result
    assert result["traceability"] == {
        "claim_trace": True,
        "claim_evidence_trace": True,
        "citation_trace": True,
    }


def test_traceability_summary_uses_api_artifacts_when_markdown_hides_internal_ids() -> None:
    payloads = {
        "claims": {
            "claims": [
                {
                    "claim_id": "claim-1",
                    "verification_status": "supported",
                }
            ]
        },
        "claim_evidence": {
            "claim_evidence": [
                {
                    "claim_evidence_id": "evidence-1",
                    "claim_id": "claim-1",
                    "citation_span_id": "citation-1",
                    "relation_type": "support",
                }
            ]
        },
    }

    clean_trace = traceability_summary("Clean report text without internal IDs.", payloads=payloads)
    markdown_only_trace = traceability_summary(
        "Clean report text without internal IDs.",
        payloads=payloads,
        require_markdown_ids=True,
    )

    assert clean_trace == {
        "claim_trace": True,
        "claim_evidence_trace": True,
        "citation_trace": True,
    }
    assert markdown_only_trace == {
        "claim_trace": False,
        "claim_evidence_trace": False,
        "citation_trace": False,
    }


def test_deployment_profile_preserves_existing_term_checks() -> None:
    payloads = {
        "detail": {
            "status": "COMPLETED",
            "progress": {"observability": {"running_mode": EXPECTED_RUNNING_MODE}},
        },
        "run": {"running_mode": EXPECTED_RUNNING_MODE},
        "source_documents": {"source_documents": []},
        "source_chunks": {"source_chunks": [{"text": "Docker", "metadata": {}}]},
        "claims": {"claims": []},
        "claim_evidence": {"claim_evidence": []},
        "report": {
            "report_language": "zh-CN",
            "writer_mode": "llm_grounded",
            "llm_writer_status": "used",
            "markdown": (
                "中文 Claim `claim-1`; claim_evidence: `evidence-1`; citation `citation-1`."
            ),
        },
        "events": {"events": []},
        "search_queries": {"search_queries": []},
        "candidate_urls": {"candidate_urls": []},
    }

    result = evaluate_deployment_acceptance("task-id", payloads["run"], payloads)

    assert result["passed"] is False
    assert result["checks"]["all_expected_terms_in_source_chunks"] is False
    assert result["source_gaps"]


def test_running_mode_legacy_exact_string_passes() -> None:
    assert running_mode_has_required_capabilities(EXPECTED_RUNNING_MODE) is True


def test_running_mode_extended_strategy_review_passes() -> None:
    assert (
        running_mode_has_required_capabilities(
            "real-search+opensearch+planner+report-LLM+assist-judge-strategy-review"
        )
        is True
    )


def test_running_mode_missing_planner_fails() -> None:
    assert (
        running_mode_has_required_capabilities(
            "real-search+opensearch+report-LLM+assist-judge-strategy-review"
        )
        is False
    )


def test_running_mode_missing_report_llm_fails() -> None:
    assert (
        running_mode_has_required_capabilities(
            "real-search+opensearch+planner+assist-judge-strategy-review"
        )
        is False
    )


def test_gap_summary_empty_events_all_zeros() -> None:
    summary = summarize_gap_loop_from_events([])
    assert summary["gap_analysis_events"] == 0
    assert summary["research_strategy_events"] == 0
    assert summary["research_more_stage_completed_count"] == 0
    assert summary["no_selected_candidates_count"] == 0
    assert summary["coverage_sufficient_optional_weak_only_count"] == 0
    assert summary["gap_category_not_allowed_count"] == 0
    assert summary["gap_priority_too_low_count"] == 0
    assert summary["skip_drafting_reason_distribution"] == {}


def test_gap_summary_optional_weak_suppression_counts() -> None:
    events = [
        {
            "sequence_no": 1,
            "event_type": "pipeline.research_strategy",
            "payload": {
                "result": {"decision": "continue_search", "planned_queries": [{"query_text": "x"}]}
            },
        },
        {
            "sequence_no": 2,
            "event_type": "pipeline.gap_analysis",
            "payload": {
                "result": {
                    "triggered": False,
                    "reason": COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY,
                    "loop_stop_reason": COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY,
                    "coverage_alignment": {
                        "suppressed_strategist_decision": "continue_search",
                        "stop_reason": COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY,
                    },
                }
            },
        },
    ]
    summary = summarize_gap_loop_from_events(events)
    assert summary["gap_analysis_events"] == 1
    assert summary["research_strategy_events"] == 1
    assert summary["research_more_stage_completed_count"] == 0
    assert summary["coverage_sufficient_optional_weak_only_count"] == 1
    assert summary["suppressed_strategist_decision_distribution"] == {"continue_search": 1}
    assert summary["loop_stop_reason_distribution"] == {
        COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY: 1,
    }
    assert summary["gap_analysis_reason_distribution"] == {
        COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY: 1,
    }


def test_gap_summary_researching_more_drafted_round() -> None:
    events = [
        {
            "sequence_no": 1,
            "event_type": "pipeline.stage_completed",
            "payload": {
                "stage": "RESEARCHING_MORE",
                "result": {
                    "gap_round_diagnostics": {
                        "gap_round_outcome": "drafted",
                        "skip_drafting_reason": None,
                        "drafting_created_claims": 2,
                        "verification_supported_claims": 1,
                    },
                    "skipped_gap_search_sources": [],
                },
            },
        },
    ]
    summary = summarize_gap_loop_from_events(events)
    assert summary["research_more_stage_completed_count"] == 1
    assert summary["gap_rounds_with_drafting"] == 1
    assert summary["gap_rounds_skipped_drafting"] == 0
    assert summary["nested_drafting_created_claims_total"] == 2
    assert summary["nested_verification_supported_claims_total"] == 1


def test_gap_summary_no_selected_and_granular_skips() -> None:
    events = [
        {
            "sequence_no": 1,
            "event_type": "pipeline.stage_completed",
            "payload": {
                "stage": "RESEARCHING_MORE",
                "result": {
                    "gap_round_diagnostics": {
                        "gap_round_outcome": "skipped_drafting",
                        "skip_drafting_reason": NO_SELECTED_CANDIDATES,
                    },
                    "skipped_gap_search_sources": [
                        {"skip_reason": GAP_SKIP_CATEGORY_NOT_ALLOWED},
                        {"skip_reason": GAP_SKIP_PRIORITY_TOO_LOW},
                    ],
                },
            },
        },
    ]
    summary = summarize_gap_loop_from_events(events)
    assert summary["no_selected_candidates_count"] == 1
    assert summary["gap_category_not_allowed_count"] == 1
    assert summary["gap_priority_too_low_count"] == 1
    assert summary["gap_rounds_skipped_drafting"] == 1
    assert summary["skip_drafting_reason_distribution"] == {NO_SELECTED_CANDIDATES: 1}


def test_write_artifacts_writes_gap_summary_and_appends_summary_md(tmp_path) -> None:
    gap_summary = summarize_gap_loop_from_events([])
    (tmp_path / "summary.md").write_text("# Run\n", encoding="utf-8")
    result = {
        "payloads": {
            "events": {"events": []},
            "detail": {},
            "report": {},
            "source_documents": {},
            "source_chunks": {},
            "claims": {},
            "claim_evidence": {},
            "search_queries": {},
            "candidate_urls": {},
        },
        "acceptance": {"passed": True},
        "gap_summary": gap_summary,
    }
    write_artifacts(tmp_path, result)
    assert (tmp_path / "gap_summary.json").is_file()
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "Gap / research loop summary" in text


def test_gap_summarize_counts_nested_skip_drafting_reason() -> None:
    events = [
        {
            "sequence_no": 1,
            "event_type": "pipeline.stage_completed",
            "payload": {
                "stage": "RESEARCHING_MORE",
                "result": {
                    "gap_round_diagnostics": {
                        "gap_round_outcome": "skipped_drafting",
                        "skip_drafting_reason": "no_new_chunks",
                    },
                },
            },
        },
    ]
    summary = summarize_gap_loop_from_events(events)
    assert summary["skip_drafting_reason_distribution"] == {"no_new_chunks": 1}


def test_gap_summarize_top_level_skip_when_nested_missing() -> None:
    events = [
        {
            "sequence_no": 1,
            "event_type": "pipeline.stage_completed",
            "payload": {
                "stage": "RESEARCHING_MORE",
                "result": {
                    "skip_drafting_reason": "no_source_chunks",
                    "gap_round_diagnostics": {
                        "gap_round_outcome": "skipped_drafting",
                    },
                },
            },
        },
    ]
    summary = summarize_gap_loop_from_events(events)
    assert summary["skip_drafting_reason_distribution"] == {"no_source_chunks": 1}


def test_gap_summarize_unknown_only_when_skip_missing_for_skipped_drafting() -> None:
    events = [
        {
            "sequence_no": 1,
            "event_type": "pipeline.stage_completed",
            "payload": {
                "stage": "RESEARCHING_MORE",
                "result": {
                    "gap_round_diagnostics": {
                        "gap_round_outcome": "skipped_drafting",
                    },
                },
            },
        },
    ]
    summary = summarize_gap_loop_from_events(events)
    assert summary["skip_drafting_reason_distribution"] == {"unknown": 1}


def test_print_gap_loop_summary_to_stdout_smoke() -> None:
    import io

    buf = io.StringIO()
    print_gap_loop_summary_to_stdout(summarize_gap_loop_from_events([]), stream=buf)
    out = buf.getvalue()
    assert "Gap summary:" in out
    assert "gap_analysis_events: 0" in out


def test_langgraph_acceptance_passes_with_extended_running_mode() -> None:
    extended = "real-search+opensearch+planner+report-LLM+assist-judge-strategy-review"
    payloads = {
        "detail": {
            "status": "COMPLETED",
            "progress": {
                "observability": {
                    "running_mode": extended,
                    "planner_status": "success",
                    "plan_source": "llm_planner",
                }
            },
        },
        "run": {"running_mode": extended},
        "source_documents": {
            "source_documents": [
                {"canonical_url": "https://docs.langchain.com/oss/python/langgraph/overview"}
            ]
        },
        "source_chunks": {
            "source_chunks": [
                {
                    "text": (
                        "LangGraph is a framework for stateful graph workflows with nodes, "
                        "edges, routing, memory, streaming, and checkpointing."
                    ),
                    "metadata": {},
                }
            ]
        },
        "claims": {
            "claims": [
                {
                    "claim_id": "claim-1",
                    "statement": "LangGraph models agent workflows as state graphs.",
                    "verification_status": "supported",
                }
            ]
        },
        "claim_evidence": {
            "claim_evidence": [
                {
                    "claim_evidence_id": "evidence-1",
                    "claim_id": "claim-1",
                    "citation_span_id": "citation-1",
                    "relation_type": "support",
                    "excerpt": "LangGraph is a framework for stateful graph workflows.",
                }
            ]
        },
        "report": {
            "report_language": "en-US",
            "writer_mode": "llm_grounded",
            "llm_writer_status": "used",
            "markdown": "\n".join(
                [
                    "# Research Report: What is LangGraph and how does it work?",
                    "## What Is LangGraph?",
                    (
                        "LangGraph is explained as a technical framework for graph-based, "
                        "stateful workflow orchestration. It connects nodes and edges around "
                        "shared state so an application can route work between steps."
                    ),
                    "## How It Works",
                    (
                        "The workflow runs through graph nodes, state updates, conditional "
                        "edges, durable checkpointing, memory, streaming, and "
                        "human-in-the-loop control."
                    ),
                ]
            ),
        },
        "events": {"events": [{"event_type": "research_plan.created"}]},
        "search_queries": {"search_queries": []},
        "candidate_urls": {"candidate_urls": []},
    }

    result = evaluate_langgraph_acceptance("task-id", payloads["run"], payloads)
    assert result["checks"]["running_mode_real_pipeline"] is True
    assert result["passed"] is True
