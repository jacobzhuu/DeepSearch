from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

CLAIM_TYPE_FACT = "fact"
CLAIM_VERIFICATION_STATUS_DRAFT = "draft"
CLAIM_EVIDENCE_RELATION_SUPPORT = "support"

_SENTENCE_PATTERN = re.compile(r"[^\n.!?。！？]+(?:[.!?。！？]+)?", re.MULTILINE)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_CLAIM_IDENTITY_PUNCTUATION_PATTERN = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")
_CJK_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_TERMINAL_SENTENCE_PATTERN = re.compile(r"[.!。！]$")
_REFERENCE_MARKER_PATTERN = re.compile(r"^(?:\[\d+\]|\d+\.|\([a-z]\))\s+", re.IGNORECASE)
_AUTHOR_REFERENCE_PATTERN = re.compile(r"^[A-Z][A-Za-z' -]+,\s+[A-Z](?:\.|\w+)")
_MEANINGLESS_CLAIMS = frozenset({"c", "data", "none", "null", "undefined"})
_LOW_VALUE_QUERY_TOKENS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "does",
    "for",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "with",
}
_GENERIC_QUERY_TOKENS = {
    "about",
    "current",
    "currently",
    "explain",
    "known",
    "overview",
    "position",
    "research",
    "tell",
    "work",
    "works",
    "working",
}
_REFERENCE_PHRASES = (
    "(pdf)",
    "(bachelor thesis)",
    "bachelor thesis",
    "master thesis",
    "dissertation",
    "doi:",
    "retrieved from",
    "implementación de un prototipo",
)
_DEFINITION_PATTERNS = (
    " is a ",
    " is an ",
    " are a ",
    " are an ",
    " is the ",
    " are the ",
)
_MECHANISM_TERMS = (
    "aggregat",
    "mixes your quer",
    "mixing your quer",
    "results",
    "search engines",
    "search services",
    "sends queries",
    "send queries",
    "upstream engines",
    "other platforms",
)
_PRIVACY_TERMS = (
    "identify users",
    "little to no information",
    "private data",
    "privacy",
    "not storing",
    "without storing",
    "stores no",
    "stores little to no",
    "doesn't generate a profile",
    "does not generate a profile",
    "profile about you",
    "tracking",
    "third-party",
    "user data",
    "search data",
)
_FEATURE_TERMS = (
    "opensearch",
    "over 70 different search engines",
    "supports",
    "self-hosted",
    "self hosted",
    "default search engine",
    "browser's search bar",
    "browser search bar",
    "categories",
    "engines",
)
_SETUP_TERMS = (
    "add your instance",
    "add searxng",
    "browser setup",
    "configure",
    "configuration",
    "get started",
    "how do i set it as",
    "install",
    "listed at",
    "run it yourself",
    "set as default",
    "set up your own",
    "using one of the instances",
)
_COMMUNITY_TERMS = (
    "come join",
    "contribution",
    "contributions",
    "development",
    "matrix",
    "report issues",
    "send contributions",
    "source code",
    "sources and run",
    "translations",
    "weblate",
)
_SLOGAN_TERMS = (
    "make the internet freer",
    "reclaim their privacy",
    "reclaim your privacy",
)
_IMPERATIVE_PREFIX_PATTERN = re.compile(
    r"^(?:"
    r"add(?:\s+your)?\s+instance|"
    r"come\s+join|"
    r"get\s+started|"
    r"make\s+searxng\s+better|"
    r"report\s+issues|"
    r"send\s+contributions|"
    r"take\s+the\s+code|"
    r"track\s+development|"
    r"run\s+it\s+yourself"
    r")\b",
    re.IGNORECASE,
)
_BROKEN_LINK_RESIDUE_PATTERN = re.compile(
    r"(?:\blisted\s+at\s+\.|\bsee\s+\.|\bat\s+\.|\bfrom\s+up\s+to\s+\d+\s+\.)",
    re.IGNORECASE,
)
_SETUP_ALLOWED_QUERY_TERMS = {
    "add",
    "browser",
    "configure",
    "default",
    "install",
    "opensearch",
    "setup",
}
_CONTRIBUTION_ALLOWED_QUERY_TERMS = {
    "community",
    "contribute",
    "contribution",
    "development",
    "matrix",
    "translate",
    "translation",
    "weblate",
}
_CATEGORY_PRIORITY = {
    "definition": 0,
    "mechanism": 1,
    "privacy": 2,
    "feature": 3,
    "other": 4,
    "setup": 5,
    "community": 6,
    "slogan": 7,
    "reference": 8,
}
MIN_CLAIM_STATEMENT_CHARS = 32
MIN_CLAIM_STATEMENT_TOKENS = 5
MIN_DRAFT_CLAIM_QUALITY_SCORE = 0.45
MIN_DRAFT_QUERY_ANSWER_SCORE = 0.35
REPORT_CLAIM_QUALITY_THRESHOLD = 0.45
REPORT_QUERY_ANSWER_THRESHOLD = 0.45


class CitationSpanValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SupportingSpan:
    start_offset: int
    end_offset: int
    excerpt: str


@dataclass(frozen=True)
class QueryIntent:
    intent_name: str
    expected_claim_types: tuple[str, ...]
    avoid_claim_types: tuple[str, ...]
    subject_terms: tuple[str, ...]
    setup_allowed: bool
    contribution_allowed: bool


@dataclass(frozen=True)
class ClaimCandidateScore:
    claim_category: str
    content_quality_score: float
    query_relevance_score: float
    claim_quality_score: float
    query_answer_score: float
    source_quality_score: float
    final_score: float
    rejected_reason: str | None

    def as_notes(self) -> dict[str, Any]:
        return {
            "claim_category": self.claim_category,
            "content_quality_score": self.content_quality_score,
            "query_relevance_score": self.query_relevance_score,
            "claim_quality_score": self.claim_quality_score,
            "query_answer_score": self.query_answer_score,
            "source_quality_score": self.source_quality_score,
            "claim_selection_score": self.final_score,
            "rejected_reason": self.rejected_reason,
        }


def draft_claim_statement(excerpt: str) -> str:
    normalized = _normalize_quotes(_normalize_whitespace(excerpt))
    if not normalized:
        raise ValueError("claim statement must not be empty")
    return normalized


def is_claimable_statement(statement: str, query: str | None = None) -> bool:
    normalized = _normalize_quotes(_normalize_whitespace(statement))
    if _claim_rejection_reason(normalized, query=query) is not None:
        return False
    return True


def classify_query_intent(query: str | None) -> QueryIntent:
    if query is None or not query.strip():
        return QueryIntent(
            intent_name="generic",
            expected_claim_types=("other", "definition", "mechanism", "privacy", "feature"),
            avoid_claim_types=("community", "slogan", "reference", "navigation"),
            subject_terms=(),
            setup_allowed=False,
            contribution_allowed=False,
        )

    normalized = _normalize_whitespace(query)
    lower = normalized.lower()
    query_tokens = set(_tokenize(normalized))
    subject_terms = _extract_subject_terms(normalized)
    setup_allowed = bool(query_tokens & _SETUP_ALLOWED_QUERY_TERMS)
    contribution_allowed = bool(query_tokens & _CONTRIBUTION_ALLOWED_QUERY_TERMS)

    if (
        subject_terms
        and "what is" in lower
        and "how" in query_tokens
        and ("work" in query_tokens or "works" in query_tokens)
    ):
        return QueryIntent(
            intent_name="definition_mechanism",
            expected_claim_types=("definition", "mechanism", "privacy", "feature"),
            avoid_claim_types=("setup", "community", "slogan", "reference", "navigation"),
            subject_terms=subject_terms,
            setup_allowed=setup_allowed,
            contribution_allowed=contribution_allowed,
        )

    if subject_terms and ("what is" in lower or "what are" in lower):
        return QueryIntent(
            intent_name="definition",
            expected_claim_types=("definition", "privacy", "feature", "mechanism"),
            avoid_claim_types=("setup", "community", "slogan", "reference", "navigation"),
            subject_terms=subject_terms,
            setup_allowed=setup_allowed,
            contribution_allowed=contribution_allowed,
        )

    return QueryIntent(
        intent_name="generic",
        expected_claim_types=("other", "definition", "mechanism", "privacy", "feature"),
        avoid_claim_types=("community", "slogan", "reference", "navigation"),
        subject_terms=subject_terms,
        setup_allowed=setup_allowed,
        contribution_allowed=contribution_allowed,
    )


