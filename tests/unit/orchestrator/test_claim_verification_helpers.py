from __future__ import annotations

from services.orchestrator.app.claims import (
    CLAIM_EVIDENCE_RELATION_CONTRADICT,
    CLAIM_EVIDENCE_RELATION_SUPPORT,
    CLAIM_VERIFICATION_STATUS_MIXED,
    CLAIM_VERIFICATION_STATUS_SUPPORTED,
    CLAIM_VERIFICATION_STATUS_UNSUPPORTED,
    build_verification_rationale,
    resolve_verification_status,
    select_verification_span,
)


def test_select_verification_span_prefers_exact_support_match() -> None:
    text = (
        "Example Domain. "
        "This domain is for use in illustrative examples in documents and test content. "
        "It may appear in documentation."
    )

    match = select_verification_span(
        text,
        "This domain is for use in illustrative examples in documents and test content.",
    )

    assert match is not None
    assert match.relation_type == CLAIM_EVIDENCE_RELATION_SUPPORT
    assert match.relation_detail == "strong_support"
    assert match.support_level == "strong"
    assert (
        match.excerpt
        == "This domain is for use in illustrative examples in documents and test content."
    )


def test_select_verification_span_detects_negated_contradiction() -> None:
    text = (
        "Example Domain. "
        "This domain is not for use in illustrative examples in documents. "
        "It is reserved for internal use."
    )

    match = select_verification_span(
        text,
        "This domain is for use in illustrative examples in documents.",
    )

    assert match is not None
    assert match.relation_type == CLAIM_EVIDENCE_RELATION_CONTRADICT
    assert match.relation_detail == "contradiction"
    assert match.excerpt == "This domain is not for use in illustrative examples in documents."


def test_select_verification_span_marks_moderate_overlap_as_weak_support() -> None:
    text = (
        "OpenSearch indexes documents and can search them through a distributed engine. "
        "It also provides dashboards for observability."
    )

    match = select_verification_span(
        text,
        "OpenSearch is a distributed engine for searching indexed documents.",
    )

    assert match is not None
    assert match.relation_type == CLAIM_EVIDENCE_RELATION_SUPPORT
    assert match.relation_detail == "weak_support"
    assert match.support_level == "weak"


def test_select_verification_span_rejects_numeric_mismatch() -> None:
    text = "The system supports 10 engines in the default configuration."

    match = select_verification_span(
        text,
        "The system supports 20 engines in the default configuration.",
    )

    assert match is None


def test_resolve_verification_status_and_rationale_cover_minimum_phase8_states() -> None:
    assert (
        resolve_verification_status(support_count=1, contradict_count=0)
        == CLAIM_VERIFICATION_STATUS_SUPPORTED
    )
    assert (
        resolve_verification_status(support_count=1, contradict_count=1)
        == CLAIM_VERIFICATION_STATUS_MIXED
    )
    assert (
        resolve_verification_status(support_count=0, contradict_count=1)
        == CLAIM_VERIFICATION_STATUS_UNSUPPORTED
    )
    assert (
        build_verification_rationale(support_count=1, contradict_count=1)
        == "Found 1 support evidence and 1 contradict evidence."
    )
