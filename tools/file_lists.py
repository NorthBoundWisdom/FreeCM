# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.tools.repo_tool list-files <dir> --suffix cpp,h [--recursive]
#   Library: from tools.file_lists import list_filenames, normalize_suffixes

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def normalize_suffixes(suffixes: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for suffix in suffixes:
        suffix = suffix.strip().lower()
        if not suffix:
            continue
        if not suffix.startswith("."):
            suffix = f".{suffix}"
        normalized.append(suffix)
    return tuple(dict.fromkeys(normalized))


def list_filenames(
    directory: Path,
    *,
    suffixes: Iterable[str] | None = None,
    recursive: bool = False,
    prefix: str = "",
) -> list[str]:
    directory = directory.resolve()
    if not directory.is_dir():
        raise NotADirectoryError(str(directory))

    suffix_filter = None if suffixes is None else set(normalize_suffixes(suffixes))
    prefix = prefix.replace("\\", "/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    iterator = directory.rglob("*") if recursive else directory.iterdir()
    names: list[str] = []
    for entry in iterator:
        if not entry.is_file():
            continue
        if suffix_filter is not None and entry.suffix.lower() not in suffix_filter:
            continue
        name = entry.relative_to(directory).as_posix() if recursive else entry.name
        names.append(f"{prefix}{name}")
    return sorted(names, key=str.lower)
