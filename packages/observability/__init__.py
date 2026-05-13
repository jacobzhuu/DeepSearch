"""Minimal Phase 10 logging and metrics helpers."""

from packages.observability.logging import configure_logging, get_logger
from packages.observability.metrics import (
    observe_http_request,
    record_browser_fallback_attempted,
    record_browser_fallback_considered,
    record_browser_fallback_failed,
    record_browser_fallback_skipped,
    record_browser_fallback_succeeded,
    record_fetch_failure_class,
    record_fetch_results,
    record_parse_results,
    record_report_result,
    record_task_command,
    record_verify_results,
    render_metrics,
)

__all__ = [
    "configure_logging",
    "get_logger",
    "observe_http_request",
    "record_browser_fallback_attempted",
    "record_browser_fallback_considered",
    "record_browser_fallback_failed",
    "record_browser_fallback_skipped",
    "record_browser_fallback_succeeded",
    "record_fetch_failure_class",
    "record_fetch_results",
    "record_parse_results",
    "record_report_result",
    "record_task_command",
    "record_verify_results",
    "render_metrics",
]