def score_claim_statement(
    *,
    statement: str,
    query: str | None,
    content_quality_score: float | None = None,
    source_quality_score: float | None = None,
) -> ClaimCandidateScore:
    normalized = _normalize_quotes(_normalize_whitespace(statement))
    intent = classify_query_intent(query)
    category = classify_claim_category(normalized, intent=intent)
    rejected_reason = _claim_rejection_reason(
        normalized, query=query, intent=intent, category=category
    )
    content_score = _clamp_score(
        content_quality_score if content_quality_score is not None else 0.6
    )
    source_score = _clamp_score(source_quality_score if source_quality_score is not None else 0.5)
    query_relevance = _compute_query_relevance_score(normalized, query=query, category=category)
    claim_quality = _compute_claim_quality_score(normalized, category=category)
    query_answer = _compute_query_answer_score(
        category=category, query_relevance=query_relevance, intent=intent
    )

    if rejected_reason is not None:
        claim_quality = min(claim_quality, 0.2)
        query_answer = min(query_answer, 0.2)

    final_score = round(
        (content_score * 0.18)
        + (query_relevance * 0.20)
        + (claim_quality * 0.24)
        + (query_answer * 0.28)
        + (source_score * 0.10),
        4,
    )
    return ClaimCandidateScore(
        claim_category=category,
        content_quality_score=round(content_score, 4),
        query_relevance_score=round(query_relevance, 4),
        claim_quality_score=round(claim_quality, 4),
        query_answer_score=round(query_answer, 4),
        source_quality_score=round(source_score, 4),
        final_score=final_score,
        rejected_reason=rejected_reason,
    )


def classify_claim_category(statement: str, *, intent: QueryIntent | None = None) -> str:
    normalized = _normalize_quotes(_normalize_whitespace(statement))
    lower = normalized.lower()
    padded = f" {lower} "
    intent = intent or classify_query_intent(None)

    if _looks_like_reference_statement(normalized, query=None):
        return "reference"
    if _contains_any(lower, _SLOGAN_TERMS):
        return "slogan"
    if _contains_any(lower, _COMMUNITY_TERMS):
        return "community"
    if _IMPERATIVE_PREFIX_PATTERN.search(normalized) or _contains_any(lower, _SETUP_TERMS):
        return "setup"
    if any(pattern in padded for pattern in _DEFINITION_PATTERNS):
        return "definition"
    if any(term in lower for term in _PRIVACY_TERMS):
        return "privacy"
    if "supports" in lower and any(term in lower for term in _FEATURE_TERMS):
        return "feature"
    if any(term in lower for term in _MECHANISM_TERMS):
        return "mechanism"
    if any(term in lower for term in _FEATURE_TERMS):
        return "feature"
    if any(subject in _tokenize(normalized) for subject in intent.subject_terms):
        return "other"
    return "other"


def candidate_category_sort_key(category: str) -> int:
    return _CATEGORY_PRIORITY.get(category, 99)


