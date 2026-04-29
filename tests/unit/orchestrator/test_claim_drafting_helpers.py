from __future__ import annotations

import pytest

from services.orchestrator.app.claims import (
    CitationSpanValidationError,
    classify_query_intent,
    compute_claim_confidence,
    draft_claim_statement,
    is_answer_relevant_score,
    is_claimable_statement,
    normalize_claim_identity,
    normalized_excerpt_hash,
    score_claim_statement,
    select_supporting_span,
    validate_citation_span,
)


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
    assert not is_claimable_statement("OpenAI artificial intelligence research organization")
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
