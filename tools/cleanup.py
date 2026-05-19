# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m cpprepomgr.tools.repo_tool remove-empty-dirs --root <dir> [--dry-run]
#   Library: from tools.cleanup import collect_empty_dirs, remove_empty_dirs

from __future__ import annotations

from pathlib import Path
from typing import Iterable

DEFAULT_EXCLUDED_DIR_NAMES = frozenset({".git"})


def collect_empty_dirs(
    root: Path,
    *,
    excluded_dir_names: Iterable[str] = DEFAULT_EXCLUDED_DIR_NAMES,
) -> list[Path]:
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    excluded = {name for name in excluded_dir_names if name}
    result: list[Path] = []

    def visit(directory: Path) -> bool:
        try:
            entries = list(directory.iterdir())
        except OSError:
            return False

        empty_after_cleanup = True
        for entry in entries:
            if entry.is_symlink():
                empty_after_cleanup = False
                continue
            if entry.is_dir():
                if entry.name in excluded:
                    empty_after_cleanup = False
                    continue
                if visit(entry):
                    result.append(entry)
                else:
                    empty_after_cleanup = False
                continue
            empty_after_cleanup = False
        return empty_after_cleanup

    visit(root)
    return sorted(set(result), key=lambda path: (len(path.parts), str(path)), reverse=True)


def remove_empty_dirs(
    root: Path,
    *,
    dry_run: bool = False,
    excluded_dir_names: Iterable[str] = DEFAULT_EXCLUDED_DIR_NAMES,
) -> list[Path]:
    candidates = collect_empty_dirs(root, excluded_dir_names=excluded_dir_names)
    if dry_run:
        return candidates

    removed: list[Path] = []
    for directory in candidates:
        try:
            directory.rmdir()
        except OSError:
            continue
        removed.append(directory)
    return removed
