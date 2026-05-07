from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from services.orchestrator.app.llm import LLMRequest, LLMResponse
from services.orchestrator.app.planning import PlannedSearchQuery, ResearchPlan
from services.orchestrator.app.research_quality.llm_assistance import (
    LLMClaimReviewService,
    LLMEvidenceRerankerService,
    LLMQueryRewriterService,
)


class StaticProvider:
    name = "static"

    def __init__(self, payload: object) -> None:
        self.payload = payload

    def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text=self.payload if isinstance(self.payload, str) else json.dumps(self.payload),
            model=request.model,
            provider=self.name,
        )


def test_query_rewriter_accepts_structured_queries() -> None:
    service = LLMQueryRewriterService(
        enabled=True,
        provider=StaticProvider(
            {
                "queries": [
                    {
                        "query_text": "LangGraph official docs StateGraph",
                        "rationale": "Prioritize official mechanism documentation.",
                        "expected_source_type": "official_docs",
                        "priority": 1,
                    }
                ],
                "notes": ["Prefer official docs."],
            }
        ),
        model="deepseek-chat",
        max_queries=4,
        max_output_tokens=500,
        input_max_chars=4000,
    )

    result = service.rewrite(query="What is LangGraph?", plan=_plan(), constraints={})

    assert result.used is True
    assert result.search_queries[0].query_text == "LangGraph official docs StateGraph"
    assert result.search_queries[0].query_source == "llm_query_rewriter"


def test_query_rewriter_malformed_output_falls_back() -> None:
    service = LLMQueryRewriterService(
        enabled=True,
        provider=StaticProvider("not json"),
        model="deepseek-chat",
        max_queries=4,
        max_output_tokens=500,
        input_max_chars=4000,
    )

    result = service.rewrite(query="What is LangGraph?", plan=_plan(), constraints={})

    assert result.used is False
    assert result.search_queries == []
    assert result.status == "fallback"


def test_query_rewriter_accepts_deepseek_aliases_and_fenced_json() -> None:
    service = LLMQueryRewriterService(
        enabled=True,
        provider=StaticProvider(
            '```json\n{"search_queries":[{"query":"LangGraph StateGraph reference",'
            '"reason":"Find reference docs.","source_type":"reference_docs",'
            '"priority":"2","extra":"ignored"}],"notes":"normalized"}\n```'
        ),
        model="deepseek-chat",
        max_queries=4,
        max_output_tokens=500,
        input_max_chars=4000,
    )

    result = service.rewrite(query="What is LangGraph?", plan=_plan(), constraints={})

    assert result.used is True
    assert result.search_queries[0].query_text == "LangGraph StateGraph reference"
    assert result.search_queries[0].expected_source_type == "reference"
    assert result.diagnostics["added_query_count"] == 1


def test_evidence_reranker_only_returns_existing_chunk_ids() -> None:
    chunk_id = uuid4()
    service = LLMEvidenceRerankerService(
        enabled=True,
        provider=StaticProvider(
            {
                "rankings": [
                    {
                        "source_chunk_id": str(uuid4()),
                        "answer_slot_ids": ["definition"],
                        "relevance_score": 1.0,
                        "evidence_strength_score": 1.0,
                        "rationale": "Invalid id should be ignored.",
                    },
                    {
                        "source_chunk_id": str(chunk_id),
                        "answer_slot_ids": ["definition"],
                        "relevance_score": 0.9,
                        "evidence_strength_score": 0.9,
                        "rationale": "Valid input chunk.",
                    },
                ]
            }
        ),
        model="deepseek-chat",
        max_chunks=10,
        max_output_tokens=500,
        input_max_chars=4000,
    )

    result = service.rerank(
        query="What is LangGraph?",
        chunks=[_chunk(chunk_id)],
        answer_slots=[{"slot_id": "definition"}],
    )

    assert result.used is True
    assert result.source_chunk_ids == [chunk_id]


def test_evidence_reranker_accepts_deepseek_ranked_chunk_aliases() -> None:
    first_chunk_id = uuid4()
    second_chunk_id = uuid4()
    service = LLMEvidenceRerankerService(
        enabled=True,
        provider=StaticProvider(
            {
                "ranked_chunks": [
                    {
                        "chunk_id": str(second_chunk_id),
                        "slots": ["mechanism"],
                        "score": 95,
                        "reason": "Direct mechanism evidence.",
                    }
                ]
            }
        ),
        model="deepseek-chat",
        max_chunks=10,
        max_output_tokens=500,
        input_max_chars=4000,
    )

    result = service.rerank(
        query="What is LangGraph?",
        chunks=[_chunk(first_chunk_id), _chunk(second_chunk_id)],
        answer_slots=[{"slot_id": "mechanism"}],
    )

    assert result.used is True
    assert result.source_chunk_ids[:2] == [second_chunk_id, first_chunk_id]
    assert result.diagnostics["candidate_chunk_count"] == 2


def test_evidence_reranker_score_only_output_is_low_quality_fallback() -> None:
    first_chunk_id = uuid4()
    second_chunk_id = uuid4()
    service = LLMEvidenceRerankerService(
        enabled=True,
        provider=StaticProvider(
            {
                "rankings": [
                    {
                        "source_chunk_id": str(second_chunk_id),
                        "relevance_score": 0.5,
                        "evidence_strength_score": 0.5,
                    }
                ]
            }
        ),
        model="deepseek-chat",
        max_chunks=10,
        max_output_tokens=500,
        input_max_chars=4000,
    )

    result = service.rerank(
        query="What is LangGraph?",
        chunks=[_chunk(first_chunk_id), _chunk(second_chunk_id)],
        answer_slots=[{"slot_id": "definition"}],
    )

    assert result.used is False
    assert result.status == "low_quality_rerank"
    assert result.source_chunk_ids == [first_chunk_id, second_chunk_id]
    assert result.diagnostics["produced_answer_slot_ids"] is False
    assert result.diagnostics["produced_rationales"] is False
    assert "flat_score_only_output" in result.diagnostics["low_quality_reasons"]


