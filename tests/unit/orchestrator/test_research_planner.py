from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from services.orchestrator.app.llm import LLMRequest, LLMResponse, NoopLLMProvider
from services.orchestrator.app.planning import ResearchPlannerError, ResearchPlannerService


@dataclass
class StaticLLMProvider:
    text: str
    name: str = "openai-compatible"

    def generate(self, request: LLMRequest) -> LLMResponse:
        del request
        return LLMResponse(text=self.text, model="test-model", provider=self.name)


def _planner(provider: object) -> ResearchPlannerService:
    return ResearchPlannerService(
        provider=provider,  # type: ignore[arg-type]
        model="test-model",
        max_output_tokens=1200,
        max_subquestions=5,
        max_search_queries=8,
    )


def test_noop_planner_returns_valid_research_plan() -> None:
    plan = _planner(NoopLLMProvider()).plan(
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
    )

    assert plan.planner_mode == "noop"
    assert plan.intent == "definition_how_it_works"
    assert any("What is SearXNG" in item for item in plan.subquestions)
    assert any("privacy" in item.lower() for item in plan.subquestions)
    assert any("upstream search engines" in item.query_text for item in plan.search_queries)


def test_planner_json_parsing_and_validation() -> None:
    provider = StaticLLMProvider(
        """
        {
          "intent": "definition",
          "normalized_question": "What is SearXNG?",
          "subquestions": ["What is SearXNG?", "How does it work?"],
          "search_queries": [
            {
              "query_text": "SearXNG official docs",
              "rationale": "official source",
              "expected_source_type": "official_docs",
              "priority": 1
            }
          ],
          "source_preferences": {
            "preferred_domains": ["docs.searxng.org"],
            "avoid_domains": ["reddit.com"],
            "freshness_required": false
          },
          "answer_outline": ["Definition"],
          "risk_notes": ["Use official docs"],
          "warnings": []
        }
        """
    )

    plan = _planner(provider).plan(
        task_id=uuid4(),
        query="What is SearXNG?",
        constraints={},
    )

    assert plan.planner_mode == "llm"
    assert plan.search_queries[0].query_text == "What is SearXNG?"
    assert plan.search_queries[1].expected_source_type == "official_docs"
    assert plan.raw_planner_queries[0]["expected_source_type"] == "official_docs"
    assert plan.intent_classification == "overview_definition_intent"
    assert plan.extracted_entity == "SearXNG"
    assert plan.source_preferences["preferred_domains"] == [
        "docs.searxng.org",
        "searxng.org",
        "en.wikipedia.org",
    ]


def test_planner_parses_json_code_fence() -> None:
    plan = _planner(
        StaticLLMProvider(
            """```json
            {
              "intent": "definition_how_it_works",
              "normalized_question": "What is SearXNG and how does it work?",
              "subquestions": ["What is SearXNG?"],
              "search_queries": [
                {"query_text": "SearXNG official docs", "priority": 1}
              ]
            }
            ```"""
        )
    ).plan(
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
    )

    assert plan.intent == "definition_how_it_works"
    assert plan.search_queries[0].query_text == "What is SearXNG and how does it work?"


def test_planner_extracts_json_object_from_surrounding_text() -> None:
    plan = _planner(
        StaticLLMProvider(
            """
            Here is the plan:
            {
              "intent": "definition",
              "normalized_question": "What is SearXNG?",
              "subquestions": ["What is SearXNG?"],
              "search_queries": ["SearXNG official docs"]
            }
            Done.
            """
        )
    ).plan(
        task_id=uuid4(),
        query="What is SearXNG?",
        constraints={},
    )

    assert plan.planner_mode == "llm"
    assert plan.subquestions == ["What is SearXNG?"]
    assert plan.search_queries[0].query_text == "What is SearXNG?"
    assert "SearXNG official docs" in [item.query_text for item in plan.search_queries]
    assert plan.raw_planner_queries[0]["query_text"] == "SearXNG official docs"


