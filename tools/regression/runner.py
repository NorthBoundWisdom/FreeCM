# Usage:
#   Library: from tools.regression.runner import run_regression_suite

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess  # nosec B404
import sys
import time
import xml.etree.ElementTree as ET  # nosec B405
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

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
    resolve_app_executable,
    validate_selected_cases,
)
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


def _resolve_token(current: Any, token: str) -> Any:
    if "[" in token and token.endswith("]"):
        key = token[: token.index("[")]
        index = int(token[token.index("[") + 1 : -1])
        if key:
            current = current[key]
        return current[index]
    return current[token]


def get_current_document(report: Mapping[str, Any]) -> Mapping[str, Any] | None:
    docs = report.get("documents", [])
    if not isinstance(docs, list):
        return None
    for doc in docs:
        if isinstance(doc, dict) and doc.get("is_current"):
            return doc
    return None


def resolve_report_path(report: Mapping[str, Any], path: str) -> Any:
    if path == "documents_count":
        docs = report.get("documents", [])
        return len(docs) if isinstance(docs, list) else 0
    if path.startswith("current_document."):
        current_doc = get_current_document(report)
        if current_doc is None:
            raise KeyError("current document not found")
        current: Any = current_doc
        path = path[len("current_document.") :]
    else:
        current = report
    for token in path.split("."):
        if token:
            current = _resolve_token(current, token)
    return current


def classify_case_outcome(
    invocation: CaseInvocation,
    timed_out: bool,
    proc_return_code: int | None,
    report: Mapping[str, Any] | None,
) -> str:
    if timed_out:
        return "timeout"
    if report is None:
        if proc_return_code in (None, 0):
            return "pass"
        return "process_crash"

    if invocation.mode == "script":
        script_result = report.get("script_result", {})
        if isinstance(script_result, dict) and script_result.get("has_error") is True:
            return "assert_fail"
    elif invocation.mode in {"scenario", "viewer2d"}:
        result_key = "viewer_perf_result" if invocation.mode == "viewer2d" else "scenario_result"
        mode_result = report.get(result_key, {})
        if isinstance(mode_result, dict) and mode_result.get("ok") is False:
            return "scenario_fail"

    return "pass" if proc_return_code in (None, 0) else "process_crash"