def test_claim_reviewer_cannot_create_claims() -> None:
    claim_id = uuid4()
    service = LLMClaimReviewService(
        enabled=True,
        provider=StaticProvider(
            {
                "decisions": [
                    {
                        "claim_id": str(uuid4()),
                        "decision": "accept",
                        "confidence": 1.0,
                        "reasons": ["Unknown id should be ignored."],
                        "covered_slot_ids": ["definition"],
                    },
                    {
                        "claim_id": str(claim_id),
                        "decision": "vague",
                        "confidence": 0.8,
                        "reasons": ["Too vague."],
                        "covered_slot_ids": [],
                    },
                ]
            }
        ),
        model="deepseek-chat",
        max_claims=10,
        max_output_tokens=500,
        input_max_chars=4000,
    )

    result = service.review(query="What is LangGraph?", claims=[_claim(claim_id)])

    assert result.used is True
    assert [item["claim_id"] for item in result.decisions] == [str(claim_id)]
    assert result.decisions[0]["decision"] == "vague"


def test_claim_reviewer_accepts_deepseek_review_aliases() -> None:
    claim_id = uuid4()
    service = LLMClaimReviewService(
        enabled=True,
        provider=StaticProvider(
            {
                "reviews": [
                    {
                        "id": str(claim_id),
                        "action": "keep",
                        "confidence": "0.9",
                        "reason": "Grounded and specific.",
                        "slots": ["definition"],
                        "extra": "ignored",
                    }
                ]
            }
        ),
        model="deepseek-chat",
        max_claims=10,
        max_output_tokens=500,
        input_max_chars=4000,
    )

    result = service.review(query="What is LangGraph?", claims=[_claim(claim_id)])

    assert result.used is True
    assert result.decisions[0]["decision"] == "accept"
    assert result.decisions[0]["covered_slot_ids"] == ["definition"]


def test_claim_reviewer_empty_reasons_do_not_default_to_accept() -> None:
    claim_id = uuid4()
    service = LLMClaimReviewService(
        enabled=True,
        provider=StaticProvider(
            {
                "decisions": [
                    {
                        "claim_id": str(claim_id),
                        "decision": "accept",
                        "confidence": 0.9,
                        "reasons": [],
                        "covered_slot_ids": ["definition"],
                    }
                ]
            }
        ),
        model="deepseek-chat",
        max_claims=10,
        max_output_tokens=500,
        input_max_chars=4000,
    )

    result = service.review(query="What is LangGraph?", claims=[_claim(claim_id)])

    assert result.used is False
    assert result.status == "low_quality_review"
    assert result.decisions[0]["decision"] == "downrank"
    assert "missing_reasons" in result.decisions[0]["quality_flags"]
    assert result.diagnostics["decision_counts"] == {"downrank": 1}


def test_claim_reviewer_low_confidence_or_malformed_accept_is_not_accept() -> None:
    claim_id = uuid4()
    service = LLMClaimReviewService(
        enabled=True,
        provider=StaticProvider(
            {
                "decisions": [
                    {
                        "claim_id": str(claim_id),
                        "decision": "accept",
                        "reasons": ["Looks plausible but weak."],
                        "covered_slot_ids": ["definition"],
                    }
                ]
            }
        ),
        model="deepseek-chat",
        max_claims=10,
        max_output_tokens=500,
        input_max_chars=4000,
    )

    result = service.review(query="What is LangGraph?", claims=[_claim(claim_id)])

    assert result.used is False
    assert result.status == "low_quality_review"
    assert result.decisions[0]["decision"] == "downrank"
    assert result.decisions[0]["confidence"] == 0.5
    assert "low_confidence" in result.decisions[0]["quality_flags"]


def test_claim_reviewer_malformed_output_falls_back() -> None:
    service = LLMClaimReviewService(
        enabled=True,
        provider=StaticProvider("not json"),
        model="deepseek-chat",
        max_claims=10,
        max_output_tokens=500,
        input_max_chars=4000,
    )

    result = service.review(query="What is LangGraph?", claims=[_claim(uuid4())])

    assert result.used is False
    assert result.status == "fallback"
    assert result.decisions == []


def _plan() -> ResearchPlan:
    return ResearchPlan(
        intent="definition_how_it_works",
        normalized_question="What is LangGraph?",
        subquestions=["What is LangGraph?"],
        search_queries=[
            PlannedSearchQuery(
                query_text="What is LangGraph?",
                rationale="Original query.",
                expected_source_type="general_web",
                priority=1,
            )
        ],
        source_preferences={},
        answer_outline=["Definition"],
        risk_notes=[],
        planner_mode="deterministic",
        answer_slots=[{"slot_id": "definition"}],
    )


def _chunk(chunk_id: object) -> object:
    return SimpleNamespace(
        id=chunk_id,
        text="LangGraph is a framework for stateful agent workflows.",
        chunk_no=0,
        metadata_json={},
        source_document=SimpleNamespace(
            domain="docs.langchain.com",
            canonical_url="https://docs.langchain.com/oss/python/langgraph/overview",
        ),
    )


def _claim(claim_id: object) -> object:
    return SimpleNamespace(
        id=claim_id,
        statement="LangGraph is a framework.",
        verification_status="draft",
        notes_json={},
    )
