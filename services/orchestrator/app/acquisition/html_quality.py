"""Lightweight static HTML heuristics for acquisition diagnostics (no JS execution)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Codes returned by ``recommended_soft_fetch_error_code`` for weak static HTML pages.
STATIC_HTML_SOFT_SIGNAL_CODES: frozenset[str] = frozenset(
    {
        "spa_shell",
        "javascript_required",
        "cookie_wall",
        "bot_check",
    }
)

_SCRIPT_BLOCK = re.compile(rb"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_BLOCK = re.compile(rb"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_TAG_STRIP = re.compile(rb"<[^>]+>")
_WS_COLLAPSE = re.compile(rb"\s+")
_LINK_TAG = re.compile(rb"<a\b", re.IGNORECASE)
_P_TAG = re.compile(rb"<p\b", re.IGNORECASE)


@dataclass(frozen=True)
class StaticHtmlQualityReport:
    visible_text_length: int
    paragraph_count: int
    script_to_text_ratio: float
    link_density: float
    likely_spa_shell: bool
    likely_javascript_required: bool
    likely_cookie_wall: bool
    likely_bot_check: bool
    signals: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "visible_text_length": self.visible_text_length,
            "paragraph_count": self.paragraph_count,
            "script_to_text_ratio": self.script_to_text_ratio,
            "link_density": self.link_density,
            "likely_spa_shell": self.likely_spa_shell,
            "likely_javascript_required": self.likely_javascript_required,
            "likely_cookie_wall": self.likely_cookie_wall,
            "likely_bot_check": self.likely_bot_check,
            "signals": dict(self.signals),
        }


def _rough_visible_text(html: bytes) -> str:
    without_scripts = _SCRIPT_BLOCK.sub(b" ", html)
    without_styles = _STYLE_BLOCK.sub(b" ", without_scripts)
    textish = _TAG_STRIP.sub(b" ", without_styles)
    textish = _WS_COLLAPSE.sub(b" ", textish).strip()
    return textish.decode("utf-8", errors="replace")


def evaluate_static_html_quality(html: bytes) -> StaticHtmlQualityReport:
    lower = html[:400_000].lower()
    text = _rough_visible_text(html[:800_000])
    visible_len = len(text.strip())
    script_bytes = sum(len(m.group(0)) for m in _SCRIPT_BLOCK.finditer(html[:800_000]))
    script_ratio = float(script_bytes) / max(1, len(html))
    link_tags = len(_LINK_TAG.findall(html[:800_000]))
    paragraphs = len(_P_TAG.findall(html[:800_000]))
    tokens = max(1, len(text.split()))
    link_density = link_tags / float(tokens)

    signals: dict[str, bool] = {
        "has_root_div_app": rb'id="app"' in lower or rb"id='app'" in lower,
        "has_next_data": b"__next_data__" in lower,
        "has_noscript_warning": b"<noscript" in lower and b"javascript" in lower,
        "react_root": b"reactdom" in lower or b"data-reactroot" in lower,
        "enable_javascript_phrase": any(
            phrase in lower
            for phrase in (
                b"enable javascript",
                b"javascript is required",
                b"javascript must be enabled",
                b"requires javascript",
            )
        ),
        "cookie_consent": any(
            phrase in lower
            for phrase in (
                b"cookie consent",
                b"accept cookies",
                b"we use cookies",
                b"this site uses cookies",
            )
        ),
        "bot_challenge": any(
            phrase in lower
            for phrase in (
                b"cf-chl",
                b"cloudflare",
                b"checking your browser",
                b"attention required",
                b"captcha",
                b"hcaptcha",
                b"recaptcha",
                b"verify you are human",
            )
        ),
    }

    likely_js = signals["has_noscript_warning"] or signals["enable_javascript_phrase"]
    likely_spa = (
        visible_len < 220
        and (
            signals["has_root_div_app"]
            or signals["has_next_data"]
            or (signals["react_root"] and script_ratio > 0.02)
        )
    )
    likely_cookie = signals["cookie_consent"] and visible_len < 260
    likely_bot = signals["bot_challenge"] and visible_len < 900

    return StaticHtmlQualityReport(
        visible_text_length=visible_len,
        paragraph_count=paragraphs,
        script_to_text_ratio=round(script_ratio, 6),
        link_density=round(link_density, 6),
        likely_spa_shell=likely_spa,
        likely_javascript_required=likely_js and visible_len < 320,
        likely_cookie_wall=likely_cookie,
        likely_bot_check=likely_bot,
        signals={k: bool(v) for k, v in signals.items()},
    )


def recommended_soft_fetch_error_code(report: StaticHtmlQualityReport) -> str | None:
    """
    When static HTML is technically 200 OK but unlikely to yield evidence without a browser.

    Conservative thresholds to limit false positives.
    """
    if report.likely_bot_check:
        return "bot_check"
    if report.likely_cookie_wall:
        return "cookie_wall"
    if report.likely_javascript_required and report.visible_text_length < 200:
        return "javascript_required"
    if report.likely_spa_shell and report.visible_text_length < 180:
        return "spa_shell"
    return None


def is_static_html_soft_signal_code(code: str | None) -> bool:
    return isinstance(code, str) and code in STATIC_HTML_SOFT_SIGNAL_CODES
