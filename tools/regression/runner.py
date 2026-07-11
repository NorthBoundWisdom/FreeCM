# Usage:
#   Library: from tools.regression.runner import run_regression_suite

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import xml.etree.ElementTree as ET  # nosec B405
from collections.abc import Sequence
from pathlib import Path

from .assertions import (
    classify_case_outcome,
    evaluate_case_result,
    get_current_document,
    load_case_report,
    resolve_report_path,
)
from .cases import (
    DEFAULT_APP_CONFIG,
    CaseConfigError,
    collect_case_meta,
    find_case_files,
    is_case_selected,
    load_app_config,
    load_case,
    load_control,
    load_json_object,
    parse_case_invocation,
    prepare_case,
    resolve_app_executable,
    validate_selected_cases,
)
from .execution import execute_case_process
from .models import (
    CaseInvocation,
    CaseMeta,
    CaseResult,
    ControlConfig,
    RegressionAppConfig,
)


class _Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _paint(text: str, *styles: str) -> str:
    if not _color_enabled() or not styles:
        return text
    return "".join(styles) + text + _Color.RESET


def _print_info(label: str, value: str) -> None:
    print(f"{_paint(label + ':', _Color.CYAN, _Color.BOLD)} {value}")


def run_case(
    app: Path,
    case_file: Path,
    case_id: str,
    out_root: Path,
    default_timeout: float,
    app_config: RegressionAppConfig = DEFAULT_APP_CONFIG,
) -> CaseResult:
    prepared = prepare_case(
        case_file,
        case_id,
        out_root,
        default_timeout,
        app_config,
    )
    if isinstance(prepared, CaseResult):
        return prepared
    process = execute_case_process(app, prepared, app_config)
    report = load_case_report(prepared.report_path)
    return evaluate_case_result(prepared, process, report)


def write_junit(
    results: Sequence[CaseResult],
    out_path: Path,
    *,
    suite_name: str = "cppkit_regression",
) -> None:
    tests = len(results)
    failures = sum(1 for result in results if not result.passed)
    duration = sum(result.duration_sec for result in results)
    suite = ET.Element(
        "testsuite",
        name=suite_name,
        tests=str(tests),
        failures=str(failures),
        errors="0",
        skipped="0",
        time=f"{duration:.3f}",
    )
    for result in results:
        case = ET.SubElement(
            suite,
            "testcase",
            classname=str(result.case_dir.parent.name),
            name=result.name,
            time=f"{result.duration_sec:.3f}",
        )
        if not result.passed:
            failure = ET.SubElement(case, "failure", message=result.reason)
            failure.text = f"exit_code={result.exit_code}, report={result.report_path}"
    suites = ET.Element("testsuites")
    suites.append(suite)
    tree = ET.ElementTree(suites)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)


def run_regression_suite(
    *,
    app: Path,
    suite_root: Path,
    out_root: Path,
    control_path: Path,
    app_config: RegressionAppConfig,
    default_timeout: float,
    case_filter: str = "",
    jobs: int = 1,
    junit_name: str = "junit.xml",
) -> int:
    out_root.mkdir(parents=True, exist_ok=True)
    control = load_control(control_path)
    all_meta = [collect_case_meta(path, suite_root) for path in find_case_files(suite_root)]
    selected_meta = [meta for meta in all_meta if is_case_selected(meta, control)]
    if case_filter:
        selected_meta = [meta for meta in selected_meta if case_filter in str(meta.case_file)]

    if not selected_meta:
        print(_paint("[WARN] no cases found", _Color.YELLOW, _Color.BOLD))
        return 0

    validation_errors = validate_selected_cases(selected_meta, app_config)
    if validation_errors:
        print(_paint("[ERROR] invalid case schema", _Color.RED, _Color.BOLD))
        for error in validation_errors:
            print(_paint(f"  - {error}", _Color.RED))
        return 2

    _print_info("App", str(app))
    _print_info("Suite root", str(suite_root))
    _print_info("Cases", str(len(selected_meta)))
    _print_info("Skipped", str(len(all_meta) - len(selected_meta)))
    _print_info("Artifacts", str(out_root))
    _print_info("Control", str(control_path))
    _print_info("Jobs", str(max(1, int(jobs))))

    results: list[CaseResult] = []

    def print_case_result(result: CaseResult) -> None:
        status = "PASS" if result.passed else "FAIL"
        color = _Color.GREEN if result.passed else _Color.RED
        print(
            f"[{_paint(status, color, _Color.BOLD)}: {result.duration_sec:.1f}s] "
            f"{result.name} :: {result.reason}"
        )

    if jobs <= 1:
        for meta in selected_meta:
            result = run_case(
                app,
                meta.case_file,
                meta.case_id,
                out_root,
                default_timeout,
                app_config,
            )
            results.append(result)
            print_case_result(result)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(jobs))) as executor:
            future_to_meta = {
                executor.submit(
                    run_case,
                    app,
                    meta.case_file,
                    meta.case_id,
                    out_root,
                    default_timeout,
                    app_config,
                ): meta
                for meta in selected_meta
            }
            for future in concurrent.futures.as_completed(future_to_meta):
                meta = future_to_meta[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001
                    result = CaseResult(
                        meta.name,
                        meta.case_dir,
                        False,
                        f"runner exception: {exc}",
                        None,
                        0.0,
                        out_root / "unknown_report.json",
                    )
                results.append(result)
                print_case_result(result)

    summary = {
        "total": len(results),
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
        "results": [
            {
                "name": result.name,
                "case_dir": str(result.case_dir),
                "passed": result.passed,
                "reason": result.reason,
                "exit_code": result.exit_code,
                "duration_sec": result.duration_sec,
                "report": str(result.report_path),
            }
            for result in results
        ],
    }
    summary_path = out_root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_junit(results, out_root / junit_name)
    _print_info("Summary", str(summary_path))
    _print_info("JUnit", str(out_root / junit_name))

    if summary["failed"] == 0:
        print(
            _paint(
                f"All cases passed ({summary['passed']}/{summary['total']})",
                _Color.GREEN,
                _Color.BOLD,
            )
        )
        return 0
    print(
        _paint(
            f"Cases failed ({summary['failed']}/{summary['total']})",
            _Color.RED,
            _Color.BOLD,
        )
    )
    return 1


__all__ = (
    "CaseConfigError",
    "CaseInvocation",
    "CaseMeta",
    "CaseResult",
    "ControlConfig",
    "DEFAULT_APP_CONFIG",
    "RegressionAppConfig",
    "classify_case_outcome",
    "collect_case_meta",
    "find_case_files",
    "get_current_document",
    "is_case_selected",
    "load_app_config",
    "load_case",
    "load_control",
    "load_json_object",
    "parse_case_invocation",
    "resolve_app_executable",
    "resolve_report_path",
    "run_case",
    "run_regression_suite",
    "validate_selected_cases",
    "write_junit",
)