def test_planner_missing_fields_use_defaults() -> None:
    plan = _planner(
        StaticLLMProvider(
            """
            {
              "intent": "definition",
              "normalized_question": "What is SearXNG?"
            }
            """
        )
    ).plan(
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
    )

    assert plan.planner_mode == "llm"
    assert plan.subquestions
    assert plan.search_queries
    assert plan.source_preferences["preferred_domains"]
    assert any("omitted planner_mode" in warning for warning in plan.warnings)


def test_invalid_llm_json_returns_structured_planner_failure() -> None:
    with pytest.raises(ResearchPlannerError) as exc_info:
        _planner(StaticLLMProvider("not json")).plan(
            task_id=uuid4(),
            query="What is SearXNG and how does it work?",
            constraints={},
        )

    assert exc_info.value.reason == "invalid_json"


def test_searxng_query_plan_contains_definition_mechanism_privacy() -> None:
    plan = _planner(NoopLLMProvider()).plan(
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
    )

    combined = " ".join([*plan.subquestions, *(item.query_text for item in plan.search_queries)])
    assert "What is SearXNG" in combined
    assert "upstream search engines" in combined
    assert "privacy" in combined.lower()


def test_definition_query_overrides_wikipedia_avoid_domain_and_keeps_reference_sources() -> None:
    plan = _planner(
        StaticLLMProvider(
            """
            {
              "intent": "definition_how_it_works",
              "normalized_question": "What is SearXNG and how does it work?",
              "subquestions": ["What is SearXNG?", "How does it work?"],
              "search_queries": [
                {
                  "query_text": "SearXNG how it works architecture",
                  "rationale": "architecture docs",
                  "expected_source_type": "official_docs",
                  "priority": 1
                },
                {
                  "query_text": "SearXNG privacy",
                  "rationale": "privacy",
                  "expected_source_type": "official_docs",
                  "priority": 2
                }
              ],
              "source_preferences": {
                "preferred_domains": ["github.com/searxng/searxng"],
                "avoid_domains": ["wikipedia.org", "reddit.com"],
                "freshness_required": false
              }
            }
            """
        )
    ).plan(
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
    )

    assert "wikipedia.org" not in plan.source_preferences["avoid_domains"]
    assert "reddit.com" in plan.source_preferences["avoid_domains"]
    assert plan.source_preferences["overridden_avoid_domains"] == ["wikipedia.org"]
    assert plan.source_preferences["preferred_domains"][:3] == [
        "docs.searxng.org",
        "searxng.org",
        "en.wikipedia.org",
    ]
    assert "planner_avoid_domains_overridden_for_overview_reference" in plan.warnings
    assert (
        "planner_avoid_domains_overridden_for_overview_reference" in plan.planner_guardrail_warnings
    )
    assert any("planner_avoid_domain_overridden: wikipedia.org" in item for item in plan.warnings)


def test_searxng_what_how_search_queries_include_baseline_guardrails() -> None:
    plan = _planner(
        StaticLLMProvider(
            """
            {
              "intent": "definition_how_it_works",
              "normalized_question": "What is SearXNG and how does it work?",
              "subquestions": ["What is SearXNG?"],
              "search_queries": [
                {"query_text": "SearXNG how it works architecture", "priority": 1},
                {"query_text": "SearXNG installation docker", "priority": 2},
                {"query_text": "SearXNG privacy", "priority": 3}
              ]
            }
            """
        )
    ).plan(
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
    )

    query_texts = [item.query_text for item in plan.search_queries]
    assert query_texts[:4] == [
        "What is SearXNG and how does it work?",
        "SearXNG official documentation",
        "SearXNG about how does it work",
        "SearXNG Wikipedia",
    ]
    assert len(query_texts) <= 8
    assert query_texts.index("SearXNG privacy") < query_texts.index(
        "SearXNG how it works architecture"
    )
    assert plan.search_queries[0].query_source == "original_user_query"
    assert plan.search_queries[1].query_source == "guardrail_query"
    assert plan.final_search_queries[0]["query_text"] == "What is SearXNG and how does it work?"
    assert any(
        item["downrank_reason"] == "architecture_or_admin_query_downweighted_for_overview"
        for item in plan.dropped_or_downweighted_planner_queries
    )
