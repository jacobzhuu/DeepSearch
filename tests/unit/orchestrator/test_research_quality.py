from __future__ import annotations

from services.orchestrator.app.research_quality import (
    answer_slot_coverage,
    answer_slots_for_query,
    classify_source_intent,
)


def test_source_intent_generalizes_docs_about_and_wikipedia() -> None:
    langgraph_about = classify_source_intent(
        canonical_url="https://docs.langchain.com/langgraph/concepts/overview",
        domain="docs.langchain.com",
        title="LangGraph overview",
        query="What is LangGraph and how does it work?",
    )
    opensearch_wikipedia = classify_source_intent(
        canonical_url="https://en.wikipedia.org/wiki/OpenSearch",
        domain="en.wikipedia.org",
        title="OpenSearch - Wikipedia",
        query="What is OpenSearch and how does it work?",
    )

    assert langgraph_about.source_intent == "official_about"
    assert langgraph_about.fetch_priority_score == 0
    assert opensearch_wikipedia.source_intent == "wikipedia_reference"
    assert opensearch_wikipedia.fetch_priority_score == 1


def test_source_intent_promotes_installation_only_for_deployment_queries() -> None:
    overview = classify_source_intent(
        canonical_url="https://docs.searxng.org/admin/installation.html",
        domain="docs.searxng.org",
        title="SearXNG installation",
        query="What is SearXNG and how does it work?",
    )
    deployment = classify_source_intent(
        canonical_url="https://docs.searxng.org/admin/installation.html",
        domain="docs.searxng.org",
        title="SearXNG installation",
        query="How can SearXNG be deployed with Docker?",
    )

    assert overview.source_intent == "official_installation_admin"
    assert overview.fetch_priority_score == 42
    assert deployment.fetch_priority_score == 0


def test_answer_slots_are_query_intent_specific() -> None:
    comparison_slots = answer_slots_for_query(
        "Compare SearXNG, Brave Search API, and Tavily for AI research agents."
    )
    privacy_coverage = answer_slot_coverage(
        "What are the privacy advantages and limitations of SearXNG?",
        {"privacy"},
    )

    assert [slot.slot_id for slot in comparison_slots] == [
        "comparison_scope",
        "comparison_mechanism",
        "comparison_tradeoffs",
    ]
    assert any(
        row["slot_id"] == "privacy_advantages" and row["covered"] for row in privacy_coverage
    )
