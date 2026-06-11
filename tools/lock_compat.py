# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m tools.lock_compat --repo-root <repo>
#   PYTHONPATH=/path/to/FreeCM python3 -m tools.lock_compat --format json <lock-file>
#   Library: from tools.lock_compat import main

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from freecm.lock_compat import default_lock_compatibility_paths, lock_compatibility_report


def _paths_from_args(args: argparse.Namespace) -> tuple[Path, ...]:
    if args.paths:
        return tuple(Path(path) for path in args.paths)
    repo_root = Path(args.repo_root).resolve()
    return default_lock_compatibility_paths(repo_root)


def _print_text_report(report: dict[str, object]) -> None:
    files = report["files"]
    if not isinstance(files, list):
        raise ValueError("Invalid compatibility report: files must be a list")
    if not files:
        print("no source_roots lock files found")
        return
    for file_report in cast(list[dict[str, Any]], files):
        status = "ok" if file_report["ok"] else "error"
        print(f"{status}: {file_report['path']}")
        problems = file_report["problems"]
        if not isinstance(problems, list):
            raise ValueError("Invalid compatibility report: problems must be a list")
        for problem in cast(list[dict[str, Any]], problems):
            print(f"  [{problem['severity']}] {problem['code']}: {problem['message']}")
            print(f"    suggestion: {problem['suggestion']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check FreeCM lock schema compatibility.")
    parser.add_argument("paths", nargs="*", help="Lock files to check.")
    parser.add_argument(
        "--repo-root", default=".", help="Repository root used when no paths are given."
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = _paths_from_args(args)
    report = lock_compatibility_report(paths)
    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        _print_text_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
