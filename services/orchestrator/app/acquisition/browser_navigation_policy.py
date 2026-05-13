"""SSRF checks for Playwright navigation and subresource URLs (http/https only)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from services.orchestrator.app.acquisition.http_client import (
    AcquisitionPolicyError,
    HttpAcquisitionClient,
)


def validate_browser_subresource_url(
    http_client: HttpAcquisitionClient,
    url: str,
) -> dict[str, Any]:
    """
    Validate a URL before allowing the browser to load it.

    Delegates to ``HttpAcquisitionClient.validate_fetch_target`` for DNS + IP policy.
    Rejects non-http(s) schemes without DNS (e.g. ``file:``, ``javascript:``).
    """
    parsed = urlsplit(url.strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise AcquisitionPolicyError(
            error_code="unsupported_scheme",
            trace={"requested_url": url, "scheme": parsed.scheme, "browser_subresource": True},
        )
    return http_client.validate_fetch_target(url)
