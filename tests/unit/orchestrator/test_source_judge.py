from __future__ import annotations

import json
from uuid import uuid4

from packages.db.models import CandidateUrl
from services.orchestrator.app.llm.types import LLMRequest, LLMResponse
from services.orchestrator.app.research_quality.source_judge import SourceJudgeService


class FakeJudgeProvider:
    name = "fake"

    def generate(self, request: LLMRequest) -> LLMResponse:
        assert request.metadata["prompt_version"] == "source_judge_shadow_v1"
        return LLMResponse(
            text=json.dumps(
                {
                    "label": "authoritative",
                    "confidence": 0.8,
                    "reasons": ["Official-looking documentation URL."],
                    "priority_adjustment": 0.1,
                }
            ),
            model=request.model,
            provider=self.name,
        )


def test_source_judge_disabled_returns_audited_fallback() -> None:
    service = SourceJudgeService(
        enabled=False,
        active_rerank=False,
        provider=None,
        model="",
        max_candidates=5,
    )

    result = service.judge_candidates([_candidate()], query="What is SearXNG?")[0]

    assert result.fallback_status == "disabled"
    assert result.output_judgment["label"] == "uncertain"
    assert result.used_in_final_ranking is False


def test_source_judge_accepts_structured_llm_shadow_result() -> None:
    service = SourceJudgeService(
        enabled=True,
        active_rerank=False,
        provider=FakeJudgeProvider(),
        model="judge-model",
        max_candidates=5,
    )

    result = service.judge_candidates([_candidate()], query="What is SearXNG?")[0]

    assert result.provider == "fake"
    assert result.model == "judge-model"
    assert result.output_judgment["label"] == "authoritative"
    assert result.confidence == 0.8
    assert result.used_in_final_ranking is False


def _candidate() -> CandidateUrl:
    return CandidateUrl(
        id=uuid4(),
        task_id=uuid4(),
        search_query_id=uuid4(),
        original_url="https://docs.searxng.org/user/about.html",
        canonical_url="https://docs.searxng.org/user/about.html",
        domain="docs.searxng.org",
        title="About SearXNG",
        rank=1,
        selected=False,
        metadata_json={"snippet": "SearXNG documentation."},
    )
