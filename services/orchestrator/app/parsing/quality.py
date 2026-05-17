from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)
_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
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
_LEADING_UI_BOILERPLATE_PHRASES = (
    "add a comment",
    "comment",
    "comments",
    "copy link",
    "email address",
    "email sent",
    "leave a comment",
    "mail sent",
    "print",
    "recipient email",
    "required fields",
    "share",
    "share this article",
    "sign in",
    "sign up",
    "skip to content",
    "subscribe",
    "your email",
    "your name",
    "分享",
    "分享此文章",
    "评论",
    "跳至内容",
    "收件人的邮箱地址",
    "您的名字",
    "你的名字",
    "邮件已发送",
    "电子邮件地址",
)
_POINTER_PROJECT_META_PHRASES = (
    "developer documentation",
    "for more information",
    "join matrix",
    "make it better",
    "make searxng better",
    "open community",
    "read the documentation",
    "report issues",
    "see installation",
    "send contributions",
    "track development",
    "visit documentation",
    "weblate",
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
_DIAGRAM_CONFIG_PHRASES = (
    "digraph g",
    "subgraph cluster",
    "node [style=",
    "valkey://",
    "use_default_settings:",
    "secret_key:",
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
_OFFICIAL_VENDOR_DOMAINS = (
    "anthropic.com",
    "openai.com",
    "googleblog.com",
    "blog.google",
    "deepmind.google",
    "nvidia.com",
    "blogs.nvidia.com",
    "microsoft.com",
    "amazon.com",
    "meta.com",
    "claude.com",
)


@dataclass(frozen=True)
class SourceQuality:
    score: float
    reason: str
    authority_score: float
    relevance_score: float
    crawlability_score: float
    information_density_score: float
    evidence_density_score: float
    freshness_score: float | None
    safety_score: float
    freshness_state: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ChunkQuality:
    content_quality_score: float
    query_relevance_score: float
    boilerplate_score: float
    information_density_score: float
    eligible_for_claims: bool
    is_boilerplate_like: bool
    is_navigation_noise: bool
    is_reference_section: bool
    is_diagram_or_config_section: bool
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
        return SourceQuality(
            score=0.1,
            reason="redirect_stub",
            authority_score=0.1,
            relevance_score=0.1,
            crawlability_score=0.1,
            information_density_score=0.1,
            evidence_density_score=0.0,
            freshness_score=None,
            safety_score=0.2,
            freshness_state="unknown",
            reasons=("redirect_stub", "freshness_unknown"),
        )

    source_category = str(metadata.get("source_category") or "")
    normalized_domain = domain.strip().lower().removeprefix("www.")
    path = urlsplit(canonical_url).path.strip().lower()
    authority_score, primary_reason = _authority_score(
        normalized_domain=normalized_domain,
        path=path,
        source_category=source_category,
    )
    relevance_score = _source_relevance_score(
        normalized_domain=normalized_domain,
        path=path,
        source_category=source_category,
    )
    crawlability_score = _crawlability_score(metadata)
    information_density_score = _source_information_density_score(metadata)
    evidence_density_score = _source_evidence_density_score(metadata)
    freshness_score, freshness_state = _freshness_score(metadata)
    safety_score = _source_safety_score(normalized_domain, source_category=source_category)
    freshness_component = 0.5 if freshness_score is None else freshness_score
    final_score = (
        (authority_score * 0.24)
        + (relevance_score * 0.28)
        + (information_density_score * 0.22)
        + (crawlability_score * 0.12)
        + (evidence_density_score * 0.08)
        + (freshness_component * 0.04)
        + (safety_score * 0.02)
    )
    if primary_reason == "official_docs":
        final_score = max(final_score, 0.9)
    elif primary_reason in {"project_homepage", "official_github_repository"}:
        final_score = max(final_score, 0.66)
    final_score = round(min(0.98, max(0.05, final_score)), 2)
    reasons = [
        primary_reason,
        f"authority:{authority_score:.2f}",
        f"relevance:{relevance_score:.2f}",
        f"crawlability:{crawlability_score:.2f}",
        f"information_density:{information_density_score:.2f}",
        f"evidence_density:{evidence_density_score:.2f}",
        f"safety:{safety_score:.2f}",
        f"freshness:{freshness_state}",
    ]
    return SourceQuality(
        score=final_score,
        reason=primary_reason,
        authority_score=round(authority_score, 2),
        relevance_score=round(relevance_score, 2),
        crawlability_score=round(crawlability_score, 2),
        information_density_score=round(information_density_score, 2),
        evidence_density_score=round(evidence_density_score, 2),
        freshness_score=None if freshness_score is None else round(freshness_score, 2),
        safety_score=round(safety_score, 2),
        freshness_state=freshness_state,
        reasons=tuple(reasons),
    )


def _authority_score(
    *,
    normalized_domain: str,
    path: str,
    source_category: str,
) -> tuple[float, str]:
    if source_category in {"official_about", "official_docs_reference"}:
        return 0.95, "official_docs"
    if source_category == "official_repository":
        return 0.86, "official_github_repository"
    if source_category == "official_home":
        return 0.72, "project_homepage"
    if source_category == "github_readme_or_repo":
        return 0.72, "official_github_repository"
    if source_category == "github_topic":
        return 0.22, "github_topic_discovery_only"
    if source_category == "search_or_topic_aggregate":
        return 0.22, "search_or_topic_directory_discovery_only"
    if source_category == "secondary_reference":
        return 0.55, "secondary_reference"
    if source_category == "low_quality_or_blocked":
        return 0.1, "low_quality_or_blocked"

    if normalized_domain.startswith(
        ("docs.", "reference.", "documentation.", "blog.", "news.")
    ) or _is_docs_path(path):
        return 0.95, "official_docs"
    if _is_official_vendor_domain(normalized_domain):
        if _is_vendor_high_trust_content_path(normalized_domain, path):
            return 0.92, "official_docs"
        return 0.75, "official_vendor_domain"
    if normalized_domain.endswith("wikipedia.org"):
        return 0.78, "wikipedia_article"
    if _is_project_homepage(normalized_domain, path):
        return 0.72, "project_homepage"
    if normalized_domain == "github.com":
        return 0.45, "github_repository_landing_page"
    if _is_social_video_or_forum_domain(normalized_domain):
        return 0.2, "social_video_or_forum"
    return 0.55, "generic_web_page"


def assess_chunk_quality(
    *,
    text: str,
    query: str,
    source_quality_score: float,
    parsed_metadata: dict[str, object] | None = None,
    chunk_no: int | None = None,
    page_title: str | None = None,
) -> ChunkQuality:
    metadata = parsed_metadata or {}
    normalized = " ".join(text.split())
    lower = normalized.lower()
    reasons: list[str] = []

    redirect_stub = metadata.get("reason") == "redirect_stub" or _looks_like_redirect_stub(lower)
    is_reference_section = _looks_like_reference_section(lower)
    is_navigation_noise = _looks_like_navigation_noise(lower)
    is_pointer_or_project_meta_noise = _looks_like_pointer_or_project_meta_noise(lower)
    is_navigation_noise = is_navigation_noise or is_pointer_or_project_meta_noise
    is_diagram_or_config_section = _looks_like_diagram_or_config_section(normalized)
    is_deployment_code_or_config = _is_deployment_query(
        query
    ) and _looks_like_deployment_code_or_config(normalized)
    if is_deployment_code_or_config:
        is_diagram_or_config_section = False
    title_or_query_overlap = _has_title_or_query_overlap(
        normalized,
        query=query,
        page_title=page_title,
    )
    substantive_short_signal = _looks_like_substantive_short_sentence(normalized)
    protected_short_meaningful = len(normalized) < 48 and (
        title_or_query_overlap or substantive_short_signal
    )
    is_boilerplate_like = _looks_like_low_density_leading_boilerplate(
        normalized,
        lower,
        chunk_no=chunk_no,
        title_or_query_overlap=title_or_query_overlap,
    )
    boilerplate_score = _boilerplate_score(lower)
    if is_boilerplate_like:
        boilerplate_score = max(boilerplate_score, 0.72)
    query_relevance_score = _query_relevance_score(normalized, query)
    information_density_score = _chunk_information_density_score(normalized)
    explanatory_score = 0.16 if _contains_explanatory_terms(lower) else 0.0
    paragraph_score = 0.08 if _looks_like_prose(normalized) else 0.0
    if protected_short_meaningful:
        paragraph_score = max(paragraph_score, 0.08)
    length_penalty = 0.0 if protected_short_meaningful else 0.22 if len(normalized) < 48 else 0.0
    repetition_penalty = 0.12 if _has_high_repetition(normalized) else 0.0

    if redirect_stub:
        reasons.append("redirect_stub")
    if is_reference_section:
        reasons.append("reference_section")
    if is_navigation_noise:
        if is_pointer_or_project_meta_noise:
            reasons.append("pointer_or_project_meta_noise")
        else:
            reasons.append("navigation_noise")
    if is_diagram_or_config_section:
        reasons.append("diagram_or_config_section")
    if is_deployment_code_or_config:
        reasons.append("deployment_code_or_config")
    if is_boilerplate_like:
        reasons.append("leading_boilerplate_like")
    if protected_short_meaningful:
        reasons.append("short_meaningful_content")
    elif len(normalized) < 48:
        reasons.append("very_short")
    if repetition_penalty:
        reasons.append("high_repetition")

    score = (
        0.22
        + (source_quality_score * 0.32)
        + (query_relevance_score * 0.22)
        + (information_density_score * 0.1)
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
    if is_boilerplate_like:
        score = min(score, 0.3)
    if is_diagram_or_config_section:
        score = min(score, 0.18)
    if is_deployment_code_or_config:
        score = max(score, 0.58)
    score = round(min(1.0, max(0.0, score)), 2)

    eligible = (
        score >= 0.35
        and not redirect_stub
        and not is_reference_section
        and not is_navigation_noise
        and not is_boilerplate_like
        and not is_diagram_or_config_section
        and (len(normalized) >= 48 or is_deployment_code_or_config or protected_short_meaningful)
    )
    if eligible:
        quality = "high" if score >= 0.72 else "medium"
    else:
        quality = "low"
    if not reasons and quality != "low":
        reasons.append("informative_prose")
    if information_density_score < 0.35:
        reasons.append("low_information_density")
    elif information_density_score >= 0.72:
        reasons.append("dense_informative_text")

    return ChunkQuality(
        content_quality_score=score,
        query_relevance_score=round(query_relevance_score, 2),
        boilerplate_score=round(boilerplate_score, 2),
        information_density_score=round(information_density_score, 2),
        eligible_for_claims=eligible,
        is_boilerplate_like=is_boilerplate_like,
        is_navigation_noise=is_navigation_noise,
        is_reference_section=is_reference_section,
        is_diagram_or_config_section=is_diagram_or_config_section,
        content_quality=quality,
        reasons=reasons,
    )


def _tokenize(value: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _TOKEN_PATTERN.findall(value))


def _query_relevance_score(text: str, query: str) -> float:
    query_tokens = [token for token in dict.fromkeys(_tokenize(query)) if token not in _STOPWORDS]
    cjk_score = _cjk_overlap_score(text, query)
    if not query_tokens:
        return cjk_score
    text_tokens = set(_tokenize(text))
    token_score = len([token for token in query_tokens if token in text_tokens]) / len(query_tokens)
    return max(token_score, cjk_score)


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


def _source_relevance_score(
    *,
    normalized_domain: str,
    path: str,
    source_category: str,
) -> float:
    if source_category in {"official_about", "official_docs_reference"}:
        return 0.92
    if source_category == "official_repository":
        return 0.86
    if source_category in {"official_home", "github_readme_or_repo"}:
        return 0.78
    if source_category == "secondary_reference":
        return 0.58
    if source_category == "low_quality_or_blocked":
        return 0.12
    if normalized_domain.startswith(("docs.", "reference.", "documentation.")) or _is_docs_path(
        path
    ):
        return 0.88
    if normalized_domain.endswith("wikipedia.org"):
        return 0.7
    if normalized_domain == "github.com":
        return 0.5
    if _is_social_video_or_forum_domain(normalized_domain):
        return 0.35
    return 0.52


def _crawlability_score(metadata: dict[str, object]) -> float:
    if metadata.get("reason") == "redirect_stub":
        return 0.1
    extracted_text_length = metadata.get("extracted_text_length")
    if isinstance(extracted_text_length, int | float) and extracted_text_length <= 0:
        return 0.2
    if metadata.get("fallback_used") is True:
        return 0.62
    if metadata.get("extractor_strategy_used") or metadata.get("extractor"):
        return 0.86
    return 0.72


def _source_information_density_score(metadata: dict[str, object]) -> float:
    extracted_text_length = metadata.get("extracted_text_length")
    if isinstance(extracted_text_length, int | float):
        if extracted_text_length >= 4000:
            return 0.88
        if extracted_text_length >= 1200:
            return 0.74
        if extracted_text_length >= 300:
            return 0.55
        if extracted_text_length > 0:
            return 0.28
    return 0.5


def _source_evidence_density_score(metadata: dict[str, object]) -> float:
    text_value = metadata.get("parsed_text") or metadata.get("text")
    if not isinstance(text_value, str) or not text_value.strip():
        return 0.45
    text = " ".join(text_value.split())
    lower_text = text.lower()
    signal_patterns = (
        r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b",
        r"\b(?:19|20)\d{2}\b",
        r"\bv?\d+\.\d+(?:\.\d+)?\b",
        r"\b\d+(?:\.\d+)?\s?(?:%|percent|ms|s|sec|seconds|gb|mb|tokens?|requests?)\b",
        r"\bapi\b",
        r"\bsdk\b",
        r"\bbenchmark(?:s|ing)?\b",
        r"\barxiv\b",
        r"\bdoi\b",
        r"\bpaper\b",
        r"\bcitation(?:s)?\b",
        r"\baccording to\b",
        r"\brelease(?:d|s)?\b",
        r"\breport(?:ed|s)?\b",
        r"\bexperiment(?:s|al)?\b",
        r"\bstudy\b",
    )
    signal_hits = sum(len(re.findall(pattern, lower_text)) for pattern in signal_patterns)
    quote_hits = lower_text.count('"') // 2 + lower_text.count("“")
    sentence_count = max(1, len(re.findall(r"[.!?。！？]", text)))
    normalized_hits = min(1.0, (signal_hits + min(quote_hits, 4)) / 12)
    sentence_density = min(1.0, sentence_count / 16)
    score = 0.18 + (normalized_hits * 0.62) + (sentence_density * 0.2)
    return min(1.0, max(0.05, score))


def _source_safety_score(normalized_domain: str, *, source_category: str) -> float:
    if source_category == "low_quality_or_blocked":
        return 0.22
    if _is_social_video_or_forum_domain(normalized_domain):
        return 0.42
    return 0.84


def _freshness_score(metadata: dict[str, object]) -> tuple[float | None, str]:
    value = metadata.get("published_at") or metadata.get("publishedDate")
    published_at = _parse_datetime(value)
    if published_at is None:
        return None, "unknown"
    now = datetime.now(UTC)
    age_days = max(0, (now - published_at.astimezone(UTC)).days)
    if age_days <= 90:
        return 0.95, "recent"
    if age_days <= 365:
        return 0.75, "current_year"
    if age_days <= 1095:
        return 0.45, "older"
    return 0.25, "stale"


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _chunk_information_density_score(text: str) -> float:
    tokens = _tokenize(text)
    if not tokens:
        cjk_chars = _cjk_chars(text)
        if not cjk_chars:
            return 0.0
        char_count = len(cjk_chars)
        unique_ratio = len(set(cjk_chars)) / char_count
        length_score = min(char_count / 120, 1.0)
        sentence_signal = 0.2 if _looks_like_prose(text) else 0.0
        score = (unique_ratio * 0.42) + (length_score * 0.38) + sentence_signal
        return min(1.0, max(0.0, score))
    token_count = len(tokens)
    unique_ratio = len(set(tokens)) / token_count
    length_score = min(token_count / 90, 1.0)
    sentence_signal = 0.2 if _looks_like_prose(text) else 0.0
    score = (unique_ratio * 0.42) + (length_score * 0.38) + sentence_signal
    if _has_high_repetition(text):
        score -= 0.25
    return min(1.0, max(0.0, score))


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


def _looks_like_low_density_leading_boilerplate(
    text: str,
    lower_text: str,
    *,
    chunk_no: int | None,
    title_or_query_overlap: bool,
) -> bool:
    if chunk_no != 0 or not text or len(text) > 260 or title_or_query_overlap:
        return False
    marker_hits = sum(1 for phrase in _LEADING_UI_BOILERPLATE_PHRASES if phrase in lower_text)
    if marker_hits < 2:
        return False
    token_count = len(_tokenize(text))
    cjk_count = len(_cjk_chars(text))
    content_units = max(1.0, token_count + (cjk_count / 2.0))
    marker_density = marker_hits / content_units
    if marker_hits >= 4:
        return True
    return marker_density >= 0.16 and not _looks_like_substantive_short_sentence(text)


def _has_title_or_query_overlap(text: str, *, query: str, page_title: str | None) -> bool:
    lower_text = text.lower()
    if _query_relevance_score(text, query) >= 0.25:
        return True
    query_terms = [
        token for token in _tokenize(query) if len(token) >= 4 and token not in _STOPWORDS
    ]
    if any(term in lower_text for term in query_terms):
        return True
    if _cjk_overlap_score(text, query) >= 0.18:
        return True
    if not page_title:
        return False
    title = page_title.strip()
    lower_title = title.lower()
    if len(title) >= 8 and (lower_text in lower_title or lower_title in lower_text):
        return True
    title_terms = [
        token for token in _tokenize(title) if len(token) >= 4 and token not in _STOPWORDS
    ]
    if title_terms and any(term in lower_text for term in title_terms):
        return True
    return _cjk_overlap_score(text, title) >= 0.18


def _looks_like_substantive_short_sentence(text: str) -> bool:
    if len(text) < 20 or not any(mark in text for mark in (".", "。", "!", "！")):
        return False
    lower = text.lower()
    tokens = _tokenize(text)
    if len(tokens) >= 6 and (
        _contains_explanatory_terms(lower)
        or any(token.endswith(("ed", "es", "ing")) for token in tokens)
    ):
        return True
    cjk_chars = _cjk_chars(text)
    if len(cjk_chars) >= 10:
        return any(
            marker in text
            for marker in (
                "是",
                "为",
                "发布",
                "宣布",
                "推出",
                "支持",
                "提供",
                "用于",
                "可以",
                "能够",
                "包括",
                "显示",
            )
        )
    return False


def _cjk_chars(value: str) -> list[str]:
    return _CJK_PATTERN.findall(value)


def _cjk_overlap_score(text: str, query: str) -> float:
    query_chars = _cjk_chars(query)
    if len(query_chars) < 2:
        return 0.0
    query_bigrams = {
        left + right for left, right in zip(query_chars, query_chars[1:], strict=False)
    }
    if not query_bigrams:
        return 0.0
    text_chars = _cjk_chars(text)
    text_bigrams = {left + right for left, right in zip(text_chars, text_chars[1:], strict=False)}
    if not text_bigrams:
        return 0.0
    return len(query_bigrams & text_bigrams) / len(query_bigrams)


def _looks_like_pointer_or_project_meta_noise(lower_text: str) -> bool:
    phrase_hits = sum(1 for phrase in _POINTER_PROJECT_META_PHRASES if phrase in lower_text)
    if phrase_hits <= 0:
        return False
    if _contains_explanatory_terms(lower_text):
        return False
    token_count = max(1, len(_tokenize(lower_text)))
    return token_count <= 90 or phrase_hits / token_count >= 0.04


def _looks_like_diagram_or_config_section(text: str) -> bool:
    lower_text = text.lower()
    if any(phrase in lower_text for phrase in _DIAGRAM_CONFIG_PHRASES):
        return True
    line_count = max(1, len(text.splitlines()))
    arrow_lines = sum(1 for line in text.splitlines() if "->" in line)
    colon_config_lines = sum(
        1
        for line in text.splitlines()
        if line.strip().endswith(":") or re.match(r"^\s*[a-z0-9_]+\s*:", line.lower())
    )
    return arrow_lines >= 2 or (line_count >= 4 and colon_config_lines / line_count >= 0.4)


def _looks_like_deployment_code_or_config(text: str) -> bool:
    lower_text = text.lower()
    deployment_markers = (
        ".env.example",
        "archived",
        "bot protection",
        "certificate",
        "certificates",
        "docker ",
        "docker-compose",
        "docker compose",
        "podman ",
        "compose.yaml",
        "compose.yml",
        "docker-compose.yml",
        "docker-compose.yaml",
        "searxng/searxng",
        "/etc/searxng",
        "/var/cache/searxng",
        "public instance",
        "publicly accessible",
        "reverse proxy",
        "searxng-valkey",
        "superseded",
    )
    config_markers = (
        "services:",
        "ports:",
        "volumes:",
        "environment:",
        "restart:",
        "cap_drop:",
        "read_only:",
        "secret_key:",
        "base_url",
        "limiter",
        "settings.yml",
        "searxng_",
        "valkey://",
    )
    return any(marker in lower_text for marker in deployment_markers) or any(
        marker in lower_text for marker in config_markers
    )


def _is_deployment_query(query: str | None) -> bool:
    lower = (query or "").lower()
    return any(
        term in lower
        for term in (
            "deploy",
            "deployment",
            "docker",
            "compose",
            "container",
            "install",
            "self-host",
            "self host",
        )
    )


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
            "/release-notes",
            "/release_notes",
        )
    )


def _is_blog_path(path: str) -> bool:
    return any(marker in path for marker in ("/blog", "/blogs", "/newsroom", "/news-room"))


def _is_news_path(path: str) -> bool:
    return any(marker in path for marker in ("/news", "/press", "/announcements"))


def _is_official_vendor_domain(domain: str) -> bool:
    return any(domain == item or domain.endswith(f".{item}") for item in _OFFICIAL_VENDOR_DOMAINS)


def _is_vendor_high_trust_content_path(domain: str, path: str) -> bool:
    if _is_blog_path(path) or _is_news_path(path) or _is_docs_path(path):
        return True
    lower = path.lower()
    if (
        domain.startswith("support.")
        and (domain.endswith(".claude.com") or domain == "support.claude.com")
        and ("/articles" in lower or "/hc/" in lower or lower.startswith("/en/"))
    ):
        return True
    if domain.startswith("code.") and domain.endswith(".claude.com") and (
        "/docs" in lower or "/reference" in lower or "/api" in lower
    ):
        return True
    return False


def _is_project_homepage(domain: str, path: str) -> bool:
    if not domain or domain.endswith("wikipedia.org"):
        return False
    return path.rstrip("/") in {"", "/"}


def _is_social_video_or_forum_domain(domain: str) -> bool:
    return any(
        domain == item or domain.endswith(f".{item}") for item in _SOCIAL_VIDEO_FORUM_DOMAINS
    )
