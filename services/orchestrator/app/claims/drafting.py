from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

CLAIM_TYPE_FACT = "fact"
CLAIM_VERIFICATION_STATUS_DRAFT = "draft"
CLAIM_EVIDENCE_RELATION_SUPPORT = "support"

_SENTENCE_PATTERN = re.compile(r"[^\n.!?。！？]+(?:[.!?。！？]+)?", re.MULTILINE)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")


class CitationSpanValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SupportingSpan:
    start_offset: int
    end_offset: int
    excerpt: str


def draft_claim_statement(excerpt: str) -> str:
    normalized = _normalize_whitespace(excerpt)
    if not normalized:
        raise ValueError("claim statement must not be empty")
    return normalized


def select_supporting_span(text: str, query: str) -> SupportingSpan:
    spans = list(iter_supporting_spans(text))
    if not spans:
        raise CitationSpanValidationError("source chunk text does not contain a claimable span")

    query_tokens = tuple(_tokenize(query))
    best_span = max(
        spans,
        key=lambda span: (
            _query_overlap_score(span.excerpt, query_tokens),
            _informative_length_score(span.excerpt),
            -span.start_offset,
        ),
    )
    validate_citation_span(text, best_span.start_offset, best_span.end_offset, best_span.excerpt)
    return best_span


def validate_citation_span(
    source_text: str,
    start_offset: int,
    end_offset: int,
    excerpt: str,
) -> None:
    if start_offset < 0:
        raise CitationSpanValidationError("citation span start_offset must be non-negative")
    if end_offset <= start_offset:
        raise CitationSpanValidationError(
            "citation span end_offset must be greater than start_offset"
        )
    if end_offset > len(source_text):
        raise CitationSpanValidationError("citation span end_offset exceeds source chunk length")
    actual_excerpt = source_text[start_offset:end_offset]
    if excerpt != actual_excerpt:
        raise CitationSpanValidationError(
            "citation span excerpt does not match the source chunk text at the given offsets"
        )
    if not excerpt.strip():
        raise CitationSpanValidationError("citation span excerpt must not be blank")


def normalized_excerpt_hash(excerpt: str) -> str:
    normalized = _normalize_whitespace(excerpt).lower()
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def compute_claim_confidence(
    *,
    query: str,
    statement: str,
    retrieval_score: float | None,
) -> float:
    query_tokens = tuple(_tokenize(query))
    statement_tokens = tuple(_tokenize(statement))

    if not query_tokens:
        coverage = 0.0
    else:
        coverage = len({token for token in statement_tokens if token in query_tokens}) / len(
            set(query_tokens)
        )

    length_score = min(len(statement), 240) / 240
    retrieval_component = min(max(retrieval_score or 0.0, 0.0), 5.0) / 5.0
    confidence = 0.45 + (coverage * 0.3) + (length_score * 0.15) + (retrieval_component * 0.1)
    return round(min(0.95, max(0.35, confidence)), 2)


def iter_supporting_spans(text: str) -> Iterable[SupportingSpan]:
    seen_offsets: set[tuple[int, int]] = set()
    yielded_any = False
    for match in _SENTENCE_PATTERN.finditer(text):
        raw_excerpt = match.group(0)
        leading = len(raw_excerpt) - len(raw_excerpt.lstrip())
        trailing = len(raw_excerpt.rstrip())
        if trailing <= leading:
            continue
        start_offset = match.start() + leading
        end_offset = match.start() + trailing
        excerpt = text[start_offset:end_offset]
        if not excerpt.strip():
            continue
        seen_offsets.add((start_offset, end_offset))
        yielded_any = True
        yield SupportingSpan(
            start_offset=start_offset,
            end_offset=end_offset,
            excerpt=excerpt,
        )

    if not yielded_any and text.strip():
        stripped = text.strip()
        start_offset = text.index(stripped)
        end_offset = start_offset + len(stripped)
        if (start_offset, end_offset) not in seen_offsets:
            yield SupportingSpan(
                start_offset=start_offset,
                end_offset=end_offset,
                excerpt=text[start_offset:end_offset],
            )


def _normalize_whitespace(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip()


def _tokenize(value: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _TOKEN_PATTERN.findall(value))


def _query_overlap_score(excerpt: str, query_tokens: tuple[str, ...]) -> int:
    if not query_tokens:
        return 0
    excerpt_lower = excerpt.lower()
    return sum(1 for token in dict.fromkeys(query_tokens) if token in excerpt_lower)


def _informative_length_score(excerpt: str) -> int:
    return min(len(_normalize_whitespace(excerpt)), 240)
