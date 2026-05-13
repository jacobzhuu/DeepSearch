from __future__ import annotations

import pytest

from services.orchestrator.app.claims import (
    CitationSpanValidationError,
    classify_query_intent,
    compute_claim_confidence,
    deployment_evidence_statement,
    deployment_slot_ids_for_claim_text,
    deployment_slot_ids_for_evidence,
    draft_claim_statement,
    is_answer_relevant_score,
    is_claimable_statement,
    is_deployment_evidence_excerpt,
    iter_deployment_evidence_spans,
    normalize_claim_identity,
    normalized_excerpt_hash,
    score_claim_statement,
    select_supporting_span,
    select_verification_span,
    validate_citation_span,
)
from services.orchestrator.app.research_quality import answer_slot_coverage


def test_select_supporting_span_prefers_informative_matching_sentence() -> None:
    text = (
        "Example Domain\n\n"
        "This domain is for use in illustrative examples in documents and test content."
    )

    span = select_supporting_span(text, "example")

    assert (
        span.excerpt
        == "This domain is for use in illustrative examples in documents and test content."
    )
    assert text[span.start_offset : span.end_offset] == span.excerpt


def test_select_supporting_span_rejects_short_title_and_fragment_spans() -> None:
    text = "What Is OpenAI?\n\nData\n\nC"

    with pytest.raises(CitationSpanValidationError):
        select_supporting_span(text, "What is OpenAI?")


def test_claim_quality_rules_require_complete_non_duplicate_statement() -> None:
    assert not is_claimable_statement("Data")
    assert not is_claimable_statement("What Is OpenAI?", query="What is OpenAI?")
    fragment_score = score_claim_statement(
        statement="OpenAI artificial intelligence research organization",
        query="What is OpenAI?",
    )
    assert is_claimable_statement(
        "OpenAI artificial intelligence research organization",
        query="What is OpenAI?",
    )
    assert fragment_score.answer_relevant is False
    assert fragment_score.candidate_tier == "recall_candidate"
    assert is_claimable_statement(
        "OpenAI is an artificial intelligence research and deployment company.",
        query="What is OpenAI?",
    )
    assert normalize_claim_identity("What is OpenAI?") == normalize_claim_identity(
        "What Is OpenAI?"
    )


def test_validate_citation_span_rejects_excerpt_mismatch() -> None:
    text = "Alpha beta gamma."

    with pytest.raises(CitationSpanValidationError):
        validate_citation_span(text, 0, 5, "Alpha ")


def test_draft_claim_helpers_normalize_and_score_claims() -> None:
    statement = draft_claim_statement("  Alpha   beta  gamma.  ")
    confidence = compute_claim_confidence(
        query="alpha gamma",
        statement=statement,
        retrieval_score=1.0,
    )

    assert statement == "Alpha beta gamma."
    assert 0.35 <= confidence <= 0.95
    assert normalized_excerpt_hash("Alpha   beta gamma.") == normalized_excerpt_hash(
        "alpha beta gamma."
    )


def test_claim_quality_filters_reference_titles_and_incomplete_quotes() -> None:
    reference_title = "Implementación De Un Prototipo (Bachelor Thesis)."
    incomplete_quote = 'SearXNG supports categories of "Web, "Images," and "Videos.'

    assert not is_claimable_statement(
        reference_title,
        query="What is SearXNG and how does it work?",
    )
    assert not is_claimable_statement(
        incomplete_quote,
        query="What is SearXNG and how does it work?",
    )


def test_draft_claim_statement_normalizes_complete_category_quotes() -> None:
    statement = draft_claim_statement('SearXNG supports categories of "Web, "Images," and "News".')

    assert statement == 'SearXNG supports categories of "Web", "Images", and "News".'
    assert is_claimable_statement(statement, query="What is SearXNG and how does it work?")


def test_valid_explanatory_searxng_sentence_remains_claimable() -> None:
    assert is_claimable_statement(
        (
            "SearXNG is a free internet metasearch engine that aggregates results from "
            "other search services."
        ),
        query="What is SearXNG and how does it work?",
    )


def test_query_intent_classifier_identifies_definition_mechanism_query() -> None:
    intent = classify_query_intent("What is SearXNG and how does it work?")

    assert intent.intent_name == "definition_mechanism"
    assert intent.subject_terms == ("searxng",)
    assert intent.expected_claim_types == ("definition", "mechanism", "privacy", "feature")
    assert "community" in intent.avoid_claim_types


