from __future__ import annotations

from packages.observability import (
    observe_http_request,
    record_fetch_results,
    record_parse_results,
    record_report_result,
    record_task_command,
    record_verify_results,
    render_metrics,
)


def test_render_metrics_includes_phase10_counters() -> None:
    record_task_command(action="create", status="PLANNED")
    record_fetch_results(created=1, skipped_existing=0, succeeded=1, failed=0)
    record_parse_results(
        created=1,
        updated=0,
        skipped_existing=0,
        skipped_unsupported=0,
        failed=0,
    )
    record_verify_results(verification_statuses=["supported", "mixed"])
    record_report_result(reused_existing=False, format="markdown")
    observe_http_request(
        method="GET",
        path="/healthz",
        status_code=200,
        duration_seconds=0.01,
    )

    payload, content_type = render_metrics()
    rendered = payload.decode("utf-8")

    assert content_type.startswith("text/plain")
    assert "deepresearch_http_requests_total" in rendered
    assert "deepresearch_task_commands_total" in rendered
    assert "deepresearch_fetch_results_total" in rendered
    assert "deepresearch_parse_results_total" in rendered
    assert "deepresearch_verify_results_total" in rendered
    assert "deepresearch_report_results_total" in rendered
