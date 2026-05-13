from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

import pytest

from services.orchestrator.app.api.routes.research_tasks import (
    _constraints_with_report_language,
)
from services.orchestrator.app.llm import LLMError, LLMRequest, LLMResponse, NoopLLMProvider
from services.orchestrator.app.planning import (
    ResearchPlannerError,
    ResearchPlannerService,
    build_default_research_plan,
    build_optional_research_plan,
)


@dataclass
class StaticLLMProvider:
    text: str
    name: str = "openai-compatible"

    def generate(self, request: LLMRequest) -> LLMResponse:
        del request
        return LLMResponse(text=self.text, model="test-model", provider=self.name)


@dataclass
class ErrorLLMProvider:
    def generate(self, request: LLMRequest) -> LLMResponse:
        del request
        raise LLMError(
            provider="openai-compatible",
            error_code="provider_error",
            message="provider unavailable",
        )


def _planner(
    provider: object,
    *,
    max_subquestions: int = 5,
    max_search_queries: int = 8,
) -> ResearchPlannerService:
    return ResearchPlannerService(
        provider=provider,  # type: ignore[arg-type]
        model="test-model",
        max_output_tokens=1200,
        max_subquestions=max_subquestions,
        max_search_queries=max_search_queries,
    )


def _planner_json(**overrides: object) -> str:
    payload: dict[str, object] = {
        "intent": "definition_how_it_works",
        "normalized_question": "What is SearXNG and how does it work?",
        "subquestions": ["What is SearXNG?", "How does it work?"],
        "search_queries": [
            {
                "query_text": "SearXNG official docs",
                "rationale": "Find official source material.",
                "expected_source_type": "official_docs",
                "priority": 1,
            }
        ],
        "source_preferences": {
            "preferred_domains": [],
            "avoid_domains": ["reddit.com"],
            "freshness_required": False,
        },
        "answer_outline": ["Definition", "How it works"],
        "risk_notes": ["Prefer official docs."],
        "planner_mode": "llm",
        "warnings": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_noop_planner_returns_valid_research_plan() -> None:
    plan = _planner(NoopLLMProvider()).plan(
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
    )

    assert plan.planner_mode == "noop"
    assert plan.intent == "technical_explanation"
    assert any("What is SearXNG" in item for item in plan.subquestions)
    assert any("core abstractions" in item.lower() for item in plan.subquestions)
    assert any("execution model" in item.query_text for item in plan.search_queries)


def test_langgraph_technical_explanation_plan_uses_slot_query_matrix() -> None:
    plan = build_default_research_plan(
        query="What is LangGraph and how does it work?",
        max_subquestions=5,
        max_search_queries=8,
    )

    slot_ids = [slot["slot_id"] for slot in plan.answer_slots]
    query_texts = [item.query_text for item in plan.search_queries]

    assert plan.intent == "technical_explanation"
    assert len(plan.search_queries) == 8
    assert slot_ids == [
        "definition",
        "motivation_problem",
        "core_abstractions",
        "architecture",
        "execution_model",
        "workflow_lifecycle",
        "key_features",
        "examples_use_cases",
        "limitations",
        "comparison_positioning",
        "official_sources",
    ]
    assert any("site:docs.langchain.com" in text for text in query_texts)
    assert any("site:reference.langchain.com" in text for text in query_texts)
    assert any("github langchain-ai langgraph README" in text for text in query_texts)
    assert {
        "official_docs",
        "official_reference",
        "official_repository",
    } <= {str(item.metadata.get("source_role")) for item in plan.search_queries}
    assert plan.source_preferences["source_role_quotas"]["generic_article"] == 0


def test_planner_json_parsing_and_validation() -> None:
    provider = StaticLLMProvider(
        _planner_json(
            intent="definition",
            normalized_question="What is SearXNG?",
            source_preferences={
                "preferred_domains": ["docs.searxng.org"],
                "avoid_domains": ["reddit.com"],
                "freshness_required": False,
            },
            answer_outline=["Definition"],
            risk_notes=["Use official docs"],
        )
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
    assert plan.planner_diagnostics["planner_output_schema_version"]
    assert plan.planner_diagnostics["schema_validated"] is True
    assert plan.planner_diagnostics["parse_stages"] == {
        "raw_text": True,
        "json_extracted": True,
        "schema_validated": True,
    }


def test_recent_nvidia_open_model_plan_uses_current_official_guardrails() -> None:
    plan = build_default_research_plan(
        query="近30天NVIDIA在开源模型生态上的关键发布与影响",
        max_subquestions=5,
        max_search_queries=8,
    )

    query_texts = [item.query_text for item in plan.search_queries]
    combined = "\n".join(query_texts).lower()

    assert query_texts[0] == "近30天NVIDIA在开源模型生态上的关键发布与影响"
    assert "site:nvidia.com" in combined
    assert "site:blogs.nvidia.com" in combined
    assert "site:developer.nvidia.com" in combined
    assert "site:nvidianews.nvidia.com" in combined
    assert "site:huggingface.co/nvidia" in combined
    assert "site:github.com/nvidia" in combined
    assert "2025" not in combined
    assert plan.source_preferences["preferred_domains"][:4] == [
        "nvidia.com",
        "blogs.nvidia.com",
        "developer.nvidia.com",
        "nvidianews.nvidia.com",
    ]
    assert "planner_queries_supplemented_for_recent_nvidia_official_sources" in plan.warnings


def test_report_language_does_not_default_search_language_constraint() -> None:
    constraints = _constraints_with_report_language(
        {},
        report_language="zh-CN",
        include_language_default=False,
    )

    assert constraints == {"report_language": "zh-CN"}


def test_planner_accepts_fenced_json_after_extraction() -> None:
    plan = _planner(
        StaticLLMProvider(
            f"""```json
            {_planner_json(subquestions=["What is SearXNG?"])}
            ```"""
        )
    ).plan(
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
    )

    assert plan.intent == "technical_explanation"
    assert plan.search_queries[0].query_text == "What is SearXNG and how does it work?"
    assert plan.planner_diagnostics["json_extraction_method"] == "single_fenced_json_object"
    assert plan.planner_diagnostics["schema_validated"] is True


def test_planner_rejects_prose_wrapped_unfenced_json() -> None:
    result = build_optional_research_plan(
        planner_service=_planner(
            StaticLLMProvider(
                f"""
                Here is the plan:
                {
                    _planner_json(
                        intent="definition",
                        normalized_question="What is SearXNG?",
                        subquestions=["What is SearXNG?"],
                    )
                }
                Done.
                """
            )
        ),
        task_id=uuid4(),
        query="What is SearXNG?",
        constraints={},
        max_subquestions=5,
        max_search_queries=8,
    )

    assert result.planner_status == "fallback"
    assert result.failure is not None
    assert result.failure["reason"] == "invalid_json"
    diagnostics = result.plan.planner_diagnostics
    assert diagnostics["json_extraction_error"] == "prose_around_unfenced_json"
    assert diagnostics["schema_validated"] is False


def test_planner_missing_fields_are_rejected_by_strict_schema() -> None:
    with pytest.raises(ResearchPlannerError) as exc_info:
        _planner(
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

    assert exc_info.value.reason == "invalid_schema"
    validation_errors = exc_info.value.details["validation_errors"]
    assert any(error["category"] == "missing_field" for error in validation_errors)
    assert any(error["path"] == "subquestions" for error in validation_errors)


def test_invalid_llm_json_returns_structured_planner_failure() -> None:
    with pytest.raises(ResearchPlannerError) as exc_info:
        _planner(StaticLLMProvider("not json")).plan(
            task_id=uuid4(),
            query="What is SearXNG and how does it work?",
            constraints={},
        )

    assert exc_info.value.reason == "invalid_json"
    assert exc_info.value.details["parse_stage"] == "raw_text"
    assert exc_info.value.details["json_extracted"] is False
    assert "raw_output_preview" in exc_info.value.details


def test_optional_planner_disabled_uses_deterministic_fallback() -> None:
    result = build_optional_research_plan(
        planner_service=None,
        task_id=uuid4(),
        query="What is LangGraph and how does it work?",
        constraints={},
        max_subquestions=5,
        max_search_queries=8,
    )

    assert result.plan_source == "deterministic_fallback"
    assert result.planner_status == "created"
    assert result.failure is None
    assert result.plan.planner_mode == "deterministic"
    assert "No LLM planner is active; deterministic planner used." in result.warnings
    assert any("site:docs.langchain.com" in item.query_text for item in result.plan.search_queries)
    assert any(
        "StateGraph graph state reference" in item.query_text for item in result.plan.search_queries
    )


def test_optional_planner_valid_deepseek_like_json_is_accepted_as_llm_planner() -> None:
    result = build_optional_research_plan(
        planner_service=_planner(StaticLLMProvider(_planner_json())),
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
        max_subquestions=5,
        max_search_queries=8,
    )

    assert result.plan_source == "llm_planner"
    assert result.planner_status == "success"
    assert result.failure is None
    assert result.plan.planner_mode == "llm"
    assert result.plan.planner_diagnostics["planner_fallback"] is False
    assert result.plan.planner_diagnostics["schema_validated"] is True
    assert "LLM planner generated this research plan." in result.warnings


def test_optional_planner_malformed_output_falls_back_to_deterministic_plan() -> None:
    result = build_optional_research_plan(
        planner_service=_planner(StaticLLMProvider("not json")),
        task_id=uuid4(),
        query="What is LangGraph and how does it work?",
        constraints={},
        max_subquestions=5,
        max_search_queries=8,
    )

    assert result.plan_source == "deterministic_fallback_after_llm_failure"
    assert result.planner_status == "fallback"
    assert result.failure is not None
    assert result.failure["reason"] == "invalid_json"
    assert result.plan.planner_mode == "deterministic"
    assert result.plan.planner_diagnostics["planner_fallback"] is True
    assert result.plan.planner_diagnostics["parse_stage"] == "raw_text"
    assert result.plan.planner_diagnostics["json_extracted"] is False
    assert "raw_output_preview" in result.plan.planner_diagnostics
    assert (
        "LLM planner failed validation/provider call; deterministic fallback was used."
        in result.warnings
    )
    assert any("using deterministic fallback" in warning for warning in result.warnings)


def test_optional_planner_provider_error_falls_back_to_deterministic_plan() -> None:
    result = build_optional_research_plan(
        planner_service=_planner(ErrorLLMProvider()),
        task_id=uuid4(),
        query="What is LangGraph and how does it work?",
        constraints={},
        max_subquestions=5,
        max_search_queries=8,
    )

    assert result.plan_source == "deterministic_fallback_after_llm_failure"
    assert result.planner_status == "fallback"
    assert result.failure is not None
    assert result.failure["reason"] == "llm_provider_failed"
    assert result.plan.search_queries
    assert (
        "LLM planner failed validation/provider call; deterministic fallback was used."
        in result.warnings
    )


def test_optional_planner_missing_field_falls_back_with_field_diagnostics() -> None:
    result = build_optional_research_plan(
        planner_service=_planner(
            StaticLLMProvider(
                """
                {
                  "intent": "definition",
                  "normalized_question": "What is SearXNG?"
                }
                """
            )
        ),
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
        max_subquestions=5,
        max_search_queries=8,
    )

    assert result.planner_status == "fallback"
    diagnostics = result.plan.planner_diagnostics
    assert diagnostics["fallback_reason"] == "invalid_schema"
    assert diagnostics["json_extracted"] is True
    assert diagnostics["schema_validated"] is False
    assert any(
        error["category"] == "missing_field" and error["path"] == "subquestions"
        for error in diagnostics["validation_errors"]
    )


def test_optional_planner_extra_top_level_field_falls_back_with_diagnostics() -> None:
    result = build_optional_research_plan(
        planner_service=_planner(
            StaticLLMProvider(_planner_json(extra_top_level="not allowed")),
        ),
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
        max_subquestions=5,
        max_search_queries=8,
    )

    diagnostics = result.plan.planner_diagnostics
    assert result.planner_status == "fallback"
    assert any(
        error["category"] == "extra_field" and error["path"] == "extra_top_level"
        for error in diagnostics["validation_errors"]
    )


def test_optional_planner_invalid_expected_source_type_falls_back_with_diagnostics() -> None:
    payload = json.loads(_planner_json())
    payload["search_queries"][0]["expected_source_type"] = "official_blog"
    result = build_optional_research_plan(
        planner_service=_planner(StaticLLMProvider(json.dumps(payload))),
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
        max_subquestions=5,
        max_search_queries=8,
    )

    diagnostics = result.plan.planner_diagnostics
    assert result.planner_status == "fallback"
    assert any(
        error["category"] == "invalid_enum_value"
        and error["path"] == "search_queries.0.expected_source_type"
        and "official_docs" in error["allowed_values"]
        for error in diagnostics["validation_errors"]
    )


def test_optional_planner_wrong_type_fields_fall_back_with_path_diagnostics() -> None:
    payload = json.loads(_planner_json())
    payload["source_preferences"]["freshness_required"] = "not needed"
    payload["risk_notes"] = "Prefer official sources."
    result = build_optional_research_plan(
        planner_service=_planner(StaticLLMProvider(json.dumps(payload))),
        task_id=uuid4(),
        query="What is LangGraph and how does it work?",
        constraints={},
        max_subquestions=5,
        max_search_queries=8,
    )

    diagnostics = result.plan.planner_diagnostics
    assert result.planner_status == "fallback"
    assert any(
        error["category"] == "wrong_type"
        and error["path"] == "source_preferences.freshness_required"
        for error in diagnostics["validation_errors"]
    )
    assert any(
        error["category"] == "wrong_type" and error["path"] == "risk_notes"
        for error in diagnostics["validation_errors"]
    )


def test_optional_planner_generated_queries_are_bounded_and_deduplicated() -> None:
    provider = StaticLLMProvider(
        _planner_json(
            intent="investigation",
            normalized_question="Investigate DeepSearch planner behavior",
            search_queries=[
                {
                    "query_text": "DeepSearch planner behavior",
                    "rationale": "initial query",
                    "expected_source_type": "official_docs",
                    "priority": 1,
                },
                {
                    "query_text": "deepsearch planner behavior",
                    "rationale": "duplicate query",
                    "expected_source_type": "official_docs",
                    "priority": 2,
                },
                {
                    "query_text": "DeepSearch planner failures",
                    "rationale": "provider failures",
                    "expected_source_type": "reference",
                    "priority": 3,
                },
                {
                    "query_text": "DeepSearch planner fallback",
                    "rationale": "fallback behavior",
                    "expected_source_type": "reference",
                    "priority": 4,
                },
            ],
        )
    )

    result = build_optional_research_plan(
        planner_service=_planner(provider, max_search_queries=2),
        task_id=uuid4(),
        query="Investigate DeepSearch planner behavior",
        constraints={},
        max_subquestions=5,
        max_search_queries=2,
    )

    query_texts = [item.query_text for item in result.plan.search_queries]
    assert len(query_texts) == 2
    assert query_texts == ["DeepSearch planner behavior", "DeepSearch planner failures"]


def test_searxng_query_plan_contains_technical_explanation_slots() -> None:
    plan = _planner(NoopLLMProvider()).plan(
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
    )

    combined = " ".join([*plan.subquestions, *(item.query_text for item in plan.search_queries)])
    assert "What is SearXNG" in combined
    assert "core abstractions" in combined
    assert "execution model" in combined
    assert "limitations comparison" in combined


def test_non_searxng_technical_plan_uses_generic_framework_mechanism_terms() -> None:
    plan = _planner(NoopLLMProvider()).plan(
        task_id=uuid4(),
        query="What is LangGraph and how does it work?",
        constraints={},
    )

    combined = " ".join(item.query_text for item in plan.search_queries)
    assert "site:docs.langchain.com concepts state graph nodes edges" in combined
    assert "StateGraph graph state reference" in combined
    assert "metasearch engine" not in combined
    assert "human-in-the-loop" in combined


def test_llm_langgraph_plan_preserves_owned_source_guardrail_queries() -> None:
    result = build_optional_research_plan(
        planner_service=_planner(
            StaticLLMProvider(
                _planner_json(
                    normalized_question="What is LangGraph and how does it work?",
                    search_queries=[
                        {
                            "query_text": "LangGraph LangChain blog announcement",
                            "rationale": "LLM picked a broad announcement.",
                            "expected_source_type": "general_web",
                            "priority": 1,
                        },
                        {
                            "query_text": "LangGraph GitHub repository",
                            "rationale": "LLM picked a broad GitHub query.",
                            "expected_source_type": "official_repository",
                            "priority": 2,
                        },
                    ],
                    source_preferences={
                        "preferred_domains": ["blog.langchain.dev", "github.com/langchain-ai"],
                        "avoid_domains": ["reddit.com"],
                        "freshness_required": False,
                    },
                )
            ),
            max_search_queries=4,
        ),
        task_id=uuid4(),
        query="What is LangGraph and how does it work?",
        constraints={},
        max_subquestions=5,
        max_search_queries=4,
    )

    assert result.plan_source == "llm_planner"
    assert result.planner_status == "success"
    query_texts = [item.query_text for item in result.plan.search_queries]
    assert "What is LangGraph and how does it work?" in query_texts
    assert "LangGraph official documentation overview" in query_texts
    assert "LangGraph site:docs.langchain.com concepts state graph nodes edges" in query_texts
    assert "LangGraph site:reference.langchain.com StateGraph graph state reference" in query_texts
    assert any(
        item["query_source"] == "guardrail_query"
        and item["metadata"]["source_role"] == "official_docs"
        for item in result.plan.final_search_queries
    )


def test_llm_langgraph_preferred_domains_supplement_owned_domains() -> None:
    plan = _planner(
        StaticLLMProvider(
            _planner_json(
                normalized_question="What is LangGraph and how does it work?",
                source_preferences={
                    "preferred_domains": [
                        "langchain-ai.github.io",
                        "github.com/langchain-ai",
                        "blog.langchain.dev",
                    ],
                    "avoid_domains": ["reddit.com"],
                    "freshness_required": False,
                },
            )
        )
    ).plan(
        task_id=uuid4(),
        query="What is LangGraph and how does it work?",
        constraints={},
    )

    preferred_domains = plan.source_preferences["preferred_domains"]
    assert preferred_domains[:5] == [
        "docs.langchain.com",
        "reference.langchain.com",
        "www.langchain.com",
        "langchain.com",
        "github.com/langchain-ai/langgraph",
    ]
    assert preferred_domains.index("github.com/langchain-ai/langgraph") < preferred_domains.index(
        "github.com/langchain-ai"
    )
    assert plan.source_preferences["secondary_preferred_domains"] == [
        "langchain-ai.github.io",
        "blog.langchain.dev",
    ]
    assert any(
        item["action"] == "domain_supplemented"
        and item["corrected_to"] == "github.com/langchain-ai/langgraph"
        for item in plan.dropped_or_downweighted_planner_queries
    )
    assert any(
        item["action"] == "domain_downweighted"
        and item["downrank_reason"] == "weak_langgraph_domain_marked_secondary"
        for item in plan.dropped_or_downweighted_planner_queries
    )


def test_llm_langgraph_broad_queries_are_downweighted_after_mechanism_guardrails() -> None:
    plan = _planner(
        StaticLLMProvider(
            _planner_json(
                normalized_question="What is LangGraph and how does it work?",
                search_queries=[
                    {
                        "query_text": "LangGraph LangChain blog announcement",
                        "rationale": "Find announcement coverage.",
                        "expected_source_type": "general_web",
                        "priority": 1,
                    },
                    {
                        "query_text": "LangGraph how it works tutorial",
                        "rationale": "Find tutorial coverage.",
                        "expected_source_type": "general_web",
                        "priority": 2,
                    },
                    {
                        "query_text": "LangGraph GitHub repository",
                        "rationale": "Find broad GitHub coverage.",
                        "expected_source_type": "official_repository",
                        "priority": 3,
                    },
                ],
            )
        ),
        max_search_queries=16,
    ).plan(
        task_id=uuid4(),
        query="What is LangGraph and how does it work?",
        constraints={},
    )

    query_texts = [item.query_text for item in plan.search_queries]
    assert query_texts.index(
        "LangGraph site:docs.langchain.com concepts state graph nodes edges"
    ) < (query_texts.index("LangGraph how it works tutorial"))
    reasons = {
        item["query_text"]: item["downrank_reason"]
        for item in plan.dropped_or_downweighted_planner_queries
        if item.get("action") == "downweighted"
    }
    assert (
        reasons["LangGraph LangChain blog announcement"]
        == "blog_or_announcement_query_downweighted_for_overview"
    )
    assert (
        reasons["LangGraph how it works tutorial"]
        == "generic_tutorial_query_downweighted_for_overview"
    )
    assert reasons["LangGraph GitHub repository"] == (
        "broad_repository_query_supplemented_by_upstream_repo"
    )


def test_definition_query_overrides_wikipedia_avoid_domain_and_keeps_reference_sources() -> None:
    plan = _planner(
        StaticLLMProvider(
            _planner_json(
                search_queries=[
                    {
                        "query_text": "SearXNG how it works architecture",
                        "rationale": "architecture docs",
                        "expected_source_type": "official_docs",
                        "priority": 1,
                    },
                    {
                        "query_text": "SearXNG privacy",
                        "rationale": "privacy",
                        "expected_source_type": "official_docs",
                        "priority": 2,
                    },
                ],
                source_preferences={
                    "preferred_domains": ["github.com/searxng/searxng"],
                    "avoid_domains": ["wikipedia.org", "reddit.com"],
                    "freshness_required": False,
                },
            )
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
            _planner_json(
                subquestions=["What is SearXNG?"],
                search_queries=[
                    {
                        "query_text": "SearXNG how it works architecture",
                        "rationale": "architecture docs",
                        "expected_source_type": "official_docs",
                        "priority": 1,
                    },
                    {
                        "query_text": "SearXNG installation docker",
                        "rationale": "installation docs",
                        "expected_source_type": "official_docs",
                        "priority": 2,
                    },
                    {
                        "query_text": "SearXNG privacy",
                        "rationale": "privacy docs",
                        "expected_source_type": "official_docs",
                        "priority": 3,
                    },
                ],
            )
        )
    ).plan(
        task_id=uuid4(),
        query="What is SearXNG and how does it work?",
        constraints={},
    )

    query_texts = [item.query_text for item in plan.search_queries]
    assert query_texts[:4] == [
        "What is SearXNG and how does it work?",
        "SearXNG official documentation overview",
        "SearXNG core concepts architecture official documentation",
        "SearXNG API reference core concepts",
    ]
    assert len(query_texts) <= 8
    assert any(
        item["query_text"] == "SearXNG how it works architecture" and item["action"] == "dropped"
        for item in plan.dropped_or_downweighted_planner_queries
    )
    assert plan.search_queries[0].query_source == "original_user_query"
    assert plan.search_queries[1].query_source == "guardrail_query"
    assert plan.final_search_queries[0]["query_text"] == "What is SearXNG and how does it work?"
    assert plan.source_preferences["source_role_quotas"]["official_docs"] == 3
