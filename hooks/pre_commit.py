#!/usr/bin/env python3
# Internal:
#   Python implementation for the shared pre-commit hook.
#   Normally invoked by hooks/pre-commit from the host repository.

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024

CLANG_FORMAT_CONFIG_KEY = "repoconfigsmgr.clangFormatPath"
QMLFORMAT_CONFIG_KEY = "repoconfigsmgr.qmlformatPath"
SOURCE_ROOTS_CONFIG_KEY = "repoconfigsmgr.hooks.sourceRoots"
EXCLUDED_DIRS_CONFIG_KEY = "repoconfigsmgr.hooks.excludeDirs"

DEFAULT_SOURCE_ROOTS = (Path("SourceCode"),)
DEFAULT_EXCLUDED_DIRS = (Path("SourceCode/thirdparty"),)

CPP_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx"}
QML_EXTENSIONS = {".qml", ".js", ".mjs"}


@dataclass(frozen=True)
class LargeFile:
    path: Path
    size_bytes: int

    @property
    def size_mb(self) -> str:
        return f"{self.size_bytes / (1024 * 1024):.2f}"


def run_git(repo_root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=check,
    )


def get_repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


def get_git_config(repo_root: Path, key: str) -> str | None:
    result = run_git(repo_root, ["config", "--get", key], check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def parse_path_list(value: str | None, default: tuple[Path, ...]) -> tuple[Path, ...]:
    if value is None:
        return default
    paths = tuple(Path(part.strip()) for part in value.split(";") if part.strip())
    return paths or default


def resolve_tool_cmd(repo_root: Path, config_key: str, label: str) -> str | None:
    configured = get_git_config(repo_root, config_key)
    if not configured:
        print(f"Error: {label} path is not configured.")
        print("Run: python hooks/install.py")
        return None

    configured_path = Path(configured).expanduser()
    if not configured_path.is_file():
        print(f"Error: configured {label} not found: {configured_path}")
        return None
    if not os.access(configured_path, os.X_OK):
        print(f"Error: configured {label} is not executable: {configured_path}")
        return None
    return str(configured_path)


def resolve_optional_tool_cmd(repo_root: Path, config_key: str, label: str) -> str | None:
    configured = get_git_config(repo_root, config_key)
    if not configured:
        return None

    configured_path = Path(configured).expanduser()
    if not configured_path.is_file():
        print(f"Warning: configured {label} not found; skipping optional formatter: {configured_path}")
        return None
    if not os.access(configured_path, os.X_OK):
        print(f"Warning: configured {label} is not executable; skipping optional formatter: {configured_path}")
        return None
    return str(configured_path)


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def is_under_configured_roots(
    path: Path,
    *,
    source_roots: tuple[Path, ...],
    excluded_dirs: tuple[Path, ...],
) -> bool:
    if not any(is_relative_to(path, source_root) for source_root in source_roots):
        return False
    return not any(is_relative_to(path, excluded) for excluded in excluded_dirs)


def is_cpp_formattable(
    path: Path,
    *,
    source_roots: tuple[Path, ...],
    excluded_dirs: tuple[Path, ...],
) -> bool:
    return path.suffix.lower() in CPP_EXTENSIONS and is_under_configured_roots(
        path,
        source_roots=source_roots,
        excluded_dirs=excluded_dirs,
    )


def is_qml_formattable(
    path: Path,
    *,
    source_roots: tuple[Path, ...],
    excluded_dirs: tuple[Path, ...],
) -> bool:
    return path.suffix.lower() in QML_EXTENSIONS and is_under_configured_roots(
        path,
        source_roots=source_roots,
        excluded_dirs=excluded_dirs,
    )


def get_staged_paths(repo_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    names = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    return [Path(name) for name in names if name]


def is_staged_binary(repo_root: Path, path: Path) -> bool:
    result = run_git(repo_root, ["diff", "--cached", "--numstat", "--", str(path)], check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return False
    first_field = result.stdout.split("\t", 1)[0]
    return first_field == "-"


def is_regular_staged_worktree_file(repo_root: Path, path: Path) -> bool:
    abs_path = repo_root / path
    return abs_path.is_file() and not abs_path.is_symlink()


def normalize_text_file(path: Path) -> bool:
    original = path.read_bytes()
    data = original.replace(b"\r\n", b"\n")
    data = re.sub(rb"[ \t]+(?=\n)", b"", data)
    data = re.sub(rb"[ \t]+$", b"", data)
    if data == original:
        return False
    path.write_bytes(data)
    return True


def stage_path(repo_root: Path, path: Path) -> None:
    subprocess.run(["git", "add", "-u", "--", str(path)], cwd=repo_root, check=True)


def normalize_staged_text_files(repo_root: Path, paths: list[Path]) -> bool:
    success = True
    for path in paths:
        if is_staged_binary(repo_root, path):
            continue
        abs_path = repo_root / path
        if not is_regular_staged_worktree_file(repo_root, path):
            continue
        try:
            if normalize_text_file(abs_path):
                stage_path(repo_root, path)
                print(f"Normalized whitespace/EOL: {path}")
        except OSError as exc:
            print(f"Error normalizing {path}: {exc}")
            success = False
    return success


def find_large_files(repo_root: Path, paths: list[Path]) -> list[LargeFile]:
    large_files: list[LargeFile] = []
    for path in paths:
        abs_path = repo_root / path
        if not is_regular_staged_worktree_file(repo_root, path):
            continue
        size = abs_path.stat().st_size
        if size > MAX_FILE_SIZE_BYTES:
            large_files.append(LargeFile(path=path, size_bytes=size))
    return large_files


def format_file(repo_root: Path, file_path: Path, formatter_cmd: str, *, qml: bool) -> bool:
    abs_path = repo_root / file_path
    if not is_regular_staged_worktree_file(repo_root, file_path):
        print(f"Skipping non-regular staged file: {file_path}")
        return True
    cmd = [formatter_cmd, "-i", str(abs_path)] if qml else [formatter_cmd, "-style=file", "-i", str(abs_path)]
    result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").rstrip()
        print(f"Error formatting {file_path}: {stderr}")
        return False
    stage_path(repo_root, file_path)
    return True


def format_staged_files(repo_root: Path, paths: list[Path]) -> bool:
    source_roots = parse_path_list(
        get_git_config(repo_root, SOURCE_ROOTS_CONFIG_KEY),
        DEFAULT_SOURCE_ROOTS,
    )
    excluded_dirs = parse_path_list(
        get_git_config(repo_root, EXCLUDED_DIRS_CONFIG_KEY),
        DEFAULT_EXCLUDED_DIRS,
    )

    cpp_files = [
        path
        for path in paths
        if is_cpp_formattable(path, source_roots=source_roots, excluded_dirs=excluded_dirs)
    ]
    qml_files = [
        path
        for path in paths
        if is_qml_formattable(path, source_roots=source_roots, excluded_dirs=excluded_dirs)
    ]

    success = True
    if cpp_files:
        clang_format = resolve_tool_cmd(repo_root, CLANG_FORMAT_CONFIG_KEY, "clang-format")
        if clang_format is None:
            return False
        for path in cpp_files:
            print(f"Formatting C/C++: {path}")
            success = format_file(repo_root, path, clang_format, qml=False) and success
    else:
        print("No C/C++ files to format.")

    if qml_files:
        qmlformat = resolve_optional_tool_cmd(repo_root, QMLFORMAT_CONFIG_KEY, "qmlformat")
        if qmlformat is None:
            print(f"Skipping QML/JS formatting: optional qmlformat is not configured ({len(qml_files)} file(s)).")
        else:
            for path in qml_files:
                print(f"Formatting QML/JS: {path}")
                success = format_file(repo_root, path, qmlformat, qml=True) and success
    else:
        print("No QML/JS files to format.")

    return success


def print_large_file_error(large_files: list[LargeFile]) -> None:
    print("Commit rejected: files larger than 15MB detected")
    for item in large_files:
        print(f"  - {item.path} ({item.size_mb} MB)")
    print("Please remove these files from staging or use Git LFS for large files.")


def run_pre_commit(repo_root: Path) -> int:
    staged_paths = get_staged_paths(repo_root)
    if not staged_paths:
        return 0

    success = True
    large_files = find_large_files(repo_root, staged_paths)
    if large_files:
        print_large_file_error(large_files)
        success = False

    if not normalize_staged_text_files(repo_root, staged_paths):
        success = False

    if not format_staged_files(repo_root, staged_paths):
        success = False

    return 0 if success else 1


def main() -> int:
    try:
        return run_pre_commit(get_repo_root())
    except subprocess.CalledProcessError as exc:
        print(f"Error running git command: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
