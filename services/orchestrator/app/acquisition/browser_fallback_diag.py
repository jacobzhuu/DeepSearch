"""Structured diagnostics for browser acquisition fallback decisions."""

from __future__ import annotations

from typing import Any

from services.orchestrator.app.acquisition.browser_backend import (
    BROWSER_FETCH_ALLOWED_ERROR_CODES,
    BROWSER_FETCH_BACKEND_NONE,
    BROWSER_FETCH_EXCLUDED_ERROR_CODES,
    should_attempt_browser_fallback,
)
from services.orchestrator.app.acquisition.html_quality import STATIC_HTML_SOFT_SIGNAL_CODES


def compute_browser_fallback_diagnostics(
    *,
    error_code: str | None,
    trace_json: dict[str, Any] | None,
    browser_fetch_backend_setting: str,
    backend_available: bool,
) -> dict[str, Any]:
    """
    Build a stable dict for logs, metrics, task_event payloads, and fetch_attempt.trace_json.

    Keys align with operator-facing observability contracts.
    """
    normalized = (browser_fetch_backend_setting or BROWSER_FETCH_BACKEND_NONE).strip().lower()
    configured = normalized not in {"", BROWSER_FETCH_BACKEND_NONE}
    trace = trace_json if isinstance(trace_json, dict) else {}
    eligible = should_attempt_browser_fallback(
        error_code=error_code,
        trace_json=trace_json,
        browser_fetch_backend=browser_fetch_backend_setting,
    )

    skipped_reason: str | None = None
    trigger_reason: str | None = None

    if not configured:
        skipped_reason = "backend_setting_none"
    elif not backend_available:
        skipped_reason = "backend_unavailable"
    elif not eligible:
        skipped_reason = _skip_reason_ineligible(error_code=error_code, trace=trace)
    else:
        trigger_reason = _trigger_reason(error_code=error_code, trace=trace)

    return {
        "browser_fallback_configured": configured,
        "browser_fallback_backend": normalized,
        "browser_fallback_available": backend_available,
        "browser_fallback_considered": configured,
        "browser_fallback_eligible": eligible,
        "browser_fallback_trigger_reason": trigger_reason,
        "browser_fallback_skipped_reason": skipped_reason,
        "browser_fallback_attempted": False,
        "browser_fallback_result": None,
        "browser_fallback_error_code": None,
        "static_error_code": error_code,
    }


def _skip_reason_ineligible(*, error_code: str | None, trace: dict[str, Any]) -> str:
    if error_code is not None and error_code in BROWSER_FETCH_EXCLUDED_ERROR_CODES:
        return f"excluded_{error_code}"
    if error_code is not None and error_code not in BROWSER_FETCH_ALLOWED_ERROR_CODES:
        return f"ineligible_{error_code}"
    decision = trace.get("static_html_quality_decision")
    if error_code is None and (
        not isinstance(decision, str) or decision not in STATIC_HTML_SOFT_SIGNAL_CODES
    ):
        return "ineligible_no_static_html_soft_signal"
    return "ineligible_unknown"


def _trigger_reason(*, error_code: str | None, trace: dict[str, Any]) -> str:
    if error_code is not None and error_code in BROWSER_FETCH_ALLOWED_ERROR_CODES:
        return f"allowed_{error_code}"
    decision = trace.get("static_html_quality_decision")
    if isinstance(decision, str) and decision in STATIC_HTML_SOFT_SIGNAL_CODES:
        return f"soft_static_html_{decision}"
    return "eligible_unknown"


def finalize_browser_attempt_diagnostics(
    base: dict[str, Any],
    *,
    attempted: bool,
    result: str | None,
    error_code: str | None,
) -> dict[str, Any]:
    out = dict(base)
    out["browser_fallback_attempted"] = attempted
    out["browser_fallback_result"] = result
    out["browser_fallback_error_code"] = error_code
    return out
