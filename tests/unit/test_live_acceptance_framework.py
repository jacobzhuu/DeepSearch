from __future__ import annotations

from scripts.live_acceptance_framework import (
    EXPECTED_RUNNING_MODE,
    evaluate_deployment_acceptance,
    evaluate_langgraph_acceptance,
    get_profiles,
    traceability_summary,
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
