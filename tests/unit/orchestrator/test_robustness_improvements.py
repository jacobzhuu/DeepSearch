from __future__ import annotations

import httpx
import pytest
from services.orchestrator.app.acquisition import HttpAcquisitionClient
from services.orchestrator.app.claims.drafting import (
    score_claim_statement,
    rewrite_claim_self_contained,
)
from services.orchestrator.app.research_quality.coverage_evaluator import evaluate_research_coverage

class StaticResolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        del host, port
        return self.addresses

def test_http_acquisition_client_sends_browser_headers() -> None:
    captured_headers = {}
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_headers
        captured_headers = dict(request.headers)
        return httpx.Response(200, content=b"ok", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    fetch_client = HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=3,
        max_response_bytes=1024,
        user_agent="Mozilla/5.0 Browser/1.0",
        accept_language="en-GB,en;q=0.9",
        resolver=StaticResolver("93.184.216.34"),
        client=client,
    )

    fetch_client.fetch("https://example.com/")

    assert captured_headers["user-agent"] == "Mozilla/5.0 Browser/1.0"
    assert captured_headers["accept-language"] == "en-GB,en;q=0.9"
    assert "sec-ch-ua" in captured_headers
    assert captured_headers["upgrade-insecure-requests"] == "1"

def test_claim_rewriting_pronouns() -> None:
    query = "What is ChatGPT?"
    page_title = "ChatGPT - Wikipedia"
    
    # Test "They"
    statement = "They design graphics and automate workflows."
    rewritten = rewrite_claim_self_contained(statement, page_title=page_title, query=query)
    assert rewritten == "Chatgpt design graphics and automate workflows."
    
    # Test "It"
    statement = "It is a large language model."
    rewritten = rewrite_claim_self_contained(statement, page_title=page_title, query=query)
    assert rewritten == "Chatgpt is a large language model."

    # Test "This tool"
    statement = "This tool helps users with writing."
    rewritten = rewrite_claim_self_contained(statement, page_title=page_title, query=query)
    assert rewritten == "Chatgpt helps users with writing."

def test_lenient_focus_filtering() -> None:
    query = "What is ChatGPT?"
    page_title = "ChatGPT - Wikipedia"
    
    # This statement doesn't contain "ChatGPT" but it's contextually relevant via page title
    statement = "It is a large language model developed by OpenAI to assist users with various tasks."
    
    score = score_claim_statement(
        statement=statement,
        query=query,
        page_title=page_title
    )
    
    # Should be at least recall_candidate instead of rejected if focus mismatch is the only issue
    assert score.candidate_tier != "rejected"

def test_coverage_distinguishes_blocked_and_underprocessed() -> None:
    slot_coverage = [
        {"slot_id": "official_news", "required": True, "status": "missing"},
        {"slot_id": "under_limit", "required": True, "status": "missing"}
    ]
    
    source_yield = [
        {
            "url": "https://openai.com/news",
            "target_slot_ids": ["official_news"],
            "dropped_reasons": ["blocked_by_policy"],
            "attempted": True,
            "fetched": False
        },
        {
            "url": "https://example.com/deep",
            "target_slot_ids": ["under_limit"],
            "fetched": True,
            "parsed": False
        }
    ]
    
    evaluation = evaluate_research_coverage(
        slot_coverage_summary=slot_coverage,
        source_yield_summary=source_yield
    )
    
    assert "official_news" in evaluation.required_slots_blocked
    assert "under_limit" in evaluation.required_slots_underprocessed
    assert "authoritative_sources_blocked" in evaluation.warnings

from services.orchestrator.app.settings import Settings

def test_limits_are_configurable() -> None:
    # Set env vars
    import os
    os.environ["RESEARCH_PARSE_LIMIT"] = "15"
    os.environ["RESEARCH_CLAIM_LIMIT"] = "50"
    
    settings = Settings()
    assert settings.research_parse_limit == 15
    assert settings.research_claim_limit == 50
