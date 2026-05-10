from __future__ import annotations

import json
import pytest
from pydantic import ValidationError
from services.orchestrator.app.research_quality.llm_research_strategist import _StrategistPayload

def test_strategist_payload_normalization() -> None:
    # DeepSeek-style minimal payload
    raw = {"decision": "continue"}
    payload = _StrategistPayload.model_validate(raw)
    assert payload.decision == "continue_search"
    assert payload.decision_confidence == 0.5
    assert payload.next_queries == []

def test_strategist_payload_aliases() -> None:
    aliases = [
        ("more", "continue_search"),
        ("search_more", "continue_search"),
        ("research_more", "continue_search"),
        ("search", "continue_search"),
        ("done", "stop_sufficient"),
        ("finish", "stop_sufficient"),
        ("complete", "stop_sufficient"),
        ("sufficient", "stop_sufficient"),
        ("stop", "stop_sufficient"),
    ]
    for alias, expected in aliases:
        payload = _StrategistPayload.model_validate({"decision": alias})
        assert payload.decision == expected

def test_strategist_payload_robustness_missing_fields() -> None:
    # Test that missing non-core fields don't cause ValidationError
    raw = {
        "decision": "continue_search",
        "next_queries": [
            {"query_text": "test query"} # missing rationale, priority, etc.
        ]
    }
    payload = _StrategistPayload.model_validate(raw)
    assert payload.decision == "continue_search"
    assert payload.next_queries[0].query_text == "test query"
    assert payload.next_queries[0].rationale == "No rationale provided."
    assert payload.next_queries[0].priority == 5

def test_strategist_payload_invalid_decision_still_fails() -> None:
    # Unknown decisions should still fail
    with pytest.raises(ValidationError):
        _StrategistPayload.model_validate({"decision": "invalid_action"})

def test_strategist_payload_empty_string_query() -> None:
    raw = {
        "decision": "continue_search",
        "next_queries": [{"query_text": ""}]
    }
    payload = _StrategistPayload.model_validate(raw)
    assert payload.next_queries[0].query_text == ""
