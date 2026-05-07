from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_local_env_and_backup_files_are_git_ignored() -> None:
    result = subprocess.run(
        [
            "git",
            "check-ignore",
            ".env.deepseek.local",
            ".env.deepseek.local.bak.2026-05-05-181037",
        ],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0


def test_tracked_env_examples_do_not_contain_real_llm_api_key() -> None:
    for relative_path in (".env.example", ".env.compose.example"):
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        match = re.search(r"(?m)^[^\S\r\n]*LLM_API_KEY[^\S\r\n]*=[^\S\r\n]*(?P<value>.*)$", text)
        assert match is not None
        assert match.group("value").strip() == ""
        assert not re.search(
            r"(?m)^[^\S\r\n]*LLM_API_KEY[^\S\r\n]*=[^\S\r\n]*sk-[A-Za-z0-9_-]{8,}",
            text,
        )
