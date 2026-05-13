#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
# Canonical minimal example (orchestrator may append ``+extra`` capability segments).
EXPECTED_RUNNING_MODE = "real-search+opensearch+planner+report-LLM"
# ``+``-delimited segments that must all be present (case-insensitive) for real-pipeline acceptance.
REQUIRED_RUNNING_MODE_SEGMENTS = frozenset({"real-search", "opensearch", "planner", "report-llm"})
REQUEST_TIMEOUT_SECONDS = float(
    os.environ.get("DEEPSEARCH_LIVE_ACCEPTANCE_HTTP_TIMEOUT", "30")
)
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
GAP_ROUND_OUTCOME_SKIPPED_LABEL = "skipped_drafting"
GAP_SUMMARY_UNKNOWN_SKIP = "unknown"


def _researching_more_skip_drafting_reason(result: dict[str, Any]) -> str | None:
    """Prefer nested ``skip_drafting_reason``, then top-level ``skip_drafting_reason``."""
    diag = result.get("gap_round_diagnostics")
    if isinstance(diag, dict):
        inner = diag.get("skip_drafting_reason")
        if isinstance(inner, str) and inner.strip():
            return inner.strip()
    top = result.get("skip_drafting_reason")
    if isinstance(top, str) and top.strip():
        return top.strip()
    return None


GROUNDED_WRITER_MODES = {"llm_grounded", "deterministic", "deterministic_grounded"}
COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY = "coverage_sufficient_optional_weak_only"
GAP_SKIP_CATEGORY_NOT_ALLOWED = "gap_category_not_allowed"
GAP_SKIP_PRIORITY_TOO_LOW = "gap_priority_too_low"
NO_SELECTED_CANDIDATES = "no_selected_candidates"


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


@dataclass(frozen=True)
class LiveAcceptanceProfile:
    profile_id: str
    query: str
    report_language: str
    description: str
    evaluate: Callable[[str, dict[str, Any], dict[str, dict[str, Any]]], dict[str, Any]]


DEPLOYMENT_QUERY = "How to deploy SearXNG with Docker?"
DEPLOYMENT_REPORT_LANGUAGE = "zh-CN"
LANGGRAPH_QUERY = "What is LangGraph and how does it work?"
LANGGRAPH_REPORT_LANGUAGE = "en-US"

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

LANGGRAPH_AUTHORITY_PATTERNS = (
    r"https?://docs\.langchain\.com/",
    r"https?://reference\.langchain\.com/",
    r"https?://(?:www\.)?langchain\.com/langgraph",
    r"https?://github\.com/langchain-ai/langgraph\b",
)
LANGGRAPH_TECHNICAL_TERMS = (
    r"\bgraph\b",
    r"\bstate\b",
    r"\bnodes?\b",
    r"\bedges?\b",
    r"\bworkflow\b",
    r"\borchestrat",
    r"\brouting\b",
    r"\bdurable\b",
    r"\bcheckpoint",
    r"\bmemory\b",
    r"\bstreaming\b",
    r"human[- ]in[- ]the[- ]loop",
)
LANGGRAPH_SECTION_TERMS = (
    r"what is",
    r"overview",
    r"how .*works?",
    r"mechanism",
    r"architecture",
    r"concepts?",
    r"state",
    r"graph",
    r"nodes?",
    r"edges?",
)


def get_profiles() -> dict[str, LiveAcceptanceProfile]:
    deployment = LiveAcceptanceProfile(
        profile_id="searxng-docker-deployment",
        query=DEPLOYMENT_QUERY,
        report_language=DEPLOYMENT_REPORT_LANGUAGE,
        description="Live SearXNG Docker deployment acceptance profile.",
        evaluate=evaluate_deployment_acceptance,
    )
    langgraph = LiveAcceptanceProfile(
        profile_id="langgraph-technical-explanation",
        query=LANGGRAPH_QUERY,
        report_language=LANGGRAPH_REPORT_LANGUAGE,
        description="Live LangGraph technical explanation acceptance profile.",
        evaluate=evaluate_langgraph_acceptance,
    )
    return {deployment.profile_id: deployment, langgraph.profile_id: langgraph}


