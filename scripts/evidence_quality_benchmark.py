#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
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
CLAIM_STATUSES = ("supported", "unsupported", "mixed", "contradicted", "draft")
PRECISE_CITATION_SPANS = {"sentence", "short_span"}
VERIFIED_EVIDENCE_RELATIONS = {"support", "weak_support", "contradict"}


@dataclass(frozen=True)
class EvidenceBenchmarkQuestion:
    query: str
    focus: str


BENCHMARK_QUESTIONS: tuple[EvidenceBenchmarkQuestion, ...] = (
    EvidenceBenchmarkQuestion(
        query="What is SearXNG and how does it work?",
        focus="overview and mechanism evidence",
    ),
    EvidenceBenchmarkQuestion(
        query="What are the privacy advantages and limitations of SearXNG?",
        focus="support versus limitation claims",
    ),
    EvidenceBenchmarkQuestion(
        query="What is OpenSearch and how does it work?",
        focus="non-SearXNG generalization",
    ),
    EvidenceBenchmarkQuestion(
        query="What is Model Context Protocol and how does it work?",
        focus="protocol source authority",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the DeepSearch evidence quality benchmark.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=len(BENCHMARK_QUESTIONS))
    parser.add_argument("--wait-seconds", type=float, default=420.0)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--json", action="store_true", help="Print compact JSON to stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    questions = BENCHMARK_QUESTIONS[: max(0, min(args.limit, len(BENCHMARK_QUESTIONS)))]
    results = run_benchmark(
        base_url=args.base_url,
        questions=questions,
        wait_seconds=args.wait_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    payload = {
        "benchmark": "evidence_quality_v1",
        "base_url": args.base_url,
        "question_count": len(results),
        "results": results,
        "aggregate": aggregate_results(results),
    }
    markdown = render_markdown_summary(payload)
    if args.json_output is not None:
        args.json_output.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.markdown_output is not None:
        args.markdown_output.write_text(markdown, encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    else:
        print(markdown)
    return 0 if all(item["completed"] for item in results) else 1


def run_benchmark(
    *,
    base_url: str,
    questions: tuple[EvidenceBenchmarkQuestion, ...],
    wait_seconds: float,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=timeout_seconds,
        trust_env=False,
    ) as client:
        for question in questions:
            task = request_json(
                client,
                "POST",
                "/api/v1/research/tasks",
                json={"query": question.query, "constraints": {}},
            )
            task_id = required_str(task, "task_id")
            run_payload = request_json(client, "POST", f"/api/v1/research/tasks/{task_id}/run")
            detail = wait_for_task(client, task_id, wait_seconds=wait_seconds)
            sources = request_json(
                client,
                "GET",
                f"/api/v1/research/tasks/{task_id}/source-documents",
            )
            chunks = request_json(client, "GET", f"/api/v1/research/tasks/{task_id}/source-chunks")
            claims = request_json(client, "GET", f"/api/v1/research/tasks/{task_id}/claims")
            evidence = request_json(
                client,
                "GET",
                f"/api/v1/research/tasks/{task_id}/claim-evidence",
            )
            report = optional_request_json(
                client,
                "GET",
                f"/api/v1/research/tasks/{task_id}/report",
            )
            rows.append(
                build_result_row(
                    question=question,
                    task_id=task_id,
                    status=str(detail.get("status") or run_payload.get("status") or "UNKNOWN"),
                    running_mode=run_payload.get("running_mode"),
                    sources=sources,
                    chunks=chunks,
                    claims=claims,
                    evidence=evidence,
                    report=report,
                )
            )
    return rows


def build_result_row(
    *,
    question: EvidenceBenchmarkQuestion,
    task_id: str,
    status: str,
    running_mode: object,
    sources: dict[str, Any],
    chunks: dict[str, Any],
    claims: dict[str, Any],
    evidence: dict[str, Any],
    report: dict[str, Any] | None,
) -> dict[str, Any]:
    source_rows = list_or_empty(sources.get("source_documents"))
    chunk_rows = list_or_empty(chunks.get("source_chunks"))
    claim_rows = list_or_empty(claims.get("claims"))
    evidence_rows = list_or_empty(evidence.get("claim_evidence"))
    verified_evidence_rows = [
        row
        for row in evidence_rows
        if str(dict_or_empty(row).get("relation_type") or "") in VERIFIED_EVIDENCE_RELATIONS
    ]
    status_distribution = {
        status_name: sum(
            1
            for claim in claim_rows
            if dict_or_empty(claim).get("verification_status") == status_name
        )
        for status_name in CLAIM_STATUSES
    }
    source_scores = [
        value
        for value in (
            numeric_or_none(dict_or_empty(source).get("final_source_score"))
            for source in source_rows
        )
        if value is not None
    ]
    citation_precision = citation_precision_metrics(verified_evidence_rows)
    all_citation_precision = citation_precision_metrics(evidence_rows)
    reuse_diagnostics = evidence_reuse_diagnostics(verified_evidence_rows)
    diversity_diagnostics = per_claim_evidence_diversity_diagnostics(
        claim_rows,
        verified_evidence_rows,
    )
    return {
        "query": question.query,
        "focus": question.focus,
        "task_id": task_id,
        "status": status,
        "completed": status == "COMPLETED",
        "running_mode": running_mode,
        "sources_count": len(source_rows),
        "chunks_count": len(chunk_rows),
        "claims_count": len(claim_rows),
        "claim_status_distribution": status_distribution,
        "average_source_quality": (
            round(sum(source_scores) / len(source_scores), 4) if source_scores else None
        ),
        "evidence_count": len(evidence_rows),
        "verified_evidence_count": len(verified_evidence_rows),
        "candidate_evidence_count": max(0, len(evidence_rows) - len(verified_evidence_rows)),
        "evidence_per_claim": (
            round(len(verified_evidence_rows) / len(claim_rows), 4) if claim_rows else 0.0
        ),
        "citation_span_precision": citation_precision,
        "all_evidence_citation_span_precision": all_citation_precision,
        "citation_precision_breakdown": citation_precision.get("distribution", {}),
        "duplicate_source_rate": duplicate_source_rate(source_rows, evidence_rows),
        "evidence_content_duplicate_rate": evidence_content_duplicate_rate(verified_evidence_rows),
        "all_evidence_content_duplicate_rate": evidence_content_duplicate_rate(evidence_rows),
        "chunk_reuse_count": reuse_diagnostics["chunk_reuse_count"],
        "span_reuse_count": reuse_diagnostics["span_reuse_count"],
        "top_reused_chunks": reuse_diagnostics["top_reused_chunks"],
        "top_reused_spans": reuse_diagnostics["top_reused_spans"],
        "per_claim_evidence_diversity": diversity_diagnostics,
        "report_artifact_generated": report is not None and bool(report.get("report_artifact_id")),
    }


def citation_precision_metrics(evidence_rows: list[Any]) -> dict[str, Any]:
    distribution: dict[str, int] = {}
    precise_count = 0
    for row in evidence_rows:
        precision = dict_or_empty(row).get("citation_precision") or "unknown"
        precision_key = str(precision)
        distribution[precision_key] = distribution.get(precision_key, 0) + 1
        if precision_key in PRECISE_CITATION_SPANS:
            precise_count += 1
    total = len(evidence_rows)
    return {
        "precise_count": precise_count,
        "total": total,
        "precision_rate": round(precise_count / total, 4) if total else 0.0,
        "distribution": dict(sorted(distribution.items())),
    }


def duplicate_source_rate(source_rows: list[Any], evidence_rows: list[Any]) -> float:
    del evidence_rows
    canonical_urls = [
        str(dict_or_empty(source).get("canonical_url") or "")
        for source in source_rows
        if dict_or_empty(source).get("canonical_url")
    ]
    canonical_rate = duplicate_rate(canonical_urls)
    content_hashes = [
        str(dict_or_empty(source).get("content_hash") or "")
        for source in source_rows
        if dict_or_empty(source).get("content_hash")
    ]
    content_rate = duplicate_rate(content_hashes)
    return round(max(canonical_rate, content_rate), 4)


def evidence_content_duplicate_rate(evidence_rows: list[Any]) -> float:
    content_identities = [evidence_content_identity(row) for row in evidence_rows]
    return round(duplicate_rate([value for value in content_identities if value]), 4)


def evidence_reuse_diagnostics(evidence_rows: list[Any]) -> dict[str, Any]:
    chunk_counts: Counter[str] = Counter()
    span_counts: Counter[str] = Counter()
    chunk_claims: dict[str, set[str]] = defaultdict(set)
    span_claims: dict[str, set[str]] = defaultdict(set)
    for row in evidence_rows:
        item = dict_or_empty(row)
        claim_id = str(item.get("claim_id") or "")
        chunk_id = str(item.get("source_chunk_id") or "")
        span_id = evidence_span_identity(item)
        if chunk_id:
            chunk_counts[chunk_id] += 1
            if claim_id:
                chunk_claims[chunk_id].add(claim_id)
        if span_id:
            span_counts[span_id] += 1
            if claim_id:
                span_claims[span_id].add(claim_id)
    return {
        "chunk_reuse_count": sum(count - 1 for count in chunk_counts.values() if count > 1),
        "span_reuse_count": sum(count - 1 for count in span_counts.values() if count > 1),
        "top_reused_chunks": top_reused_rows(chunk_counts, chunk_claims),
        "top_reused_spans": top_reused_rows(span_counts, span_claims),
    }


def top_reused_rows(
    counts: Counter[str],
    claim_map: dict[str, set[str]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    rows = []
    for value, count in counts.most_common(limit):
        if count <= 1:
            continue
        rows.append(
            {
                "id": value,
                "count": count,
                "claim_count": len(claim_map.get(value, set())),
            }
        )
    return rows


def per_claim_evidence_diversity_diagnostics(
    claim_rows: list[Any],
    evidence_rows: list[Any],
) -> list[dict[str, Any]]:
    evidence_by_claim_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence_rows:
        item = dict_or_empty(row)
        claim_id = str(item.get("claim_id") or "")
        if claim_id:
            evidence_by_claim_id[claim_id].append(item)

    diagnostics: list[dict[str, Any]] = []
    for claim in claim_rows:
        claim_item = dict_or_empty(claim)
        claim_id = str(claim_item.get("claim_id") or "")
        rows = evidence_by_claim_id.get(claim_id, [])
        chunk_ids = [str(row.get("source_chunk_id") or "") for row in rows]
        span_ids = [evidence_span_identity(row) for row in rows]
        content_ids = [evidence_content_identity(row) for row in rows]
        relation_types = [str(row.get("relation_type") or "") for row in rows]
        precision_distribution: dict[str, int] = {}
        for row in rows:
            precision = str(row.get("citation_precision") or "unknown")
            precision_distribution[precision] = precision_distribution.get(precision, 0) + 1
        diagnostics.append(
            {
                "claim_id": claim_id,
                "verification_status": claim_item.get("verification_status"),
                "evidence_count": len(rows),
                "unique_chunk_count": len({value for value in chunk_ids if value}),
                "unique_span_count": len({value for value in span_ids if value}),
                "unique_content_count": len({value for value in content_ids if value}),
                "duplicate_chunk_count": _duplicate_count(chunk_ids),
                "duplicate_span_count": _duplicate_count(span_ids),
                "duplicate_content_count": _duplicate_count(content_ids),
                "relation_types": sorted({value for value in relation_types if value}),
                "citation_precision_distribution": dict(sorted(precision_distribution.items())),
            }
        )
    return diagnostics


def evidence_span_identity(row: dict[str, Any]) -> str:
    quality = dict_or_empty(row.get("quality"))
    span_hash = quality.get("span_text_hash")
    if isinstance(span_hash, str) and span_hash:
        return span_hash
    citation_span_id = row.get("citation_span_id")
    if isinstance(citation_span_id, str) and citation_span_id:
        return citation_span_id
    return (
        f"{row.get('source_chunk_id') or ''}:"
        f"{row.get('start_offset') or ''}:"
        f"{row.get('end_offset') or ''}"
    )


def evidence_content_identity(row: Any) -> str:
    item = dict_or_empty(row)
    quality = dict_or_empty(item.get("quality"))
    for key in ("span_text_hash", "chunk_text_hash", "content_hash"):
        value = quality.get(key)
        if isinstance(value, str) and value:
            return value
    return evidence_span_identity(item)


def duplicate_rate(values: list[str]) -> float:
    if not values:
        return 0.0
    return 1.0 - (len(set(values)) / len(values))


def _duplicate_count(values: list[str]) -> int:
    filtered = [value for value in values if value]
    return len(filtered) - len(set(filtered))


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {}
    average_source_quality_values = [
        item["average_source_quality"]
        for item in results
        if isinstance(item.get("average_source_quality"), int | float)
    ]
    return {
        "completed_count": sum(1 for item in results if item.get("completed") is True),
        "average_source_quality": (
            round(
                sum(average_source_quality_values) / len(average_source_quality_values),
                4,
            )
            if average_source_quality_values
            else None
        ),
        "average_evidence_per_claim": round(
            sum(float(item.get("evidence_per_claim") or 0.0) for item in results) / len(results),
            4,
        ),
        "average_citation_precision_rate": round(
            sum(
                float(
                    dict_or_empty(item.get("citation_span_precision")).get("precision_rate") or 0.0
                )
                for item in results
            )
            / len(results),
            4,
        ),
        "average_duplicate_source_rate": round(
            sum(float(item.get("duplicate_source_rate") or 0.0) for item in results) / len(results),
            4,
        ),
        "average_evidence_content_duplicate_rate": round(
            sum(float(item.get("evidence_content_duplicate_rate") or 0.0) for item in results)
            / len(results),
            4,
        ),
        "average_all_evidence_content_duplicate_rate": round(
            sum(float(item.get("all_evidence_content_duplicate_rate") or 0.0) for item in results)
            / len(results),
            4,
        ),
        "total_chunk_reuse_count": sum(int(item.get("chunk_reuse_count") or 0) for item in results),
        "total_span_reuse_count": sum(int(item.get("span_reuse_count") or 0) for item in results),
    }


def render_markdown_summary(payload: dict[str, Any]) -> str:
    lines = [
        "# Evidence Quality Benchmark",
        "",
        f"- Benchmark: `{payload['benchmark']}`",
        f"- Base URL: `{payload['base_url']}`",
        f"- Questions: {payload['question_count']}",
        "",
        (
            "| Query | Status | Sources | Chunks | Claims | Supported | Unsupported | Mixed | "
            "Contradicted | Avg source quality | Evidence/claim | Citation precision | "
            "Duplicate source rate | Evidence content dup | Chunk reuse | Span reuse | Report |"
        ),
        (
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
            "---: | ---: | ---: | ---: | ---: | --- |"
        ),
    ]
    for row in list_or_empty(payload.get("results")):
        item = dict_or_empty(row)
        distribution = dict_or_empty(item.get("claim_status_distribution"))
        precision = dict_or_empty(item.get("citation_span_precision"))
        lines.append(
            "| "
            f"{escape_cell(str(item.get('query') or ''))} | "
            f"{escape_cell(str(item.get('status') or ''))} | "
            f"{item.get('sources_count', 0)} | "
            f"{item.get('chunks_count', 0)} | "
            f"{item.get('claims_count', 0)} | "
            f"{distribution.get('supported', 0)} | "
            f"{distribution.get('unsupported', 0)} | "
            f"{distribution.get('mixed', 0)} | "
            f"{distribution.get('contradicted', 0)} | "
            f"{format_metric(item.get('average_source_quality'))} | "
            f"{format_metric(item.get('evidence_per_claim'))} | "
            f"{format_metric(precision.get('precision_rate'))} | "
            f"{format_metric(item.get('duplicate_source_rate'))} | "
            f"{format_metric(item.get('evidence_content_duplicate_rate'))} | "
            f"{item.get('chunk_reuse_count', 0)} | "
            f"{item.get('span_reuse_count', 0)} | "
            f"{'yes' if item.get('report_artifact_generated') else 'no'} |"
        )
    lines.extend(["", "## Per-query reuse diagnostics", ""])
    for row in list_or_empty(payload.get("results")):
        item = dict_or_empty(row)
        precision = dict_or_empty(item.get("citation_precision_breakdown"))
        lines.extend(
            [
                f"### {escape_cell(str(item.get('query') or ''))}",
                "",
                f"- Citation precision breakdown: `{json.dumps(precision, sort_keys=True)}`",
                (
                    "- Top reused chunks: "
                    f"`{json.dumps(item.get('top_reused_chunks') or [], sort_keys=True)}`"
                ),
                (
                    "- Top reused spans: "
                    f"`{json.dumps(item.get('top_reused_spans') or [], sort_keys=True)}`"
                ),
                (
                    "- Per-query duplicate content rate: "
                    f"{format_metric(item.get('evidence_content_duplicate_rate'))}"
                ),
                "",
                (
                    "| Claim | Status | Evidence | Unique chunks | Unique spans | "
                    "Duplicate content | Relations | Precision |"
                ),
                "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
            ]
        )
        for diagnostic in list_or_empty(item.get("per_claim_evidence_diversity")):
            diag = dict_or_empty(diagnostic)
            lines.append(
                "| "
                f"{escape_cell(str(diag.get('claim_id') or '')[:8])} | "
                f"{escape_cell(str(diag.get('verification_status') or ''))} | "
                f"{diag.get('evidence_count', 0)} | "
                f"{diag.get('unique_chunk_count', 0)} | "
                f"{diag.get('unique_span_count', 0)} | "
                f"{diag.get('duplicate_content_count', 0)} | "
                f"{escape_cell(_format_relation_types(diag))} | "
                f"{escape_cell(_format_precision_distribution(diag))} |"
            )
        lines.append("")
    aggregate = dict_or_empty(payload.get("aggregate"))
    lines.extend(
        [
            "",
            "## Aggregate",
            "",
            f"- Completed: {aggregate.get('completed_count', 0)}/{payload['question_count']}",
            f"- Average source quality: {format_metric(aggregate.get('average_source_quality'))}",
            (
                "- Average evidence per claim: "
                f"{format_metric(aggregate.get('average_evidence_per_claim'))}"
            ),
            (
                "- Average citation precision: "
                f"{format_metric(aggregate.get('average_citation_precision_rate'))}"
            ),
            (
                "- Average duplicate source rate: "
                f"{format_metric(aggregate.get('average_duplicate_source_rate'))}"
            ),
            (
                "- Average evidence content duplicate rate: "
                f"{format_metric(aggregate.get('average_evidence_content_duplicate_rate'))}"
            ),
            (
                "- Average all-evidence content duplicate rate: "
                f"{format_metric(aggregate.get('average_all_evidence_content_duplicate_rate'))}"
            ),
            f"- Total chunk reuse count: {aggregate.get('total_chunk_reuse_count', 0)}",
            f"- Total span reuse count: {aggregate.get('total_span_reuse_count', 0)}",
            "",
        ]
    )
    return "\n".join(lines)


def wait_for_task(client: httpx.Client, task_id: str, *, wait_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, wait_seconds)
    detail = request_json(client, "GET", f"/api/v1/research/tasks/{task_id}")
    while detail.get("status") in ACTIVE_STATUSES and time.monotonic() < deadline:
        time.sleep(2.0)
        detail = request_json(client, "GET", f"/api/v1/research/tasks/{task_id}")
    return detail


def request_json(
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


def optional_request_json(client: httpx.Client, method: str, path: str) -> dict[str, Any] | None:
    response = client.request(method, path)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object from {path}, got {payload!r}")
    return payload


def required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"expected non-empty string at {key!r}, got {value!r}")
    return value


def list_or_empty(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def dict_or_empty(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def numeric_or_none(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def format_metric(value: object) -> str:
    if isinstance(value, int | float):
        return f"{float(value):.4f}"
    return "n/a"


def escape_cell(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def _format_relation_types(diagnostic: dict[str, Any]) -> str:
    return ", ".join(str(item) for item in list_or_empty(diagnostic.get("relation_types")))


def _format_precision_distribution(diagnostic: dict[str, Any]) -> str:
    return json.dumps(
        diagnostic.get("citation_precision_distribution") or {},
        sort_keys=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
