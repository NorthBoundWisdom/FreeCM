from __future__ import annotations

import os
import subprocess
from pathlib import Path


def git_fixture_output_enabled() -> bool:
    return os.environ.get("FREECM_TEST_GIT_OUTPUT") == "1"


def run_git_fixture(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=not git_fixture_output_enabled(),
        text=True,
    )
    return completed.stdout.strip() if completed.stdout is not None else ""
