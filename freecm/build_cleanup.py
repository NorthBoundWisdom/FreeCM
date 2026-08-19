# Internal: conservative cross-language build-output cleanup.

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

from .git_repositories import remove_path
from .workspace_lock import workspace_mutation_lock

BUILD_DIRECTORY_NAME = "build"
PRESERVED_BUILD_CHILDREN = frozenset(
    {
        "dependency_seed_repos",
        "dependency_source_roots",
    }
)


@dataclass(frozen=True)
class CleanBuildResult:
    targets: tuple[str, ...]
    preserved: tuple[str, ...]
    dry_run: bool


def clean_build(repo_root: Path, *, dry_run: bool = False) -> CleanBuildResult:
    root = repo_root.resolve()
    with workspace_mutation_lock(root):
        return _clean_build_unlocked(root, dry_run=dry_run)


def _clean_build_unlocked(repo_root: Path, *, dry_run: bool) -> CleanBuildResult:
    build_root = repo_root / BUILD_DIRECTORY_NAME
    try:
        build_stat = build_root.lstat()
    except FileNotFoundError:
        return CleanBuildResult(targets=(), preserved=(), dry_run=dry_run)
    except OSError as exc:
        raise RuntimeError(f"Unable to inspect build directory {build_root}: {exc}") from exc

    if stat.S_ISLNK(build_stat.st_mode):
        raise RuntimeError(f"Refusing to clean symlinked build directory: {build_root}")
    if not stat.S_ISDIR(build_stat.st_mode):
        raise RuntimeError(f"Refusing to clean non-directory build path: {build_root}")

    try:
        entries = sorted(build_root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise RuntimeError(f"Unable to inspect build directory {build_root}: {exc}") from exc

    targets: list[str] = []
    preserved: list[str] = []
    for entry in entries:
        label = (Path(BUILD_DIRECTORY_NAME) / entry.name).as_posix()
        if entry.name in PRESERVED_BUILD_CHILDREN:
            preserved.append(label)
            continue
        if entry.parent != build_root:
            raise RuntimeError(f"Refusing to remove path outside build directory: {entry}")
        targets.append(label)
        if dry_run:
            continue
        try:
            remove_path(entry)
        except OSError as exc:
            raise RuntimeError(f"Unable to remove build output {entry}: {exc}") from exc

    return CleanBuildResult(
        targets=tuple(targets),
        preserved=tuple(preserved),
        dry_run=dry_run,
    )


__all__ = (
    "BUILD_DIRECTORY_NAME",
    "PRESERVED_BUILD_CHILDREN",
    "CleanBuildResult",
    "clean_build",
)
