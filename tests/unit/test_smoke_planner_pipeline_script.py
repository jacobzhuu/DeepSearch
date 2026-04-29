from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_smoke_planner_pipeline_missing_server_is_clean_and_hides_secrets(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "LLM_API_KEY=sk-test-secret-value\n" "DEEPSEARCH_BASE_URL=http://127.0.0.1:9\n",
        encoding="utf-8",
    )
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "smoke_planner_pipeline.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--query",
            "What is SearXNG and how does it work?",
            "--base-url",
            "http://127.0.0.1:9",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    combined_output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 2
    assert "Service unavailable" in combined_output
    assert "sk-test-secret-value" not in combined_output
