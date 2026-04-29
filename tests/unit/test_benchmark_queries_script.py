from __future__ import annotations

from scripts.benchmark_queries import (
    BENCHMARK_QUERIES,
    _contamination_check,
    _dict_or_empty,
    _list_or_empty,
    _select_benchmark_queries,
)


def test_generalization_benchmark_contains_required_queries() -> None:
    queries = [item.query for item in BENCHMARK_QUERIES]

    assert queries == [
        "What is SearXNG and how does it work?",
        "What is OpenSearch and how does it work?",
        "What is LangGraph and how does it work?",
        "What is Model Context Protocol and how does it work?",
        "What is Dify and how does it work?",
        "What are the privacy advantages and limitations of SearXNG?",
        "How can SearXNG be deployed with Docker?",
        "Compare SearXNG, Brave Search API, and Tavily for AI research agents.",
        "What is Retrieval-Augmented Generation and what are its limitations?",
        "What are the main differences between ChatGPT Deep Research and Gemini Deep Research?",
    ]


def test_generalization_benchmark_labels_capabilities() -> None:
    for item in BENCHMARK_QUERIES:
        assert item.capabilities
        assert all(capability.strip() for capability in item.capabilities)


def test_benchmark_contamination_check_flags_non_searxng_source_leakage() -> None:
    clean = _contamination_check(
        "What is OpenSearch and how does it work?",
        observability={"selected_sources": [{"canonical_url": "https://opensearch.org/docs"}]},
    )
    contaminated = _contamination_check(
        "What is OpenSearch and how does it work?",
        observability={"selected_sources": [{"canonical_url": "https://docs.searxng.org"}]},
    )

    assert clean["checked"] is True
    assert clean["passed"] is True
    assert contaminated["passed"] is False
    assert contaminated["searxng_source_count"] == 1


def test_benchmark_summary_helpers_keep_legacy_missing_fields_stable() -> None:
    assert _list_or_empty(None) == []
    assert _list_or_empty({"not": "a list"}) == []
    assert _dict_or_empty(None) == {}
    assert _dict_or_empty(["not", "a", "dict"]) == {}


def test_benchmark_selection_supports_limit_and_query_id() -> None:
    assert len(_select_benchmark_queries(limit=2)) == 2
    selected = _select_benchmark_queries(limit=10, query_id=3)
    assert [item.query for item in selected] == [
        "What is LangGraph and how does it work?",
    ]
