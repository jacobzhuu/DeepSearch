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


def test_source_intent_does_not_promote_generic_what_is_pages_to_official() -> None:
    query = "What is LangGraph and how does it work?"
    geeksforgeeks = classify_source_intent(
        canonical_url="https://www.geeksforgeeks.org/machine-learning/what-is-langgraph/",
        domain="www.geeksforgeeks.org",
        title="What is LangGraph - GeeksForGeeks",
        query=query,
    )
    ibm = classify_source_intent(
        canonical_url="https://www.ibm.com/think/topics/langgraph",
        domain="www.ibm.com",
        title="What is LangGraph? - IBM",
        query=query,
    )
    official_docs = classify_source_intent(
        canonical_url="https://docs.langchain.com/oss/python/langgraph/overview",
        domain="docs.langchain.com",
        title="LangGraph overview - Docs by LangChain",
        query=query,
    )
    reference_docs = classify_source_intent(
        canonical_url="https://reference.langchain.com/python/langgraph",
        domain="reference.langchain.com",
        title="langgraph - LangChain Reference Docs",
        query=query,
    )
    unrelated_docs = classify_source_intent(
        canonical_url="https://docs.langchain.com/langsmith/data-storage-and-privacy",
        domain="docs.langchain.com",
        title="Data storage and privacy - Docs by LangChain",
        query=query,
    )

    assert geeksforgeeks.source_intent == "generic_article"
    assert ibm.source_intent == "generic_article"
    assert official_docs.source_intent == "official_about"
    assert reference_docs.source_intent == "official_docs_reference"
    assert reference_docs.fetch_priority_score < geeksforgeeks.fetch_priority_score
    assert unrelated_docs.downrank_reason == "off_subject_source_downranked_for_query"


def test_langgraph_mirrors_and_third_party_github_are_not_official_owned() -> None:
    query = "What is LangGraph and how does it work?"
    mirror_docs = classify_source_intent(
        canonical_url="https://github.langchain.ac.cn/langgraph/",
        domain="github.langchain.ac.cn",
        title="LangGraph docs mirror",
        query=query,
    )
    localized_site = classify_source_intent(
        canonical_url="https://langgraph.com.cn/",
        domain="langgraph.com.cn",
        title="LangGraph 中文文档",
        query=query,
    )
    localized_docs = classify_source_intent(
        canonical_url="https://langchain-doc.cn/docs/langgraph/",
        domain="langchain-doc.cn",
        title="LangGraph 中文文档",
        query=query,
    )
    official_github = classify_source_intent(
        canonical_url="https://github.com/langchain-ai/langgraph",
        domain="github.com",
        title="langchain-ai/langgraph: Build resilient language agents as graphs.",
        query=query,
    )
    third_party_github = classify_source_intent(
        canonical_url="https://github.com/datawhalechina/easy-langent",
        domain="github.com",
        title="datawhalechina/easy-langent LangGraph tutorial",
        query=query,
    )
    freelancer = classify_source_intent(
        canonical_url="https://www.freelancer.hk/job-search/langgraph/",
        domain="www.freelancer.hk",
        title="LangGraph jobs",
        query=query,
    )

    assert mirror_docs.source_intent == "secondary_reference"
    assert localized_site.source_intent == "secondary_reference"
    assert localized_docs.source_intent == "secondary_reference"
    assert mirror_docs.downrank_reason == "secondary_reference_not_official_owned"
    assert official_github.source_intent == "github_readme_or_repo"
    assert third_party_github.source_intent == "secondary_reference"
    assert official_github.fetch_priority_score < third_party_github.fetch_priority_score
    assert freelancer.source_intent == "low_quality_or_blocked"
    assert freelancer.fetch_priority_score == 99


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
