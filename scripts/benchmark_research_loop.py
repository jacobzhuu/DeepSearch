#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

# Queries to benchmark
BENCHMARK_QUERIES = [
    "什么是LLM中的token？",
    "LangGraph 是什么，它如何工作？",
    "RAG 系统常见失败模式有哪些？",
    "Transformer 注意力机制为什么适合序列推荐？",
    "BPE、WordPiece 和 SentencePiece 有什么区别？",
    "OpenSearch 和 Elasticsearch 的关系是什么？",
    "什么是向量数据库中的 hybrid search？",
    "Agentic RAG 和传统 RAG 的区别是什么？",
    "推荐系统中的目标投毒攻击是什么？",
    "一个低证据或模糊问题：2026年5月8日深搜系统的最新内核版本号是多少？",
]

# Mode Configurations
CONFIG_DETERMINISTIC = {
    "RESEARCH_LOOP_ENABLED": "false",
}

CONFIG_ACTIVE_LOOP = {
    "RESEARCH_LOOP_ENABLED": "true",
    "RESEARCH_LOOP_STRATEGIST_ENABLED": "true",
    "RESEARCH_LOOP_STRATEGIST_SHADOW_MODE": "false",
    "LLM_SOURCE_JUDGE_ENABLED": "true",
    "LLM_SOURCE_TRIAGE_ACTIVE": "true",
}

BASE_URL = "http://127.0.0.1:8000"


def log(msg: str):
    print(f"[{datetime.now().isoformat()}] {msg}")


def run_cmd(cmd: list[str], env_overrides: dict[str, str] = None):
    new_env = os.environ.copy()
    if env_overrides:
        new_env.update(env_overrides)
    subprocess.run(cmd, env=new_env, check=True)


def restart_services(env_overrides: dict[str, str]):
    log(f"Restarting services with overrides: {env_overrides}")
    env_file = ".env.deepseek.local"
    if not os.path.exists(env_file):
        log(f"Warning: {env_file} not found, using default .env")
        env_file = ".env"

    overrides = env_overrides.copy()
    overrides["DEV_ENV_FILE"] = env_file

    # dev.sh restart handles both backend and worker
    run_cmd(["./dev.sh", "restart"], env_overrides=overrides)

    # Wait for health
    for _ in range(30):
        try:
            with httpx.Client(trust_env=False) as client:
                resp = client.get(f"{BASE_URL}/healthz")
                if resp.status_code == 200:
                    log("Backend is healthy")
                    # Give worker a moment to settle
                    time.sleep(2)
                    return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("Backend failed to start or remain healthy")


def get_task_detail(task_id: str) -> dict[str, Any]:
    with httpx.Client(trust_env=False) as client:
        resp = client.get(f"{BASE_URL}/api/v1/research/tasks/{task_id}")
        resp.raise_for_status()
        return resp.json()


def get_task_events(task_id: str) -> list[dict[str, Any]]:
    with httpx.Client(trust_env=False) as client:
        resp = client.get(f"{BASE_URL}/api/v1/research/tasks/{task_id}/events")
        resp.raise_for_status()
        return resp.json().get("events", [])


def wait_for_task(task_id: str, timeout: int = 900) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = get_task_detail(task_id)
        status = detail["status"]
        if status in {"COMPLETED", "FAILED", "CANCELLED"}:
            return detail
        time.sleep(5)
    raise TimeoutError(f"Task {task_id} timed out")


