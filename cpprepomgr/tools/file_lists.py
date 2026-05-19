# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m cpprepomgr.tools.repo_tool qrc-entries <search-path> <suffix> [--base <base>] [--output <file>]
#   Library: from cpprepomgr.tools.file_lists import generate_qrc_entries

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Sequence

from tools.file_lists import normalize_suffixes


CPP_EXTENSIONS = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".c++",
        ".h",
        ".hh",
        ".hpp",
        ".hxx",
    }
)


def _is_probable_path(value: str) -> bool:
    return (
        os.path.isabs(value)
        or os.path.isdir(value)
        or os.sep in value
        or "/" in value
        or "\\" in value
    )


def split_legacy_qrc_suffix_args(raw_suffixes: Sequence[str]) -> tuple[list[str], str | None]:
    if len(raw_suffixes) <= 1:
        return list(raw_suffixes), None
    last = raw_suffixes[-1]
    if _is_probable_path(last):
        return list(raw_suffixes[:-1]), last
    return list(raw_suffixes), None


def generate_qrc_entries(
    search_path: Path,
    suffixes: Iterable[str],
    *,
    base_path: Path | None = None,
    indent: str = "    ",
) -> list[str]:
    search_path = search_path.resolve()
    if not search_path.is_dir():
        raise NotADirectoryError(str(search_path))
    base_path = search_path if base_path is None else base_path.resolve()
    if not base_path.is_dir():
        raise NotADirectoryError(str(base_path))

    suffix_filter = set(normalize_suffixes(suffixes))
    if not suffix_filter:
        raise ValueError("At least one suffix is required")

    files_by_dir: dict[str, list[str]] = {}
    for root, _dirs, files in os.walk(search_path):
        root_path = Path(root).resolve()
        for filename in files:
            if Path(filename).suffix.lower() not in suffix_filter:
                continue
            file_path = root_path / filename
            try:
                relative_path = file_path.relative_to(base_path)
            except ValueError:
                relative_path = file_path.relative_to(search_path)
            dir_key = relative_path.parent.as_posix()
            if dir_key == ".":
                dir_key = ""
            files_by_dir.setdefault(dir_key, []).append(
                f"{indent}<file>{relative_path.as_posix()}</file>"
            )

    entries: list[str] = []
    sorted_dirs = sorted(files_by_dir)
    for index, dir_key in enumerate(sorted_dirs):
        entries.extend(sorted(files_by_dir[dir_key]))
        if index != len(sorted_dirs) - 1:
            entries.append("")
    return entries
