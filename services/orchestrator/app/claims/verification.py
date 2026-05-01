from __future__ import annotations

import re
from dataclasses import dataclass

from services.orchestrator.app.claims.drafting import (
    CLAIM_EVIDENCE_RELATION_SUPPORT,
    CitationSpanValidationError,
    SupportingSpan,
    is_claimable_excerpt,
    iter_supporting_spans,
    validate_citation_span,
)

CLAIM_EVIDENCE_RELATION_CONTRADICT = "contradict"
CLAIM_EVIDENCE_RELATION_WEAK_SUPPORT = "weak_support"
CLAIM_VERIFICATION_STATUS_CONTRADICTED = "contradicted"
CLAIM_VERIFICATION_STATUS_SUPPORTED = "supported"
CLAIM_VERIFICATION_STATUS_MIXED = "mixed"
CLAIM_VERIFICATION_STATUS_UNSUPPORTED = "unsupported"
VERIFIER_METHOD_LEXICAL_HEURISTIC_V2 = "lexical_overlap_contradiction_scan_v2"

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_NUMBER_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)?%?\b")
_DATE_PATTERN = re.compile(r"\b(?:19|20)\d{2}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?\b")
_NEGATION_PATTERN = re.compile(
    r"\b(?:not|no|never|without|cannot|can't|did not|does not|doesn't|is not|isn't|"
    r"was not|wasn't|were not|weren't|failed to|false|incorrect)\b",
    re.IGNORECASE,
)
_CJK_NEGATION_PATTERN = re.compile(r"(不|未|没有|無|无|並非|并非)")
_GENERIC_TOKENS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "can",
    "does",
    "for",
    "from",
    "has",
    "have",
    "how",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "with",
    "work",
    "works",
}
_SCOPE_TERMS = {
    "all",
    "always",
    "any",
    "cannot",
    "can",
    "every",
    "may",
    "must",
    "never",
    "only",
    "requires",
    "should",
}
_DEFINITION_RELATION_PATTERN = re.compile(
    r"\b(?:is|are|means|refers to|defined as|consists of|uses|provides|supports)\b",
    re.IGNORECASE,
)
_CAUSAL_OR_MECHANISM_PATTERN = re.compile(
    r"\b(?:because|therefore|through|by|via|using|aggregat|route|send|return|index|query|"
    r"search|workflow|graph|node|edge|state|protocol|server|client)\b",
    re.IGNORECASE,
)
_COMPARISON_PATTERN = re.compile(
    r"\b(?:more|less|than|unlike|whereas|compared|advantage|limitation|however|but)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VerificationSpanMatch:
    relation_type: str
    relation_detail: str
    support_level: str | None
    start_offset: int
    end_offset: int
    excerpt: str
    score: float
    overlap_ratio: float
    meaningful_overlap_ratio: float
    verifier_method: str
    citation_precision: str
    citation_precision_reason: str
    reasons: tuple[str, ...]
    flags: dict[str, bool]

    def to_metadata(self) -> dict[str, object]:
        return {
            "relation_type": self.relation_type,
            "relation_detail": self.relation_detail,
            "support_level": self.support_level,
            "score": self.score,
            "overlap_ratio": self.overlap_ratio,
            "meaningful_overlap_ratio": self.meaningful_overlap_ratio,
            "verifier_method": self.verifier_method,
            "citation_precision": self.citation_precision,
            "citation_precision_reason": self.citation_precision_reason,
            "reasons": list(self.reasons),
            "flags": dict(self.flags),
            "excerpt": self.excerpt,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
        }


def select_verification_span(source_text: str, statement: str) -> VerificationSpanMatch | None:
    normalized_statement = _normalize_whitespace(statement)
    if not normalized_statement:
        raise CitationSpanValidationError("verification statement must not be blank")

    best_match: tuple[tuple[float, ...], VerificationSpanMatch] | None = None
    for span in _iter_verification_candidate_spans(source_text):
        if not is_claimable_excerpt(span.excerpt):
            continue
        classified = _classify_relation(statement=normalized_statement, excerpt=span.excerpt)
        if classified is None:
            continue

        (
            relation_type,
            relation_detail,
            support_level,
            evidence_score,
            overlap_ratio,
            meaningful_overlap_ratio,
            relation_rank,
            reasons,
            flags,
        ) = classified
        validate_citation_span(
            source_text,
            span.start_offset,
            span.end_offset,
            span.excerpt,
        )
        citation_precision, citation_precision_reason = _citation_precision(
            source_text,
            start_offset=span.start_offset,
            end_offset=span.end_offset,
            excerpt=span.excerpt,
        )
        candidate = VerificationSpanMatch(
            relation_type=relation_type,
            relation_detail=relation_detail,
            support_level=support_level,
            start_offset=span.start_offset,
            end_offset=span.end_offset,
            excerpt=span.excerpt,
            score=evidence_score,
            overlap_ratio=overlap_ratio,
            meaningful_overlap_ratio=meaningful_overlap_ratio,
            verifier_method=VERIFIER_METHOD_LEXICAL_HEURISTIC_V2,
            citation_precision=citation_precision,
            citation_precision_reason=citation_precision_reason,
            reasons=reasons,
            flags=flags,
        )
        specificity_score = _specificity_score(
            statement=normalized_statement,
            excerpt=span.excerpt,
        )
        precision_rank = _citation_precision_rank(citation_precision)
        candidate_key = (
            relation_rank,
            precision_rank,
            specificity_score,
            evidence_score,
            overlap_ratio,
            -min(len(_normalize_whitespace(span.excerpt)), 720) / 720,
            -span.start_offset,
        )
        if best_match is None or candidate_key > best_match[0]:
            best_match = (candidate_key, candidate)

    if best_match is None:
        return None
    return best_match[1]


def _iter_verification_candidate_spans(source_text: str) -> tuple[SupportingSpan, ...]:
    sentence_spans = tuple(iter_supporting_spans(source_text))
    if len(sentence_spans) <= 1:
        return sentence_spans

    candidates: list[SupportingSpan] = list(sentence_spans)
    seen_offsets = {(span.start_offset, span.end_offset) for span in sentence_spans}
    for left, right in zip(sentence_spans, sentence_spans[1:], strict=False):
        gap = source_text[left.end_offset : right.start_offset]
        if len(gap) > 120:
            continue
        start_offset = left.start_offset
        end_offset = right.end_offset
        excerpt = source_text[start_offset:end_offset]
        normalized_excerpt = _normalize_whitespace(excerpt)
        if len(normalized_excerpt) > 520:
            continue
        key = (start_offset, end_offset)
        if key in seen_offsets:
            continue
        candidates.append(
            SupportingSpan(
                start_offset=start_offset,
                end_offset=end_offset,
                excerpt=excerpt,
            )
        )
        seen_offsets.add(key)
    return tuple(candidates)


def resolve_verification_status(
    *,
    support_count: int,
    contradict_count: int,
    weak_support_count: int = 0,
) -> str:
    if support_count > 0 and contradict_count == 0:
        return CLAIM_VERIFICATION_STATUS_SUPPORTED
    if support_count > 0 and contradict_count > 0:
        return CLAIM_VERIFICATION_STATUS_MIXED
    if weak_support_count > 0 and contradict_count > 0:
        return CLAIM_VERIFICATION_STATUS_MIXED
    if contradict_count > 0:
        return CLAIM_VERIFICATION_STATUS_CONTRADICTED
    return CLAIM_VERIFICATION_STATUS_UNSUPPORTED


def build_verification_rationale(
    *,
    support_count: int,
    contradict_count: int,
    weak_support_count: int = 0,
) -> str:
    weak_suffix = f" Weak support evidence: {weak_support_count}." if weak_support_count > 0 else ""
    if support_count > 0 and contradict_count == 0 and weak_support_count == 0:
        return f"Found {support_count} support evidence and no contradict evidence."
    if support_count > 0 and contradict_count > 0 and weak_support_count == 0:
        return (
            f"Found {support_count} support evidence and "
            f"{contradict_count} contradict evidence."
        )
    if support_count > 0 and contradict_count == 0:
        return (
            f"Found {support_count} strong support evidence and no contradict "
            f"evidence.{weak_suffix}"
        )
    if support_count > 0 and contradict_count > 0:
        return (
            f"Found {support_count} strong support evidence and "
            f"{contradict_count} contradict evidence.{weak_suffix}"
        )
    if weak_support_count > 0 and contradict_count == 0:
        return (
            "Only weak lexical support evidence was found; no strong support or "
            "contradict evidence found."
        )
    if contradict_count > 0 and weak_support_count == 0:
        return f"No support evidence found; found {contradict_count} contradict evidence."
    if contradict_count > 0:
        return (
            f"No strong support evidence found; found {contradict_count} "
            f"contradict evidence.{weak_suffix}"
        )
    return "No support or contradict evidence found."


def _classify_relation(
    *,
    statement: str,
    excerpt: str,
) -> (
    tuple[
        str,
        str,
        str | None,
        float,
        float,
        float,
        float,
        tuple[str, ...],
        dict[str, bool],
    ]
    | None
):
    normalized_statement = _normalize_whitespace(statement).lower()
    normalized_excerpt = _normalize_whitespace(excerpt).lower()
    if not normalized_statement or not normalized_excerpt:
        return None

    statement_tokens = set(_tokenize(statement))
    excerpt_tokens = set(_tokenize(excerpt))
    overlap_ratio = _compute_overlap_ratio(statement_tokens, excerpt_tokens)
    meaningful_statement_tokens = _meaningful_tokens(statement_tokens)
    meaningful_excerpt_tokens = _meaningful_tokens(excerpt_tokens)
    meaningful_overlap_ratio = _compute_overlap_ratio(
        meaningful_statement_tokens,
        meaningful_excerpt_tokens,
    )
    negation_differs = _has_negation(statement) != _has_negation(excerpt)
    numeric_mismatch = _has_numeric_or_date_mismatch(statement, excerpt)
    scope_mismatch = _has_scope_mismatch(statement_tokens, excerpt_tokens)
    shallow_overlap = overlap_ratio >= 0.45 and meaningful_overlap_ratio < 0.35
    flags = {
        "negation_differs": negation_differs,
        "numeric_or_date_mismatch": numeric_mismatch,
        "scope_mismatch": scope_mismatch,
        "shallow_generic_overlap": shallow_overlap,
    }
    exact_support = (
        normalized_statement == normalized_excerpt
        or normalized_statement in normalized_excerpt
        or normalized_excerpt in normalized_statement
    )

    if exact_support and not negation_differs and not numeric_mismatch:
        return (
            CLAIM_EVIDENCE_RELATION_SUPPORT,
            "strong_support",
            "strong",
            0.95,
            max(overlap_ratio, 1.0 if statement_tokens else 0.0),
            max(meaningful_overlap_ratio, 1.0 if meaningful_statement_tokens else 0.0),
            3.0,
            ("exact_or_substring_match",),
            flags,
        )

    if overlap_ratio >= 0.55 and meaningful_overlap_ratio >= 0.45 and negation_differs:
        return (
            CLAIM_EVIDENCE_RELATION_CONTRADICT,
            "contradiction",
            None,
            round(min(0.95, 0.55 + (overlap_ratio * 0.35)), 2),
            overlap_ratio,
            meaningful_overlap_ratio,
            2.0,
            ("negation_differs_with_meaningful_overlap",),
            flags,
        )

    if numeric_mismatch or scope_mismatch or shallow_overlap:
        return None

    if overlap_ratio >= 0.72 and meaningful_overlap_ratio >= 0.6 and not negation_differs:
        return (
            CLAIM_EVIDENCE_RELATION_SUPPORT,
            "strong_support",
            "strong",
            round(min(0.9, 0.5 + (overlap_ratio * 0.3)), 2),
            overlap_ratio,
            meaningful_overlap_ratio,
            1.0,
            ("high_meaningful_lexical_overlap",),
            flags,
        )

    if overlap_ratio >= 0.5 and meaningful_overlap_ratio >= 0.4 and not negation_differs:
        return (
            CLAIM_EVIDENCE_RELATION_WEAK_SUPPORT,
            "weak_support",
            "weak",
            round(min(0.68, 0.35 + (meaningful_overlap_ratio * 0.35)), 2),
            overlap_ratio,
            meaningful_overlap_ratio,
            0.5,
            ("moderate_lexical_overlap_only", "not_full_entailment"),
            flags,
        )

    return None


def _citation_precision(
    source_text: str,
    *,
    start_offset: int,
    end_offset: int,
    excerpt: str,
) -> tuple[str, str]:
    stripped = source_text.strip()
    normalized_excerpt = _normalize_whitespace(excerpt)
    if stripped == excerpt.strip() and len(stripped) > 360:
        return "chunk_fallback", "no_sentence_boundary_detected"
    if len(normalized_excerpt) <= 520 and _contains_multiple_sentences(normalized_excerpt):
        return "short_span", "adjacent_sentences_needed_for_claim_support"
    if len(normalized_excerpt) <= 360 and _sentence_like(normalized_excerpt):
        return "sentence", "matched_sentence_or_short_span"
    if len(normalized_excerpt) <= 500:
        return "short_span", "matched_short_nonterminal_span"
    if start_offset == 0 and end_offset >= len(source_text.strip()):
        return "chunk_fallback", "span_covers_most_of_chunk"
    return "coarse_span", "span_longer_than_target_precision"


def _sentence_like(value: str) -> bool:
    return bool(value.endswith((".", "!", "?", "。", "！", "？"))) or len(value.split()) <= 32


def _contains_multiple_sentences(value: str) -> bool:
    terminal_count = sum(value.count(item) for item in (".", "!", "?", "。", "！", "？"))
    return terminal_count >= 2


def _citation_precision_rank(citation_precision: str) -> float:
    if citation_precision == "sentence":
        return 3.0
    if citation_precision == "short_span":
        return 2.0
    if citation_precision == "coarse_span":
        return 1.0
    return 0.0


def _specificity_score(*, statement: str, excerpt: str) -> float:
    statement_tokens = set(_tokenize(statement))
    excerpt_tokens = set(_tokenize(excerpt))
    meaningful_statement_tokens = _meaningful_tokens(statement_tokens)
    if not meaningful_statement_tokens:
        token_score = 0.0
    else:
        token_score = len(meaningful_statement_tokens & excerpt_tokens) / len(
            meaningful_statement_tokens
        )

    signal_score = 0.0
    if _NUMBER_PATTERN.search(statement) and _NUMBER_PATTERN.search(excerpt):
        signal_score += 0.18
    if _DATE_PATTERN.search(statement) and _DATE_PATTERN.search(excerpt):
        signal_score += 0.18
    if _DEFINITION_RELATION_PATTERN.search(excerpt):
        signal_score += 0.14
    if _CAUSAL_OR_MECHANISM_PATTERN.search(excerpt):
        signal_score += 0.14
    if _COMPARISON_PATTERN.search(excerpt):
        signal_score += 0.12

    return round(min(1.0, (token_score * 0.72) + signal_score), 4)


def _normalize_whitespace(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip()


def _tokenize(value: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _TOKEN_PATTERN.findall(value))


def _compute_overlap_ratio(statement_tokens: set[str], excerpt_tokens: set[str]) -> float:
    if not statement_tokens:
        return 0.0
    return len(statement_tokens & excerpt_tokens) / len(statement_tokens)


def _meaningful_tokens(tokens: set[str]) -> set[str]:
    return {token for token in tokens if token not in _GENERIC_TOKENS and len(token) > 2}


def _has_numeric_or_date_mismatch(statement: str, excerpt: str) -> bool:
    statement_values = set(_NUMBER_PATTERN.findall(statement)) | set(
        _DATE_PATTERN.findall(statement)
    )
    excerpt_values = set(_NUMBER_PATTERN.findall(excerpt)) | set(_DATE_PATTERN.findall(excerpt))
    return bool(statement_values and excerpt_values and statement_values != excerpt_values)


def _has_scope_mismatch(statement_tokens: set[str], excerpt_tokens: set[str]) -> bool:
    statement_scope = statement_tokens & _SCOPE_TERMS
    excerpt_scope = excerpt_tokens & _SCOPE_TERMS
    return bool(statement_scope and excerpt_scope and statement_scope != excerpt_scope)


def _has_negation(value: str) -> bool:
    return bool(_NEGATION_PATTERN.search(value) or _CJK_NEGATION_PATTERN.search(value))