def test_chinese_transformer_definition_claims_survive_hard_filter() -> None:
    query = "什么是 transformer 架构？"
    accepted_sentences = [
        "Transformer 是一种基于注意力机制的神经网络架构",
        "编码器由多头注意力和前馈网络组成",
        "自注意力用于建模序列中不同位置之间的关系",
        "Transformer architecture relies on self-attention",
    ]

    intent = classify_query_intent(query)

    assert intent.intent_name == "generic"
    assert intent.subject_terms == ()
    for sentence in accepted_sentences:
        score = score_claim_statement(statement=sentence, query=query, domain="arxiv.org")
        assert is_claimable_statement(sentence, query=query)
        assert score.rejected_reason is None
        assert score.candidate_tier in {
            "main_candidate",
            "supporting_candidate",
            "recall_candidate",
        }
        assert score.answer_role in {"definition", "mechanism", "other"}


def test_claim_hard_filter_still_rejects_garbage() -> None:
    query = "什么是 transformer 架构？"
    rejected = [
        "We use cookies to improve your experience.",
        "Privacy Policy",
        "Home",
        "-",
        '{"key": "value"}',
        "Join our community on GitHub",
        "Search without being tracked.",
    ]

    for statement in rejected:
        score = score_claim_statement(statement=statement, query=query)
        assert not is_claimable_statement(statement, query=query)
        assert score.triage_status.value == "reject_fatal"
        assert score.candidate_tier == "rejected"


def test_chinese_transformer_scoring_uses_source_suitability_without_punctuation_bias() -> None:
    query = "什么是 transformer 架构？"
    definition = "Transformer 是一种基于注意力机制的神经网络架构"
    component = "编码器由多头注意力和前馈网络组成"
    mechanism = "自注意力用于建模序列中不同位置之间的关系"
    arxiv_score = score_claim_statement(statement=definition, query=query, domain="arxiv.org")
    wiki_score = score_claim_statement(statement=definition, query=query, domain="wikipedia.org")
    raw_score = score_claim_statement(
        statement=definition,
        query=query,
        domain="raw.githubusercontent.com",
    )
    product_docs_score = score_claim_statement(
        statement="Amazon SageMaker includes a Transformer integration for deployment",
        query=query,
        domain="docs.aws.amazon.com",
    )

    assert arxiv_score.source_suitability_score > raw_score.source_suitability_score
    assert wiki_score.source_suitability_score > raw_score.source_suitability_score
    assert product_docs_score.source_suitability_score < arxiv_score.source_suitability_score
    for statement in (definition, component, mechanism):
        score = score_claim_statement(statement=statement, query=query, domain="arxiv.org")
        assert score.candidate_tier in {
            "main_candidate",
            "supporting_candidate",
            "recall_candidate",
        }
        assert score.rejected_reason is None
        assert score.claim_quality_score >= 0.45


def test_readme_repository_sources_get_narrow_suitability_boost() -> None:
    query = "What is LangGraph and how does it work?"
    statement = "LangGraph supports durable execution and human-in-the-loop workflows."
    raw_non_readme = score_claim_statement(
        statement=statement,
        query=query,
        domain="raw.githubusercontent.com",
        source_url="https://raw.githubusercontent.com/langchain-ai/langgraph/main/src/core.py",
    )
    raw_readme = score_claim_statement(
        statement=statement,
        query=query,
        domain="raw.githubusercontent.com",
        source_url="https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md",
    )
    github_repo = score_claim_statement(
        statement=statement,
        query=query,
        domain="github.com",
        source_url="https://github.com/langchain-ai/langgraph",
    )

    assert raw_readme.source_suitability_score > raw_non_readme.source_suitability_score
    assert github_repo.source_suitability_score >= raw_non_readme.source_suitability_score


def test_query_aware_claim_filters_accept_answer_sentences() -> None:
    query = "What is SearXNG and how does it work?"
    accepted_sentences = [
        (
            "SearXNG is a metasearch engine, aggregating the results of other search "
            "engines while not storing information about its users."
        ),
        (
            "It provides basic privacy by mixing your queries with searches on other "
            "platforms without storing search data."
        ),
        "SearXNG supports OpenSearch.",
    ]

    for sentence in accepted_sentences:
        score = score_claim_statement(statement=sentence, query=query)
        assert is_claimable_statement(sentence, query=query)
        assert score.rejected_reason is None
        assert score.claim_quality_score >= 0.45
        assert score.query_answer_score >= 0.35


