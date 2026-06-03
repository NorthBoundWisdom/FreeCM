from __future__ import annotations

import os
import shutil
import subprocess
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RemoteDefaultHead:
    branch: str
    commit: str


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    capture_output: bool = False,
    check: bool = True,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    quiet = quiet or _quiet_test_git_output()
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture_output or quiet,
        check=check,
    )


def git(
    work_tree: Path,
    *args: str,
    capture_output: bool = False,
    check: bool = True,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    return run(
        ["git", "-C", str(work_tree), *args],
        capture_output=capture_output,
        check=check,
        quiet=quiet,
    )


def git_output(work_tree: Path, *args: str) -> str:
    return git(work_tree, *args, capture_output=True).stdout.strip()


def git_is_work_tree(work_tree: Path) -> bool:
    if not work_tree.exists():
        return False
    completed = git(
        work_tree,
        "rev-parse",
        "--is-inside-work-tree",
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def git_common_dir(work_tree: Path) -> Path | None:
    if not git_is_work_tree(work_tree):
        return None
    try:
        common_dir = git_output(
            work_tree,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        )
    except subprocess.CalledProcessError:
        return None
    return Path(common_dir).resolve()


def git_has_commit(work_tree: Path, commit: str) -> bool:
    completed = git(
        work_tree,
        "rev-parse",
        "--verify",
        f"{commit}^{{commit}}",
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def git_worktree_matches_commit(work_tree: Path, commit: str) -> bool:
    completed = git(
        work_tree,
        "status",
        "--porcelain",
        "--untracked-files=all",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or completed.stdout.strip():
        return False
    return git_output(work_tree, "rev-parse", "HEAD") == commit


def remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        _rmtree(path)
    else:
        path.unlink()


def _rmtree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_make_writable_and_retry)
    else:
        shutil.rmtree(path, onerror=_make_writable_and_retry_legacy)


def _make_writable_and_retry(function: object, path: str, excinfo: BaseException) -> None:
    if not isinstance(excinfo, PermissionError):
        raise excinfo
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _make_writable_and_retry_legacy(
    function: object,
    path: str,
    excinfo: tuple[type[BaseException], BaseException, object],
) -> None:
    _make_writable_and_retry(function, path, excinfo[1])


def git_remote_url(work_tree: Path, remote_name: str) -> str | None:
    completed = git(
        work_tree,
        "remote",
        "get-url",
        remote_name,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def fetch_remote_refs(seed_root: Path, dependency_name: str, remote: str) -> None:
    del dependency_name
    fetch_remote = "origin" if git_remote_url(seed_root, "origin") == remote else remote
    git(seed_root, "fetch", "--prune", "--force", "--tags", fetch_remote, quiet=True)


def remote_default_head(remote: str) -> RemoteDefaultHead:
    completed = run(
        ["git", "ls-remote", "--symref", remote, "HEAD"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Unable to resolve remote HEAD from {remote}")

    branch: str | None = None
    commit: str | None = None
    for line in completed.stdout.splitlines():
        if line.startswith("ref: ") and line.endswith("\tHEAD"):
            ref_name = line[len("ref: ") :].split("\t", 1)[0].strip()
            prefix = "refs/heads/"
            if ref_name.startswith(prefix):
                branch = ref_name[len(prefix) :].strip() or None
        elif line.endswith("\tHEAD"):
            candidate = line.split("\t", 1)[0].strip()
            if candidate:
                commit = candidate

    if branch is None or commit is None:
        raise RuntimeError(f"Unable to determine remote default HEAD from {remote}")
    return RemoteDefaultHead(branch=branch, commit=commit)


def same_git_common_dir(left: Path, right: Path) -> bool:
    left_common = git_common_dir(left)
    right_common = git_common_dir(right)
    return left_common is not None and right_common is not None and left_common == right_common


def ensure_worktree_at_commit(seed_root: Path, target_root: Path, commit: str) -> None:
    git(seed_root, "worktree", "prune", quiet=True)
    if target_root.exists():
        if same_git_common_dir(target_root, seed_root):
            if git_worktree_matches_commit(target_root, commit):
                return
            git(target_root, "reset", "--hard", "HEAD", quiet=True)
            git(target_root, "clean", "-ffdqx", quiet=True)
            git(target_root, "checkout", "--detach", "--force", commit, quiet=True)
            return
        remove_path(target_root)
    target_root.parent.mkdir(parents=True, exist_ok=True)
    git(
        seed_root,
        "worktree",
        "add",
        "--detach",
        "--force",
        str(target_root),
        commit,
        quiet=True,
    )


def git_toplevel(cwd: Path) -> Path:
    completed = run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
    )
    return Path(completed.stdout.strip()).resolve()


def _quiet_test_git_output() -> bool:
    return os.environ.get("FREECM_TEST_GIT_OUTPUT") == "0"
