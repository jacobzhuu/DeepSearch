from __future__ import annotations

import json

from services.orchestrator.app.llm.types import LLMRequest, LLMResponse
from services.orchestrator.app.research_quality.llm_research_strategist import (
    LLMResearchStrategistService,
)


class FakeStrategistProvider:
    name = "fake-strategist"

    def __init__(self, payload: dict[str, object] | str) -> None:
        self.payload = payload

    def generate(self, request: LLMRequest) -> LLMResponse:
        assert request.metadata["prompt_version"] == "llm_research_strategist_v1"
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return LLMResponse(text=text, model=request.model, provider=self.name)


def test_llm_research_strategist_parses_and_deduplicates_queries() -> None:
    service = LLMResearchStrategistService(
        enabled=True,
        provider=FakeStrategistProvider(
            {
                "decision": "continue_search",
                "decision_confidence": 0.82,
                "stop_reason": None,
                "coverage_assessment": {
                    "overall_status": "insufficient",
                    "required_slots_missing": ["mechanism"],
                    "main_problem": "Missing tokenization mechanism evidence.",
                },
                "next_queries": [
                    {
                        "query_text": "LLM token tokenization subword example",
                        "language": "en",
                        "target_slots": ["mechanism"],
                        "expected_source_types": ["technical_reference"],
                        "rationale": "Find mechanism evidence.",
                        "priority": 1,
                    },
                    {
                        "query_text": "existing query",
                        "language": "en",
                        "target_slots": ["definition"],
                        "expected_source_types": ["official_docs"],
                        "rationale": "Duplicate should be skipped.",
                        "priority": 2,
                    },
                ],
                "source_selection_guidance": {
                    "must_fetch_source_types": ["official_docs"],
                    "prefer_new_domains": True,
                    "avoid_domains": [],
                    "avoid_reason": None,
                },
                "minimum_evidence_to_stop": {
                    "required_slots_must_be_at_least": "moderate",
                    "min_distinct_domains": 3,
                    "min_primary_or_reference_sources": 1,
                    "allow_report_with_warning": False,
                },
            }
        ),
        model="strategy-model",
        max_queries=4,
        max_output_tokens=1200,
        input_max_chars=4000,
    )

    result = service.decide(
        _research_state(),
        existing_query_texts={"Existing Query"},
    )

    assert result.status == "used"
    assert result.used is True
    assert result.decision == "continue_search"
    assert [query.query_text for query in result.planned_queries] == [
        "LLM token tokenization subword example"
    ]
    assert result.planned_queries[0].query_source == "llm_research_strategist"
    assert result.diagnostics["accepted_next_query_count"] == 1


def test_llm_research_strategist_invalid_json_falls_back() -> None:
    service = LLMResearchStrategistService(
        enabled=True,
        provider=FakeStrategistProvider("not json"),
        model="strategy-model",
        max_queries=4,
        max_output_tokens=1200,
        input_max_chars=4000,
    )

    result = service.decide(_research_state())

    assert result.status == "fallback"
    assert result.used is False
    assert result.planned_queries == ()
    assert result.fallback_reason == "JSONDecodeError"


def test_llm_research_strategist_stop_decision_does_not_emit_queries() -> None:
    service = LLMResearchStrategistService(
        enabled=True,
        provider=FakeStrategistProvider(
            {
                "decision": "stop_sufficient",
                "decision_confidence": 0.9,
                "stop_reason": "Required slots are covered.",
                "coverage_assessment": {
                    "overall_status": "sufficient",
                    "required_slots_missing": [],
                    "main_problem": None,
                },
                "next_queries": [
                    {
                        "query_text": "unneeded query",
                        "language": "en",
                        "target_slots": ["definition"],
                        "expected_source_types": ["official_docs"],
                        "rationale": "Should not be used for stop decisions.",
                        "priority": 1,
                    }
                ],
            }
        ),
        model="strategy-model",
        max_queries=4,
        max_output_tokens=1200,
        input_max_chars=4000,
    )

    result = service.decide(_research_state())

    assert result.status == "used"
    assert result.decision == "stop_sufficient"
    assert result.planned_queries == ()


def _research_state() -> dict[str, object]:
    return {
        "question": "什么是LLM中的token？",
        "round_index": 1,
        "budget_remaining": {
            "max_rounds_remaining": 2,
            "search_queries_remaining": 6,
            "fetch_attempts_remaining": 8,
            "llm_calls_remaining": 3,
        },
        "answer_slots": [
            {
                "slot_id": "definition",
                "required": True,
                "status": "weak",
            }
        ],
        "previous_queries": [{"query_text": "existing query", "round": 0}],
        "candidate_summary": [],
        "verified_claim_summary": [],
    }
