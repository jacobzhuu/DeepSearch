#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Phase2Question:
    query: str
    expected_coverage: str


QUESTIONS: tuple[Phase2Question, ...] = (
    Phase2Question("What is SearXNG and how does it work?", "html_plain_text"),
    Phase2Question("SearXNG documentation PDF filetype:pdf", "pdf"),
    Phase2Question("SearXNG presentation pptx OR docx OR xlsx", "office_or_attachment"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run DeepSearch Phase 2 multiformat benchmark.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=len(QUESTIONS))
    parser.add_argument("--wait-seconds", type=float, default=420.0)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    questions = QUESTIONS[: max(0, min(args.limit, len(QUESTIONS)))]
    results = run_benchmark(
        base_url=args.base_url,
        questions=questions,
        wait_seconds=args.wait_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    payload = {
        "benchmark": "phase2_multiformat_v1",
        "base_url": args.base_url,
        "results": results,
        "aggregate": aggregate_results(results),
    }
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
    questions: tuple[Phase2Question, ...],
    wait_seconds: float,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with httpx.Client(
        base_url=base_url.rstrip("/"), timeout=timeout_seconds, trust_env=False
    ) as client:
        for question in questions:
            task = request_json(
                client,
                "POST",
                "/api/v1/research/tasks",
                json={"query": question.query, "constraints": {}},
            )
            task_id = str(task["task_id"])
            run_payload = request_json(client, "POST", f"/api/v1/research/tasks/{task_id}/run")
            detail = wait_for_task(client, task_id, wait_seconds=wait_seconds)
            sources = request_json(client, "GET", f"/api/v1/research/tasks/{task_id}/sources")
            chunks = request_json(client, "GET", f"/api/v1/research/tasks/{task_id}/source-chunks")
            retrieval = optional_request_json(
                client,
                "GET",
                f"/api/v1/research/tasks/{task_id}/retrieve",
                params={"query": question.query, "limit": 10},
            )
            rows.append(
                build_row(
                    question=question,
                    task_id=task_id,
                    status=str(detail.get("status") or run_payload.get("status") or "UNKNOWN"),
                    detail=detail,
                    sources=sources,
                    chunks=chunks,
                    retrieval=retrieval,
                )
            )
    return rows


def build_row(
    *,
    question: Phase2Question,
    task_id: str,
    status: str,
    detail: dict[str, Any],
    sources: dict[str, Any],
    chunks: dict[str, Any],
    retrieval: dict[str, Any] | None,
) -> dict[str, Any]:
    source_rows = list_or_empty(sources.get("sources"))
    chunk_rows = list_or_empty(chunks.get("source_chunks"))
    retrieval_hits = list_or_empty((retrieval or {}).get("hits"))
    formats = sorted(
        {
            str(
                dict_or_empty(dict_or_empty(source).get("parser_metadata")).get("parser_kind")
                or dict_or_empty(source).get("source_type")
                or "unknown"
            )
            for source in source_rows
        }
    )
    parse_decisions = list_or_empty(
        dict_or_empty(dict_or_empty(detail.get("progress")).get("observability")).get(
            "parse_decisions"
        )
    )
    unsupported_or_failed = [
        row
        for row in parse_decisions
        if dict_or_empty(row).get("decision")
        in {"skipped_unsupported_mime", "parse_error", "skipped_empty"}
    ]
    retrieval_diagnostics = [
        dict_or_empty(dict_or_empty(hit).get("metadata")).get("retrieval_diagnostics")
        for hit in retrieval_hits
        if dict_or_empty(dict_or_empty(hit).get("metadata")).get("retrieval_diagnostics")
    ]
    return {
        "query": question.query,
        "expected_coverage": question.expected_coverage,
        "task_id": task_id,
        "status": status,
        "completed": status == "COMPLETED",
        "source_count": len(source_rows),
        "chunk_count": len(chunk_rows),
        "source_formats": formats,
        "parse_decision_count": len(parse_decisions),
        "unsupported_or_failed_parse_count": len(unsupported_or_failed),
        "retrieval_hit_count": len(retrieval_hits),
        "retrieval_diagnostics_count": len(retrieval_diagnostics),
        "has_rerank_diagnostics": bool(retrieval_diagnostics),
    }


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    formats = sorted({fmt for row in results for fmt in list_or_empty(row.get("source_formats"))})
    return {
        "completed_count": sum(1 for row in results if row.get("completed") is True),
        "source_formats_seen": formats,
        "has_pdf": "pdf" in formats,
        "has_office": any(fmt in {"docx", "pptx", "xlsx"} for fmt in formats),
        "has_rerank_diagnostics": any(row.get("has_rerank_diagnostics") for row in results),
        "unsupported_or_failed_parse_count": sum(
            int(row.get("unsupported_or_failed_parse_count") or 0) for row in results
        ),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    aggregate = dict_or_empty(payload.get("aggregate"))
    result_count = len(list_or_empty(payload.get("results")))
    formats_seen = ", ".join(list_or_empty(aggregate.get("source_formats_seen"))) or "none"
    lines = [
        "# Phase 2 Multiformat Benchmark",
        "",
        f"- completed: {aggregate.get('completed_count')} / {result_count}",
        f"- source formats seen: {formats_seen}",
        f"- PDF seen: {aggregate.get('has_pdf')}",
        f"- Office seen: {aggregate.get('has_office')}",
        f"- rerank diagnostics: {aggregate.get('has_rerank_diagnostics')}",
        "",
        "| query | status | formats | chunks | rerank diagnostics |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for row in list_or_empty(payload.get("results")):
        formats = ", ".join(list_or_empty(dict_or_empty(row).get("source_formats"))) or "none"
        lines.append(
            "| {query} | {status} | {formats} | {chunks} | {rerank} |".format(
                query=str(dict_or_empty(row).get("query") or "").replace("|", "\\|"),
                status=dict_or_empty(row).get("status"),
                formats=formats,
                chunks=dict_or_empty(row).get("chunk_count"),
                rerank=dict_or_empty(row).get("has_rerank_diagnostics"),
            )
        )
    return "\n".join(lines) + "\n"


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
