#!/usr/bin/env python3
import sys
import json
import time
import httpx
from uuid import UUID
from datetime import datetime

def get_task_detail(base_url, task_id):
    with httpx.Client(trust_env=False) as client:
        resp = client.get(f"{base_url}/api/v1/research/tasks/{task_id}")
        resp.raise_for_status()
        return resp.json()

def wait_for_task(base_url, task_id, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = get_task_detail(base_url, task_id)
        status = detail["status"]
        if status in {"COMPLETED", "FAILED", "CANCELLED"}:
            return detail
        time.sleep(2)
    raise TimeoutError("Task timed out")

def collect_metrics(detail):
    obs = detail.get("progress", {}).get("observability", {})
    counts = obs.get("pipeline_counts", {})
    ver = obs.get("verification_summary", {})
    
    # Try to find coverage_evaluation
    # It might be in research_strategy or in a gap round
    cov = {}
    strategy = obs.get("research_strategy")
    if strategy and strategy.get("coverage_evaluation"):
        cov = strategy.get("coverage_evaluation")
    elif obs.get("gap_rounds"):
        # Check the last round
        last_round = obs["gap_rounds"][-1]
        if last_round.get("gap_analysis") and last_round["gap_analysis"].get("coverage_evaluation"):
            cov = last_round["gap_analysis"]["coverage_evaluation"]

    # Calculate wall time
    started_at = detail.get("started_at")
    ended_at = detail.get("ended_at")
    wall_time = 0
    if started_at and ended_at:
        s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        e = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        wall_time = (e - s).total_seconds()

    # LLM call counts
    llm_calls = 0
    assistance = obs.get("llm_assistance", {})
    for stage, data in assistance.items():
        if isinstance(data, dict):
            llm_calls += data.get("counts", {}).get("llm_calls", 0)
    
    # Plus strategist calls
    strategy_calls = 0
    events_url = f"http://127.0.0.1:8000/api/v1/research/tasks/{detail['task_id']}/events"
    try:
        with httpx.Client(trust_env=False) as client:
            events_resp = client.get(events_url)
            events_resp.raise_for_status()
            events = events_resp.json().get("events", [])
            strategy_calls = sum(1 for e in events if e["event_type"].endswith(".research_strategy"))
    except:
        pass
    llm_calls += strategy_calls

    return {
        "search_rounds": len(obs.get("gap_rounds", [])),
        "fetch_attempts": counts.get("fetch_attempts", 0),
        "successful_snapshots": counts.get("content_snapshots", 0),
        "strong_supported_claims": ver.get("strong_supported_claim_count", 0),
        "required_slot_coverage": f"{cov.get('required_slots_sufficient', 0)} / {cov.get('required_slots_total', 0)}",
        "final_stop_reason": cov.get("stop_reason"),
        "report_low_coverage_warnings": len([w for w in obs.get("warnings", []) if "coverage" in w.lower() or "evidence" in w.lower()]),
        "wall_time": f"{wall_time:.1f}s",
        "llm_call_count": llm_calls
    }

def main():
    base_url = "http://127.0.0.1:8000"
    query = "什么是LLM中的token？"
    
    with httpx.Client(trust_env=False) as client:
        # 1. Create task
        resp = client.post(f"{base_url}/api/v1/research/tasks", json={"query": query, "constraints": {}})
        resp.raise_for_status()
        task_id = resp.json()["task_id"]
        
        # 2. Run task
        resp = client.post(f"{base_url}/api/v1/research/tasks/{task_id}/run")
        resp.raise_for_status()
    
    # 3. Wait
    detail = wait_for_task(base_url, task_id)
    
    # 4. Extract
    metrics = collect_metrics(detail)
    print(json.dumps(metrics, indent=2))

if __name__ == "__main__":
    main()
