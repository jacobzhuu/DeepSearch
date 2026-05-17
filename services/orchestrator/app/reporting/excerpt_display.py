"""Display-only excerpt expansion (verification still uses persisted span offsets)."""

from __future__ import annotations

import re

_SENTENCE_END_RE = re.compile(r"[.!?。！？]")


def expand_excerpt_for_display(
    excerpt: str,
    chunk_text: str,
    *,
    start_offset: int,
    end_offset: int,
    max_window: int = 520,
) -> str:
    """Extend a clipped span excerpt toward sentence boundaries within chunk text."""
    raw_excerpt = (excerpt or "").strip()
    text = chunk_text or ""
    if not text.strip():
        return raw_excerpt
    lo = max(0, min(start_offset, len(text)))
    hi = max(lo + 1, min(end_offset, len(text)))
    span = text[lo:hi].strip()
    if not span:
        return raw_excerpt

    def _bad_tail(s: str) -> bool:
        t = s.rstrip()
        if not t:
            return True
        last = t[-1]
        if last in ",，、;:：":
            return True
        if last.isdigit():
            return True
        tail3 = t[-3:].lower()
        if tail3.endswith(" vs") or tail3.endswith(" 和"):
            return True
        lowered = t.lower()
        if re.search(
            r"\b(?:over|than|vs|and|or)\s*$",
            lowered,
        ):
            return True
        if re.search(r"(?:以及|及|与|或|和|比如|例如|如)\s*$", t):
            return True
        if re.search(
            r"(?:\b(?:the|a|an|this|that|these|those)\s+)$",
            lowered,
        ):
            return True
        return False

    left = lo
    right = hi
    # Expand left to sentence start (or window cap)
    while left > 0 and (lo - left) < max_window:
        if _SENTENCE_END_RE.search(text[left - 1 : left]):
            break
        left -= 1
        if text[left] in "\n\r":
            left += 1
            break
    # Expand right to sentence end
    while right < len(text) and (right - hi) < max_window:
        ch = text[right]
        if _SENTENCE_END_RE.match(ch):
            right += 1
            break
        if ch in "\n\r":
            break
        right += 1

    expanded = text[left:right].strip()
    if not expanded:
        return raw_excerpt
    if len(expanded) > max_window:
        expanded = expanded[:max_window].rsplit(" ", 1)[0].strip()

    if _bad_tail(expanded) and right < len(text):
        extra = text[right : min(len(text), right + 200)]
        m = _SENTENCE_END_RE.search(extra)
        if m:
            expanded = (expanded + extra[: m.end()]).strip()

    if _bad_tail(expanded):
        for punct in (".", "。", "!", "！", "?", "？"):
            idx = expanded.rfind(punct)
            if idx > max(len(span), 24):
                expanded = expanded[: idx + 1].strip()
                break

    # Second pass: keep stretching until sentence end if still mid-clause
    if _bad_tail(expanded) and right < len(text):
        extra2 = text[right : min(len(text), right + max_window)]
        m2 = _SENTENCE_END_RE.search(extra2)
        if m2:
            expanded = (expanded + extra2[: m2.end()]).strip()

    return expanded if len(expanded) >= len(span) else raw_excerpt
