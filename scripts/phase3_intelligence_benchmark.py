#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

ACTIVE_STATUSES = {
    "QUEUED",
    "RUNNING",
    "SEARCHING",
    "ACQUIRING",
    "PARSING",
    "INDEXING",
    "DRAFTING_CLAIMS",
    "VERIFYING",
    "RESEARCHING_MORE",
    "REPORTING",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run DeepSearch Phase 3 intelligence benchmark.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--query", default="What is SearXNG and how does it work?")
    parser.add_argument("--wait-seconds", type=float, default=420.0)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = run_benchmark(
        base_url=args.base_url,
        query=args.query,
        wait_seconds=args.wait_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    markdown = render_markdown(payload)
    if args.json_output is not None:
        args.json_output.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.markdown_output is not None:
        args.markdown_output.write_text(markdown, encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True) if args.json else markdown)
    return 0


def run_benchmark(
    *,
    base_url: str,
    query: str,
    wait_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    with httpx.Client(
        base_url=base_url.rstrip("/"), timeout=timeout_seconds, trust_env=False
    ) as client:
        task = request_json(
            client,
            "POST",
            "/api/v1/research/tasks",
            json={"query": query, "constraints": {}},
        )
        task_id = str(task["task_id"])
        plan = request_json(client, "POST", f"/api/v1/research/tasks/{task_id}/plan")
        plan_readback = request_json(client, "GET", f"/api/v1/research/tasks/{task_id}/plan")
        run_payload = request_json(client, "POST", f"/api/v1/research/tasks/{task_id}/run")
        detail = wait_for_task(client, task_id, wait_seconds=wait_seconds)
        claims = optional_request_json(client, "GET", f"/api/v1/research/tasks/{task_id}/claims")
        report = optional_request_json(client, "GET", f"/api/v1/research/tasks/{task_id}/report")
    result = build_result(
        query=query,
        task_id=task_id,
        status=str(detail.get("status") or run_payload.get("status") or "UNKNOWN"),
        plan=plan,
        plan_readback=plan_readback,
        detail=detail,
        claims=claims,
        report=report,
    )
    return {"benchmark": "phase3_intelligence_v1", "base_url": base_url, "result": result}


def build_result(
    *,
    query: str,
    task_id: str,
    status: str,
    plan: dict[str, Any],
    plan_readback: dict[str, Any],
    detail: dict[str, Any],
    claims: dict[str, Any] | None,
    report: dict[str, Any] | None,
) -> dict[str, Any]:
    observability = dict_or_empty(dict_or_empty(detail.get("progress")).get("observability"))
    claim_rows = list_or_empty(dict_or_empty(claims).get("claims"))
    status_counts: dict[str, int] = {}
    for claim in claim_rows:
        status_name = str(dict_or_empty(claim).get("verification_status") or "unknown")
        status_counts[status_name] = status_counts.get(status_name, 0) + 1
    source_judgments = list_or_empty(observability.get("source_judgments"))
    gap_rounds = list_or_empty(observability.get("gap_rounds"))
    return {
        "query": query,
        "task_id": task_id,
        "status": status,
        "completed": status == "COMPLETED",
        "plan_created": bool(plan.get("research_plan")),
        "plan_readback_available": bool(plan_readback.get("research_plan")),
        "planner_status": plan.get("planner_status"),
        "plan_source": plan.get("plan_source"),
        "gap_count": _gap_count(observability),
        "gap_round_count": len(gap_rounds),
        "query_iterations": len(gap_rounds),
        "source_judge_count": len(source_judgments),
        "source_judge_fallback_count": sum(
            1 for item in source_judgments if dict_or_empty(item).get("fallback_status") != "none"
        ),
        "claim_status_distribution": status_counts,
        "unsupported_claim_count": status_counts.get("unsupported", 0),
        "mixed_or_contradicted_claim_count": status_counts.get("mixed", 0)
        + status_counts.get("contradicted", 0),
        "citation_coverage": _citation_coverage(report),
        "report_artifact_type": dict_or_empty(report).get("format"),
        "llm_fallback_or_error_behavior": _llm_fallback_summary(observability),
    }


def _gap_count(observability: dict[str, Any]) -> int:
    slot_rows = list_or_empty(observability.get("slot_coverage_summary"))
    return sum(
        1
        for row in slot_rows
        if dict_or_empty(row).get("required") is True
        and dict_or_empty(row).get("status") in {"missing", "weak"}
    )


def _citation_coverage(report: dict[str, Any] | None) -> float | None:
    manifest = dict_or_empty(dict_or_empty(report).get("manifest"))
    value = manifest.get("citation_coverage")
    return float(value) if isinstance(value, int | float) else None


def _llm_fallback_summary(observability: dict[str, Any]) -> dict[str, Any]:
    warnings = list_or_empty(observability.get("warnings"))
    source_judgments = list_or_empty(observability.get("source_judgments"))
    return {
        "planner_status": observability.get("planner_status"),
        "plan_source": observability.get("plan_source"),
        "warnings": warnings,
        "source_judge_fallback_statuses": sorted(
            {
                str(dict_or_empty(item).get("fallback_status"))
                for item in source_judgments
                if dict_or_empty(item).get("fallback_status")
            }
        ),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    result = dict_or_empty(payload.get("result"))
    return "\n".join(
        [
            "# Phase 3 Intelligence Benchmark",
            "",
            f"- query: {result.get('query')}",
            f"- status: {result.get('status')}",
            f"- plan readback: {result.get('plan_readback_available')}",
            f"- gap count: {result.get('gap_count')}",
            f"- query iterations: {result.get('query_iterations')}",
            f"- source judge results: {result.get('source_judge_count')}",
            f"- unsupported claims: {result.get('unsupported_claim_count')}",
            f"- mixed/contradicted claims: {result.get('mixed_or_contradicted_claim_count')}",
            f"- citation coverage: {result.get('citation_coverage')}",
            "",
        ]
    )


def wait_for_task(client: httpx.Client, task_id: str, *, wait_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + wait_seconds
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = request_json(client, "GET", f"/api/v1/research/tasks/{task_id}")
        if str(latest.get("status") or "") not in ACTIVE_STATUSES:
            return latest
        time.sleep(2)
    return latest


def request_json(client: httpx.Client, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{method} {path} did not return a JSON object")
    return payload


def optional_request_json(
    client: httpx.Client,
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any] | None:
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        return None
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def list_or_empty(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def dict_or_empty(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
