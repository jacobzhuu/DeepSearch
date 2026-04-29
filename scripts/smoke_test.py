#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the minimum end-to-end DeepSearch smoke test."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--domain-allow", default="example.com")
    parser.add_argument("--claim-query", default="example")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    task_query = f"Phase 11 smoke validation {int(time.time())}"
    summary: dict[str, object] = {"base_url": args.base_url, "task_query": task_query}

    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        timeout=args.timeout_seconds,
        trust_env=False,
    ) as client:
        _request_json(client, "GET", "/healthz")
        _request_json(client, "GET", "/readyz")

        task_payload = {
            "query": task_query,
            "constraints": {"language": "en", "domains_allow": [args.domain_allow]},
        }
        task = _request_json(client, "POST", "/api/v1/research/tasks", json=task_payload)
        task_id = _get_str(task, "task_id")
        summary["task_id"] = task_id

        search_result = _request_json(client, "POST", f"/api/v1/research/tasks/{task_id}/searches")
        candidate_urls = _request_json(
            client,
            "GET",
            f"/api/v1/research/tasks/{task_id}/candidate-urls",
        )
        candidates = _get_list(candidate_urls, "candidate_urls")
        if not candidates:
            raise RuntimeError(f"search produced no candidate URLs: {json.dumps(search_result)}")
        summary["candidate_url_count"] = len(candidates)

        fetch_result = _request_json(
            client,
            "POST",
            f"/api/v1/research/tasks/{task_id}/fetches",
            json={"limit": 1},
        )
        snapshots = _request_json(
            client,
            "GET",
            f"/api/v1/research/tasks/{task_id}/content-snapshots",
        )
        content_snapshots = _get_list(snapshots, "content_snapshots")
        if not content_snapshots:
            raise RuntimeError(f"fetch produced no snapshots: {json.dumps(fetch_result)}")
        summary["content_snapshot_count"] = len(content_snapshots)

        parse_result = _request_json(
            client,
            "POST",
            f"/api/v1/research/tasks/{task_id}/parse",
            json={"limit": 1},
        )
        source_documents = _request_json(
            client,
            "GET",
            f"/api/v1/research/tasks/{task_id}/source-documents",
        )
        source_chunks = _request_json(
            client,
            "GET",
            f"/api/v1/research/tasks/{task_id}/source-chunks",
        )
        documents = _get_list(source_documents, "source_documents")
        chunks = _get_list(source_chunks, "source_chunks")
        if not documents or not chunks:
            raise RuntimeError(f"parse produced no documents or chunks: {json.dumps(parse_result)}")
        summary["source_document_count"] = len(documents)
        summary["source_chunk_count"] = len(chunks)

        index_result = _request_json(
            client,
            "POST",
            f"/api/v1/research/tasks/{task_id}/index",
            json={"limit": 5},
        )
        indexed_count = int(index_result.get("indexed_count", 0))
        if indexed_count <= 0:
            raise RuntimeError(f"index produced no indexed chunks: {json.dumps(index_result)}")
        summary["indexed_count"] = indexed_count

        draft_result = _request_json(
            client,
            "POST",
            f"/api/v1/research/tasks/{task_id}/claims/draft",
            json={"query": args.claim_query, "limit": 5},
        )
        claim_count = int(draft_result.get("created_claims", 0)) + int(
            draft_result.get("reused_claims", 0)
        )
        if claim_count <= 0:
            raise RuntimeError(f"claim drafting produced no claims: {json.dumps(draft_result)}")
        summary["drafted_claim_count"] = claim_count

        verify_result = _request_json(
            client,
            "POST",
            f"/api/v1/research/tasks/{task_id}/claims/verify",
            json={"limit": 5},
        )
        claims = _request_json(client, "GET", f"/api/v1/research/tasks/{task_id}/claims")
        claim_list = _get_list(claims, "claims")
        if not claim_list:
            raise RuntimeError(f"verification left no claim records: {json.dumps(verify_result)}")
        summary["verified_claim_count"] = int(verify_result.get("verified_claims", 0))
        summary["verification_statuses"] = [claim["verification_status"] for claim in claim_list]

        report = _request_json(client, "POST", f"/api/v1/research/tasks/{task_id}/report")
        markdown = _get_str(report, "markdown")
        required_sections = (
            "## Executive Summary",
            "## Answer",
            "## Evidence Table",
            "## Appendix: Claim Evidence Mapping",
        )
        for section in required_sections:
            if section not in markdown:
                raise RuntimeError(f"report missing section {section!r}")
        summary["report_artifact_id"] = _get_str(report, "report_artifact_id")
        summary["report_version"] = int(report.get("version", 0))

    print(json.dumps(summary, ensure_ascii=True))
    return 0


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json: Mapping[str, object] | None = None,
) -> dict[str, object]:
    response = client.request(method, path, json=json)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object from {path}, got: {payload!r}")
    return payload


def _get_list(payload: Mapping[str, object], key: str) -> list[dict[str, object]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise RuntimeError(f"expected list at key {key!r}, got: {value!r}")
    normalized: list[dict[str, object]] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise RuntimeError(f"expected list entry object at key {key!r}, got: {entry!r}")
        normalized.append(entry)
    return normalized


def _get_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"expected non-empty string at key {key!r}, got: {value!r}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
