from __future__ import annotations

import json
from uuid import uuid4

from packages.db.models import CandidateUrl
from services.orchestrator.app.llm.types import LLMRequest, LLMResponse
from services.orchestrator.app.research_quality.source_judge import SourceJudgeService
from services.orchestrator.app.services.acquisition import _sort_candidates_for_fetch


class FakeTriageProvider:
    name = "fake-triage"

    def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text=json.dumps(
                {
                    "label": "authoritative",
                    "confidence": 0.86,
                    "reasons": ["Official reference likely covers the missing slot."],
                    "priority_adjustment": -4,
                    "source_type": "official_docs",
                    "topic_fit": "high",
                    "authority": "high",
                    "novelty": "medium",
                    "expected_covered_slots": ["definition", "mechanism"],
                    "source_role": "primary_reference",
                    "triage_decision": "must_fetch",
                    "fetch_priority": 1,
                    "risk_flags": [],
                }
            ),
            model=request.model,
            provider=self.name,
        )


def test_source_judge_parses_structured_triage_fields() -> None:
    service = SourceJudgeService(
        enabled=True,
        active_rerank=False,
        active_triage=True,
        provider=FakeTriageProvider(),
        model="judge-model",
        max_candidates=5,
    )

    result = service.judge_candidates(
        [_candidate("https://docs.example.com/a", rank=1)],
        query="q",
    )[0]

    assert result.output_judgment["triage_decision"] == "must_fetch"
    assert result.output_judgment["source_role"] == "primary_reference"
    assert result.output_judgment["expected_covered_slots"] == ["definition", "mechanism"]
    assert result.used_in_final_ranking is True


def test_active_triage_must_fetch_is_ordered_before_generic_and_skips_low_value() -> None:
    must_fetch = _candidate(
        "https://docs.example.com/reference",
        rank=5,
        metadata={
            "llm_source_triage_active": True,
            "llm_source_judge": {
                "output_judgment": {
                    "triage_decision": "must_fetch",
                    "fetch_priority": 1,
                    "source_role": "primary_reference",
                }
            },
        },
    )
    skipped = _candidate(
        "https://seo.example.com/post",
        rank=1,
        metadata={
            "llm_source_triage_active": True,
            "llm_source_judge": {
                "output_judgment": {
                    "triage_decision": "skip_low_value",
                    "fetch_priority": 99,
                }
            },
        },
    )
    generic = _candidate("https://example.org/article", rank=2)

    ordered, skipped_by_triage = _sort_candidates_for_fetch(
        [skipped, generic, must_fetch],
        query="What is a token?",
        max_must_fetch_per_round=3,
    )

    assert [candidate.canonical_url for candidate in ordered] == [
        "https://docs.example.com/reference",
        "https://example.org/article",
    ]
    assert [candidate.canonical_url for candidate in skipped_by_triage] == [
        "https://seo.example.com/post"
    ]


def _candidate(url: str, *, rank: int, metadata: dict[str, object] | None = None) -> CandidateUrl:
    return CandidateUrl(
        id=uuid4(),
        task_id=uuid4(),
        search_query_id=uuid4(),
        original_url=url,
        canonical_url=url,
        domain=url.split("://", 1)[1].split("/", 1)[0],
        title="Candidate",
        rank=rank,
        selected=False,
        metadata_json=metadata or {},
    )
