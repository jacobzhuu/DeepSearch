"""Playwright-backed browser fetch for acquisition (bounded, deterministic)."""

from __future__ import annotations

import hashlib
import importlib.util
import time
from collections.abc import Mapping
from typing import Any

from services.orchestrator.app.acquisition.browser_backend import BrowserFetchBackend
from services.orchestrator.app.acquisition.browser_navigation_policy import (
    validate_browser_subresource_url,
)
from services.orchestrator.app.acquisition.http_client import (
    AcquisitionPolicyError,
    HttpAcquisitionClient,
    HttpFetchResult,
)
from services.orchestrator.app.settings import Settings

_PLAYWRIGHT_WAIT_UNTIL_ALLOWED = frozenset({"commit", "domcontentloaded", "load", "networkidle"})
_MAX_BLOCKED_REQUEST_SAMPLES = 24


class PlaywrightBrowserFetchBackend:
    """
    Headless Chromium fetch returning ``HttpFetchResult`` for the static snapshot pipeline.

    Interactions are limited to navigation, bounded scrolling, and read-only DOM capture.
    """

    name = "playwright"

    def __init__(
        self,
        http_client: HttpAcquisitionClient,
        *,
        timeout_seconds: float,
        max_scrolls: int,
        wait_until: str,
        capture_screenshot: bool,
    ) -> None:
        self._http_client = http_client
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._max_scrolls = max(0, int(max_scrolls))
        normalized = (wait_until or "domcontentloaded").strip().lower()
        self._wait_until = (
            normalized if normalized in _PLAYWRIGHT_WAIT_UNTIL_ALLOWED else "domcontentloaded"
        )
        self._capture_screenshot = bool(capture_screenshot)

    def fetch_rendered(
        self,
        url: str,
        *,
        trace_context: Mapping[str, Any] | None = None,
    ) -> HttpFetchResult:
        ctx = dict(trace_context) if isinstance(trace_context, dict) else {}
        try:
            policy_trace = self._http_client.validate_fetch_target(url)
        except AcquisitionPolicyError as error:
            return HttpFetchResult(
                requested_url=url,
                final_url=None,
                http_status=error.http_status,
                error_code=error.error_code,
                mime_type=None,
                content=None,
                content_hash=None,
                trace={
                    "acquisition_channel": "browser_playwright",
                    "requested_url": url,
                    **ctx,
                    **error.trace,
                },
            )

        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

        timeout_ms = int(self._timeout_seconds * 1000)
        blocked_samples: list[dict[str, str]] = []

        def _route_handler(route: Any) -> None:
            req_url = route.request.url
            try:
                validate_browser_subresource_url(self._http_client, req_url)
            except AcquisitionPolicyError as error:
                if len(blocked_samples) < _MAX_BLOCKED_REQUEST_SAMPLES:
                    blocked_samples.append(
                        {
                            "url": req_url[:800],
                            "error_code": error.error_code,
                        }
                    )
                route.abort()
                return
            route.continue_()

        diagnostics: dict[str, Any] = {
            "acquisition_channel": "browser_playwright",
            "requested_url": url,
            "browser_wait_until": self._wait_until,
            "browser_timeout_ms": timeout_ms,
            "browser_max_scrolls": self._max_scrolls,
            **policy_trace,
            **ctx,
        }

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(
                        user_agent=self._http_client.user_agent,
                        extra_http_headers={
                            "Accept-Language": self._http_client.accept_language,
                        },
                        viewport={"width": 1280, "height": 720},
                    )
                    context.route("**/*", _route_handler)
                    page = context.new_page()
                    response = page.goto(url, wait_until=self._wait_until, timeout=timeout_ms)
                    http_status = int(response.status) if response is not None else None

                    try:
                        validate_browser_subresource_url(self._http_client, page.url)
                    except AcquisitionPolicyError as error:
                        return HttpFetchResult(
                            requested_url=url,
                            final_url=page.url,
                            http_status=http_status,
                            error_code=error.error_code,
                            mime_type=None,
                            content=None,
                            content_hash=None,
                            trace={
                                **diagnostics,
                                "final_url": page.url,
                                "browser_blocked_request_count": len(blocked_samples),
                                "browser_blocked_request_reasons": list(blocked_samples),
                                "browser_final_url_policy_block": True,
                                **error.trace,
                            },
                        )

                    for _ in range(self._max_scrolls):
                        page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.9))")
                        time.sleep(0.15)

                    final_url = page.url
                    try:
                        validate_browser_subresource_url(self._http_client, final_url)
                    except AcquisitionPolicyError as error:
                        return HttpFetchResult(
                            requested_url=url,
                            final_url=final_url,
                            http_status=http_status,
                            error_code=error.error_code,
                            mime_type=None,
                            content=None,
                            content_hash=None,
                            trace={
                                **diagnostics,
                                "final_url": final_url,
                                "browser_blocked_request_count": len(blocked_samples),
                                "browser_blocked_request_reasons": list(blocked_samples),
                                "browser_final_url_policy_block": True,
                                **error.trace,
                            },
                        )

                    title = page.title()
                    html = page.content()
                    try:
                        visible_text = page.inner_text("body", timeout=min(5000, timeout_ms))
                    except Exception:
                        visible_text = ""

                    screenshot_sha256: str | None = None
                    if self._capture_screenshot:
                        png = page.screenshot(full_page=False, type="png")
                        screenshot_sha256 = f"sha256:{hashlib.sha256(png).hexdigest()}"
                        diagnostics["browser_screenshot_sha256"] = screenshot_sha256
                        diagnostics["browser_screenshot_bytes"] = len(png)

                    raw = html.encode("utf-8")
                    if len(raw) > self._http_client.max_response_bytes:
                        return HttpFetchResult(
                            requested_url=url,
                            final_url=final_url,
                            http_status=http_status,
                            error_code="body_too_large",
                            mime_type="text/html",
                            content=None,
                            content_hash=None,
                            trace={
                                **diagnostics,
                                "final_url": final_url,
                                "browser_title": title,
                                "response_bytes": len(raw),
                                "max_response_bytes": self._http_client.max_response_bytes,
                                "browser_blocked_request_count": len(blocked_samples),
                                "browser_blocked_request_reasons": list(blocked_samples),
                            },
                        )

                    digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
                    visible_sample = visible_text.strip()
                    if len(visible_sample) > 8192:
                        visible_sample = visible_sample[:8192] + "…"

                    trace = {
                        **diagnostics,
                        "final_url": final_url,
                        "browser_title": title,
                        "browser_visible_text_sample": visible_sample,
                        "response_bytes": len(raw),
                        "browser_blocked_request_count": len(blocked_samples),
                        "browser_blocked_request_reasons": list(blocked_samples),
                    }
                    return HttpFetchResult(
                        requested_url=url,
                        final_url=final_url,
                        http_status=http_status,
                        error_code=None,
                        mime_type="text/html",
                        content=raw,
                        content_hash=digest,
                        trace=trace,
                    )
                finally:
                    browser.close()
        except Exception as error:  # noqa: BLE001 — surface as acquisition failure, not crash
            return HttpFetchResult(
                requested_url=url,
                final_url=None,
                http_status=None,
                error_code="browser_fetch_failed",
                mime_type=None,
                content=None,
                content_hash=None,
                trace={
                    **diagnostics,
                    "exception_type": type(error).__name__,
                    "message": str(error),
                },
            )


def build_playwright_browser_fetch_backend(
    settings: Settings,
    http_client: HttpAcquisitionClient,
) -> BrowserFetchBackend | None:
    """
    Instantiate Playwright backend when ``BROWSER_FETCH_BACKEND=playwright``.

    Returns ``None`` when Playwright is not installed or the setting is not ``playwright``.
    """
    backend = settings.browser_fetch_backend.strip().lower()
    if backend != "playwright":
        return None
    if importlib.util.find_spec("playwright") is None:
        return None
    return PlaywrightBrowserFetchBackend(
        http_client,
        timeout_seconds=float(settings.browser_fetch_timeout_seconds),
        max_scrolls=int(settings.browser_fetch_max_scrolls),
        wait_until=str(settings.browser_fetch_wait_until),
        capture_screenshot=bool(settings.browser_fetch_capture_screenshot),
    )