def _claim_rejection_reason(
    statement: str,
    *,
    query: str | None,
    intent: QueryIntent | None = None,
    category: str | None = None,
) -> str | None:
    normalized = _normalize_quotes(_normalize_whitespace(statement))
    intent = intent or classify_query_intent(query)
    category = category or classify_claim_category(normalized, intent=intent)
    if len(normalized) < MIN_CLAIM_STATEMENT_CHARS and not (
        category == "feature" and len(normalized) >= 24
    ):
        return "too_short"
    if normalized.lower() in _MEANINGLESS_CLAIMS:
        return "meaningless_fragment"
    if normalized.endswith(("?", "？")):
        return "question_like"
    if _has_unbalanced_quotes(normalized):
        return "unbalanced_quotes"
    if _has_broken_link_residue(normalized):
        return "broken_link_residue"
    if _looks_like_reference_statement(normalized, query=query):
        return "reference_or_citation"
    if _TERMINAL_SENTENCE_PATTERN.search(normalized) is None:
        return "incomplete_sentence"
    if _starts_with_lowercase_fragment(normalized, intent=intent):
        return "lowercase_fragment"
    if _IMPERATIVE_PREFIX_PATTERN.search(normalized):
        return "imperative_or_call_to_action"
    lower = normalized.lower()
    if normalized.endswith("!") and (
        category in {"setup", "community", "slogan"} or "run it yourself" in lower
    ):
        return "promotional_or_imperative_exclamation"
    if category == "slogan":
        return "slogan_fragment"
    if category == "community" and not intent.contribution_allowed:
        return "community_or_contribution"
    if category == "setup" and not intent.setup_allowed and _is_setup_instruction(lower):
        return "setup_instruction"
    tokens = _tokenize(normalized)
    semantic_units = len(tokens) + (len(_CJK_CHAR_PATTERN.findall(normalized)) // 2)
    if semantic_units < MIN_CLAIM_STATEMENT_TOKENS and not (
        category == "feature" and semantic_units >= 3
    ):
        return "too_few_informative_terms"
    if query is not None and normalize_claim_identity(normalized) == normalize_claim_identity(
        query
    ):
        return "duplicates_query"
    return None


def is_claimable_excerpt(excerpt: str, query: str | None = None) -> bool:
    return is_claimable_statement(draft_claim_statement(excerpt), query=query)


def normalize_claim_identity(statement: str) -> str:
    normalized = _normalize_whitespace(statement).lower()
    return _CLAIM_IDENTITY_PUNCTUATION_PATTERN.sub("", normalized)


def select_supporting_span(text: str, query: str) -> SupportingSpan:
    spans = [
        span
        for span in iter_supporting_spans(text)
        if is_claimable_excerpt(span.excerpt, query=query)
    ]
    if not spans:
        raise CitationSpanValidationError("source chunk text does not contain a claimable span")

    query_tokens = tuple(_tokenize(query))
    best_span = max(
        spans,
        key=lambda span: (
            score_claim_statement(statement=span.excerpt, query=query).final_score,
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


def _normalize_quotes(value: str) -> str:
    normalized = value.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    previous = None
    while previous != normalized:
        previous = normalized
        normalized = re.sub(r'"([^",]{1,80}),\s+"', r'"\1", "', normalized)
        normalized = re.sub(r'"([^"]{1,80}),"', r'"\1",', normalized)
    return normalized


def _has_unbalanced_quotes(value: str) -> bool:
    return value.count('"') % 2 == 1


def _tokenize(value: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _TOKEN_PATTERN.findall(value))


def _looks_like_reference_statement(statement: str, query: str | None) -> bool:
    lower = statement.lower()
    if _REFERENCE_MARKER_PATTERN.search(statement):
        return True
    if _AUTHOR_REFERENCE_PATTERN.search(statement) and not _has_explanatory_claim_verb(lower):
        return True
    if any(phrase in lower for phrase in _REFERENCE_PHRASES):
        return True
    if (
        _looks_like_title_case_citation(statement)
        and _query_overlap_score(
            statement,
            _meaningful_query_tokens(query),
        )
        == 0
    ):
        return True
    return False


def _looks_like_title_case_citation(statement: str) -> bool:
    tokens = [token for token in re.findall(r"[^\W\d_]+", statement, flags=re.UNICODE)]
    if len(tokens) < 5:
        return False
    uppercase_initial = sum(1 for token in tokens if token[:1].isupper())
    lower = statement.lower()
    has_explanatory_verb = _has_explanatory_claim_verb(lower)
    return uppercase_initial / len(tokens) >= 0.65 and not has_explanatory_verb


def _has_explanatory_claim_verb(lower_statement: str) -> bool:
    return any(
        f" {verb} " in f" {lower_statement} "
        for verb in (
            "aggregates",
            "are",
            "functions",
            "is",
            "removes",
            "returns",
            "sends",
            "stores",
            "supports",
            "uses",
            "works",
        )
    )


def _meaningful_query_tokens(query: str | None) -> tuple[str, ...]:
    if query is None:
        return ()
    return tuple(
        token
        for token in _tokenize(query)
        if token not in _LOW_VALUE_QUERY_TOKENS and token not in _GENERIC_QUERY_TOKENS
    )


def _query_overlap_score(excerpt: str, query_tokens: tuple[str, ...]) -> int:
    if not query_tokens:
        return 0
    excerpt_lower = excerpt.lower()
    return sum(1 for token in dict.fromkeys(query_tokens) if token in excerpt_lower)


def _informative_length_score(excerpt: str) -> int:
    return min(len(_normalize_whitespace(excerpt)), 240)


def _extract_subject_terms(query: str) -> tuple[str, ...]:
    words = re.findall(r"[A-Za-z0-9_.-]+", query)
    if not words:
        return ()

    lowered = [word.lower() for word in words]
    for index, token in enumerate(lowered[:-1]):
        if token == "what" and lowered[index + 1] in {"is", "are"}:
            for candidate in words[index + 2 : index + 6]:
                candidate_lower = candidate.lower()
                if candidate_lower in _LOW_VALUE_QUERY_TOKENS:
                    continue
                if candidate_lower in _GENERIC_QUERY_TOKENS:
                    continue
                if candidate[:1].isupper() or any(char.isdigit() for char in candidate):
                    return (candidate_lower,)
                break

    proper_terms = [
        word.lower()
        for word in words
        if word[:1].isupper()
        and word.lower() not in _LOW_VALUE_QUERY_TOKENS
        and word.lower() not in _GENERIC_QUERY_TOKENS
    ]
    return tuple(dict.fromkeys(proper_terms[:2]))


def _compute_query_relevance_score(
    statement: str,
    *,
    query: str | None,
    category: str,
) -> float:
    query_tokens = set(_meaningful_query_tokens(query))
    statement_tokens = set(_tokenize(statement))
    literal_score = 0.0
    if query_tokens:
        literal_score = len(query_tokens & statement_tokens) / len(query_tokens)

    category_floor = {
        "definition": 0.9,
        "mechanism": 0.85,
        "privacy": 0.75,
        "feature": 0.7,
        "other": 0.0,
        "setup": 0.2,
        "community": 0.1,
        "slogan": 0.1,
        "reference": 0.0,
    }.get(category, 0.0)
    if classify_query_intent(query).intent_name == "generic":
        category_floor = min(category_floor, 0.55)
    return _clamp_score(max(literal_score, category_floor))


def _compute_claim_quality_score(statement: str, *, category: str) -> float:
    normalized = _normalize_whitespace(statement)
    lower = normalized.lower()
    score = 0.55
    if _TERMINAL_SENTENCE_PATTERN.search(normalized):
        score += 0.15
    if _starts_with_explanatory_subject(normalized):
        score += 0.1
    if 60 <= len(normalized) <= 260:
        score += 0.1
    if any(verb in f" {lower} " for verb in (" is ", " are ", " provides ", " supports ")):
        score += 0.08
    if category in {"definition", "mechanism", "privacy", "feature"}:
        score += 0.08
    if normalized.endswith("!"):
        score -= 0.2
    if category in {"community", "slogan", "reference"}:
        score -= 0.35
    if category == "setup":
        score -= 0.2
    return _clamp_score(score)


def _compute_query_answer_score(
    *,
    category: str,
    query_relevance: float,
    intent: QueryIntent,
) -> float:
    if intent.intent_name == "generic":
        if category in intent.avoid_claim_types:
            return 0.15
        return _clamp_score(max(query_relevance, 0.45))

    expected_scores = {
        "definition": 1.0,
        "mechanism": 0.95,
        "privacy": 0.85,
        "feature": 0.75,
    }
    if category in intent.expected_claim_types:
        return expected_scores.get(category, max(query_relevance, 0.6))
    if category in intent.avoid_claim_types:
        return 0.1
    return _clamp_score(query_relevance * 0.55)


def _starts_with_lowercase_fragment(statement: str, *, intent: QueryIntent) -> bool:
    stripped = statement.lstrip()
    first_alpha = re.search(r"[A-Za-z]", stripped)
    if first_alpha is None:
        return False
    first_char = first_alpha.group(0)
    if not first_char.islower():
        return False
    lower = stripped.lower()
    if lower.startswith(("it ", "its ", "the ", "this ")):
        return False
    return not any(lower.startswith(f"{subject} ") for subject in intent.subject_terms)


def _starts_with_explanatory_subject(statement: str) -> bool:
    stripped = statement.lstrip()
    return bool(
        stripped.startswith(("It ", "Its ", "The ", "This "))
        or re.match(r"^[A-Z][A-Za-z0-9_.-]+(?:\s+[A-Z][A-Za-z0-9_.-]+){0,3}\s+", stripped)
    )


def _is_setup_instruction(lower_statement: str) -> bool:
    if _contains_any(
        lower_statement,
        (
            "add your instance",
            "get started",
            "how do i set it as",
            "listed at",
            "run it yourself",
            "set up your own",
            "using one of the instances",
        ),
    ):
        return True
    return bool(
        lower_statement.startswith(("add ", "click ", "configure ", "install ", "set "))
        or "follow these" in lower_statement
    )


def _has_broken_link_residue(statement: str) -> bool:
    return _BROKEN_LINK_RESIDUE_PATTERN.search(statement) is not None


def _contains_any(value: str, terms: Iterable[str]) -> bool:
    return any(term in value for term in terms)


def _clamp_score(value: float) -> float:
    return min(1.0, max(0.0, float(value)))
