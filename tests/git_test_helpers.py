from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path


os.environ.setdefault("FREECM_TEST_GIT_OUTPUT", "0")


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


def create_git_fixture_repo(
    remotes_root: Path,
    name: str,
    required_relative_paths: Iterable[str],
) -> tuple[Path, str]:
    repo_root = remotes_root / name
    repo_root.mkdir(parents=True)
    run_git_fixture(repo_root, "init")
    run_git_fixture(repo_root, "config", "user.name", "Codex")
    run_git_fixture(repo_root, "config", "user.email", "codex@example.com")
    for relative_path in required_relative_paths:
        target = repo_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if "." in target.name:
            target.write_text(f"{name}:{relative_path}\n", encoding="utf-8")
        else:
            target.mkdir(parents=True, exist_ok=True)
            (target / ".keep").write_text("", encoding="utf-8")
    return repo_root, commit_git_fixture_repo(repo_root, "init")


def commit_git_fixture_repo(repo_root: Path, message: str) -> str:
    run_git_fixture(repo_root, "add", ".")
    run_git_fixture(repo_root, "commit", "-m", message)
    return run_git_fixture(repo_root, "rev-parse", "HEAD")