def test_query_aware_claim_filters_reject_cta_slogans_and_community_text() -> None:
    query = "What is SearXNG and how does it work?"
    rejected_sentences = [
        "Track development, send contributions, and report issues at SearXNG sources.",
        "reclaim their privacy and make the internet freer.",
        "Come join us on Matrix if you have questions.",
        "You can improve SearXNG translations at Weblate.",
        "SearXNG sources and run it yourself!",
    ]

    for sentence in rejected_sentences:
        score = score_claim_statement(statement=sentence, query=query)
        assert not is_claimable_statement(sentence, query=query)
        assert score.rejected_reason is not None
        assert score.query_answer_score < 0.35


def test_query_aware_claim_filters_reject_setup_and_broken_link_residue() -> None:
    query = "What is SearXNG and how does it work?"
    rejected_sentences = [
        "Get started with SearXNG by using one of the instances listed at .",
        "SearXNG users can choose an instance listed at .",
        "If you don't trust anyone, you can set up your own, see .",
        "SearXNG is a free internet metasearch engine which aggregates results from up to 251 .",
        "How do I set it as the default search engine?",
    ]

    for sentence in rejected_sentences:
        score = score_claim_statement(statement=sentence, query=query)
        assert not is_claimable_statement(sentence, query=query)
        assert score.rejected_reason in {
            "broken_link_residue",
            "question_like",
            "setup_instruction",
            "imperative_or_call_to_action",
        }
        assert score.query_answer_score < 0.35


def test_query_aware_claim_filters_accept_privacy_sentences_from_wikipedia_chunk() -> None:
    query = "What is SearXNG and how does it work?"
    privacy_sentences = [
        "SearXNG removes private data from requests sent to search services.",
        "SearXNG itself stores little to no information that can be used to identify users.",
    ]

    for sentence in privacy_sentences:
        score = score_claim_statement(statement=sentence, query=query)
        assert is_claimable_statement(sentence, query=query)
        assert score.rejected_reason is None
        assert score.claim_category == "privacy"
        assert score.query_answer_score >= 0.85

    assert not is_claimable_statement("See also", query=query)
    assert not is_claimable_statement("References", query=query)


def test_answer_focused_scores_rank_definition_above_contribution_text() -> None:
    query = "What is SearXNG and how does it work?"

    definition = score_claim_statement(
        statement=(
            "SearXNG is a metasearch engine, aggregating the results of other search "
            "engines while not storing information about its users."
        ),
        query=query,
    )
    privacy = score_claim_statement(
        statement=(
            "It provides basic privacy by mixing your queries with searches on other "
            "platforms without storing search data."
        ),
        query=query,
    )
    contribution = score_claim_statement(
        statement="Track development, send contributions, and report issues at SearXNG sources.",
        query=query,
    )

    assert definition.final_score > contribution.final_score
    assert privacy.final_score > contribution.final_score
    assert definition.claim_category == "definition"
    assert privacy.claim_category == "privacy"


def test_answer_role_classifies_official_about_style_sentences() -> None:
    query = "What is SearXNG and how does it work?"
    expected = [
        ("SearXNG does not generate a profile about users.", "privacy"),
        (
            "SearXNG mixes queries with searches on other platforms without storing "
            "search data.",
            "privacy",
        ),
        ("SearXNG aggregates results from multiple search services.", "mechanism"),
        ("SearXNG is free software and can be self-hosted.", "deployment/self_hosting"),
    ]

    for sentence, category in expected:
        score = score_claim_statement(statement=sentence, query=query)
        assert score.rejected_reason is None
        assert score.claim_category == category
        assert score.answer_role == category
        assert score.answer_relevant is True
        assert is_answer_relevant_score(score, query=query)


def test_technical_framework_sentences_cover_mechanism_features_and_trust() -> None:
    query = "What is LangGraph and how does it work?"
    expected = [
        (
            (
                "LangGraph uses graph-based architectures to model relationships between "
                "components of an AI agent workflow."
            ),
            "mechanism",
        ),
        (
            "Edges determine which node should run next based on the current state.",
            "mechanism",
        ),
        (
            (
                "LangGraph provides durable execution, streaming, memory, checkpointing, "
                "and integrations for long-running agents."
            ),
            "feature",
        ),
        (
            "Human-in-the-loop lets developers inspect and modify agent state at any point.",
            "privacy",
        ),
    ]

    for sentence, category in expected:
        score = score_claim_statement(statement=sentence, query=query)
        assert is_claimable_statement(sentence, query=query)
        assert score.rejected_reason is None
        assert score.claim_category == category
        assert score.answer_role == category
        assert score.answer_relevant is True
        assert is_answer_relevant_score(score, query=query)


