#!/usr/bin/env python3
# Usage:
#   python3 /path/to/FreeCM/tools/host_clang_format.py <file-or-dir> --host-root <repo>
#   PYTHONPATH=/path/to/FreeCM python3 -m tools.host_clang_format <file-or-dir> --host-root <repo>

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


CPP_EXTENSIONS = frozenset({".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx"})
HOST_STYLE_FILENAMES = (".clang-format", ".clangformat", "_clang-format")
DEFAULT_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        ".vs",
        ".vscode",
        "__pycache__",
        "build",
        "cmake-build-debug",
        "cmake-build-release",
        "node_modules",
    }
)
CLANG_FORMAT_CONFIG_KEY = "freecm.clangFormatPath"


@dataclass(frozen=True)
class HostClangFormatResult:
    matched_files: int
    formatted_files: int
    failed_files: int
    clang_format: str
    style_file: Path

    @property
    def ok(self) -> bool:
        return self.failed_files == 0


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


def git_config(repo_root: Path, key: str) -> str | None:
    completed = subprocess.run(
        ["git", "config", "--get", key],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    return completed.stdout.strip()


def find_executable(name_or_path: str | None) -> str | None:
    if not name_or_path:
        return None
    candidate = Path(name_or_path).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which(name_or_path)


def resolve_clang_format(host_root: Path, explicit: str | None = None) -> str:
    resolved = (
        find_executable(explicit)
        or find_executable(git_config(host_root, CLANG_FORMAT_CONFIG_KEY))
        or find_executable(os.environ.get("CLANG_FORMAT"))
        or find_executable("clang-format")
    )
    if resolved is None:
        raise FileNotFoundError(
            "clang-format was not found; pass --clang-format, set CLANG_FORMAT, "
            f"or configure git key {CLANG_FORMAT_CONFIG_KEY}"
        )
    return resolved


def resolve_host_root(explicit: str | None) -> Path:
    if explicit:
        host_root = Path(explicit).expanduser().resolve()
        if not host_root.is_dir():
            raise NotADirectoryError(str(host_root))
        return host_root
    repo_root = git_toplevel(Path.cwd())
    if repo_root is None:
        raise RuntimeError("Unable to locate host repository; pass --host-root")
    return repo_root


def resolve_style_file(host_root: Path, explicit: str | None = None) -> Path:
    if explicit:
        style_file = Path(explicit).expanduser().resolve()
        if not style_file.is_file():
            raise FileNotFoundError(f"clang-format style file not found: {style_file}")
        return style_file
    for filename in HOST_STYLE_FILENAMES:
        candidate = host_root / filename
        if candidate.is_file():
            return candidate.resolve()
    names = ", ".join(HOST_STYLE_FILENAMES)
    raise FileNotFoundError(f"No host clang-format style file found in {host_root}: {names}")


def normalize_suffixes(values: Sequence[str]) -> frozenset[str]:
    suffixes: set[str] = set()
    for value in values:
        for part in value.split(","):
            suffix = part.strip().lower()
            if not suffix:
                continue
            suffixes.add(suffix if suffix.startswith(".") else f".{suffix}")
    return frozenset(suffixes or CPP_EXTENSIONS)


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def read_files_from(path: str) -> list[Path]:
    if path == "-":
        text = sys.stdin.read()
    else:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    return [Path(line.strip()) for line in text.splitlines() if line.strip()]


def collect_candidate_files(
    inputs: Iterable[Path],
    *,
    suffixes: frozenset[str],
    excluded_dirs: Sequence[Path],
    excluded_dir_names: frozenset[str],
) -> tuple[Path, ...]:
    files: list[Path] = []
    seen: set[Path] = set()
    resolved_excluded_dirs = tuple(path.expanduser().resolve() for path in excluded_dirs)

    def excluded(path: Path) -> bool:
        if any(part in excluded_dir_names for part in path.parts):
            return True
        return any(is_relative_to(path, excluded_dir) for excluded_dir in resolved_excluded_dirs)

    def add_file(path: Path) -> None:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            return
        if not resolved.is_file() or resolved.suffix.lower() not in suffixes:
            return
        if excluded(resolved):
            return
        seen.add(resolved)
        files.append(resolved)

    for raw_input in inputs:
        resolved_input = raw_input.expanduser().resolve()
        if resolved_input.is_dir():
            for child in sorted(resolved_input.rglob("*")):
                add_file(child)
        else:
            add_file(resolved_input)

    return tuple(files)


def run_clang_format(
    files: Sequence[Path],
    *,
    clang_format: str,
    style_file: Path,
    dry_run: bool = False,
) -> HostClangFormatResult:
    failed_files = 0
    formatted_files = 0
    style_arg = f"-style=file:{style_file}"
    for path in files:
        command = (
            [clang_format, style_arg, "--dry-run", "--Werror", str(path)]
            if dry_run
            else [clang_format, style_arg, "-i", str(path)]
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            print(f"failed: {path}" + (f"\n{detail}" if detail else ""), file=sys.stderr)
            failed_files += 1
            continue
        formatted_files += 1
        print(("checked: " if dry_run else "formatted: ") + str(path))
    return HostClangFormatResult(
        matched_files=len(files),
        formatted_files=formatted_files,
        failed_files=failed_files,
        clang_format=clang_format,
        style_file=style_file,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Format selected C/C++ files with the host repository clang-format style.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files or directories to format. Directories are scanned recursively.",
    )
    parser.add_argument("--host-root", help="Host repository root. Defaults to the current git root.")
    parser.add_argument("--style-file", help="Explicit host .clang-format file.")
    parser.add_argument("--clang-format", help="clang-format executable path or command name.")
    parser.add_argument(
        "--suffix",
        action="append",
        default=[],
        help="Comma-separated suffixes to include. Defaults to C/C++ source/header suffixes.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Directory path to exclude. May be repeated.",
    )
    parser.add_argument(
        "--exclude-dir-name",
        action="append",
        default=[],
        help="Directory basename to exclude. May be repeated.",
    )
    parser.add_argument(
        "--files-from",
        action="append",
        default=[],
        help="Read newline-separated files/directories from a file, or '-' for stdin.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Check formatting without changing files.")
    parser.add_argument("--quiet", action="store_true", help="Print only the final summary and errors.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        host_root = resolve_host_root(args.host_root)
        style_file = resolve_style_file(host_root, args.style_file)
        clang_format = resolve_clang_format(host_root, args.clang_format)
        inputs = [Path(path) for path in args.paths]
        for files_from in args.files_from:
            inputs.extend(read_files_from(files_from))
        if not inputs:
            parser.error("provide at least one path or --files-from")

        suffixes = normalize_suffixes(args.suffix)
        excluded_dir_names = frozenset(DEFAULT_EXCLUDED_DIR_NAMES).union(args.exclude_dir_name)
        files = collect_candidate_files(
            inputs,
            suffixes=suffixes,
            excluded_dirs=tuple(Path(path) for path in args.exclude),
            excluded_dir_names=excluded_dir_names,
        )
        if args.quiet:
            with open(os.devnull, "w", encoding="utf-8") as devnull:
                original_stdout = sys.stdout
                sys.stdout = devnull
                try:
                    result = run_clang_format(
                        files,
                        clang_format=clang_format,
                        style_file=style_file,
                        dry_run=args.dry_run,
                    )
                finally:
                    sys.stdout = original_stdout
        else:
            result = run_clang_format(
                files,
                clang_format=clang_format,
                style_file=style_file,
                dry_run=args.dry_run,
            )
        action = "checked" if args.dry_run else "formatted"
        print(
            f"{action}: {result.formatted_files}/{result.matched_files}, "
            f"failed: {result.failed_files}, style: {result.style_file}, "
            f"clang-format: {result.clang_format}"
        )
        return 0 if result.ok else 1
    except (FileNotFoundError, NotADirectoryError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
