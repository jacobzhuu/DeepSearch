"""Browser-backed acquisition seam (Playwright / MCP reserved; not implemented in Phase 4)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from services.orchestrator.app.acquisition.html_quality import STATIC_HTML_SOFT_SIGNAL_CODES
from services.orchestrator.app.acquisition.http_client import HttpFetchResult

# Values accepted by Settings.browser_fetch_backend (reserved).
BROWSER_FETCH_BACKEND_NONE = "none"
BROWSER_FETCH_BACKEND_PLAYWRIGHT = "playwright"
BROWSER_FETCH_BACKEND_MCP = "mcp"

# HTTP / transport outcomes where a rendered browser retry may help (never policy/binary gates).
BROWSER_FETCH_ALLOWED_ERROR_CODES: frozenset[str] = frozenset(
    {
        "status_403",
        "status_429",
        "empty_response",
        "spa_shell",
        "javascript_required",
        "cookie_wall",
        "bot_check",
    }
)

# Explicitly never trigger a browser retry for policy, SSRF, size, or MIME class failures.
BROWSER_FETCH_EXCLUDED_ERROR_CODES: frozenset[str] = frozenset(
    {
        "unsupported_mime",
        "body_too_large",
        "target_blocked",
        "unsupported_scheme",
        "invalid_target",
        "dns_resolution_failed",
        "storage_write_failed",
        "smoke_fixture_missing",
        "redirect_loop",
        "http_error_status",
        "network_error",
        "timeout_connect",
        "timeout_read",
        "ssl_error",
        "dns_error",
    }
)


@runtime_checkable
class BrowserFetchBackend(Protocol):
    """Future browser renderer implementing the same evidence contract as static HTTP."""

    name: str

    def fetch_rendered(
        self,
        url: str,
        *,
        trace_context: Mapping[str, Any] | None = None,
    ) -> HttpFetchResult:
        """
        Return an HttpFetchResult suitable for the existing snapshot pipeline.

        Implementations must respect acquisition policy (or delegate to a policy-aware client).
        A future pass should persist a **new** ``fetch_job``/``fetch_attempt`` (for example
        ``mode=BROWSER``) so operators can diff static ``content_snapshot`` bytes against a
        rendered snapshot without rewriting the static attempt.
        """


def should_attempt_browser_fallback(
    *,
    error_code: str | None,
    trace_json: dict[str, Any] | None,
    browser_fetch_backend: str,
) -> bool:
    """
    Whether a future orchestrator pass should retry with ``BrowserFetchBackend``.

    Uses ``fetch_attempt.error_code`` for transport/HTTP failures and
    ``trace_json["static_html_quality_decision"]`` for weak static HTML holds (static snapshot
    persisted, evidence parse blocked).

    Hook location: ``AcquisitionService._execute_candidate_fetch`` after the static attempt is
    committed, when ``settings.browser_fetch_backend`` is not ``none`` and a backend is wired in.
    """
    normalized = (browser_fetch_backend or BROWSER_FETCH_BACKEND_NONE).strip().lower()
    if normalized in {"", BROWSER_FETCH_BACKEND_NONE}:
        return False

    trace = trace_json if isinstance(trace_json, dict) else {}

    if error_code is not None and error_code in BROWSER_FETCH_EXCLUDED_ERROR_CODES:
        return False

    if error_code is not None and error_code in BROWSER_FETCH_ALLOWED_ERROR_CODES:
        return True

    if error_code is None:
        decision = trace.get("static_html_quality_decision")
        if isinstance(decision, str) and decision in STATIC_HTML_SOFT_SIGNAL_CODES:
            return True
        return False

    return False
