from __future__ import annotations

from urllib.parse import urlsplit
from services.orchestrator.app.claims import classify_query_intent, score_claim_statement
from services.orchestrator.app.parsing.quality import assess_source_quality

def test_news_intent_classification() -> None:
    queries = [
        "介绍一下Claude的近期讯息",
        "OpenAI recent news",
        "Anthropic latest updates",
        "Claude 3.5 Sonnet recent developments",
        "GPT-4o mini 最新动态",
    ]
    
    for query in queries:
        intent = classify_query_intent(query)
        assert intent.intent_name == "news"
        assert "feature" in intent.expected_claim_types
        assert "mechanism" in intent.expected_claim_types

def test_news_claim_thresholds() -> None:
    query = "介绍一下Claude的近期讯息"
    # A typical news statement that might be borderline
    statement = "Anthropic recently updated Claude with new tool use capabilities in the latest API release."
    
    score = score_claim_statement(statement=statement, query=query, domain="anthropic.com")
    
    assert score.rejected_reason is None
    # With news intent, thresholds are lower (0.40/0.32 vs 0.45/0.35)
    assert score.claim_quality_score >= 0.40
    assert score.candidate_tier in {"main_candidate", "supporting_candidate"}

def test_official_vendor_source_quality() -> None:
    # Test that vendor domains get high authority scores
    vendor_urls = [
        ("https://www.anthropic.com/news/claude-3-5-sonnet", "official_docs"),
        ("https://openai.com/blog/gpt-4o-mini-advancing-cost-efficient-intelligence", "official_docs"),
        ("https://blog.google/technology/ai/google-gemini-update-flash-ai-assistant/", "official_docs"),
        ("https://deepmind.google/technologies/gemini/", "official_vendor_domain"), # No blog in path
        ("https://blogs.nvidia.com/blog/2024/03/18/6g-research-platform-ai/", "official_docs"),
        ("https://aws.amazon.com/blogs/aws/new-amazon-bedrock-features-at-reinvent/", "official_docs"),
    ]
    
    for url, expected_label in vendor_urls:
        quality = assess_source_quality(canonical_url=url, domain=urlsplit(url).netloc)
        assert quality.reason == expected_label
        if expected_label == "official_docs":
            assert quality.authority_score >= 0.90
        else:
            assert quality.authority_score >= 0.75

def test_short_chinese_news_claim() -> None:
    query = "介绍一下Claude的近期讯息"
    # A short but meaningful Chinese news update (14 CJK chars)
    statement = "Anthropic 发布了 Claude 3.5 Sonnet 模型。"
    
    score = score_claim_statement(statement=statement, query=query)
    
    assert score.rejected_reason is None
    assert score.candidate_tier in {"main_candidate", "supporting_candidate"}
