# Usage:
#   PYTHONPATH=/path/to/RepoConfigsMgr python3 -m cpprepomgr.tools.repo_tool format-code <root> [--dry-run] [--no-qml]
#   Library: from cpprepomgr.tools.format_code import format_source_tree

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

CPP_FORMAT_EXTENSIONS = frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"})
QML_FORMAT_EXTENSIONS = frozenset({".qml"})


@dataclass(frozen=True)
class FormatResult:
    cpp_files: int
    cpp_failed: int
    qml_files: int
    qml_failed: int
    clang_format: str | None
    qml_format: str | None

    @property
    def failed(self) -> int:
        return self.cpp_failed + self.qml_failed


def find_executable(name_or_path: str | None) -> str | None:
    if not name_or_path:
        return None
    candidate = Path(name_or_path)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which(name_or_path)


def resolve_clang_format(explicit: str | None = None) -> str | None:
    return (
        find_executable(explicit)
        or find_executable(os.environ.get("CLANG_FORMAT"))
        or find_executable("clang-format")
    )


def resolve_qml_format(explicit: str | None = None) -> str | None:
    return (
        find_executable(explicit)
        or find_executable(os.environ.get("QMLFORMAT"))
        or find_executable("qmlformat")
    )


def ensure_trailing_newline(path: Path) -> None:
    try:
        content = path.read_bytes()
        if not content or not content.endswith(b"\n"):
            path.write_bytes(content + b"\n")
    except OSError:
        return


def _run_formatter(command: list[str]) -> bool:
    completed = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def _iter_files(root: Path, suffixes: Iterable[str]) -> list[Path]:
    suffix_filter = {suffix.lower() for suffix in suffixes}
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in suffix_filter
    )


def format_source_tree(
    root: Path,
    *,
    clang_format: str | None = None,
    qml_format: str | None = None,
    include_qml: bool = True,
    dry_run: bool = False,
) -> FormatResult:
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    clang_tool = resolve_clang_format(clang_format)
    qml_tool = resolve_qml_format(qml_format) if include_qml else None

    cpp_files = _iter_files(root, CPP_FORMAT_EXTENSIONS)
    qml_files = _iter_files(root, QML_FORMAT_EXTENSIONS) if qml_tool else []

    cpp_failed = 0
    qml_failed = 0

    if clang_tool:
        for path in cpp_files:
            command = [clang_tool, "--dry-run", "--Werror", str(path)] if dry_run else [clang_tool, "-i", str(path)]
            if _run_formatter(command):
                if not dry_run:
                    ensure_trailing_newline(path)
            else:
                cpp_failed += 1
    else:
        cpp_failed = len(cpp_files)

    if qml_tool:
        for path in qml_files:
            command = [qml_tool, str(path)] if dry_run else [qml_tool, "-i", str(path)]
            if _run_formatter(command):
                if not dry_run:
                    ensure_trailing_newline(path)
            else:
                qml_failed += 1

    return FormatResult(
        cpp_files=len(cpp_files),
        cpp_failed=cpp_failed,
        qml_files=len(qml_files),
        qml_failed=qml_failed,
        clang_format=clang_tool,
        qml_format=qml_tool,
    )
