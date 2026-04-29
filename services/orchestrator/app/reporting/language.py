from __future__ import annotations

from typing import Any

DEFAULT_REPORT_LANGUAGE = "en-US"
ZH_CN = "zh-CN"


def resolve_report_language(constraints: dict[str, Any] | None) -> str:
    values = constraints or {}
    for key in ("report_language", "language"):
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_report_language(value)
    return DEFAULT_REPORT_LANGUAGE


def normalize_report_language(value: str | None) -> str:
    normalized = (value or "").strip().replace("_", "-")
    if not normalized:
        return DEFAULT_REPORT_LANGUAGE
    lowered = normalized.lower()
    if lowered in {"zh", "zh-cn", "zh-hans", "zh-hans-cn", "cn"}:
        return ZH_CN
    if lowered in {"en", "en-us", "en_us"}:
        return DEFAULT_REPORT_LANGUAGE
    return normalized


def is_chinese_report_language(value: str | None) -> bool:
    return normalize_report_language(value).lower().startswith("zh")
