"""Post-process HttpFetchResult for MIME/body gates and static HTML quality (no browser)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from services.orchestrator.app.acquisition.failure_classification import (
    merge_trace,
    normalize_redirect_loop_code,
)
from services.orchestrator.app.acquisition.html_quality import (
    evaluate_static_html_quality,
    recommended_soft_fetch_error_code,
)
from services.orchestrator.app.acquisition.http_client import HttpFetchResult

_HTML_MIME_PREFIXES = ("text/html", "application/xhtml+xml")

# Trace keys consumed by parsing and browser-fallback planning (no DB migration).
TRACE_ELIGIBLE_FOR_EVIDENCE_PARSE = "eligible_for_evidence_parse"
TRACE_STATIC_HTML_QUALITY_DECISION = "static_html_quality_decision"
TRACE_PARSE_HOLD_REASON = "parse_hold_reason"


def refine_http_fetch_result(fetch_result: HttpFetchResult) -> HttpFetchResult:
    """Normalize legacy transport codes (e.g. redirect aliases)."""
    error_code = normalize_redirect_loop_code(fetch_result.error_code)
    trace = dict(fetch_result.trace)
    if error_code == fetch_result.error_code:
        return fetch_result
    return replace(fetch_result, error_code=error_code, trace=trace)


def apply_static_html_quality_gate(fetch_result: HttpFetchResult) -> HttpFetchResult:
    """
    Attach ``static_html_quality`` diagnostics and optional parse-evidence hold.

    Weak SPA / bot / cookie / JS-only pages keep **raw bytes** and ``error_code=None`` so
    ``content_snapshot`` rows remain auditable; downstream parsing consults
    ``eligible_for_evidence_parse`` in ``fetch_attempt.trace_json`` instead of dropping bodies.
    """
    if fetch_result.error_code is not None:
        return fetch_result
    content = fetch_result.content
    if not content:
        base = merge_trace(
            dict(fetch_result.trace),
            {TRACE_ELIGIBLE_FOR_EVIDENCE_PARSE: True},
        )
        return replace(fetch_result, trace=base)
    mime = (fetch_result.mime_type or "").split(";", 1)[0].strip().lower()
    if not mime.startswith(_HTML_MIME_PREFIXES):
        base = merge_trace(
            dict(fetch_result.trace),
            {TRACE_ELIGIBLE_FOR_EVIDENCE_PARSE: True},
        )
        return replace(fetch_result, trace=base)

    report = evaluate_static_html_quality(content)
    soft = recommended_soft_fetch_error_code(report)
    quality_trace = {"static_html_quality": report.to_dict()}
    if soft is None:
        trace = merge_trace(
            dict(fetch_result.trace),
            {
                **quality_trace,
                TRACE_ELIGIBLE_FOR_EVIDENCE_PARSE: True,
            },
        )
        return replace(fetch_result, trace=trace)

    trace = merge_trace(
        dict(fetch_result.trace),
        {
            **quality_trace,
            TRACE_STATIC_HTML_QUALITY_DECISION: soft,
            TRACE_PARSE_HOLD_REASON: soft,
            TRACE_ELIGIBLE_FOR_EVIDENCE_PARSE: False,
            "post_fetch_gate": soft,
        },
    )
    return replace(fetch_result, trace=trace)


def finalize_static_fetch_result(fetch_result: HttpFetchResult) -> HttpFetchResult:
    """Full static pipeline: HTTP refinements, then HTML quality gate."""
    return apply_static_html_quality_gate(refine_http_fetch_result(fetch_result))
