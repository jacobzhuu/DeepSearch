#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_QUERY = "How to deploy SearXNG with Docker?"
DEFAULT_REPORT_LANGUAGE = "zh-CN"
EXPECTED_RUNNING_MODE = "real-search+opensearch+planner+report-LLM"
REQUEST_TIMEOUT_SECONDS = 30
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
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "PAUSED", "NEEDS_REVISION"}
GROUNDED_WRITER_MODES = {"llm_grounded", "deterministic", "deterministic_grounded"}


class AcceptanceError(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, *, status: int, method: str, path: str, payload: Any) -> None:
        super().__init__(f"{method} {path} returned HTTP {status}")
        self.status = status
        self.method = method
        self.path = path
        self.payload = payload


@dataclass(frozen=True)
class EvidenceTerm:
    term: str
    patterns: tuple[str, ...]


EVIDENCE_TERMS = (
    EvidenceTerm("docker_or_podman", (r"\bDocker\b", r"\bPodman\b")),
    EvidenceTerm("sudo_usermod", (r"sudo\s+usermod\s+-aG\s+docker",)),
    EvidenceTerm("docker_compose_pull", (r"docker\s+compose\s+pull",)),
    EvidenceTerm("settings_yml", (r"settings\.yml",)),
    EvidenceTerm("env_file", (r"\.env\.example", r"\.env\b")),
    EvidenceTerm("searxng_env", (r"SEARXNG_\*", r"SEARXNG_")),
    EvidenceTerm("reverse_proxy", (r"reverse proxy", r"反向代理")),
    EvidenceTerm("limiter_bot_protection", (r"limiter", r"bot protection")),
    EvidenceTerm("certificates", (r"certificates", r"update-ca-certificates", r"证书")),
    EvidenceTerm("docker_run_name", (r"docker\s+run\s+--name\s+searxng",)),
    EvidenceTerm("docker_run_port", (r"-p\s+8888:8080",)),
    EvidenceTerm(
        "docker_run_config_volume",
        (r"-v\s+[\"']?\./config/:/etc/searxng/[\"']?",),
    ),
    EvidenceTerm(
        "docker_run_data_volume",
        (r"-v\s+[\"']?\./data/:/var/cache/searxng/[\"']?",),
    ),
    EvidenceTerm("docker_run_image", (r"docker\.io/searxng/searxng:latest",)),
    EvidenceTerm("logs_command", (r"docker\s+container\s+logs\s+-f\s+searxng",)),
    EvidenceTerm(
        "exec_command",
        (r"docker\s+container\s+exec\s+-it\s+--user\s+root\s+searxng\s+/bin/sh\s+-l",),
    ),
)
EVIDENCE_GROUPS = {
    "complete_docker_run_block": (
        "docker_run_name",
        "docker_run_port",
        "docker_run_config_volume",
        "docker_run_data_volume",
        "docker_run_image",
    ),
    "troubleshooting_commands": ("logs_command", "exec_command"),
}


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    try:
        result = run_acceptance(base_url=base_url, wait_seconds=args.wait_seconds)
    except urllib.error.URLError as error:
        print(f"Service unavailable: {error.reason}", file=sys.stderr)
        return 2
    except TimeoutError as error:
        print(f"Timed out: {error}", file=sys.stderr)
        return 2
    except ApiError as error:
        print(str(error), file=sys.stderr)
        print_json(error.payload, stream=sys.stderr)
        return 2 if error.status in {409, 500, 502, 503, 504} else 1
    except AcceptanceError as error:
        print(f"Acceptance error: {error}", file=sys.stderr)
        return 1

    if args.artifact_dir:
        write_artifacts(Path(args.artifact_dir), result)
    if args.json_output:
        Path(args.json_output).write_text(
            json.dumps(result["acceptance"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print_json(result["acceptance"])
    return 0 if result["acceptance"]["passed"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create and run a fresh live SearXNG Docker deployment research task, then "
            "validate source_chunks -> claims -> claim_evidence -> report coverage."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DEEPSEARCH_BASE_URL")
        or os.environ.get("API_BASE_URL")
        or DEFAULT_BASE_URL,
        help="DeepSearch orchestrator base URL.",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=float(os.environ.get("DEEPSEARCH_DEPLOYMENT_ACCEPTANCE_WAIT", "900")),
        help="Maximum seconds to wait for the fresh task to reach a terminal status.",
    )
    parser.add_argument(
        "--artifact-dir",
        help="Optional directory where raw API payloads and report markdown are written.",
    )
    parser.add_argument(
        "--json-output",
        help="Optional path for the acceptance summary JSON.",
    )
    return parser.parse_args()


def run_acceptance(*, base_url: str, wait_seconds: float) -> dict[str, Any]:
    ready_status, ready_payload = request_json(base_url, "GET", "/readyz")
    if ready_status >= 500:
        raise ApiError(status=ready_status, method="GET", path="/readyz", payload=ready_payload)

    create_payload = {
        "query": DEFAULT_QUERY,
        "report_language": DEFAULT_REPORT_LANGUAGE,
        "constraints": {"language": DEFAULT_REPORT_LANGUAGE},
    }
    create_status, created = request_json(
        base_url,
        "POST",
        "/api/v1/research/tasks",
        create_payload,
    )
    if create_status >= 400:
        raise ApiError(
            status=create_status,
            method="POST",
            path="/api/v1/research/tasks",
            payload=created,
        )
    task_id = str(created["task_id"])

    run_status, run_payload = request_json(
        base_url,
        "POST",
        f"/api/v1/research/tasks/{task_id}/run",
    )
    if run_status >= 400:
        raise ApiError(
            status=run_status,
            method="POST",
            path=f"/api/v1/research/tasks/{task_id}/run",
            payload=run_payload,
        )

    detail = wait_for_task(base_url, task_id, timeout_seconds=wait_seconds)
    payloads = {
        "detail": detail,
        "run": run_payload,
        "source_chunks": get_optional_payload(
            base_url,
            f"/api/v1/research/tasks/{task_id}/source-chunks?limit=500",
        ),
        "claims": get_optional_payload(
            base_url,
            f"/api/v1/research/tasks/{task_id}/claims?limit=500",
        ),
        "claim_evidence": get_optional_payload(
            base_url,
            f"/api/v1/research/tasks/{task_id}/claim-evidence?limit=500",
        ),
        "report": get_optional_payload(base_url, f"/api/v1/research/tasks/{task_id}/report"),
        "events": get_optional_payload(
            base_url,
            f"/api/v1/research/tasks/{task_id}/events?limit=500",
        ),
    }
    acceptance = evaluate_acceptance(task_id=task_id, run_payload=run_payload, payloads=payloads)
    return {
        "task_id": task_id,
        "base_url": base_url,
        "payloads": payloads,
        "acceptance": acceptance,
    }


def wait_for_task(base_url: str, task_id: str, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    detail = get_payload(base_url, f"/api/v1/research/tasks/{task_id}")
    while detail.get("status") in ACTIVE_STATUSES:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"task {task_id} did not reach a terminal status")
        time.sleep(2)
        detail = get_payload(base_url, f"/api/v1/research/tasks/{task_id}")
    if detail.get("status") not in TERMINAL_STATUSES:
        raise AcceptanceError(f"task {task_id} reached unknown status {detail.get('status')}")
    return detail


def evaluate_acceptance(
    *,
    task_id: str,
    run_payload: dict[str, Any],
    payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    detail = payloads["detail"]
    report = payloads["report"]
    layers = layer_text(payloads)
    term_coverage = term_coverage_by_layer(layers)
    group_coverage = group_coverage_by_layer(term_coverage)
    source_gaps = [row for row in term_coverage if not row["source_chunks"]]
    downstream_gaps = [
        row
        for row in term_coverage
        if row["source_chunks"]
        and (not row["claims"] or not row["claim_evidence"] or not row["report_markdown"])
    ]
    running_mode = running_mode_from_detail(detail) or string_or_none(
        run_payload.get("running_mode")
    )
    traceability = traceability_summary(report.get("markdown"))
    checks = {
        "task_completed": detail.get("status") == "COMPLETED",
        "running_mode_real_pipeline": running_mode == EXPECTED_RUNNING_MODE,
        "report_language_zh_cn": report.get("report_language") == DEFAULT_REPORT_LANGUAGE,
        "writer_grounded": string_or_none(report.get("writer_mode")) in GROUNDED_WRITER_MODES,
        "report_is_chinese": contains_chinese(report.get("markdown")),
        "all_expected_terms_in_source_chunks": not source_gaps,
        "source_terms_preserved_downstream": not downstream_gaps,
        "evidence_groups_preserved": all(
            all(bool(group.get(layer)) for layer in layers) for group in group_coverage
        ),
        "traceability_present": all(traceability.values()),
    }
    return {
        "passed": all(checks.values()),
        "task_id": task_id,
        "status": detail.get("status"),
        "running_mode": running_mode,
        "report_language": report.get("report_language"),
        "writer_mode": report.get("writer_mode"),
        "llm_writer_status": report.get("llm_writer_status"),
        "counts": {
            "source_chunks": len(as_list(payloads["source_chunks"].get("source_chunks"))),
            "claims": len(as_list(payloads["claims"].get("claims"))),
            "claim_evidence": len(as_list(payloads["claim_evidence"].get("claim_evidence"))),
        },
        "checks": checks,
        "traceability": traceability,
        "term_coverage": term_coverage,
        "group_coverage": group_coverage,
        "source_gaps": source_gaps,
        "downstream_gaps": downstream_gaps,
    }


def layer_text(payloads: dict[str, dict[str, Any]]) -> dict[str, str]:
    chunks = as_list(payloads["source_chunks"].get("source_chunks"))
    claims = as_list(payloads["claims"].get("claims"))
    evidence = as_list(payloads["claim_evidence"].get("claim_evidence"))
    report_markdown = payloads["report"].get("markdown")
    return {
        "source_chunks": "\n".join(
            f"{item.get('text') or ''}\n{json_blob(item.get('metadata') or {})}"
            for item in chunks
            if isinstance(item, dict)
        ),
        "claims": "\n".join(
            f"{item.get('statement') or ''}\n{json_blob(item.get('notes') or {})}"
            for item in claims
            if isinstance(item, dict)
        ),
        "claim_evidence": "\n".join(
            f"{item.get('statement') or ''}\n{item.get('excerpt') or ''}\n"
            f"{json_blob(item.get('quality') or {})}"
            for item in evidence
            if isinstance(item, dict)
        ),
        "report_markdown": report_markdown if isinstance(report_markdown, str) else "",
    }


def term_coverage_by_layer(layers: dict[str, str]) -> list[dict[str, Any]]:
    coverage: list[dict[str, Any]] = []
    for term in EVIDENCE_TERMS:
        row: dict[str, Any] = {"term": term.term}
        for layer, text in layers.items():
            row[layer] = any(
                re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
                for pattern in term.patterns
            )
        coverage.append(row)
    return coverage


def group_coverage_by_layer(term_coverage: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_term = {str(row["term"]): row for row in term_coverage}
    groups: list[dict[str, Any]] = []
    layer_names = ("source_chunks", "claims", "claim_evidence", "report_markdown")
    for group_name, term_names in EVIDENCE_GROUPS.items():
        row: dict[str, Any] = {"group": group_name}
        for layer_name in layer_names:
            row[layer_name] = all(
                bool(rows_by_term[term_name][layer_name]) for term_name in term_names
            )
        groups.append(row)
    return groups


def traceability_summary(markdown: object) -> dict[str, bool]:
    text = markdown if isinstance(markdown, str) else ""
    return {
        "claim_trace": bool(re.search(r"(?:claims?:\s*`|Claim `)", text)),
        "claim_evidence_trace": bool(re.search(r"(?:claim_evidence:\s*`|claim_evidence `)", text)),
        "citation_trace": bool(re.search(r"(?:citations?:\s*`|citation `)", text)),
    }


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
            return response.status, decode_json(response.read())
    except urllib.error.HTTPError as error:
        return error.code, decode_json(error.read())


def get_payload(base_url: str, path: str) -> dict[str, Any]:
    status, payload = request_json(base_url, "GET", path)
    if status >= 400:
        raise ApiError(status=status, method="GET", path=path, payload=payload)
    return payload if isinstance(payload, dict) else {}


def get_optional_payload(base_url: str, path: str) -> dict[str, Any]:
    status, payload = request_json(base_url, "GET", path)
    if status == 404:
        return {}
    if status >= 400:
        raise ApiError(status=status, method="GET", path=path, payload=payload)
    return payload if isinstance(payload, dict) else {}


def write_artifacts(output_dir: Path, result: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in result["payloads"].items():
        (output_dir / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    report_markdown = result["payloads"]["report"].get("markdown")
    if isinstance(report_markdown, str):
        (output_dir / "report.md").write_text(report_markdown, encoding="utf-8")
    (output_dir / "acceptance.json").write_text(
        json.dumps(result["acceptance"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def running_mode_from_detail(detail: dict[str, Any]) -> str | None:
    progress = detail.get("progress")
    if not isinstance(progress, dict):
        return None
    observability = progress.get("observability")
    if not isinstance(observability, dict):
        return None
    return string_or_none(observability.get("running_mode"))


def decode_json(data: bytes) -> Any:
    if not data:
        return {}
    try:
        return json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        return {"raw": data.decode("utf-8", errors="replace")}


def contains_chinese(value: object) -> bool:
    return isinstance(value, str) and bool(re.search(r"[\u4e00-\u9fff]", value))


def json_blob(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def print_json(value: object, *, stream: Any = sys.stdout) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), file=stream)


if __name__ == "__main__":
    raise SystemExit(main())
