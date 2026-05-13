#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
from pathlib import Path

try:
    from live_acceptance_framework import (
        DEFAULT_BASE_URL,
        AcceptanceError,
        ApiError,
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
        get_profiles,
        print_gap_loop_summary_to_stdout,
        print_json,
        run_live_acceptance,
        write_artifacts,
    )


def main() -> int:
    args = parse_args()
    profiles = get_profiles()
    profile = profiles[args.profile]
    base_url = args.base_url.rstrip("/")
    try:
        result = run_live_acceptance(
            base_url=base_url,
            profile=profile,
            wait_seconds=args.wait_seconds,
        )
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
            print_json_string(result["acceptance"]),
            encoding="utf-8",
        )

    print_json(result["acceptance"])
    print_gap_loop_summary_to_stdout(result.get("gap_summary") or {})
    return 0 if result["acceptance"]["passed"] else 1


def parse_args() -> argparse.Namespace:
    profiles = get_profiles()
    parser = argparse.ArgumentParser(
        description=(
            "Run a fresh live DeepSearch research task through the real worker pipeline "
            "and validate it with a reusable acceptance profile."
        ),
    )
    parser.add_argument(
        "--profile",
        choices=sorted(profiles),
        default="langgraph-technical-explanation",
        help="Acceptance profile to run.",
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
        "--timeout-seconds",
        dest="wait_seconds",
        type=float,
        default=float(os.environ.get("DEEPSEARCH_LIVE_ACCEPTANCE_WAIT", "900")),
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


def print_json_string(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
