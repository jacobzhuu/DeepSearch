"""Scoring alignment for technical_explanation ``limitations`` with official planner slots."""

from __future__ import annotations

from uuid import UUID, uuid4

from services.orchestrator.app.claims.drafting import (
    answer_role_for_claim_category,
    classify_claim_category,
    classify_query_intent,
    score_claim_statement,
)
from services.orchestrator.app.services.claims import _limitations_official_planner_target_slot_id


def test_other_without_limitations_slot_is_non_answer_for_definition_mechanism() -> None:
    query = "What is LangGraph and how does it work?"
    intent = classify_query_intent(query)
    statement = (
        "LangGraph caveats for integrators include unbounded growth of persisted state "
        "without pruning."
    )
    category = classify_claim_category(statement, intent=intent)
    assert category == "other"
    assert (
        answer_role_for_claim_category(category, intent=intent, target_slot_id=None) == "non_answer"
    )


def test_official_planner_limitations_slot_maps_other_to_feature_role() -> None:
    query = "What is LangGraph and how does it work?"
    intent = classify_query_intent(query)
    statement = (
        "LangGraph caveats for integrators include unbounded growth of persisted state "
        "without pruning."
    )
    category = classify_claim_category(statement, intent=intent)
    assert category == "other"
    assert (
        answer_role_for_claim_category(category, intent=intent, target_slot_id="limitations")
        == "feature"
    )


def test_official_planner_limitations_slot_enables_main_or_supporting_tier() -> None:
    query = "What is LangGraph and how does it work?"
    statement = (
        "LangGraph caveats for integrators include unbounded growth of persisted state "
        "without pruning."
    )
    score = score_claim_statement(
        statement=statement,
        query=query,
        content_quality_score=0.92,
        source_quality_score=0.9,
        domain="docs.langchain.com",
        source_url="https://docs.langchain.com/oss/python/langgraph/overview",
        page_title="LangGraph overview",
        target_slot_id="limitations",
    )
    assert score.claim_category == "other"
    assert score.answer_relevant is True
    assert score.candidate_tier in {"main_candidate", "supporting_candidate"}


def test_limitations_planner_target_slot_id_requires_official_role() -> None:
    doc_id = uuid4()
    slots: dict[UUID, frozenset[str]] = {doc_id: frozenset({"limitations"})}
    assert (
        _limitations_official_planner_target_slot_id(
            technical_explanation=True,
            document_target_slots=slots,
            source_document_id=doc_id,
            source_role="generic_article",
        )
        is None
    )
    assert (
        _limitations_official_planner_target_slot_id(
            technical_explanation=True,
            document_target_slots=slots,
            source_document_id=doc_id,
            source_role="official_docs",
        )
        == "limitations"
    )
