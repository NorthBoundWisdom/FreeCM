#!/usr/bin/env python3
# Usage:
#   python3 /path/to/FreeCM/tools/remove_old_build.py --repo-root <repo> [--dry-run]
#   python3 /path/to/FreeCM/tools/remove_old_build.py --repo-root <repo> --remove-root-path <path>
#   PYTHONPATH=/path/to/FreeCM python3 -m tools.remove_old_build --repo-root <repo>
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.tools.repo_tool remove-old-build --repo-root <repo>

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_BUILD_DIR = Path("build")
DEFAULT_PRESERVED_BUILD_CHILDREN = (
    Path("dependency_seed_repos"),
    Path("dependency_source_roots"),
)
DEFAULT_REMOVABLE_ROOT_PATHS = (
    Path("DerivedData"),
    Path(".build"),
    Path(".swiftpm"),
)


@dataclass(frozen=True)
class OldBuildCleanupResult:
    removed_count: int
    preserved_count: int
    dry_run: bool


def git_toplevel(cwd: Path) -> Path | None:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    return Path(completed.stdout.strip()).resolve()


def resolve_repo_root(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        repo_root = Path(explicit).expanduser().resolve()
        if not repo_root.is_dir():
            raise NotADirectoryError(str(repo_root))
        return repo_root
    return git_toplevel(Path.cwd()) or Path.cwd().resolve()


def resolve_repo_path(repo_root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (repo_root / path).resolve()


def _relative_label(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _assert_removable_repo_child(repo_root: Path, build_dir: Path, path: Path) -> None:
    resolved_repo = repo_root.resolve()
    resolved_build = build_dir.resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    if resolved_path == resolved_repo or not _is_relative_to(resolved_path, resolved_repo):
        raise RuntimeError(f"refusing to remove path outside repository: {path}")
    if resolved_path == resolved_build:
        raise RuntimeError(f"refusing to remove build directory itself: {path}")


def _remove_path(repo_root: Path, build_dir: Path, path: Path, *, dry_run: bool) -> bool:
    _assert_removable_repo_child(repo_root, build_dir, path)
    if not path.exists() and not path.is_symlink():
        return False

    label = _relative_label(repo_root, path)
    if dry_run:
        print(f"would remove {label}")
        return True

    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        raise RuntimeError(f"unsupported removable path type: {path}")
    print(f"removed {label}")
    return True


def _normalize_build_children(build_dir: Path, paths: Iterable[str | Path]) -> set[Path]:
    result: set[Path] = set()
    for value in paths:
        path = Path(value).expanduser()
        result.add(path.resolve() if path.is_absolute() else (build_dir / path).resolve())
    return result


def remove_old_build(
    *,
    repo_root: Path,
    build_dir: Path = DEFAULT_BUILD_DIR,
    preserved_build_children: Iterable[str | Path] = DEFAULT_PRESERVED_BUILD_CHILDREN,
    remove_root_paths: Iterable[str | Path] = DEFAULT_REMOVABLE_ROOT_PATHS,
    dry_run: bool = False,
) -> OldBuildCleanupResult:
    repo_root = repo_root.expanduser().resolve()
    resolved_build_dir = resolve_repo_path(repo_root, build_dir)
    preserved_paths = _normalize_build_children(resolved_build_dir, preserved_build_children)
    removed_count = 0
    preserved_count = 0

    if resolved_build_dir.is_symlink():
        raise RuntimeError(f"refusing to clean symlinked build directory: {resolved_build_dir}")

    if resolved_build_dir.is_dir():
        for child in sorted(resolved_build_dir.iterdir(), key=lambda item: item.name):
            if child.resolve(strict=False) in preserved_paths:
                print(f"preserved {_relative_label(repo_root, child)}")
                preserved_count += 1
                continue
            if _remove_path(repo_root, resolved_build_dir, child, dry_run=dry_run):
                removed_count += 1
    elif resolved_build_dir.exists():
        raise RuntimeError(f"refusing to remove non-directory build path: {resolved_build_dir}")

    for path in (resolve_repo_path(repo_root, value) for value in remove_root_paths):
        if _remove_path(repo_root, resolved_build_dir, path, dry_run=dry_run):
            removed_count += 1

    if removed_count == 0:
        print("no old build outputs found")
    return OldBuildCleanupResult(
        removed_count=removed_count,
        preserved_count=preserved_count,
        dry_run=dry_run,
    )


def _default_or_empty(disabled: bool, defaults: Sequence[Path], values: Sequence[str]) -> list[str | Path]:
    result: list[str | Path] = [] if disabled else list(defaults)
    result.extend(values)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Remove stale repository build outputs while preserving FreeCM "
            "dependency roots under build/."
        )
    )
    parser.add_argument("--repo-root", help="Repository root. Defaults to the current git root or cwd.")
    parser.add_argument(
        "--build-dir",
        default=str(DEFAULT_BUILD_DIR),
        help='Build directory to clean. Defaults to "build" under the repository root.',
    )
    parser.add_argument(
        "--preserve-build-child",
        action="append",
        default=[],
        help="Path under the build directory to preserve. May be repeated.",
    )
    parser.add_argument(
        "--no-default-preserves",
        action="store_true",
        help="Do not preserve the default dependency_seed_repos/dependency_source_roots children.",
    )
    parser.add_argument(
        "--remove-root-path",
        action="append",
        default=[],
        help="Repository-root path to remove in addition to default root outputs. May be repeated.",
    )
    parser.add_argument(
        "--no-default-root-paths",
        action="store_true",
        help="Do not remove the default DerivedData, .build, and .swiftpm root paths.",
    )
    parser.add_argument(
        "--include-xcodeproj",
        action="store_true",
        help="Remove root-level *.xcodeproj directories. Use only when the project is generated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print paths that would be removed without deleting them.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        repo_root = resolve_repo_root(args.repo_root)
        root_paths = _default_or_empty(
            args.no_default_root_paths,
            DEFAULT_REMOVABLE_ROOT_PATHS,
            args.remove_root_path,
        )
        if args.include_xcodeproj:
            root_paths.extend(path.name for path in sorted(repo_root.glob("*.xcodeproj")))
        remove_old_build(
            repo_root=repo_root,
            build_dir=Path(args.build_dir),
            preserved_build_children=_default_or_empty(
                args.no_default_preserves,
                DEFAULT_PRESERVED_BUILD_CHILDREN,
                args.preserve_build_child,
            ),
            remove_root_paths=root_paths,
            dry_run=args.dry_run,
        )
        return 0
    except (FileNotFoundError, NotADirectoryError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