def test_langgraph_cjk_framework_claims_cover_definition_and_mechanism_slots() -> None:
    query = "What is LangGraph and how does it work?"
    expected = [
        (
            "LangGraph是一个低级编排框架和运行时，用于构建、管理和部署长时间运行的有状态代理。",
            "definition",
        ),
        (
            "LangGraph为任何长时间运行的有状态工作流或代理提供低级支持基础设施。",
            "mechanism",
        ),
        (
            "LangGraph专注于对代理编排重要的底层功能：持久执行、流式传输、人机协作等。",
            "privacy",
        ),
    ]
    categories: set[str] = set()

    for sentence, category in expected:
        score = score_claim_statement(statement=sentence, query=query)
        assert score.rejected_reason is None
        assert score.claim_category == category
        assert score.answer_relevant is True
        assert is_answer_relevant_score(score, query=query)
        categories.add(score.claim_category)

    coverage = answer_slot_coverage(query, categories)
    required_coverage = {
        row["slot_id"]: row["covered"]
        for row in coverage
        if row["slot_id"] in {"definition", "core_abstractions"}
    }
    assert required_coverage["definition"] is True
    assert required_coverage["core_abstractions"] is True


def test_draft_claim_statement_strips_leading_dash_fragment_before_definition() -> None:
    statement = draft_claim_statement(
        "Morgan, and more— LangGraph is a low-level orchestration framework and runtime "
        "for building, managing, and deploying long-running, stateful agents."
    )

    assert statement.startswith("LangGraph is a low-level orchestration framework")


def test_answer_role_rejects_navigation_project_meta_and_documentation_pointers() -> None:
    query = "What is SearXNG and how does it work?"
    rejected = [
        "For more information, visit the documentation.",
        "Read the documentation page to continue.",
        "SearXNG has an open community that makes it better.",
        "Join Matrix and send contributions to the source code.",
    ]

    for sentence in rejected:
        score = score_claim_statement(statement=sentence, query=query)
        assert not is_claimable_statement(sentence, query=query)
        assert not is_answer_relevant_score(score, query=query)
        assert score.answer_role == "non_answer"
        assert score.rejected_reason in {
            "navigation_or_documentation_pointer",
            "community_or_contribution",
        }


def test_deployment_command_snippet_becomes_claimable_verified_evidence() -> None:
    query = "How to deploy SearXNG with Docker?"
    source_text = (
        "Manual instancing example:\n\n"
        "$ docker run --name searxng -d \\\n"
        "    -p 8888:8080 \\\n"
        '    -v "./config/:/etc/searxng/" \\\n'
        '    -v "./data/:/var/cache/searxng/" \\\n'
        "    docker.io/searxng/searxng:latest\n"
    )
    spans = list(iter_deployment_evidence_spans(source_text))
    assert spans
    span = spans[0]
    statement = deployment_evidence_statement(span.excerpt)
    score = score_claim_statement(statement=statement, query=query)
    verification = select_verification_span(source_text, statement)

    assert is_deployment_evidence_excerpt(span.excerpt)
    assert is_claimable_statement(statement, query=query)
    assert score.claim_category == "deployment/self_hosting"
    assert score.answer_relevant is True
    assert {
        "deployment_run_or_compose",
        "deployment_ports",
        "deployment_volumes",
    }.issubset(set(deployment_slot_ids_for_evidence(statement, span.excerpt)))
    assert verification is not None
    assert verification.relation_type == "support"
    assert verification.support_level == "strong"


