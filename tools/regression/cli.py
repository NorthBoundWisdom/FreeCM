# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m tools.regression.cli --app <app> --suite-root <cases> --out <out>
#   regression-tool --app <app> --suite-root <cases> --out <out>

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .runner import CaseConfigError, load_app_config, resolve_app_executable, run_regression_suite


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Config-driven regression runner.")
    parser.add_argument(
        "--app", required=True, help="Executable path, app bundle, or build directory."
    )
    parser.add_argument(
        "--suite-root", required=True, help="Root directory containing case.json files."
    )
    parser.add_argument("--out", required=True, help="Artifact output root.")
    parser.add_argument("--control", default="", help="Optional case control JSON.")
    parser.add_argument("--config", default="", help="Optional runner app config JSON.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--case", default="", help="Run only cases whose path contains this substring."
    )
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--junit", default="junit.xml")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        app_config = load_app_config(Path(args.config).resolve() if args.config else None)
        app = resolve_app_executable(args.app, app_config)
        if app is None:
            print(f"error: app executable not found from --app={args.app}", file=sys.stderr)
            return 2
        suite_root = Path(args.suite_root).resolve()
        if not suite_root.is_dir():
            print(f"error: suite root not found: {suite_root}", file=sys.stderr)
            return 2
        control_path = (
            Path(args.control).resolve() if args.control else suite_root / "case_control.json"
        )
        return run_regression_suite(
            app=app,
            suite_root=suite_root,
            out_root=Path(args.out).resolve(),
            control_path=control_path,
            app_config=app_config,
            default_timeout=args.timeout,
            case_filter=args.case,
            jobs=args.jobs,
            junit_name=args.junit,
        )
    except (OSError, ValueError, CaseConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
