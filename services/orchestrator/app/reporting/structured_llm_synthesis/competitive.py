from __future__ import annotations

# Mirrors grounded report competitive heuristics (lightweight; keep in sync intentionally).
_COMPETITIVE_NEGATIVE_MARKERS: tuple[str, ...] = (
    "优于",
    "胜过",
    "碾压",
    "吊打",
    "更快",
    "更强",
    "更好",
    "best",
    "better than",
    "beats",
    "outperform",
    "劣势",
    "不如",
)


def statement_has_competitive_negative_tone(statement: str) -> bool:
    low = (statement or "").lower()
    return any(m in low for m in _COMPETITIVE_NEGATIVE_MARKERS)
