#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
from pathlib import Path
from typing import Any

try:
    from live_acceptance_framework import (
        DEFAULT_BASE_URL,
        AcceptanceError,
        ApiError,
        evaluate_deployment_acceptance,
        get_profiles,
        print_gap_loop_summary_to_stdout,
        print_json,
        run_live_acceptance,
        write_artifacts,
    )
except ModuleNotFoundError:
    from scripts.live_acceptance_framework import (
        DEFAULT_BASE_URL,
        AcceptanceError,
        ApiError,
        evaluate_deployment_acceptance,
        get_profiles,
        print_gap_loop_summary_to_stdout,
        print_json,
        run_live_acceptance,
        write_artifacts,
    )


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
    print_gap_loop_summary_to_stdout(result.get("gap_summary") or {})
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
    return run_live_acceptance(
        base_url=base_url,
        profile=get_profiles()["searxng-docker-deployment"],
        wait_seconds=wait_seconds,
    )


def evaluate_acceptance(
    *,
    task_id: str,
    run_payload: dict[str, Any],
    payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return evaluate_deployment_acceptance(task_id, run_payload, payloads)


if __name__ == "__main__":
    raise SystemExit(main())
