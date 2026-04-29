from __future__ import annotations

from services.orchestrator.app.claims import classify_query_intent, score_claim_statement
from services.orchestrator.app.planning import build_default_research_plan
from services.orchestrator.app.research_quality import (
    answer_slot_coverage,
    slot_ids_for_candidate_category,
)


def test_docker_deployment_query_uses_deployment_intent_and_slots() -> None:
    query = "How to deploy SearXNG with Docker?"
    statement = (
        "SearXNG can be deployed with Docker by running a container, mounting "
        "configuration files, and connecting it to required storage or network services."
    )

    intent = classify_query_intent(query)
    score = score_claim_statement(statement=statement, query=query)
    slot_ids = slot_ids_for_candidate_category(score.claim_category, query=query)
    coverage = answer_slot_coverage(query, {score.claim_category})

    assert intent.intent_name == "deployment"
    assert score.claim_category == "deployment/self_hosting"
    assert score.answer_role == "deployment/self_hosting"
    assert score.answer_relevant is True
    assert {
        "deployment_target",
        "deployment_steps",
        "deployment_configuration",
    }.issubset(set(slot_ids))
    assert {row["slot_id"]: row["covered"] for row in coverage if row["required"]} == {
        "deployment_target": True,
        "deployment_steps": True,
        "deployment_configuration": True,
    }


def test_deployment_configuration_statement_is_not_demoted_to_setup() -> None:
    query = "How to deploy SearXNG with Docker?"
    statement = (
        "Operators deploying SearXNG should configure secrets, base URLs, persistent "
        "storage, health checks, and reverse-proxy settings before exposing the service."
    )

    score = score_claim_statement(statement=statement, query=query)

    assert score.claim_category == "deployment/self_hosting"
    assert score.answer_relevant is True


def test_deterministic_planner_builds_deployment_plan() -> None:
    plan = build_default_research_plan(
        "How to deploy SearXNG with Docker?",
        max_subquestions=5,
        max_search_queries=8,
    )

    assert plan.intent == "deployment"
    assert plan.intent_classification == "deployment_intent"
    assert any(
        query.query_text == "SearXNG Docker deployment official documentation"
        for query in plan.search_queries
    )
    assert {
        "deployment_target",
        "deployment_steps",
        "deployment_configuration",
    }.issubset({slot["slot_id"] for slot in plan.answer_slots})
