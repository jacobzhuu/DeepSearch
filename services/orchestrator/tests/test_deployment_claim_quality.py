from __future__ import annotations

from services.orchestrator.app.claims import (
    classify_query_intent,
    deployment_slot_ids_for_claim_text,
    score_claim_statement,
)
from services.orchestrator.app.planning import build_default_research_plan
from services.orchestrator.app.research_quality import (
    answer_slot_coverage,
    build_slot_coverage_summary,
)


def test_docker_deployment_query_uses_deployment_intent_and_slots() -> None:
    query = "How to deploy SearXNG with Docker?"
    statement = (
        "SearXNG deployment requires Docker or Podman, uses Docker Compose, defines volumes, "
        "and reads configuration from settings.yml."
    )

    intent = classify_query_intent(query)
    score = score_claim_statement(statement=statement, query=query)
    slot_ids = deployment_slot_ids_for_claim_text(statement, statement)
    coverage = answer_slot_coverage(query, {score.claim_category})

    assert intent.intent_name == "deployment"
    assert score.claim_category == "deployment/self_hosting"
    assert score.answer_role == "deployment/self_hosting"
    assert score.answer_relevant is True
    assert slot_ids == (
        "deployment_prerequisites",
        "deployment_run_or_compose",
        "deployment_volumes",
        "deployment_configuration",
    )
    assert {row["slot_id"]: row["covered"] for row in coverage if row["required"]} == {
        "deployment_prerequisites": True,
        "deployment_run_or_compose": True,
        "deployment_volumes": True,
        "deployment_ports": True,
        "deployment_configuration": True,
        "deployment_security": True,
        "deployment_troubleshooting": False,
        "deployment_update_maintenance": True,
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


def test_generic_deployment_claim_without_specific_slot_ids_does_not_cover_security() -> None:
    rows = build_slot_coverage_summary(
        "How to deploy SearXNG with Docker?",
        claim_rows=[
            {
                "claim_id": "claim-1",
                "verification_status": "supported",
                "slot_ids": [],
                "source_document_id": "source-1",
                "support_level": "strong",
            }
        ],
    )

    security = next(row for row in rows if row["slot_id"] == "deployment_security")
    assert security["status"] == "missing"


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
        "deployment_prerequisites",
        "deployment_run_or_compose",
        "deployment_volumes",
        "deployment_configuration",
    }.issubset({slot["slot_id"] for slot in plan.answer_slots})