def _as_text(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def _format_command_tokens(
    tokens: Sequence[str],
    *,
    invocation: CaseInvocation,
    target_path: Path | None,
    report_path: Path,
) -> list[str]:
    result: list[str] = []
    backend_flag = f"--backend={invocation.backend}" if invocation.backend else ""
    strict_flag = "--strict" if invocation.strict else ""
    target_value = str(target_path) if target_path is not None else invocation.target
    values = {
        "target": target_value,
        "report": str(report_path),
        "backend": invocation.backend,
        "backend_flag": backend_flag,
        "strict_flag": strict_flag,
    }
    for token in tokens:
        value = token.format(**values)
        if value:
            result.append(value)
    return result


def run_case(
    app: Path,
    case_file: Path,
    case_id: str,
    out_root: Path,
    default_timeout: float,
    app_config: RegressionAppConfig = DEFAULT_APP_CONFIG,
) -> CaseResult:
    case = load_case(case_file)
    case_dir = case_file.parent
    name = str(case.get("name", case_dir.name))
    invocation = parse_case_invocation(case, case_file, validate_paths=True)
    if invocation.mode not in app_config.mode_commands:
        return CaseResult(
            name,
            case_dir,
            False,
            f"unsupported invoke.mode: {invocation.mode}",
            None,
            0.0,
            out_root / "unknown_report.json",
        )

    target_path: Path | None = None
    if invocation.mode in {"script", "viewer2d"}:
        target_path = (case_dir / invocation.target).resolve()

    timeout = float(case.get("timeout_sec", default_timeout))
    assert_cfg = case.get("assert", {})
    if not isinstance(assert_cfg, dict):
        return CaseResult(
            name,
            case_dir,
            False,
            "assert must be an object",
            None,
            0.0,
            out_root / "unknown_report.json",
        )
    expected_outcome = str(assert_cfg.get("outcome", "pass")).lower()
    valid_outcomes = {"pass", "assert_fail", "timeout", "scenario_fail", "process_crash"}
    if expected_outcome not in valid_outcomes:
        report_path = out_root / case_id.replace("/", "__") / "report.json"
        return CaseResult(
            name,
            case_dir,
            False,
            f"invalid assert.outcome: {expected_outcome}",
            None,
            0.0,
            report_path,
        )

    case_out_dir = out_root / case_id.replace("/", "__")
    if case_out_dir.exists():
        import shutil

        shutil.rmtree(case_out_dir)
    case_out_dir.mkdir(parents=True, exist_ok=True)
    report_path = case_out_dir / "report.json"
    stdout_path = case_out_dir / "stdout.log"
    stderr_path = case_out_dir / "stderr.log"

    command_tail = _format_command_tokens(
        app_config.mode_commands[invocation.mode],
        invocation=invocation,
        target_path=target_path,
        report_path=report_path,
    )
    cmd = [str(app), *command_tail]

    timed_out = False
    duration_sec = 0.0
    proc_return_code: int | None = None
    try:
        start = time.monotonic()
        run_cwd = case_dir if target_path is not None else app.parent
        proc = subprocess.run(  # nosec B603
            cmd,
            cwd=run_cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        duration_sec = time.monotonic() - start
        proc_return_code = proc.returncode
        stdout_path.write_text(_as_text(proc.stdout), encoding="utf-8")
        stderr_path.write_text(_as_text(proc.stderr), encoding="utf-8")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        duration_sec = timeout
        stdout_path.write_text(_as_text(exc.stdout), encoding="utf-8")
        stderr_path.write_text(_as_text(exc.stderr), encoding="utf-8")

    report: dict[str, Any] | None = None
    if report_path.exists():
        report = load_json_object(report_path)

    actual_outcome = classify_case_outcome(invocation, timed_out, proc_return_code, report)
    if actual_outcome != expected_outcome:
        return CaseResult(
            name,
            case_dir,
            False,
            f"outcome mismatch: expected {expected_outcome}, got {actual_outcome}",
            proc_return_code,
            duration_sec,
            report_path,
        )

    max_duration_sec = assert_cfg.get("max_duration_sec")
    if max_duration_sec is not None and duration_sec > float(max_duration_sec):
        return CaseResult(
            name,
            case_dir,
            False,
            f"duration exceeded: {duration_sec:.3f}s > {float(max_duration_sec):.3f}s",
            proc_return_code,
            duration_sec,
            report_path,
        )

    report_required = bool(assert_cfg.get("report_paths")) or bool(
        assert_cfg.get("report_relations")
    )
    if report is None:
        if report_required:
            return CaseResult(
                name,
                case_dir,
                False,
                "regression report not generated",
                proc_return_code,
                duration_sec,
                report_path,
            )
        return CaseResult(name, case_dir, True, "ok", proc_return_code, duration_sec, report_path)

    expected_exit = assert_cfg.get(
        "exit_code", 0 if expected_outcome == "pass" else proc_return_code
    )
    if proc_return_code != expected_exit:
        return CaseResult(
            name,
            case_dir,
            False,
            f"exit_code mismatch: expected {expected_exit}, got {proc_return_code}",
            proc_return_code,
            duration_sec,
            report_path,
        )

    for path, expected in assert_cfg.get("report_paths", {}).items():
        try:
            actual = resolve_report_path(report, str(path))
        except Exception as exc:  # noqa: BLE001
            return CaseResult(
                name,
                case_dir,
                False,
                f"resolve path failed ({path}): {exc}",
                proc_return_code,
                duration_sec,
                report_path,
            )
        if actual != expected:
            return CaseResult(
                name,
                case_dir,
                False,
                f"assert failed at {path}: expected {expected!r}, got {actual!r}",
                proc_return_code,
                duration_sec,
                report_path,
            )

    for relation in assert_cfg.get("report_relations", []):
        left_path = relation.get("left", "")
        right_path = relation.get("right", "")
        op = relation.get("op", "eq")
        if not left_path or not right_path:
            return CaseResult(
                name,
                case_dir,
                False,
                "invalid report_relations entry",
                proc_return_code,
                duration_sec,
                report_path,
            )
        try:
            left_value = resolve_report_path(report, left_path)
            right_value = resolve_report_path(report, right_path)
        except Exception as exc:  # noqa: BLE001
            return CaseResult(
                name,
                case_dir,
                False,
                f"resolve relation failed: {exc}",
                proc_return_code,
                duration_sec,
                report_path,
            )
        ok = (
            left_value == right_value
            if op == "eq"
            else left_value != right_value if op == "ne" else None
        )
        if ok is None:
            return CaseResult(
                name,
                case_dir,
                False,
                f"unsupported relation op: {op}",
                proc_return_code,
                duration_sec,
                report_path,
            )
        if not ok:
            return CaseResult(
                name,
                case_dir,
                False,
                f"relation failed: {left_path} {op} {right_path} ({left_value!r} vs {right_value!r})",
                proc_return_code,
                duration_sec,
                report_path,
            )

    return CaseResult(name, case_dir, True, "ok", proc_return_code, duration_sec, report_path)


def write_junit(
    results: Sequence[CaseResult], out_path: Path, *, suite_name: str = "cppkit_regression"
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
            f"[{_paint(status, color, _Color.BOLD)}: {result.duration_sec:.1f}s] {result.name} :: {result.reason}"
        )

    if jobs <= 1:
        for meta in selected_meta:
            result = run_case(
                app, meta.case_file, meta.case_id, out_root, default_timeout, app_config
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
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
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
    print(_paint(f"Cases failed ({summary['failed']}/{summary['total']})", _Color.RED, _Color.BOLD))
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