def run_live_acceptance(
    *,
    base_url: str,
    profile: LiveAcceptanceProfile,
    wait_seconds: float,
) -> dict[str, Any]:
    ready_status, ready_payload = request_json(base_url, "GET", "/readyz")
    if ready_status >= 500:
        raise ApiError(status=ready_status, method="GET", path="/readyz", payload=ready_payload)

    create_payload = {
        "query": profile.query,
        "report_language": profile.report_language,
        "constraints": {"language": profile.report_language},
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
    payloads = collect_task_artifacts(base_url=base_url, task_id=task_id, detail=detail)
    payloads["run"] = run_payload if isinstance(run_payload, dict) else {}
    acceptance = profile.evaluate(task_id, payloads["run"], payloads)
    gap_summary = summarize_gap_loop_from_payloads(payloads)
    return {
        "task_id": task_id,
        "profile": profile.profile_id,
        "base_url": base_url,
        "payloads": payloads,
        "acceptance": acceptance,
        "gap_summary": gap_summary,
    }


def collect_task_artifacts(
    *,
    base_url: str,
    task_id: str,
    detail: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        "detail": detail,
        "source_documents": get_optional_payload(
            base_url,
            f"/api/v1/research/tasks/{task_id}/source-documents?limit=500",
        ),
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
        "search_queries": get_optional_payload(
            base_url,
            f"/api/v1/research/tasks/{task_id}/search-queries?limit=500",
        ),
        "candidate_urls": get_optional_payload(
            base_url,
            f"/api/v1/research/tasks/{task_id}/candidate-urls?limit=500",
        ),
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


def evaluate_deployment_acceptance(
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
    traceability = traceability_summary(report.get("markdown"), payloads=payloads)
    checks = {
        "task_completed": detail.get("status") == "COMPLETED",
        "running_mode_real_pipeline": running_mode_has_required_capabilities(running_mode),
        "report_language_zh_cn": report.get("report_language") == DEPLOYMENT_REPORT_LANGUAGE,
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


def evaluate_langgraph_acceptance(
    task_id: str,
    run_payload: dict[str, Any],
    payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    detail = payloads["detail"]
    report = payloads["report"]
    report_markdown = report.get("markdown")
    running_mode = running_mode_from_detail(detail) or string_or_none(
        run_payload.get("running_mode")
    )
    traceability = traceability_summary(report_markdown, payloads=payloads)
    llm_usage = planner_and_report_llm_summary(detail, report, payloads["events"])
    official_sources = langgraph_official_sources(payloads)
    report_sections = technical_report_section_summary(report_markdown)
    evidence_chain = evidence_chain_summary(payloads)
    benchmark = benchmark_metrics(payloads, report_markdown=report_markdown)
    checks = {
        "task_completed": detail.get("status") == "COMPLETED",
        "running_mode_real_pipeline": running_mode_has_required_capabilities(running_mode),
        "planner_or_explicit_fallback": llm_usage["planner_ok"],
        "report_llm_or_grounded_fallback": llm_usage["report_ok"],
        "traceability_present": all(traceability.values()),
        "verified_evidence_present": evidence_chain["verified_evidence_count"] > 0,
        "report_not_empty": isinstance(report_markdown, str) and bool(report_markdown.strip()),
        "answer_relevant_technical_sections": report_sections["passed"],
        "official_or_high_authority_source_selected": bool(official_sources),
        "deployment_specific_checks_not_applied": True,
    }
    return {
        "passed": all(checks.values()),
        "profile": "langgraph-technical-explanation",
        "task_id": task_id,
        "status": detail.get("status"),
        "running_mode": running_mode,
        "report_language": report.get("report_language"),
        "writer_mode": report.get("writer_mode"),
        "llm_writer_status": report.get("llm_writer_status"),
        "counts": artifact_counts(payloads),
        "checks": checks,
        "traceability": traceability,
        "llm_usage": llm_usage,
        "evidence_chain": evidence_chain,
        "technical_report_sections": report_sections,
        "official_or_high_authority_sources": official_sources,
        "benchmark": benchmark,
    }


def artifact_counts(payloads: dict[str, dict[str, Any]]) -> dict[str, int]:
    return {
        "source_documents": len(
            as_list(payloads.get("source_documents", {}).get("source_documents"))
        ),
        "source_chunks": len(as_list(payloads.get("source_chunks", {}).get("source_chunks"))),
        "claims": len(as_list(payloads.get("claims", {}).get("claims"))),
        "claim_evidence": len(as_list(payloads.get("claim_evidence", {}).get("claim_evidence"))),
        "events": len(as_list(payloads.get("events", {}).get("events"))),
    }


def benchmark_metrics(
    payloads: dict[str, dict[str, Any]],
    *,
    report_markdown: object,
) -> dict[str, Any]:
    search_queries = as_list(payloads.get("search_queries", {}).get("search_queries"))
    observability = observability_from_detail(payloads.get("detail", {}))
    planned_search_queries = as_list(
        observability.get("final_search_queries")
        or (observability.get("research_plan") or {}).get("search_queries")
    )
    candidate_urls = as_list(payloads.get("candidate_urls", {}).get("candidate_urls"))
    source_documents = as_list(payloads.get("source_documents", {}).get("source_documents"))
    source_chunks = as_list(payloads.get("source_chunks", {}).get("source_chunks"))
    claims = as_list(payloads.get("claims", {}).get("claims"))
    claim_evidence = as_list(payloads.get("claim_evidence", {}).get("claim_evidence"))
    report_text = report_markdown if isinstance(report_markdown, str) else ""
    report_word_count = len(re.findall(r"\b\w+\b", report_text))
    source_yield_summary = _first_list_by_key(payloads, "source_yield_summary")
    slot_coverage_summary = _first_list_by_key(payloads, "slot_coverage_summary")
    source_rows = source_yield_summary or candidate_urls or source_documents
    source_domains = sorted(
        {
            str(item.get("domain") or "").strip().lower()
            for item in source_documents
            if isinstance(item, dict) and str(item.get("domain") or "").strip()
        }
    )
    source_roles = Counter(
        str(item.get("source_role") or item.get("source_intent") or "").strip()
        for item in source_rows
        if isinstance(item, dict)
    )
    source_categories = Counter(
        str(item.get("source_category") or item.get("source_intent") or "").strip()
        for item in source_rows
        if isinstance(item, dict)
    )
    supported_claims = [
        item
        for item in claims
        if isinstance(item, dict) and item.get("verification_status") == "supported"
    ]
    report_citation_density = (
        round(len(claim_evidence) / max(report_word_count / 1000.0, 1.0), 3)
        if report_word_count
        else 0.0
    )
    return {
        "search_queries": max(len(search_queries), len(planned_search_queries)),
        "search_queries_persisted": len(search_queries),
        "search_queries_planned": len(planned_search_queries),
        "candidate_urls": len(candidate_urls),
        "source_documents": len(source_documents),
        "source_domains": source_domains,
        "source_domain_count": len(source_domains),
        "source_categories": {
            key: count for key, count in sorted(source_categories.items()) if key
        },
        "source_roles": {key: count for key, count in sorted(source_roles.items()) if key},
        "source_chunks": len(source_chunks),
        "claims": len(claims),
        "supported_claims": len(supported_claims),
        "claim_evidence": len(claim_evidence),
        "report_length": {
            "characters": len(report_text),
            "words": report_word_count,
        },
        "citation_density_per_1000_words": report_citation_density,
        "answer_slot_coverage": _answer_slot_coverage_metrics(slot_coverage_summary),
        "gap_summary": summarize_gap_loop_from_payloads(payloads),
    }


def _answer_slot_coverage_metrics(rows: list[Any]) -> dict[str, Any]:
    normalized_rows = [row for row in rows if isinstance(row, dict)]
    status_counts = Counter(str(row.get("status") or "unknown") for row in normalized_rows)
    covered = [
        str(row.get("slot_id"))
        for row in normalized_rows
        if row.get("status") in {"covered", "strong", "moderate"}
    ]
    weak = [str(row.get("slot_id")) for row in normalized_rows if row.get("status") == "weak"]
    missing = [str(row.get("slot_id")) for row in normalized_rows if row.get("status") == "missing"]
    return {
        "total": len(normalized_rows),
        "status_counts": {key: count for key, count in sorted(status_counts.items())},
        "covered_slots": [slot for slot in covered if slot and slot != "None"],
        "weak_slots": [slot for slot in weak if slot and slot != "None"],
        "missing_slots": [slot for slot in missing if slot and slot != "None"],
    }


def _first_list_by_key(value: Any, key: str) -> list[Any]:
    if isinstance(value, dict):
        direct = value.get(key)
        if isinstance(direct, list):
            return direct
        for nested in value.values():
            found = _first_list_by_key(nested, key)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _first_list_by_key(item, key)
            if found:
                return found
    return []


def evidence_chain_summary(payloads: dict[str, dict[str, Any]]) -> dict[str, int]:
    claims = as_list(payloads["claims"].get("claims"))
    evidence = as_list(payloads["claim_evidence"].get("claim_evidence"))
    verified = [
        item
        for item in evidence
        if isinstance(item, dict)
        and item.get("relation_type") in {"support", "weak_support", "contradict"}
    ]
    supported_claims = [
        item
        for item in claims
        if isinstance(item, dict)
        and item.get("verification_status") in {"supported", "mixed", "contradicted"}
    ]
    return {
        "claim_count": len(claims),
        "claim_evidence_count": len(evidence),
        "verified_evidence_count": len(verified),
        "supported_or_checked_claim_count": len(supported_claims),
    }


def planner_and_report_llm_summary(
    detail: dict[str, Any],
    report: dict[str, Any],
    events: dict[str, Any],
) -> dict[str, Any]:
    observability = observability_from_detail(detail)
    planner_status = string_or_none(observability.get("planner_status"))
    plan_source = string_or_none(observability.get("plan_source"))
    planner_text = json_blob(
        {
            "observability": observability,
            "events": events,
        }
    )
    planner_used = planner_status == "success" or plan_source == "llm_planner"
    planner_fallback_explicit = planner_status in {"fallback", "created"} and (
        plan_source
        in {
            "deterministic_fallback_after_llm_failure",
            "deterministic_fallback",
        }
        or "deterministic fallback" in planner_text.lower()
    )

    writer_mode = string_or_none(report.get("writer_mode"))
    writer_status = string_or_none(report.get("llm_writer_status"))
    report_used = writer_mode == "llm_grounded" and writer_status in {None, "used"}
    report_fallback_explicit = writer_mode in {"deterministic", "deterministic_grounded"} and (
        writer_status is not None
        or "fallback" in json_blob(report).lower()
        or "report_writer" in json_blob(observability)
    )
    return {
        "planner_status": planner_status,
        "plan_source": plan_source,
        "planner_used": planner_used,
        "planner_fallback_explicit": planner_fallback_explicit,
        "planner_ok": planner_used or planner_fallback_explicit,
        "writer_mode": writer_mode,
        "llm_writer_status": writer_status,
        "report_used": report_used,
        "report_fallback_explicit": report_fallback_explicit,
        "report_ok": report_used or report_fallback_explicit,
    }


def langgraph_official_sources(payloads: dict[str, dict[str, Any]]) -> list[str]:
    text = "\n".join(
        json_blob(payloads.get(name, {}))
        for name in ("detail", "source_documents", "source_chunks", "events", "candidate_urls")
    )
    matches: list[str] = []
    for pattern in LANGGRAPH_AUTHORITY_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = match.group(0)
            if value not in matches:
                matches.append(value)
    return matches


def technical_report_section_summary(markdown: object) -> dict[str, Any]:
    text = markdown if isinstance(markdown, str) else ""
    headings = [
        line.strip("# ").strip() for line in text.splitlines() if re.match(r"^#{1,4}\s+\S", line)
    ]
    matching_headings = [
        heading
        for heading in headings
        if any(
            re.search(pattern, heading, flags=re.IGNORECASE) for pattern in LANGGRAPH_SECTION_TERMS
        )
    ]
    technical_terms = [
        pattern
        for pattern in LANGGRAPH_TECHNICAL_TERMS
        if re.search(pattern, text, flags=re.IGNORECASE)
    ]
    return {
        "passed": "langgraph" in text.lower()
        and len(text.strip()) >= 400
        and len(headings) >= 2
        and len(technical_terms) >= 3
        and bool(matching_headings),
        "heading_count": len(headings),
        "matching_headings": matching_headings,
        "technical_term_hits": len(technical_terms),
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


def traceability_summary(
    markdown: object,
    *,
    payloads: dict[str, dict[str, Any]] | None = None,
    require_markdown_ids: bool = False,
) -> dict[str, bool]:
    text = markdown if isinstance(markdown, str) else ""
    markdown_trace = {
        "claim_trace": bool(re.search(r"(?:claims?:\s*`|Claim `)", text)),
        "claim_evidence_trace": bool(re.search(r"(?:claim_evidence:\s*`|claim_evidence `)", text)),
        "citation_trace": bool(re.search(r"(?:citations?:\s*`|citation `)", text)),
    }
    if require_markdown_ids or payloads is None:
        return markdown_trace

    claims = as_list(payloads.get("claims", {}).get("claims"))
    evidence = as_list(payloads.get("claim_evidence", {}).get("claim_evidence"))
    verified_claim_ids = {
        str(item.get("claim_id") or item.get("id"))
        for item in claims
        if isinstance(item, dict)
        and item.get("verification_status") in {"supported", "mixed", "contradicted"}
        and (item.get("claim_id") or item.get("id"))
    }
    verified_evidence = [
        item
        for item in evidence
        if isinstance(item, dict)
        and item.get("relation_type") in {"support", "weak_support", "contradict"}
    ]
    evidence_ids = {
        str(item.get("claim_evidence_id") or item.get("id"))
        for item in verified_evidence
        if item.get("claim_evidence_id") or item.get("id")
    }
    citation_ids = {
        str(item.get("citation_span_id") or item.get("citation_id"))
        for item in verified_evidence
        if item.get("citation_span_id") or item.get("citation_id")
    }
    evidence_claim_ids = {
        str(item.get("claim_id")) for item in verified_evidence if item.get("claim_id")
    }
    api_trace = {
        "claim_trace": bool(verified_claim_ids or evidence_claim_ids),
        "claim_evidence_trace": bool(evidence_ids or verified_evidence),
        "citation_trace": bool(citation_ids),
    }
    return {key: bool(markdown_trace[key] or api_trace[key]) for key in markdown_trace}


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


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return payload
    legacy = event.get("payload_json")
    return legacy if isinstance(legacy, dict) else {}


def _sorted_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if events and all(isinstance(item.get("sequence_no"), int) for item in events):
        return sorted(events, key=lambda item: int(item["sequence_no"]))
    return list(events)


def _sorted_int_counter(counter: Counter) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def summarize_gap_loop_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Read-only rollup of ``pipeline.gap_analysis``, ``pipeline.research_strategy``, and
    ``RESEARCHING_MORE`` ``stage_completed`` diagnostics from API event rows.
    """
    ordered = _sorted_events(events)
    gap_analysis_events = 0
    research_strategy_events = 0
    research_more_stage_completed_count = 0

    gap_analysis_reason: Counter[str] = Counter()
    loop_stop_reason: Counter[str] = Counter()
    suppressed_strategist: Counter[str] = Counter()
    coverage_optional_weak_only = 0

    outcome_dist: Counter[str] = Counter()
    skip_drafting_reason: Counter[str] = Counter()
    nested_drafting_created = 0
    nested_verification_supported = 0
    no_selected_candidates = 0
    gap_category_not_allowed = 0
    gap_priority_too_low = 0

    for event in ordered:
        et = str(event.get("event_type") or "")
        payload = _event_payload(event)
        if et.endswith(".gap_analysis"):
            gap_analysis_events += 1
            result = payload.get("result")
            if isinstance(result, dict):
                reason = result.get("reason")
                if isinstance(reason, str) and reason.strip():
                    gap_analysis_reason[reason.strip()] += 1
                loop = result.get("loop_stop_reason")
                if isinstance(loop, str) and loop.strip():
                    loop_stop_reason[loop.strip()] += 1
                if reason == COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY or loop == (
                    COVERAGE_SUFFICIENT_OPTIONAL_WEAK_ONLY
                ):
                    coverage_optional_weak_only += 1
                alignment = result.get("coverage_alignment")
                if isinstance(alignment, dict):
                    sd = alignment.get("suppressed_strategist_decision")
                    if isinstance(sd, str) and sd.strip():
                        suppressed_strategist[sd.strip()] += 1
            continue

        if et.endswith(".research_strategy"):
            research_strategy_events += 1
            continue

        if et.endswith(".stage_completed") and payload.get("stage") == "RESEARCHING_MORE":
            research_more_stage_completed_count += 1
            result = payload.get("result")
            if not isinstance(result, dict):
                continue
            diag = result.get("gap_round_diagnostics")
            outcome_str: str | None = None
            if isinstance(diag, dict):
                raw_out = diag.get("gap_round_outcome")
                if isinstance(raw_out, str) and raw_out.strip():
                    outcome_str = raw_out.strip()
                nested_drafting_created += int(diag.get("drafting_created_claims") or 0)
                raw_ver = diag.get("verification_supported_claims")
                if raw_ver is not None:
                    nested_verification_supported += int(raw_ver or 0)
            if outcome_str is None:
                top_out = result.get("gap_round_outcome")
                if isinstance(top_out, str) and top_out.strip():
                    outcome_str = top_out.strip()
            if outcome_str:
                outcome_dist[outcome_str] += 1

            skip = _researching_more_skip_drafting_reason(result)
            if skip:
                skip_drafting_reason[skip] += 1
            elif outcome_str == GAP_ROUND_OUTCOME_SKIPPED_LABEL:
                skip_drafting_reason[GAP_SUMMARY_UNKNOWN_SKIP] += 1
            if skip == NO_SELECTED_CANDIDATES:
                no_selected_candidates += 1
            for row in result.get("skipped_gap_search_sources") or []:
                if not isinstance(row, dict):
                    continue
                sr = row.get("skip_reason")
                if sr == GAP_SKIP_CATEGORY_NOT_ALLOWED:
                    gap_category_not_allowed += 1
                elif sr == GAP_SKIP_PRIORITY_TOO_LOW:
                    gap_priority_too_low += 1

    return {
        "gap_analysis_events": gap_analysis_events,
        "research_strategy_events": research_strategy_events,
        "research_more_stage_completed_count": research_more_stage_completed_count,
        "research_more_gap_rounds_total": research_more_stage_completed_count,
        "gap_rounds_with_drafting": int(outcome_dist.get("drafted", 0)),
        "gap_rounds_skipped_drafting": int(outcome_dist.get("skipped_drafting", 0)),
        "skip_drafting_reason_distribution": _sorted_int_counter(skip_drafting_reason),
        "gap_round_outcome_distribution": _sorted_int_counter(outcome_dist),
        "loop_stop_reason_distribution": _sorted_int_counter(loop_stop_reason),
        "gap_analysis_reason_distribution": _sorted_int_counter(gap_analysis_reason),
        "coverage_sufficient_optional_weak_only_count": coverage_optional_weak_only,
        "suppressed_strategist_decision_distribution": _sorted_int_counter(suppressed_strategist),
        "nested_drafting_created_claims_total": nested_drafting_created,
        "nested_verification_supported_claims_total": nested_verification_supported,
        "no_selected_candidates_count": no_selected_candidates,
        "gap_category_not_allowed_count": gap_category_not_allowed,
        "gap_priority_too_low_count": gap_priority_too_low,
    }


def summarize_gap_loop_from_payloads(payloads: dict[str, Any]) -> dict[str, Any]:
    events = as_list((payloads.get("events") or {}).get("events"))
    return summarize_gap_loop_from_events(events)


def print_gap_loop_summary_to_stdout(summary: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    """Emit a short operator-facing gap / research-loop footer after acceptance JSON."""
    print("Gap summary:", file=stream)
    print(f"- gap_analysis_events: {summary.get('gap_analysis_events', 0)}", file=stream)
    print(
        f"- RESEARCHING_MORE stages: {summary.get('research_more_stage_completed_count', 0)}",
        file=stream,
    )
    suppressed = summary.get("suppressed_strategist_decision_distribution") or {}
    continue_ct = int(suppressed.get("continue_search", 0))
    print(f"- suppressed continue_search: {continue_ct}", file=stream)
    loop_dist = summary.get("loop_stop_reason_distribution") or {}
    if len(loop_dist) == 1:
        only = next(iter(loop_dist.items()))
        print(f"- loop_stop_reason: {only[0]}", file=stream)
    elif loop_dist:
        print(f"- loop_stop_reason_distribution: {loop_dist}", file=stream)
    else:
        print("- loop_stop_reason: (none)", file=stream)
    print(
        f"- no_selected_candidates: {summary.get('no_selected_candidates_count', 0)}",
        file=stream,
    )


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
    benchmark = result.get("acceptance", {}).get("benchmark")
    if isinstance(benchmark, dict):
        (output_dir / "benchmark.json").write_text(
            json.dumps(benchmark, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    gap_summary = result.get("gap_summary")
    if gap_summary is None and isinstance(result.get("payloads"), dict):
        gap_summary = summarize_gap_loop_from_payloads(result["payloads"])
    if isinstance(gap_summary, dict):
        (output_dir / "gap_summary.json").write_text(
            json.dumps(gap_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary_path = output_dir / "summary.md"
        if summary_path.is_file():
            block = (
                "\n## Gap / research loop summary\n\n"
                f"- gap_analysis_events: {gap_summary.get('gap_analysis_events', 0)}\n"
                f"- RESEARCHING_MORE stages: "
                f"{gap_summary.get('research_more_stage_completed_count', 0)}\n"
                f"- research_strategy_events: {gap_summary.get('research_strategy_events', 0)}\n"
                f"- coverage_sufficient_optional_weak_only_count: "
                f"{gap_summary.get('coverage_sufficient_optional_weak_only_count', 0)}\n"
                f"- suppressed_strategist_decision_distribution: "
                f"{gap_summary.get('suppressed_strategist_decision_distribution') or {}}\n"
                f"- loop_stop_reason_distribution: "
                f"{gap_summary.get('loop_stop_reason_distribution') or {}}\n"
                f"- no_selected_candidates_count: "
                f"{gap_summary.get('no_selected_candidates_count', 0)}\n"
                f"- gap_category_not_allowed_count: "
                f"{gap_summary.get('gap_category_not_allowed_count', 0)}\n"
                f"- gap_priority_too_low_count: "
                f"{gap_summary.get('gap_priority_too_low_count', 0)}\n"
            )
            prev = summary_path.read_text(encoding="utf-8")
            summary_path.write_text(prev + block, encoding="utf-8")


def running_mode_from_detail(detail: dict[str, Any]) -> str | None:
    return string_or_none(observability_from_detail(detail).get("running_mode"))


def running_mode_has_required_capabilities(running_mode: str | None) -> bool:
    """
    True when ``running_mode`` includes every required orchestrator capability segment.

    Segments are ``+``-separated (as emitted in ``progress.observability.running_mode``).
    Additional segments (for example ``assist-judge-strategy-review``) are allowed.
    """
    if running_mode is None:
        return False
    text = str(running_mode).strip()
    if not text:
        return False
    segments = {part.strip().lower() for part in text.split("+") if part.strip()}
    return REQUIRED_RUNNING_MODE_SEGMENTS <= segments


def observability_from_detail(detail: dict[str, Any]) -> dict[str, Any]:
    progress = detail.get("progress")
    if not isinstance(progress, dict):
        return {}
    observability = progress.get("observability")
    return observability if isinstance(observability, dict) else {}


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
