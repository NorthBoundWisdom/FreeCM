# Usage:
#   Library: from tools.regression.runner import run_regression_suite

from __future__ import annotations

import concurrent.futures
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
from .reporting import (
    RegressionConsoleReporter,
    build_summary,
    write_junit,
    write_summary,
)


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
    reporter = RegressionConsoleReporter()
    out_root.mkdir(parents=True, exist_ok=True)
    control = load_control(control_path)
    all_meta = [collect_case_meta(path, suite_root) for path in find_case_files(suite_root)]
    selected_meta = [meta for meta in all_meta if is_case_selected(meta, control)]
    if case_filter:
        selected_meta = [meta for meta in selected_meta if case_filter in str(meta.case_file)]

    if not selected_meta:
        reporter.warn_no_cases()
        return 0

    validation_errors = validate_selected_cases(selected_meta, app_config)
    if validation_errors:
        reporter.report_validation_errors(validation_errors)
        return 2

    reporter.report_suite_start(
        app=app,
        suite_root=suite_root,
        selected_count=len(selected_meta),
        skipped_count=len(all_meta) - len(selected_meta),
        out_root=out_root,
        control_path=control_path,
        jobs=jobs,
    )

    results: list[CaseResult] = []
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
            reporter.report_case_result(result)
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
                reporter.report_case_result(result)

    summary = build_summary(results)
    summary_path = out_root / "summary.json"
    junit_path = out_root / junit_name
    write_summary(summary, summary_path)
    write_junit(results, junit_path)
    reporter.report_artifacts(summary_path, junit_path)
    reporter.report_final(summary)
    return 0 if summary["failed"] == 0 else 1


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
