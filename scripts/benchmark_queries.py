#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class BenchmarkQuery:
    query: str
    capabilities: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "capabilities": list(self.capabilities),
        }


BENCHMARK_QUERIES: tuple[BenchmarkQuery, ...] = (
    BenchmarkQuery(
        "What is SearXNG and how does it work?",
        (
            "overview_planning",
            "official_reference_balance",
            "mechanism_claims",
            "privacy_noise_filtering",
        ),
    ),
    BenchmarkQuery(
        "What is OpenSearch and how does it work?",
        (
            "non_searxng_generalization",
            "official_docs_selection",
            "mechanism_claims",
        ),
    ),
    BenchmarkQuery(
        "What is LangGraph and how does it work?",
        (
            "framework_docs_selection",
            "github_vs_docs_balance",
            "install_noise_filtering",
        ),
    ),
    BenchmarkQuery(
        "What is Model Context Protocol and how does it work?",
        (
            "protocol_spec_sources",
            "emerging_topic_source_authority",
            "terminology_extraction",
        ),
    ),
    BenchmarkQuery(
        "What is Dify and how does it work?",
        (
            "product_platform_sources",
            "docs_github_community_filtering",
            "overview_slots",
        ),
    ),
    BenchmarkQuery(
        "What are the privacy advantages and limitations of SearXNG?",
        (
            "privacy_slots",
            "limitation_coverage",
            "overclaim_prevention",
        ),
    ),
    BenchmarkQuery(
        "How can SearXNG be deployed with Docker?",
        (
            "deployment_intent",
            "install_source_promotion",
            "docker_specific_evidence",
        ),
    ),
    BenchmarkQuery(
        "Compare SearXNG, Brave Search API, and Tavily for AI research agents.",
        (
            "comparison_slots",
            "source_diversity",
            "vendor_docs_balance",
        ),
    ),
    BenchmarkQuery(
        "What is Retrieval-Augmented Generation and what are its limitations?",
        (
            "conceptual_research",
            "limitation_coverage",
            "non_product_source_selection",
        ),
    ),
    BenchmarkQuery(
        "What are the main differences between ChatGPT Deep Research and Gemini Deep Research?",
        (
            "current_product_comparison",
            "freshness_sensitive_sources",
            "citation_traceability",
        ),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List or run the minimum DeepSearch generalization benchmark queries."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--run", action="store_true", help="Create and run tasks through the API.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--limit", type=int, default=len(BENCHMARK_QUERIES))
    parser.add_argument("--query-id", type=int, dest="query_id")
    parser.add_argument("--only", type=int, dest="query_id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        selected = _select_benchmark_queries(limit=args.limit, query_id=args.query_id)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if not args.run:
        payload = {
            "mode": "list",
            "benchmark_queries": [item.to_payload() for item in selected],
        }
        _print_payload(payload, as_json=args.json, output_path=args.output)
        return 0

    results = _run_benchmark(
        base_url=args.base_url,
        queries=selected,
        timeout_seconds=args.timeout_seconds,
    )
    payload = {
        "mode": "run",
        "base_url": args.base_url,
        "benchmark_queries": results,
    }
    _print_payload(payload, as_json=args.json, output_path=args.output)
    return 0 if all(item["completed"] for item in results) else 1


def _select_benchmark_queries(
    *,
    limit: int,
    query_id: int | None = None,
) -> tuple[BenchmarkQuery, ...]:
    if query_id is not None:
        if query_id < 1 or query_id > len(BENCHMARK_QUERIES):
            raise ValueError(f"--query-id must be between 1 and {len(BENCHMARK_QUERIES)}")
        return (BENCHMARK_QUERIES[query_id - 1],)
    return BENCHMARK_QUERIES[: max(0, min(limit, len(BENCHMARK_QUERIES)))]


def _run_benchmark(
    *,
    base_url: str,
    queries: tuple[BenchmarkQuery, ...],
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=timeout_seconds,
        trust_env=False,
    ) as client:
        for benchmark in queries:
            task = _request_json(
                client,
                "POST",
                "/api/v1/research/tasks",
                json={"query": benchmark.query, "constraints": {}},
            )
            task_id = _required_str(task, "task_id")
            run = _request_json(client, "POST", f"/api/v1/research/tasks/{task_id}/run")
            detail = _request_json(client, "GET", f"/api/v1/research/tasks/{task_id}")
            completed = bool(run.get("completed"))
            observability = _observability_from_detail(detail)
            rows.append(
                {
                    **benchmark.to_payload(),
                    "task_id": task_id,
                    "completed": completed,
                    "status": run.get("status"),
                    "running_mode": run.get("running_mode"),
                    "counts": run.get("counts"),
                    "failure": run.get("failure"),
                    "slot_coverage_summary": _list_or_empty(
                        observability.get("slot_coverage_summary")
                    ),
                    "source_yield_summary": _list_or_empty(
                        observability.get("source_yield_summary")
                    ),
                    "evidence_yield_summary": _dict_or_empty(
                        observability.get("evidence_yield_summary")
                    ),
                    "verification_summary": _dict_or_empty(
                        observability.get("verification_summary")
                    ),
                    "contamination_check": _contamination_check(
                        benchmark.query,
                        observability=observability,
                    ),
                }
            )
    return rows


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json: dict[str, object] | None = None,
) -> dict[str, Any]:
    response = client.request(method, path, json=json)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object from {path}, got {payload!r}")
    return payload


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"expected non-empty string at {key!r}, got {value!r}")
    return value


def _observability_from_detail(payload: dict[str, Any]) -> dict[str, Any]:
    progress = payload.get("progress")
    if not isinstance(progress, dict):
        return {}
    observability = progress.get("observability")
    return observability if isinstance(observability, dict) else {}


def _list_or_empty(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict_or_empty(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _contamination_check(query: str, *, observability: dict[str, Any]) -> dict[str, Any]:
    is_searxng_query = "searxng" in query.lower()
    source_rows: list[dict[str, Any]] = []
    for key in ("selected_sources", "source_yield_summary", "attempted_sources"):
        value = observability.get(key)
        if isinstance(value, list):
            source_rows.extend(item for item in value if isinstance(item, dict))
    searxng_source_count = sum(
        1
        for row in source_rows
        if "searxng"
        in str(row.get("canonical_url") or row.get("url") or row.get("domain") or "").lower()
    )
    return {
        "checked": not is_searxng_query,
        "passed": is_searxng_query or searxng_source_count == 0,
        "searxng_source_count": searxng_source_count,
    }


def _print_payload(
    payload: dict[str, Any],
    *,
    as_json: bool,
    output_path: Path | None = None,
) -> None:
    if output_path is not None:
        output_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if as_json:
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        return
    if payload["mode"] == "list":
        for index, item in enumerate(payload["benchmark_queries"], start=1):
            print(f"{index}. {item['query']}")
            print(f"   capabilities: {', '.join(item['capabilities'])}")
        return
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
