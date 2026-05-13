from __future__ import annotations

from services.orchestrator.app.acquisition.browser_fallback_diag import (
    compute_browser_fallback_diagnostics,
    finalize_browser_attempt_diagnostics,
)


def test_diag_excluded_body_too_large() -> None:
    d = compute_browser_fallback_diagnostics(
        error_code="body_too_large",
        trace_json={},
        browser_fetch_backend_setting="playwright",
        backend_available=True,
    )
    assert d["browser_fallback_skipped_reason"] == "excluded_body_too_large"
    assert d["browser_fallback_eligible"] is False


def test_diag_soft_spa_eligible() -> None:
    d = compute_browser_fallback_diagnostics(
        error_code=None,
        trace_json={"static_html_quality_decision": "spa_shell"},
        browser_fetch_backend_setting="playwright",
        backend_available=True,
    )
    assert d["browser_fallback_eligible"] is True
    assert d["browser_fallback_trigger_reason"] == "soft_static_html_spa_shell"


def test_finalize_attempt_fields() -> None:
    base = compute_browser_fallback_diagnostics(
        error_code=None,
        trace_json={"static_html_quality_decision": "spa_shell"},
        browser_fetch_backend_setting="playwright",
        backend_available=True,
    )
    fin = finalize_browser_attempt_diagnostics(
        base,
        attempted=True,
        result="succeeded",
        error_code=None,
    )
    assert fin["browser_fallback_attempted"] is True
    assert fin["browser_fallback_result"] == "succeeded"