def test_deployment_fenced_yaml_span_covers_complete_block_for_verification() -> None:
    source_text = (
        "Compose template:\n\n"
        "```yaml\n"
        "services:\n"
        "  searxng:\n"
        "    image: docker.io/searxng/searxng:latest\n"
        "    environment:\n"
        "      - SEARXNG_SECRET=change-me\n"
        "      - SEARXNG_BASE_URL=https://example.test/\n"
        "    volumes:\n"
        "      - ./searxng:/etc/searxng:rw\n"
        "    ports:\n"
        "      - 8888:8080\n"
        "```\n\n"
        "After the block, run the stack."
    )

    spans = list(iter_deployment_evidence_spans(source_text))
    span = spans[0]
    statement = deployment_evidence_statement(span.excerpt)
    verification = select_verification_span(source_text, statement)

    assert span.excerpt.startswith("```yaml")
    assert span.excerpt.endswith("```")
    assert "SEARXNG_SECRET=change-me" in span.excerpt
    assert "8888:8080" in span.excerpt
    assert source_text[span.start_offset : span.end_offset] == span.excerpt
    assert verification is not None
    assert verification.excerpt == span.excerpt
    assert "SEARXNG_BASE_URL=https://example.test/" in verification.excerpt


def test_deployment_claim_text_maps_specific_slots_without_generic_fill() -> None:
    query = "How to deploy SearXNG with Docker?"
    advanced = "This section is intended for advanced users."
    reverse_proxy = (
        "Use a reverse proxy with certificates and limiter bot protection before exposing a "
        "public SearXNG instance."
    )
    update = "Update SearXNG by running docker compose pull and reviewing new templates."
    archived = "The searxng-docker repository is archived and superseded by the main repository."

    assert deployment_slot_ids_for_claim_text(advanced, advanced) == ()
    assert deployment_slot_ids_for_claim_text(reverse_proxy, reverse_proxy) == (
        "deployment_configuration",
        "deployment_security",
    )
    assert deployment_slot_ids_for_claim_text(update, update) == (
        "deployment_configuration",
        "deployment_update_maintenance",
    )
    assert deployment_slot_ids_for_claim_text(archived, archived) == (
        "deployment_update_maintenance",
    )
    assert score_claim_statement(statement=archived, query=query).claim_category == (
        "deployment/self_hosting"
    )


def test_force_ownership_is_not_security_evidence_by_itself() -> None:
    excerpt = "FORCE_OWNERSHIP=1"
    statement = deployment_evidence_statement(excerpt)

    slot_ids = deployment_slot_ids_for_evidence(statement, excerpt)

    assert "deployment_configuration" in slot_ids
    assert "deployment_volumes" in slot_ids
    assert "deployment_security" not in slot_ids


def test_docker_exec_root_is_troubleshooting_not_security() -> None:
    excerpt = "docker compose exec -u root searxng sh"
    statement = deployment_evidence_statement(excerpt)

    slot_ids = deployment_slot_ids_for_evidence(statement, excerpt)

    assert slot_ids == ("deployment_troubleshooting",)
    assert "deployment_troubleshooting" in slot_ids
    assert "deployment_security" not in slot_ids


def test_generic_intent_zero_overlap_off_topic_sentence_is_not_answer_relevant() -> None:
    # Reproduces the failure mode from task 2ee01a1c-... where the generic
    # intent floor of 0.45 made a PubMed sentence about HIV pharmacology pass
    # the answer-relevance gate for a question about NVIDIA's open-source
    # ecosystem.
    query = "近30天 NVIDIA在开源模型生态上的关键发布与影响"
    off_topic_statement = (
        "To compare time to suicidality with efavirenz-containing versus "
        "efavirenz-free antiretroviral regimens for initial treatment of HIV."
    )
    page_title = (
        "Association between efavirenz as initial therapy for HIV-1 infection "
        "and increased risk for suicidal ideation or attempted or completed suicide."
    )

    score = score_claim_statement(
        statement=off_topic_statement,
        query=query,
        page_title=page_title,
    )

    intent = classify_query_intent(query)
    assert intent.intent_name == "generic"
    assert score.query_relevance_score == 0.0
    assert score.query_answer_score < 0.35
    assert score.answer_relevant is False


def test_generic_intent_keeps_floor_when_query_token_appears_in_title() -> None:
    # A page whose title carries the query subject should not be punished even
    # when the individual sentence lacks any overlap, so pronoun-led
    # continuations remain answer-relevant under generic intent.
    query = "近30天 NVIDIA在开源模型生态上的关键发布与影响"
    pronoun_statement = "It runs on commodity hardware and is widely deployed."
    page_title = "NVIDIA open source release overview"

    score = score_claim_statement(
        statement=pronoun_statement,
        query=query,
        page_title=page_title,
    )

    assert score.query_answer_score >= 0.35
