from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlannedSearchQuery:
    query_text: str
    rationale: str
    expected_source_type: str
    priority: int
    query_source: str = "planner_query"

    def to_payload(self) -> dict[str, Any]:
        return {
            "query_text": self.query_text,
            "rationale": self.rationale,
            "expected_source_type": self.expected_source_type,
            "priority": self.priority,
            "query_source": self.query_source,
        }


@dataclass(frozen=True)
class ResearchPlan:
    intent: str
    normalized_question: str
    subquestions: list[str]
    search_queries: list[PlannedSearchQuery]
    source_preferences: dict[str, Any]
    answer_outline: list[str]
    risk_notes: list[str]
    planner_mode: str
    warnings: list[str] = field(default_factory=list)
    answer_slots: list[dict[str, Any]] = field(default_factory=list)
    raw_planner_queries: list[dict[str, Any]] = field(default_factory=list)
    final_search_queries: list[dict[str, Any]] = field(default_factory=list)
    dropped_or_downweighted_planner_queries: list[dict[str, Any]] = field(default_factory=list)
    planner_guardrail_warnings: list[str] = field(default_factory=list)
    intent_classification: str | None = None
    extracted_entity: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "normalized_question": self.normalized_question,
            "subquestions": list(self.subquestions),
            "search_queries": [query.to_payload() for query in self.search_queries],
            "source_preferences": dict(self.source_preferences),
            "answer_outline": list(self.answer_outline),
            "answer_slots": list(self.answer_slots),
            "risk_notes": list(self.risk_notes),
            "planner_mode": self.planner_mode,
            "warnings": list(self.warnings),
            "raw_planner_queries": list(self.raw_planner_queries),
            "final_search_queries": list(self.final_search_queries),
            "dropped_or_downweighted_planner_queries": list(
                self.dropped_or_downweighted_planner_queries
            ),
            "planner_guardrail_warnings": list(self.planner_guardrail_warnings),
            "intent_classification": self.intent_classification,
            "extracted_entity": self.extracted_entity,
        }

    def summary_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "planner_mode": self.planner_mode,
            "intent": self.intent,
            "subquestion_count": len(self.subquestions),
            "search_query_count": len(self.search_queries),
            "warnings": list(self.warnings),
            "answer_slots": list(self.answer_slots),
            "raw_planner_queries": list(self.raw_planner_queries),
            "final_search_queries": list(self.final_search_queries),
            "dropped_or_downweighted_planner_queries": list(
                self.dropped_or_downweighted_planner_queries
            ),
            "planner_guardrail_warnings": list(self.planner_guardrail_warnings),
            "intent_classification": self.intent_classification,
            "extracted_entity": self.extracted_entity,
        }
        overridden = self.source_preferences.get("overridden_avoid_domains")
        if isinstance(overridden, list):
            payload["planner_avoid_domains_overridden"] = list(overridden)
        return payload
