from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

_HTTP_REQUESTS_TOTAL = Counter(
    "deepresearch_http_requests_total",
    "Total HTTP requests handled by the orchestrator.",
    labelnames=("method", "path", "status_code"),
)
_HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "deepresearch_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    labelnames=("method", "path"),
)
_TASK_COMMANDS_TOTAL = Counter(
    "deepresearch_task_commands_total",
    "Task command completions by action and resulting status.",
    labelnames=("action", "status"),
)
_FETCH_RESULTS_TOTAL = Counter(
    "deepresearch_fetch_results_total",
    "Fetch pipeline batch result counters.",
    labelnames=("result",),
)
_PARSE_RESULTS_TOTAL = Counter(
    "deepresearch_parse_results_total",
    "Parse pipeline entry results by status and reason.",
    labelnames=("status", "reason"),
)
_VERIFY_RESULTS_TOTAL = Counter(
    "deepresearch_verify_results_total",
    "Verification outcomes by final claim status.",
    labelnames=("verification_status",),
)
_REPORT_RESULTS_TOTAL = Counter(
    "deepresearch_report_results_total",
    "Report generation outcomes by reuse mode and format.",
    labelnames=("result", "format"),
)


def observe_http_request(
    *,
    method: str,
    path: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    normalized_method = method.upper()
    normalized_path = path or "/"
    _HTTP_REQUESTS_TOTAL.labels(
        method=normalized_method,
        path=normalized_path,
        status_code=str(status_code),
    ).inc()
    _HTTP_REQUEST_DURATION_SECONDS.labels(
        method=normalized_method,
        path=normalized_path,
    ).observe(duration_seconds)


def record_task_command(*, action: str, status: str) -> None:
    _TASK_COMMANDS_TOTAL.labels(action=action, status=status).inc()


def record_fetch_results(
    *,
    created: int,
    skipped_existing: int,
    succeeded: int,
    failed: int,
) -> None:
    _FETCH_RESULTS_TOTAL.labels(result="created").inc(created)
    _FETCH_RESULTS_TOTAL.labels(result="skipped_existing").inc(skipped_existing)
    _FETCH_RESULTS_TOTAL.labels(result="succeeded").inc(succeeded)
    _FETCH_RESULTS_TOTAL.labels(result="failed").inc(failed)


def record_parse_results(
    *,
    created: int,
    updated: int,
    skipped_existing: int,
    skipped_unsupported: int,
    failed: int,
) -> None:
    _PARSE_RESULTS_TOTAL.labels(status="CREATED", reason="none").inc(created)
    _PARSE_RESULTS_TOTAL.labels(status="UPDATED", reason="none").inc(updated)
    _PARSE_RESULTS_TOTAL.labels(status="SKIPPED", reason="already_parsed").inc(skipped_existing)
    _PARSE_RESULTS_TOTAL.labels(status="SKIPPED", reason="unsupported_mime_type").inc(
        skipped_unsupported
    )
    _PARSE_RESULTS_TOTAL.labels(status="FAILED", reason="other").inc(failed)


def record_verify_results(*, verification_statuses: list[str]) -> None:
    for verification_status in verification_statuses:
        _VERIFY_RESULTS_TOTAL.labels(verification_status=verification_status).inc()


def record_report_result(*, reused_existing: bool, format: str) -> None:
    _REPORT_RESULTS_TOTAL.labels(
        result="reused" if reused_existing else "generated",
        format=format,
    ).inc()


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
