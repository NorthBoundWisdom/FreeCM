# Usage:
#   python3 scripts/test-fast.py
#   python3 scripts/test-fast.py -v
#   python3 scripts/test-fast.py --module tests.test_cmake_workflow

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FAST_TEST_MODULES = (
    "tests.test_android_workflow",
    "tests.test_asset_seeds",
    "tests.test_atomic_write",
    "tests.test_cmake_tools",
    "tests.test_dependency_lock",
    "tests.test_dependency_models",
    "tests.test_dotnet_workflow",
    "tests.test_hooks",
    "tests.test_package_tools",
    "tests.test_regression_tools",
    "tests.test_repo_tools",
    "tests.test_version",
)
INTEGRATION_HEAVY_MODULES = (
    "tests.test_cmake_workflow",
    "tests.test_dependency_roots",
    "tests.test_examples",
    "tests.test_repomgrswift",
)


def selected_test_modules(module_names: tuple[str, ...]) -> tuple[str, ...]:
    if module_names:
        return module_names
    return FAST_TEST_MODULES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the local fast Python test profile. CI still runs full unittest "
            "discovery, including integration-heavy dependency materialization tests."
        )
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Use verbose unittest output.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List included and excluded test modules without running them.",
    )
    parser.add_argument(
        "--module",
        action="append",
        default=[],
        metavar="MODULE",
        help=(
            "Run a directly related unittest module instead of the default fast profile. "
            "Repeat for more than one module."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    module_names = selected_test_modules(tuple(args.module))
    if args.list:
        print("selected test modules:")
        for module in module_names:
            print(f"  {module}")
        if not args.module:
            print("integration-heavy modules skipped by fast profile:")
            for module in INTEGRATION_HEAVY_MODULES:
                print(f"  {module}")
        return 0

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite(loader.loadTestsFromName(module) for module in module_names)
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
