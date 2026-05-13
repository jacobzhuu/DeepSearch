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
_FETCH_FAILURE_CLASS_TOTAL = Counter(
    "deepresearch_fetch_failure_class_total",
    "Fetch attempts by normalized failure class (ok when error_code is null).",
    labelnames=("code",),
)
_BROWSER_FALLBACK_CONSIDERED_TOTAL = Counter(
    "deepresearch_browser_fallback_considered_total",
    "Browser fallback decision evaluated after static fetch (requires non-none backend setting).",
    labelnames=("reason",),
)
_BROWSER_FALLBACK_ATTEMPTED_TOTAL = Counter(
    "deepresearch_browser_fallback_attempted_total",
    "Browser fallback render attempts started.",
    labelnames=("backend",),
)
_BROWSER_FALLBACK_SUCCEEDED_TOTAL = Counter(
    "deepresearch_browser_fallback_succeeded_total",
    "Browser fallback attempts that ended without fetch_attempt.error_code.",
    labelnames=("backend",),
)
_BROWSER_FALLBACK_FAILED_TOTAL = Counter(
    "deepresearch_browser_fallback_failed_total",
    "Browser fallback attempts that ended with an error_code.",
    labelnames=("backend", "code"),
)
_BROWSER_FALLBACK_SKIPPED_TOTAL = Counter(
    "deepresearch_browser_fallback_skipped_total",
    "Browser fallback not attempted after evaluation.",
    labelnames=("reason",),
)


def record_browser_fallback_considered(*, reason: str) -> None:
    _BROWSER_FALLBACK_CONSIDERED_TOTAL.labels(reason=reason or "unknown").inc()


def record_browser_fallback_attempted(*, backend: str) -> None:
    _BROWSER_FALLBACK_ATTEMPTED_TOTAL.labels(backend=backend or "unknown").inc()


def record_browser_fallback_succeeded(*, backend: str) -> None:
    _BROWSER_FALLBACK_SUCCEEDED_TOTAL.labels(backend=backend or "unknown").inc()


def record_browser_fallback_failed(*, backend: str, code: str) -> None:
    _BROWSER_FALLBACK_FAILED_TOTAL.labels(
        backend=backend or "unknown",
        code=code or "unknown",
    ).inc()


def record_browser_fallback_skipped(*, reason: str) -> None:
    _BROWSER_FALLBACK_SKIPPED_TOTAL.labels(reason=reason or "unknown").inc()


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


def record_fetch_failure_class(*, code: str | None) -> None:
    label = "ok" if code is None or not str(code).strip() else str(code).strip()
    _FETCH_FAILURE_CLASS_TOTAL.labels(code=label).inc()


def record_parse_results(
    *,
    created: int,
    updated: int,
    skipped_existing: int,
    skipped_unsupported: int,
    skipped_static_html_hold: int = 0,
    failed: int,
) -> None:
    _PARSE_RESULTS_TOTAL.labels(status="CREATED", reason="none").inc(created)
    _PARSE_RESULTS_TOTAL.labels(status="UPDATED", reason="none").inc(updated)
    _PARSE_RESULTS_TOTAL.labels(status="SKIPPED", reason="already_parsed").inc(skipped_existing)
    _PARSE_RESULTS_TOTAL.labels(status="SKIPPED", reason="unsupported_mime_type").inc(
        skipped_unsupported
    )
    _PARSE_RESULTS_TOTAL.labels(status="SKIPPED", reason="static_html_acquire_hold").inc(
        skipped_static_html_hold
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
