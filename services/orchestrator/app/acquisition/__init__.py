"""Acquisition helpers for policy-guarded HTTP fetching."""

from services.orchestrator.app.acquisition.browser_backend import (
    BROWSER_FETCH_BACKEND_MCP,
    BROWSER_FETCH_BACKEND_NONE,
    BROWSER_FETCH_BACKEND_PLAYWRIGHT,
    BrowserFetchBackend,
    should_attempt_browser_fallback,
)
from services.orchestrator.app.acquisition.failure_classification import (
    classify_http_response,
    classify_httpx_request_error,
)
from services.orchestrator.app.acquisition.fetch_outcome import (
    TRACE_ELIGIBLE_FOR_EVIDENCE_PARSE,
    TRACE_PARSE_HOLD_REASON,
    TRACE_STATIC_HTML_QUALITY_DECISION,
    apply_static_html_quality_gate,
    finalize_static_fetch_result,
    refine_http_fetch_result,
)
from services.orchestrator.app.acquisition.html_quality import (
    STATIC_HTML_SOFT_SIGNAL_CODES,
    StaticHtmlQualityReport,
    evaluate_static_html_quality,
    is_static_html_soft_signal_code,
    recommended_soft_fetch_error_code,
)
from services.orchestrator.app.acquisition.http_client import (
    AcquisitionPolicyError,
    HostResolver,
    HttpAcquisitionClient,
    HttpFetchResult,
    SocketHostResolver,
)
from services.orchestrator.app.acquisition.playwright_backend import (
    PlaywrightBrowserFetchBackend,
    build_playwright_browser_fetch_backend,
)
from services.orchestrator.app.acquisition.smoke import SmokeAcquisitionClient

__all__ = [
    "AcquisitionPolicyError",
    "BROWSER_FETCH_BACKEND_MCP",
    "BROWSER_FETCH_BACKEND_NONE",
    "BROWSER_FETCH_BACKEND_PLAYWRIGHT",
    "BrowserFetchBackend",
    "build_playwright_browser_fetch_backend",
    "STATIC_HTML_SOFT_SIGNAL_CODES",
    "TRACE_ELIGIBLE_FOR_EVIDENCE_PARSE",
    "TRACE_PARSE_HOLD_REASON",
    "TRACE_STATIC_HTML_QUALITY_DECISION",
    "HostResolver",
    "HttpAcquisitionClient",
    "HttpFetchResult",
    "SocketHostResolver",
    "PlaywrightBrowserFetchBackend",
    "SmokeAcquisitionClient",
    "StaticHtmlQualityReport",
    "apply_static_html_quality_gate",
    "classify_http_response",
    "classify_httpx_request_error",
    "evaluate_static_html_quality",
    "finalize_static_fetch_result",
    "is_static_html_soft_signal_code",
    "recommended_soft_fetch_error_code",
    "refine_http_fetch_result",
    "should_attempt_browser_fallback",
]
