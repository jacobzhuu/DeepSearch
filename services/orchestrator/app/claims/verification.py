from __future__ import annotations

import re
from dataclasses import dataclass

from services.orchestrator.app.claims.drafting import (
    CLAIM_EVIDENCE_RELATION_SUPPORT,
    CitationSpanValidationError,
    iter_supporting_spans,
    validate_citation_span,
)

CLAIM_EVIDENCE_RELATION_CONTRADICT = "contradict"
CLAIM_VERIFICATION_STATUS_SUPPORTED = "supported"
CLAIM_VERIFICATION_STATUS_MIXED = "mixed"
CLAIM_VERIFICATION_STATUS_UNSUPPORTED = "unsupported"

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_NEGATION_PATTERN = re.compile(
    r"\b(?:not|no|never|without|cannot|can't|did not|does not|doesn't|is not|isn't|"
    r"was not|wasn't|were not|weren't|failed to|false|incorrect)\b",
    re.IGNORECASE,
)
_CJK_NEGATION_PATTERN = re.compile(r"(不|未|没有|無|无|並非|并非)")


@dataclass(frozen=True)
class VerificationSpanMatch:
    relation_type: str
    start_offset: int
    end_offset: int
    excerpt: str
    score: float
    overlap_ratio: float


def select_verification_span(source_text: str, statement: str) -> VerificationSpanMatch | None:
    normalized_statement = _normalize_whitespace(statement)
    if not normalized_statement:
        raise CitationSpanValidationError("verification statement must not be blank")

    best_match: tuple[tuple[float, float, float, int], VerificationSpanMatch] | None = None
    for span in iter_supporting_spans(source_text):
        classified = _classify_relation(statement=normalized_statement, excerpt=span.excerpt)
        if classified is None:
            continue

        relation_type, evidence_score, overlap_ratio, relation_rank = classified
        validate_citation_span(
            source_text,
            span.start_offset,
            span.end_offset,
            span.excerpt,
        )
        candidate = VerificationSpanMatch(
            relation_type=relation_type,
            start_offset=span.start_offset,
            end_offset=span.end_offset,
            excerpt=span.excerpt,
            score=evidence_score,
            overlap_ratio=overlap_ratio,
        )
        candidate_key = (
            relation_rank,
            overlap_ratio,
            min(len(_normalize_whitespace(span.excerpt)), 240) / 240,
            -span.start_offset,
        )
        if best_match is None or candidate_key > best_match[0]:
            best_match = (candidate_key, candidate)

    if best_match is None:
        return None
    return best_match[1]


def resolve_verification_status(*, support_count: int, contradict_count: int) -> str:
    if support_count > 0 and contradict_count == 0:
        return CLAIM_VERIFICATION_STATUS_SUPPORTED
    if support_count > 0 and contradict_count > 0:
        return CLAIM_VERIFICATION_STATUS_MIXED
    return CLAIM_VERIFICATION_STATUS_UNSUPPORTED


def build_verification_rationale(*, support_count: int, contradict_count: int) -> str:
    if support_count > 0 and contradict_count == 0:
        return f"Found {support_count} support evidence and no contradict evidence."
    if support_count > 0 and contradict_count > 0:
        return (
            f"Found {support_count} support evidence and "
            f"{contradict_count} contradict evidence."
        )
    if contradict_count > 0:
        return f"No support evidence found; found {contradict_count} contradict evidence."
    return "No support or contradict evidence found."


def _classify_relation(
    *,
    statement: str,
    excerpt: str,
) -> tuple[str, float, float, float] | None:
    normalized_statement = _normalize_whitespace(statement).lower()
    normalized_excerpt = _normalize_whitespace(excerpt).lower()
    if not normalized_statement or not normalized_excerpt:
        return None

    statement_tokens = set(_tokenize(statement))
    excerpt_tokens = set(_tokenize(excerpt))
    overlap_ratio = _compute_overlap_ratio(statement_tokens, excerpt_tokens)
    negation_differs = _has_negation(statement) != _has_negation(excerpt)
    exact_support = (
        normalized_statement == normalized_excerpt
        or normalized_statement in normalized_excerpt
        or normalized_excerpt in normalized_statement
    )

    if exact_support and not negation_differs:
        return (
            CLAIM_EVIDENCE_RELATION_SUPPORT,
            0.95,
            max(overlap_ratio, 1.0 if statement_tokens else 0.0),
            3.0,
        )

    if overlap_ratio >= 0.45 and negation_differs:
        return (
            CLAIM_EVIDENCE_RELATION_CONTRADICT,
            round(min(0.95, 0.55 + (overlap_ratio * 0.35)), 2),
            overlap_ratio,
            2.0,
        )

    if overlap_ratio >= 0.6 and not negation_differs:
        return (
            CLAIM_EVIDENCE_RELATION_SUPPORT,
            round(min(0.9, 0.5 + (overlap_ratio * 0.3)), 2),
            overlap_ratio,
            1.0,
        )

    return None


def _normalize_whitespace(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip()


def _tokenize(value: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _TOKEN_PATTERN.findall(value))


def _compute_overlap_ratio(statement_tokens: set[str], excerpt_tokens: set[str]) -> float:
    if not statement_tokens:
        return 0.0
    return len(statement_tokens & excerpt_tokens) / len(statement_tokens)


def _has_negation(value: str) -> bool:
    return bool(_NEGATION_PATTERN.search(value) or _CJK_NEGATION_PATTERN.search(value))