def collect_metrics(detail: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    obs = detail.get("progress", {}).get("observability", {}) or {}
    counts = obs.get("pipeline_counts", {})
    ver = obs.get("verification_summary", {})

    # Find coverage evaluation
    cov = {}
    strategy = obs.get("research_strategy")
    if strategy and strategy.get("coverage_evaluation"):
        cov = strategy.get("coverage_evaluation")
    elif obs.get("gap_rounds"):
        last_round = obs["gap_rounds"][-1]
        if last_round.get("gap_analysis") and last_round["gap_analysis"].get("coverage_evaluation"):
            cov = last_round["gap_analysis"]["coverage_evaluation"]

    # Standard fallback stop reason
    stop_reason = cov.get("stop_reason")
    if not stop_reason and not CONFIG_ACTIVE_LOOP["RESEARCH_LOOP_ENABLED"] == "true":
        # Check for gap analysis reason
        for event in reversed(events):
            if event["event_type"] == "pipeline.gap_analysis":
                stop_reason = event["payload"].get("result", {}).get("reason")
                if stop_reason:
                    break

    # Wall time
    started_at = detail.get("started_at")
    ended_at = detail.get("ended_at")
    wall_time = 0.0
    if started_at and ended_at:
        try:
            s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            e = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            wall_time = (e - s).total_seconds()
        except Exception:
            pass

    # LLM calls
    llm_calls = 0
    assistance = obs.get("llm_assistance", {})
    for stage_data in assistance.values():
        if isinstance(stage_data, dict):
            llm_calls += stage_data.get("counts", {}).get("llm_calls", 0)

    # Add strategist calls from events
    strategy_calls = sum(1 for e in events if e["event_type"].endswith(".research_strategy"))
    llm_calls += strategy_calls

    # Add planner calls
    planner_calls = sum(1 for e in events if e["event_type"].endswith(".research_plan.created"))
    llm_calls += planner_calls

    # Final report status
    report_status = "unknown"
    if obs.get("report_artifact_id"):
        report_status = cov.get("overall_status") or "sufficient"
    elif detail["status"] == "FAILED":
        report_status = "failed"

    # Strategist diagnostics for Active Loop
    strategist_diag = []
    for e in events:
        if e["event_type"].endswith(".research_strategy"):
            p = e["payload"].get("result", {})
            strategist_diag.append(
                {
                    "round": e.get("sequence_no"),
                    "decision": p.get("decision"),
                    "next_queries": [q.get("query_text") for q in p.get("planned_queries", [])],
                    "coverage_score": p.get("coverage_evaluation", {}).get(
                        "required_slots_sufficient"
                    ),
                }
            )

    strong_moderate_claims = ver.get("strong_supported_claim_count", 0) + ver.get(
        "weak_supported_claim_count", 0
    )
    req_sufficient = cov.get("required_slots_sufficient", 0)
    req_total = cov.get("required_slots_total", 0)
    low_cov_warnings = [
        w for w in obs.get("warnings", []) if "coverage" in w.lower() or "evidence" in w.lower()
    ]

    return {
        "total_rounds": len(obs.get("gap_rounds", [])),
        "search_queries": counts.get("search_queries", 0),
        "fetch_attempts": counts.get("fetch_attempts", 0),
        "successful_snapshots": counts.get("content_snapshots", 0),
        "source_documents": counts.get("source_documents", 0),
        "strong_moderate_claims": strong_moderate_claims,
        "required_slot_coverage": f"{req_sufficient} / {req_total}",
        "final_stop_reason": stop_reason,
        "low_coverage_warnings": len(low_cov_warnings),
        "wall_time": f"{wall_time:.1f}s",
        "llm_call_count": llm_calls,
        "report_status": report_status,
        "strategist_diagnostics": strategist_diag,
    }


def run_query(query: str) -> dict[str, Any]:
    log(f"Running query: {query}")
    with httpx.Client(trust_env=False) as client:
        # Create task
        resp = client.post(
            f"{BASE_URL}/api/v1/research/tasks", json={"query": query, "constraints": {}}
        )
        resp.raise_for_status()
        task_id = resp.json()["task_id"]

        # Run task
        resp = client.post(f"{BASE_URL}/api/v1/research/tasks/{task_id}/run")
        resp.raise_for_status()

        # Wait
        detail = wait_for_task(task_id)
        events = get_task_events(task_id)

        return collect_metrics(detail, events)


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(f"artifacts/research_loop_benchmark/{timestamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "timestamp": timestamp,
        "queries": BENCHMARK_QUERIES,
        "modes": {"deterministic": [], "active_loop": []},
    }

    # 1. Run Deterministic
    restart_services(CONFIG_DETERMINISTIC)
    for q in BENCHMARK_QUERIES:
        try:
            m = run_query(q)
            m["query"] = q
            results["modes"]["deterministic"].append(m)
        except Exception as e:
            log(f"Error running query '{q}': {e}")
            results["modes"]["deterministic"].append({"query": q, "error": str(e)})

    # 2. Run Active Loop
    restart_services(CONFIG_ACTIVE_LOOP)
    for q in BENCHMARK_QUERIES:
        try:
            m = run_query(q)
            m["query"] = q
            results["modes"]["active_loop"].append(m)
        except Exception as e:
            log(f"Error running query '{q}': {e}")
            results["modes"]["active_loop"].append({"query": q, "error": str(e)})

    # Save JSON
    with open(out_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Generate Summary MD
    generate_summary(results, out_dir / "summary.md")
    log(f"Benchmark complete. Results in {out_dir}")


def generate_summary(results: dict[str, Any], out_path: Path):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Research Loop Benchmark Summary\n\n")
        f.write(f"Timestamp: {results['timestamp']}\n\n")

        f.write("## Per-Query Comparison\n\n")
        headers = [
            "Query",
            "Mode",
            "Rounds",
            "Queries",
            "Fetch",
            "Claims",
            "Coverage",
            "Stop Reason",
            "Time",
            "LLM Calls",
        ]
        f.write("| " + " | ".join(headers) + " |\n")
        f.write("| :--- " + "| :---: " * (len(headers) - 1) + "|\n")

        for i, q in enumerate(results["queries"]):
            d = results["modes"]["deterministic"][i]
            a = results["modes"]["active_loop"][i]

            if "error" in d:
                f.write(f"| {q} | Det | ERR | - | - | - | - | - | - | - |\n")
            else:
                f.write(
                    f"| {q} | Det | {d['total_rounds']} | {d['search_queries']} | "
                    f"{d['fetch_attempts']} | {d['strong_moderate_claims']} | "
                    f"{d['required_slot_coverage']} | {d['final_stop_reason']} | "
                    f"{d['wall_time']} | {d['llm_call_count']} |\n"
                )

            if "error" in a:
                f.write("| | Active | ERR | - | - | - | - | - | - | - |\n")
            else:
                f.write(
                    f"| | Active | **{a['total_rounds']}** | **{a['search_queries']}** | "
                    f"{a['fetch_attempts']} | **{a['strong_moderate_claims']}** | "
                    f"**{a['required_slot_coverage']}** | {a['final_stop_reason']} | "
                    f"{a['wall_time']} | {a['llm_call_count']} |\n"
                )
            f.write("| --- " * len(headers) + "|\n")

        # Aggregate averages
        f.write("\n## Aggregate Averages\n\n")
        f.write("| Metric | Deterministic | Active Loop | Delta |\n")
        f.write("| :--- | :---: | :---: | :---: |\n")

        metrics = [
            "total_rounds",
            "search_queries",
            "fetch_attempts",
            "strong_moderate_claims",
            "llm_call_count",
        ]
        for m in metrics:
            d_vals = [r[m] for r in results["modes"]["deterministic"] if "error" not in r]
            a_vals = [r[m] for r in results["modes"]["active_loop"] if "error" not in r]
            d_avg = sum(d_vals) / len(d_vals) if d_vals else 0
            a_avg = sum(a_vals) / len(a_vals) if a_vals else 0
            diff = a_avg - d_avg
            f.write(
                f"| {m.replace('_', ' ').title()} | {d_avg:.2f} | {a_avg:.2f} | {diff:+.2f} |\n"
            )

        f.write("\n## Key Observations\n\n")
        f.write(
            "- **Coverage Improvements**: Identifies queries where Active Loop achieved "
            "higher slot coverage or claim counts.\n"
        )
        f.write(
            "- **Efficiency**: Comparison of rounds and queries issued vs wall time increase.\n"
        )
        f.write(
            "- **Strategist Behavior**: Notes on whether `fetch_more_existing_candidates` "
            "was effectively used.\n"
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Benchmark interrupted by user.")
        sys.exit(1)
    except Exception as e:
        log(f"Fatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
