#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_QUERY = "What is SearXNG and how does it work?"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
REQUEST_TIMEOUT_SECONDS = 20


class ServiceUnavailable(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, *, status: int, method: str, path: str, payload: Any) -> None:
        super().__init__(f"{method} {path} returned HTTP {status}")
        self.status = status
        self.method = method
        self.path = path
        self.payload = payload


def main() -> int:
    load_dotenv(Path.cwd() / ".env")
    args = parse_args()
    base_url = args.base_url.rstrip("/")

    try:
        health = request_json(base_url, "GET", "/readyz")
        if health[0] >= 500:
            raise ServiceUnavailable(f"readyz returned HTTP {health[0]}")

        create_status, create_payload = request_json(
            base_url,
            "POST",
            "/api/v1/research/tasks",
            {"query": args.query, "constraints": {}},
        )
        if create_status >= 400:
            raise ApiError(
                status=create_status,
                method="POST",
                path="/api/v1/research/tasks",
                payload=create_payload,
            )
        task_id = str(create_payload["task_id"])

        run_status, run_payload = request_json(
            base_url,
            "POST",
            f"/api/v1/research/tasks/{task_id}/run",
        )
        if run_status == 409:
            print("Pipeline configuration/precondition error:")
            print_json(run_payload)
            return 2
        if run_status >= 400:
            raise ApiError(
                status=run_status,
                method="POST",
                path=f"/api/v1/research/tasks/{task_id}/run",
                payload=run_payload,
            )

        detail = get_optional(base_url, f"/api/v1/research/tasks/{task_id}")
        events = get_optional(base_url, f"/api/v1/research/tasks/{task_id}/events")
        search_queries = get_optional(base_url, f"/api/v1/research/tasks/{task_id}/search-queries")
        candidate_urls = get_optional(base_url, f"/api/v1/research/tasks/{task_id}/candidate-urls")
        source_documents = get_optional(
            base_url,
            f"/api/v1/research/tasks/{task_id}/source-documents",
        )
        source_chunks = get_optional(base_url, f"/api/v1/research/tasks/{task_id}/source-chunks")
        claims = get_optional(base_url, f"/api/v1/research/tasks/{task_id}/claims")
        report = get_optional(base_url, f"/api/v1/research/tasks/{task_id}/report")

        print_summary(
            task_id=task_id,
            run_payload=run_payload,
            detail=detail,
            events=events,
            search_queries=search_queries,
            candidate_urls=candidate_urls,
            source_documents=source_documents,
            source_chunks=source_chunks,
            claims=claims,
            report=report,
        )

        claim_count = len(as_list(claims.get("claims") if isinstance(claims, dict) else []))
        report_markdown = report.get("markdown") if isinstance(report, dict) else None
        if run_payload.get("completed") is True and claim_count >= 3 and report_markdown:
            return 0
        return 1
    except ServiceUnavailable as error:
        print(f"Service unavailable: {error}", file=sys.stderr)
        return 2
    except urllib.error.URLError as error:
        print(f"Service unavailable: {error.reason}", file=sys.stderr)
        return 2
    except TimeoutError as error:
        print(f"Service unavailable: {error}", file=sys.stderr)
        return 2
    except ApiError as error:
        print(str(error), file=sys.stderr)
        print_json(error.payload)
        return 2 if error.status in {409, 500, 502, 503, 504} else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one DeepSearch planner-pipeline smoke task and print ledger highlights.",
    )
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DEEPSEARCH_BASE_URL")
        or os.environ.get("API_BASE_URL")
        or DEFAULT_BASE_URL,
    )
    return parser.parse_args()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={"content-type": "application/json", "accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            data = response.read()
            return response.status, decode_json(data)
    except urllib.error.HTTPError as error:
        return error.code, decode_json(error.read())


def get_optional(base_url: str, path: str) -> dict[str, Any]:
    status, payload = request_json(base_url, "GET", path)
    if status == 404:
        return {}
    if status >= 400:
        raise ApiError(status=status, method="GET", path=path, payload=payload)
    return payload if isinstance(payload, dict) else {}


def decode_json(data: bytes) -> Any:
    if not data:
        return {}
    try:
        return json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        return {"raw": data.decode("utf-8", errors="replace")}


def print_summary(
    *,
    task_id: str,
    run_payload: dict[str, Any],
    detail: dict[str, Any],
    events: dict[str, Any],
    search_queries: dict[str, Any],
    candidate_urls: dict[str, Any],
    source_documents: dict[str, Any],
    source_chunks: dict[str, Any],
    claims: dict[str, Any],
    report: dict[str, Any],
) -> None:
    observability = (
        detail.get("progress", {}).get("observability", {}) if isinstance(detail, dict) else {}
    )
    print(f"task_id: {task_id}")
    print(f"status: {run_payload.get('status')}")
    print(f"running_mode: {run_payload.get('running_mode')}")
    print(f"planner_status: {observability.get('planner_status') or 'n/a'}")
    print(f"planner_mode: {observability.get('planner_mode') or 'n/a'}")

    final_queries = observability.get("final_search_queries") or []
    if not final_queries:
        final_queries = (
            search_queries.get("search_queries", []) if isinstance(search_queries, dict) else []
        )
    print_list(
        "final_search_queries",
        [item.get("query_text") or item.get("query") for item in as_list(final_queries)],
    )

    attempted = observability.get("attempted_sources") or []
    print_list(
        "attempted_sources",
        [item.get("canonical_url") or item.get("final_url") for item in as_list(attempted)],
    )

    print_list(
        "candidate_urls",
        [item.get("canonical_url") for item in as_list(candidate_urls.get("candidate_urls"))],
        limit=8,
    )
    print_list(
        "source_documents",
        [item.get("canonical_url") for item in as_list(source_documents.get("source_documents"))],
        limit=8,
    )

    chunks = as_list(source_chunks.get("source_chunks"))
    print(f"chunks_count: {len(chunks)}")
    print_claims_by_category(claims)

    markdown = report.get("markdown") if isinstance(report, dict) else None
    if isinstance(markdown, str) and markdown.strip():
        preview = "\n".join(markdown.strip().splitlines()[:24])
        print("report_preview:")
        print(preview)
    else:
        print("report_preview: n/a")

    failure = run_payload.get("failure")
    if failure:
        print("failure_details:")
        print_json(failure)
    elif run_payload.get("completed") is not True:
        print("failure_details: pipeline did not complete")

    event_types = [item.get("event_type") for item in as_list(events.get("events"))]
    print_list("events", event_types, limit=12)


def print_claims_by_category(claims_payload: dict[str, Any]) -> None:
    counts: dict[str, int] = {}
    for claim in as_list(claims_payload.get("claims")):
        notes = claim.get("notes") if isinstance(claim, dict) else {}
        category = notes.get("claim_category") if isinstance(notes, dict) else None
        if not isinstance(category, str) or not category:
            category = "unknown"
        counts[category] = counts.get(category, 0) + 1
    print(f"claims_total: {sum(counts.values())}")
    print(f"claims_by_category: {counts}")


def print_list(label: str, values: list[Any], *, limit: int = 10) -> None:
    clean_values = [str(value) for value in values if value]
    print(f"{label}:")
    if not clean_values:
        print("  - n/a")
        return
    for value in clean_values[:limit]:
        print(f"  - {value}")
    if len(clean_values) > limit:
        print(f"  - ... {len(clean_values) - limit} more")


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


if __name__ == "__main__":
    raise SystemExit(main())
