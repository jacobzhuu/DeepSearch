"""Guardrail helpers for Chinese million-scale context wording in planner output.

Chinese ``100万`` (and close variants) denote one million (1M), not 100K. LLM planners
sometimes emit ``100K``/``100 k`` in subquestions or search queries; this module corrects
those strings when the user query clearly signals a million-scale context intent and the
user did not explicitly ask for 100K.
"""

from __future__ import annotations

import re
from dataclasses import replace

from services.orchestrator.app.planning.types import PlannedSearchQuery

_CH_MILLION_MARKERS = (
    re.compile(r"100\s*万"),
    re.compile(r"一百万"),
    re.compile(r"百万级"),
)


def query_explicitly_requests_100k_context(query: str) -> bool:
    """True when the user clearly asked for ~100 thousand, not one million."""
    lower = query.lower()
    if re.search(r"\b100\s*k\b", lower):
        return True
    if "100千" in query:
        return True
    if "十万" in query and (
        "token" in lower
        or "上下文" in query
        or "窗口" in query
        or "context" in lower
    ):
        return True
    return False


def query_signals_chinese_million_context_scale(query: str) -> bool:
    """Heuristic: user cares about million-token / 百万级 context, not 100K."""
    if query_explicitly_requests_100k_context(query):
        return False
    if not any(p.search(query) for p in _CH_MILLION_MARKERS):
        return False
    lower = query.lower()
    if any(
        needle in query or needle in lower
        for needle in ("上下文", "语境", "窗口", "token", "context")
    ):
        return True
    if "一百万" in query:
        return True
    return False


def _context_numeric_scope(text: str) -> bool:
    lower = text.lower()
    return any(
        needle in text or needle in lower
        for needle in ("上下文", "语境", "窗口", "token", "tokens", "context")
    )


def _fix_hundred_k_misread_to_million(text: str) -> str:
    """Replace mistaken 100K-style wording with 1M when text is context/token scoped."""
    if not _context_numeric_scope(text):
        return text
    out = re.sub(r"\b100\s*k\b", "1M", text, flags=re.IGNORECASE)
    out = re.sub(r"\b100k\b", "1M", out, flags=re.IGNORECASE)
    out = re.sub(
        r"\b100000\b(?=\s*[- ]?(tokens?|上下文|语境|窗口|context))",
        "1000000",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"\b100,000\b(?=\s*[- ]?(tokens?|上下文|语境|窗口|context))",
        "1,000,000",
        out,
        flags=re.IGNORECASE,
    )
    return out


def apply_million_context_corrections(
    query: str,
    *,
    subquestions: list[str],
    normalized_question: str,
    search_queries: list[PlannedSearchQuery],
) -> tuple[list[str], str, list[PlannedSearchQuery], list[str]]:
    """Return possibly-corrected subquestions, normalized_question, queries, new warnings."""
    if not query_signals_chinese_million_context_scale(query):
        return subquestions, normalized_question, search_queries, []

    new_subs = [_fix_hundred_k_misread_to_million(s) for s in subquestions]
    new_norm = _fix_hundred_k_misread_to_million(normalized_question)
    new_queries: list[PlannedSearchQuery] = []
    for q in search_queries:
        new_queries.append(
            replace(
                q,
                query_text=_fix_hundred_k_misread_to_million(q.query_text),
                rationale=_fix_hundred_k_misread_to_million(q.rationale),
            )
        )

    changed = (
        new_subs != subquestions
        or new_norm != normalized_question
        or [q.query_text for q in new_queries] != [q.query_text for q in search_queries]
        or [q.rationale for q in new_queries] != [q.rationale for q in search_queries]
    )
    warnings: list[str] = []
    if changed:
        warnings.append("planner_subplan_string_adjusted_million_context_not_100k")
    return new_subs, new_norm, new_queries, warnings
