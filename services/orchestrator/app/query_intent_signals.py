"""Shared lexical signals for planner, answer slots, acquisition, and drafting.

English and Chinese are supported without treating every Chinese question as technical.

Placed at package root (not under ``research_quality``) to avoid import cycles with
``claims.drafting`` ↔ ``research_quality`` package initialization.
"""

from __future__ import annotations

import re
from typing import Final

_CJK_TECH_MARKERS: Final[tuple[str, ...]] = (
    "详细解释",
    "原理",
    "架构",
    "执行模型",
    "工作机制",
    "如何工作",
    "如何运作",
    "怎样工作",
    "工作原理",
)

_CJK_DEFINITION_MARKERS: Final[tuple[str, ...]] = (
    "是什么",
    "何为",
    "哪个是",
    "介绍",
    "概述",
)

_CJK_COMPARISON_MARKERS: Final[tuple[str, ...]] = (
    "相比",
    "相较",
    "区别",
    "对比",
    "比较",
)

_VS_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\s)(vs\.?|VS\.?)(?:\s|$)", re.IGNORECASE)


def query_asks_comparison(query: str | None) -> bool:
    if not query or not query.strip():
        return False
    raw = query.strip()
    lower = raw.lower()
    padded = f" {lower} "
    if " compare " in padded or " comparison " in padded:
        return True
    if " versus " in padded or " differences between" in lower:
        return True
    if " vs " in padded or " vs." in padded:
        return True
    if _VS_RE.search(raw) is not None:
        return True
    return any(marker in raw for marker in _CJK_COMPARISON_MARKERS)


_ENTITY_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9+.-]{1,47}(?![A-Za-z0-9])"
)
_ENTITY_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "the",
        "and",
        "what",
        "how",
        "for",
        "this",
        "that",
        "when",
        "why",
        "with",
        "each",
        "both",
        "their",
        "your",
        "our",
        "from",
        "into",
        "onto",
        "does",
        "did",
        "are",
        "was",
        "were",
        "been",
        "being",
        "have",
        "has",
        "had",
        "not",
        "but",
        "you",
        "can",
        "may",
        "its",
        "any",
        "all",
    }
)


def extract_comparison_entities(query: str | None, *, max_entities: int = 6) -> list[str]:
    """Surface comparable named subjects (frameworks, models, databases, etc.).

    Broader than CamelCase-only heuristics: supports acronyms (BERT, T5), product tokens
    (React, PostgreSQL, MySQL), and mixed technical names while filtering common English
    sentence starters.
    """
    if not query or not query.strip():
        return []
    raw = query.strip()
    scored: list[tuple[int, str]] = []
    seen_lower: set[str] = set()
    for match in _ENTITY_TOKEN_RE.finditer(raw):
        token = match.group(0).strip()
        if len(token) < 2:
            continue
        lower = token.lower()
        if lower in _ENTITY_STOPWORDS:
            continue
        if lower in seen_lower:
            continue
        seen_lower.add(lower)
        scored.append((match.start(), token))
    scored.sort(key=lambda item: item[0])
    return [token for _, token in scored[:max_entities]]


def query_asks_definition_mechanism_signals(query: str | None) -> bool:
    if not query or not query.strip():
        return False
    raw = query.strip()
    lower = raw.lower()
    if "what is" in lower or "what are" in lower or "overview" in lower:
        return True
    if "how does" in lower or "how do" in lower:
        return True
    if "explain" in lower and (
        "how" in lower or "architecture" in lower or "what" in lower
    ):
        return True
    return any(marker in raw for marker in (*_CJK_DEFINITION_MARKERS, "如何工作", "工作机制"))


def query_asks_technical_explanation(query: str | None) -> bool:
    if not query_asks_definition_mechanism_signals(query):
        return False
    raw = query.strip()
    lower = raw.lower()
    if any(term in lower for term in ("deploy", "deployment", "docker", "install")):
        return False
    if ("what is" in lower or "what are" in lower) and "how" in lower and "work" in lower:
        return True
    if lower.startswith("explain ") and ("how" in lower or "architecture" in lower):
        return True
    if any(
        term in lower
        for term in (
            "technical explanation",
            "architecture and execution",
            "execution model",
            "how it works",
        )
    ):
        return True
    if any(marker in raw for marker in _CJK_TECH_MARKERS):
        return True
    if "如何工作" in raw or "工作机制" in raw or "如何运作" in raw:
        return True
    if "是什么" in raw and (
        "如何" in raw
        or "怎么" in raw
        or "原理" in raw
        or "架构" in raw
        or "工作" in raw
        or "机制" in raw
    ):
        return True
    return any(
        term in lower
        for term in (
            "architecture",
            "execution model",
            "workflow",
            "framework",
            "library",
            "agent",
            "orchestration",
        )
    )


def query_has_lexical_recency_or_update_markers(query: str | None) -> bool:
    """True when the *query text itself* reads like recency/news/changelog (not planner metadata)."""
    if not query or not query.strip():
        return False
    raw = query.strip()
    lower = raw.lower()
    if any(
        term in lower
        for term in (
            "last 30 days",
            "past 30 days",
            "recent",
            "latest",
            "changelog",
            "release notes",
            "breaking change",
            "this week",
            "this month",
        )
    ):
        return True
    return any(
        term in raw
        for term in (
            "近30天",
            "最近30天",
            "近期更新",
            "最新发布",
            "今年",
            "今年以来",
            "更新日志",
            "发布公告",
            "版本更新",
            "安全公告",
            "官方更新",
        )
    )


