"""Normalize fetch-layer failure codes for diagnostics and future browser fallback."""

from __future__ import annotations

import socket
import ssl
from typing import Any

import httpx

# Stable codes consumed by metrics, APIs, and future BrowserFetchBackend routing.
KNOWN_FETCH_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "status_403",
        "status_429",
        "timeout_connect",
        "timeout_read",
        "ssl_error",
        "dns_error",
        "redirect_loop",
        "body_too_large",
        "unsupported_mime",
        "empty_response",
        "javascript_required",
        "spa_shell",
        "cookie_wall",
        "bot_check",
        "http_error_status",
        "network_error",
        "dns_resolution_failed",
        "target_blocked",
        "unsupported_scheme",
        "invalid_target",
        "too_many_redirects",
        "storage_write_failed",
        "browser_fetch_failed",
    }
)


def classify_httpx_request_error(error: httpx.RequestError) -> str:
    """Map httpx transport failures to stable acquisition error_code values."""
    if isinstance(error, httpx.ConnectTimeout):
        return "timeout_connect"
    if isinstance(error, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return "timeout_read"
    if isinstance(error, httpx.ConnectError):
        cause = getattr(error, "__cause__", None)
        if isinstance(cause, ssl.SSLError):
            return "ssl_error"
        if isinstance(cause, socket.gaierror | OSError):
            if isinstance(cause, socket.gaierror):
                return "dns_error"
            errno = getattr(cause, "errno", None)
            if errno in {socket.EAI_NONAME, socket.EAI_FAIL}:
                return "dns_error"
        message = str(error).lower()
        if any(
            token in message
            for token in (
                "getaddrinfo failed",
                "name or service not known",
                "nodename nor servname",
                "temporary failure in name resolution",
                "could not resolve host",
            )
        ):
            return "dns_error"
        if "certificate verify failed" in message or "ssl" in message:
            return "ssl_error"
        return "network_error"
    if isinstance(error, httpx.ReadError):
        message = str(error).lower()
        if "ssl" in message or "tls" in message or "certificate" in message:
            return "ssl_error"
        return "timeout_read"
    return "network_error"


def classify_http_response(
    *,
    http_status: int,
    mime_type: str | None,
    body_len: int,
) -> tuple[str | None, bool]:
    """
    Classify HTTP response for acquisition.

    Returns ``(error_code, strip_body)`` where ``strip_body`` requests dropping bytes
    before snapshot persistence (2xx semantic failures only).
    """
    if 200 <= http_status < 300:
        if body_len == 0:
            return "empty_response", True
        if mime_type:
            primary = mime_type.split(";", 1)[0].strip().lower()
            if primary.startswith(("image/", "video/", "audio/", "font/")):
                return "unsupported_mime", True
        return None, False
    if http_status == 403:
        return "status_403", False
    if http_status == 429:
        return "status_429", False
    return "http_error_status", False


def normalize_redirect_loop_code(error_code: str | None) -> str | None:
    """Alias historical too_many_redirects to redirect_loop for metrics and dashboards."""
    if error_code == "too_many_redirects":
        return "redirect_loop"
    return error_code


def merge_trace(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update(patch)
    return merged
