from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)
_STOPWORDS = {
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
_EXPLANATORY_TERMS = {
    "aggregates",
    "allows",
    "can",
    "combines",
    "forwards",
    "is",
    "lets",
    "provides",
    "returns",
    "sends",
    "supports",
    "uses",
    "works",
}
_NAVIGATION_PHRASES = (
    "jump to content",
    "main menu",
    "move to sidebar",
    "privacy policy",
    "about wikipedia",
    "edit links",
    "cookie policy",
    "terms of use",
    "navigation menu",
)
_REFERENCE_PHRASES = (
    "references",
    "bibliography",
    "external links",
    "retrieved from",
    "doi:",
    "(pdf)",
    "(bachelor thesis)",
    "bachelor thesis",
    "master thesis",
    "dissertation",
)
_STRONG_REFERENCE_PHRASES = (
    "(pdf)",
    "(bachelor thesis)",
    "bachelor thesis",
    "master thesis",
    "dissertation",
    "doi:",
    "retrieved from",
    "implementación de un prototipo",
)
_SOCIAL_VIDEO_FORUM_DOMAINS = (
    "reddit.com",
    "youtube.com",
    "youtu.be",
    "x.com",
    "twitter.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "news.ycombinator.com",
    "stackoverflow.com",
    "stackexchange.com",
    "quora.com",
)


@dataclass(frozen=True)
class SourceQuality:
    score: float
    reason: str


@dataclass(frozen=True)
class ChunkQuality:
    content_quality_score: float
    query_relevance_score: float
    boilerplate_score: float
    eligible_for_claims: bool
    is_navigation_noise: bool
    is_reference_section: bool
    content_quality: str
    reasons: list[str]


def assess_source_quality(
    *,
    canonical_url: str,
    domain: str,
    parsed_metadata: dict[str, object] | None = None,
) -> SourceQuality:
    metadata = parsed_metadata or {}
    if metadata.get("reason") == "redirect_stub":
        return SourceQuality(score=0.1, reason="redirect_stub")

    normalized_domain = domain.strip().lower().removeprefix("www.")
    path = urlsplit(canonical_url).path.strip().lower()
    if normalized_domain.startswith("docs.") or _is_docs_path(path):
        return SourceQuality(score=0.95, reason="official_docs")
    if normalized_domain.endswith("wikipedia.org"):
        return SourceQuality(score=0.78, reason="wikipedia_article")
    if _is_project_homepage(normalized_domain, path):
        return SourceQuality(score=0.72, reason="project_homepage")
    if normalized_domain == "github.com":
        return SourceQuality(score=0.45, reason="github_repository_landing_page")
    if _is_social_video_or_forum_domain(normalized_domain):
        return SourceQuality(score=0.2, reason="social_video_or_forum")
    return SourceQuality(score=0.55, reason="generic_web_page")


def assess_chunk_quality(
    *,
    text: str,
    query: str,
    source_quality_score: float,
    parsed_metadata: dict[str, object] | None = None,
) -> ChunkQuality:
    metadata = parsed_metadata or {}
    normalized = " ".join(text.split())
    lower = normalized.lower()
    reasons: list[str] = []

    redirect_stub = metadata.get("reason") == "redirect_stub" or _looks_like_redirect_stub(lower)
    is_reference_section = _looks_like_reference_section(lower)
    is_navigation_noise = _looks_like_navigation_noise(lower)
    boilerplate_score = _boilerplate_score(lower)
    query_relevance_score = _query_relevance_score(normalized, query)
    explanatory_score = 0.16 if _contains_explanatory_terms(lower) else 0.0
    paragraph_score = 0.08 if _looks_like_prose(normalized) else 0.0
    length_penalty = 0.22 if len(normalized) < 48 else 0.0
    repetition_penalty = 0.12 if _has_high_repetition(normalized) else 0.0

    if redirect_stub:
        reasons.append("redirect_stub")
    if is_reference_section:
        reasons.append("reference_section")
    if is_navigation_noise:
        reasons.append("navigation_noise")
    if len(normalized) < 48:
        reasons.append("very_short")
    if repetition_penalty:
        reasons.append("high_repetition")

    score = (
        0.22
        + (source_quality_score * 0.32)
        + (query_relevance_score * 0.22)
        + explanatory_score
        + paragraph_score
        - (boilerplate_score * 0.46)
        - length_penalty
        - repetition_penalty
    )
    if redirect_stub:
        score = min(score, 0.12)
    if is_reference_section or is_navigation_noise:
        score = min(score, 0.24)
    score = round(min(1.0, max(0.0, score)), 2)

    eligible = (
        score >= 0.35
        and not redirect_stub
        and not is_reference_section
        and not is_navigation_noise
        and len(normalized) >= 48
    )
    if eligible:
        quality = "high" if score >= 0.72 else "medium"
    else:
        quality = "low"
    if not reasons and quality != "low":
        reasons.append("informative_prose")

    return ChunkQuality(
        content_quality_score=score,
        query_relevance_score=round(query_relevance_score, 2),
        boilerplate_score=round(boilerplate_score, 2),
        eligible_for_claims=eligible,
        is_navigation_noise=is_navigation_noise,
        is_reference_section=is_reference_section,
        content_quality=quality,
        reasons=reasons,
    )


def _tokenize(value: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _TOKEN_PATTERN.findall(value))


def _query_relevance_score(text: str, query: str) -> float:
    query_tokens = [token for token in dict.fromkeys(_tokenize(query)) if token not in _STOPWORDS]
    if not query_tokens:
        return 0.0
    text_tokens = set(_tokenize(text))
    return len([token for token in query_tokens if token in text_tokens]) / len(query_tokens)


def _boilerplate_score(lower_text: str) -> float:
    if not lower_text:
        return 1.0
    score = 0.0
    score += 0.18 * sum(1 for phrase in _NAVIGATION_PHRASES if phrase in lower_text)
    score += 0.16 * sum(1 for phrase in _REFERENCE_PHRASES if phrase in lower_text)
    token_count = max(1, len(_tokenize(lower_text)))
    linkish_terms = sum(
        lower_text.count(term)
        for term in ("menu", "sidebar", "navigation", "footer", "privacy", "cookie", "edit")
    )
    score += min(0.45, linkish_terms / token_count)
    return min(1.0, score)


def _looks_like_redirect_stub(lower_text: str) -> bool:
    return len(lower_text) <= 500 and any(
        phrase in lower_text
        for phrase in (
            "redirecting to ",
            "you are being redirected",
            "moved permanently",
            "click here if you are not redirected",
        )
    )


def _looks_like_reference_section(lower_text: str) -> bool:
    stripped = lower_text.strip()
    if stripped in {"references", "bibliography", "external links"}:
        return True
    if stripped.startswith(("references ", "bibliography ", "external links ")):
        return True
    strong_reference_hits = sum(1 for phrase in _STRONG_REFERENCE_PHRASES if phrase in lower_text)
    if strong_reference_hits >= 1 and not _contains_explanatory_terms(lower_text):
        return True

    reference_hits = sum(1 for phrase in _REFERENCE_PHRASES if phrase in lower_text)
    token_count = max(1, len(_tokenize(lower_text)))
    return reference_hits >= 2 and reference_hits / token_count >= 0.08


def _looks_like_navigation_noise(lower_text: str) -> bool:
    phrase_hits = sum(1 for phrase in _NAVIGATION_PHRASES if phrase in lower_text)
    if phrase_hits >= 2:
        return True
    token_count = max(1, len(_tokenize(lower_text)))
    nav_token_hits = sum(
        lower_text.count(term)
        for term in ("menu", "sidebar", "navigation", "footer", "privacy", "cookie", "edit")
    )
    return token_count <= 80 and nav_token_hits / token_count >= 0.12


def _contains_explanatory_terms(lower_text: str) -> bool:
    tokens = set(_tokenize(lower_text))
    return any(term in tokens for term in _EXPLANATORY_TERMS)


def _looks_like_prose(text: str) -> bool:
    return len(text) >= 80 and any(mark in text for mark in (".", "。", "!", "！"))


def _has_high_repetition(text: str) -> bool:
    tokens = _tokenize(text)
    if len(tokens) < 24:
        return False
    unique_ratio = len(set(tokens)) / len(tokens)
    return unique_ratio < 0.32


def _is_docs_path(path: str) -> bool:
    return any(
        marker in path
        for marker in (
            "/docs",
            "/doc/",
            "/documentation",
            "/guide",
            "/guides",
            "/manual",
            "/reference",
        )
    )


def _is_project_homepage(domain: str, path: str) -> bool:
    if not domain or domain.endswith("wikipedia.org"):
        return False
    return path.rstrip("/") in {"", "/"}


def _is_social_video_or_forum_domain(domain: str) -> bool:
    return any(
        domain == item or domain.endswith(f".{item}") for item in _SOCIAL_VIDEO_FORUM_DOMAINS
    )
