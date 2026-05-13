from __future__ import annotations

import socket
import ssl

import httpx

from services.orchestrator.app.acquisition.failure_classification import (
    classify_http_response,
    classify_httpx_request_error,
    normalize_redirect_loop_code,
)


def test_classify_http_response_empty_2xx() -> None:
    code, strip = classify_http_response(http_status=200, mime_type="text/html", body_len=0)
    assert code == "empty_response"
    assert strip is True


def test_classify_http_response_binary_2xx() -> None:
    code, strip = classify_http_response(http_status=200, mime_type="image/png", body_len=10)
    assert code == "unsupported_mime"
    assert strip is True


def test_classify_http_response_403_429() -> None:
    assert classify_http_response(http_status=403, mime_type="text/html", body_len=12) == (
        "status_403",
        False,
    )
    assert classify_http_response(http_status=429, mime_type="text/html", body_len=1) == (
        "status_429",
        False,
    )


def test_classify_httpx_connect_timeout() -> None:
    exc = httpx.ConnectTimeout("timed out", request=httpx.Request("GET", "https://example.com"))
    assert classify_httpx_request_error(exc) == "timeout_connect"


def test_classify_httpx_read_timeout() -> None:
    exc = httpx.ReadTimeout("timed out", request=httpx.Request("GET", "https://example.com"))
    assert classify_httpx_request_error(exc) == "timeout_read"


def test_classify_httpx_ssl_wrapped_connect_error() -> None:
    inner = ssl.SSLError("certificate verify failed")
    exc = httpx.ConnectError("ssl failed", request=httpx.Request("GET", "https://example.com"))
    exc.__cause__ = inner
    assert classify_httpx_request_error(exc) == "ssl_error"


def test_classify_httpx_dns_via_gaierror() -> None:
    inner = socket.gaierror(8, "nodename nor servname provided, or not known")
    exc = httpx.ConnectError("dns", request=httpx.Request("GET", "https://missing.invalid"))
    exc.__cause__ = inner
    assert classify_httpx_request_error(exc) == "dns_error"


def test_normalize_redirect_loop_code() -> None:
    assert normalize_redirect_loop_code("too_many_redirects") == "redirect_loop"
    assert normalize_redirect_loop_code("redirect_loop") == "redirect_loop"


def test_should_attempt_browser_fallback() -> None:
    from services.orchestrator.app.acquisition.browser_backend import (
        should_attempt_browser_fallback,
    )

    assert should_attempt_browser_fallback(
        error_code="status_403",
        trace_json={},
        browser_fetch_backend="playwright",
    )
    assert should_attempt_browser_fallback(
        error_code="status_429",
        trace_json={},
        browser_fetch_backend="mcp",
    )
    assert should_attempt_browser_fallback(
        error_code="empty_response",
        trace_json={},
        browser_fetch_backend="playwright",
    )
    assert should_attempt_browser_fallback(
        error_code="javascript_required",
        trace_json={},
        browser_fetch_backend="playwright",
    )
    assert should_attempt_browser_fallback(
        error_code=None,
        trace_json={"static_html_quality_decision": "spa_shell"},
        browser_fetch_backend="playwright",
    )
    assert should_attempt_browser_fallback(
        error_code=None,
        trace_json={"static_html_quality_decision": "javascript_required"},
        browser_fetch_backend="playwright",
    )
    assert not should_attempt_browser_fallback(
        error_code="spa_shell",
        trace_json={},
        browser_fetch_backend="none",
    )
    assert not should_attempt_browser_fallback(
        error_code=None,
        trace_json={"static_html_quality_decision": "spa_shell"},
        browser_fetch_backend="none",
    )
    assert not should_attempt_browser_fallback(
        error_code=None,
        trace_json={},
        browser_fetch_backend="mcp",
    )


def test_should_attempt_browser_fallback_false_for_policy_failures() -> None:
    from services.orchestrator.app.acquisition.browser_backend import (
        should_attempt_browser_fallback,
    )

    for code in (
        "unsupported_mime",
        "body_too_large",
        "target_blocked",
        "dns_resolution_failed",
        "unsupported_scheme",
        "invalid_target",
    ):
        assert not should_attempt_browser_fallback(
            error_code=code,
            trace_json={},
            browser_fetch_backend="playwright",
        ), code