_RESEARCH_SURVEY_ZH_MARKERS: Final[tuple[str, ...]] = (
    "综述",
    "文献综述",
    "系统性综述",
    "研究综述",
    "调研综述",
    "综合研究报告",
)

_RESEARCH_SURVEY_EN_PHRASES: Final[tuple[str, ...]] = (
    "literature review",
    "survey paper",
    "research survey",
    "systematic review",
    "comprehensive review",
    "related work",
    "future direction",
    "future directions",
    "state of the art survey",
    "state-of-the-art survey",
)

_SURVEY_OF_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(survey|review)\s+of\b|\b(survey|review)\s+on\b", re.IGNORECASE
)


def query_requests_research_survey(query: str | None) -> bool:
    """True when the user explicitly asks for a survey / literature-style synthesis."""
    if not query or not query.strip():
        return False
    raw = query.strip()
    lower = raw.lower()
    if any(marker in raw for marker in _RESEARCH_SURVEY_ZH_MARKERS):
        return True
    if "文献调研" in raw:
        return True
    if any(phrase in lower for phrase in _RESEARCH_SURVEY_EN_PHRASES):
        return True
    if "literature survey" in lower:
        return True
    if _SURVEY_OF_RE.search(lower) is not None:
        return True
    if re.search(r"\bsurvey\b", lower) is not None:
        if any(
            term in lower
            for term in (
                "paper",
                "papers",
                "arxiv",
                "method",
                "methods",
                "algorithm",
                "algorithms",
            )
        ):
            return True
        if any(term in raw for term in ("论文", "方法", "算法")):
            return True
    return False


_PAPER_LIKE_DOMAIN_MARKERS: Final[tuple[str, ...]] = (
    "arxiv.org",
    "openreview.net",
    "aclanthology.org",
    "semanticscholar.org",
    "doi.org",
    "ieee",
    "neurips.cc",
    "icml.cc",
    "iclr.cc",
    "aaai.org",
    "acm.org",
)


def domain_looks_paper_like(domain: str | None) -> bool:
    if not domain:
        return False
    d = domain.lower()
    return any(fragment in d for fragment in _PAPER_LIKE_DOMAIN_MARKERS)


def conservative_research_survey_source_hint(
    query: str | None,
    *,
    source_domains: list[str],
) -> bool:
    """Very conservative booster: many paper-like domains + thematic cue, never comparisons/recency."""
    if not query or not query.strip():
        return False
    raw = query.strip()
    lower = raw.lower()
    if query_has_lexical_recency_or_update_markers(raw):
        return False
    if query_is_news_or_recency_update(raw, plan_intent=None):
        return False
    if query_requests_research_survey(raw):
        return False
    if query_asks_comparison(raw):
        return False
    if len(source_domains) < 6:
        return False
    paperish = sum(1 for dom in source_domains if domain_looks_paper_like(dom))
    if paperish < 5:
        return False
    thematic = any(
        term in lower
        for term in (
            "methods",
            "method families",
            "taxonomy",
            "approaches",
            "landscape",
            "overview of",
        )
    ) or any(term in raw for term in ("方法族", "方法分类", "研究脉络", "技术路线"))
    return thematic


def detect_report_archetype(
    query: str | None,
    *,
    plan_intent: str | None = None,
    source_domains: list[str] | None = None,
) -> str:
    """Select a report template archetype (not the planner task intent).

    Priority:
    1. Lexical recency / planner news-like intents → ``news_update``.
    2. Explicit survey / literature-review cues → ``research_survey``.
    3. Optional conservative paper-domain clustering hint → ``research_survey``.
    4. Technical explanation + comparison (non-news) → ``technical_comparison``.
    5. Otherwise → ``general``.
    """
    if not query or not query.strip():
        if plan_intent and plan_intent.strip().lower() in {"news", "update", "recent_events"}:
            return "news_update"
        return "general"
    raw = query.strip()
    if query_has_lexical_recency_or_update_markers(raw):
        return "news_update"
    if query_is_news_or_recency_update(raw, plan_intent=plan_intent):
        return "news_update"
    if query_requests_research_survey(raw):
        return "research_survey"
    if source_domains and conservative_research_survey_source_hint(raw, source_domains=source_domains):
        return "research_survey"
    if query_requests_explanation_comparison_template(raw, plan_intent=plan_intent):
        return "technical_comparison"
    return "general"


def query_is_news_or_recency_update(query: str | None, *, plan_intent: str | None = None) -> bool:
    if not query or not query.strip():
        return bool(plan_intent and plan_intent.strip().lower() in {"news", "update", "recent_events"})
    raw = query.strip()
    lexical = query_has_lexical_recency_or_update_markers(raw)
    # Planner may label a shelf as "news" while the user question is a stable tech+compare brief.
    # In that case, follow the user query for report template selection unless the query is also
    # explicitly recency-shaped.
    if plan_intent and plan_intent.strip().lower() in {"news", "update", "recent_events"}:
        if (
            (
                query_asks_technical_explanation(raw)
                or len(extract_comparison_entities(raw)) >= 2
            )
            and query_asks_comparison(raw)
            and not lexical
        ):
            return False
        return True
    return lexical


def query_requests_explanation_comparison_template(
    query: str | None, *, plan_intent: str | None = None
) -> bool:
    if not query or not query.strip():
        return False
    if not query_asks_comparison(query):
        return False
    if not (
        query_asks_technical_explanation(query) or len(extract_comparison_entities(query)) >= 2
    ):
        return False
    return not query_is_news_or_recency_update(query, plan_intent=plan_intent)
