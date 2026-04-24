from __future__ import annotations

import pytest

from services.orchestrator.app.claims import (
    CitationSpanValidationError,
    compute_claim_confidence,
    draft_claim_statement,
    normalized_excerpt_hash,
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
