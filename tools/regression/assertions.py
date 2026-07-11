# Internal: regression report paths, outcomes, and assertion evaluation.

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .cases import load_json_object
from .models import CaseInvocation, CaseProcessResult, CaseResult, PreparedCase


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


def load_case_report(report_path: Path) -> dict[str, Any] | None:
    if not report_path.exists():
        return None
    return load_json_object(report_path)


def evaluate_case_result(
    prepared: PreparedCase,
    process: CaseProcessResult,
    report: Mapping[str, Any] | None,
) -> CaseResult:
    actual_outcome = classify_case_outcome(
        prepared.invocation,
        process.timed_out,
        process.return_code,
        report,
    )
    if actual_outcome != prepared.expected_outcome:
        return CaseResult(
            prepared.name,
            prepared.case_dir,
            False,
            f"outcome mismatch: expected {prepared.expected_outcome}, got {actual_outcome}",
            process.return_code,
            process.duration_sec,
            prepared.report_path,
        )

    max_duration_sec = prepared.assert_config.get("max_duration_sec")
    if max_duration_sec is not None and process.duration_sec > float(max_duration_sec):
        return CaseResult(
            prepared.name,
            prepared.case_dir,
            False,
            f"duration exceeded: {process.duration_sec:.3f}s > " f"{float(max_duration_sec):.3f}s",
            process.return_code,
            process.duration_sec,
            prepared.report_path,
        )

    report_required = bool(prepared.assert_config.get("report_paths")) or bool(
        prepared.assert_config.get("report_relations")
    )
    if report is None:
        if report_required:
            return CaseResult(
                prepared.name,
                prepared.case_dir,
                False,
                "regression report not generated",
                process.return_code,
                process.duration_sec,
                prepared.report_path,
            )
        return CaseResult(
            prepared.name,
            prepared.case_dir,
            True,
            "ok",
            process.return_code,
            process.duration_sec,
            prepared.report_path,
        )

    expected_exit = prepared.assert_config.get(
        "exit_code",
        0 if prepared.expected_outcome == "pass" else process.return_code,
    )
    if process.return_code != expected_exit:
        return CaseResult(
            prepared.name,
            prepared.case_dir,
            False,
            f"exit_code mismatch: expected {expected_exit}, got {process.return_code}",
            process.return_code,
            process.duration_sec,
            prepared.report_path,
        )

    for path, expected in prepared.assert_config.get("report_paths", {}).items():
        try:
            actual = resolve_report_path(report, str(path))
        except Exception as exc:  # noqa: BLE001
            return CaseResult(
                prepared.name,
                prepared.case_dir,
                False,
                f"resolve path failed ({path}): {exc}",
                process.return_code,
                process.duration_sec,
                prepared.report_path,
            )
        if actual != expected:
            return CaseResult(
                prepared.name,
                prepared.case_dir,
                False,
                f"assert failed at {path}: expected {expected!r}, got {actual!r}",
                process.return_code,
                process.duration_sec,
                prepared.report_path,
            )

    for relation in prepared.assert_config.get("report_relations", []):
        left_path = relation.get("left", "")
        right_path = relation.get("right", "")
        op = relation.get("op", "eq")
        if not left_path or not right_path:
            return CaseResult(
                prepared.name,
                prepared.case_dir,
                False,
                "invalid report_relations entry",
                process.return_code,
                process.duration_sec,
                prepared.report_path,
            )
        try:
            left_value = resolve_report_path(report, left_path)
            right_value = resolve_report_path(report, right_path)
        except Exception as exc:  # noqa: BLE001
            return CaseResult(
                prepared.name,
                prepared.case_dir,
                False,
                f"resolve relation failed: {exc}",
                process.return_code,
                process.duration_sec,
                prepared.report_path,
            )
        ok = (
            left_value == right_value
            if op == "eq"
            else left_value != right_value if op == "ne" else None
        )
        if ok is None:
            return CaseResult(
                prepared.name,
                prepared.case_dir,
                False,
                f"unsupported relation op: {op}",
                process.return_code,
                process.duration_sec,
                prepared.report_path,
            )
        if not ok:
            return CaseResult(
                prepared.name,
                prepared.case_dir,
                False,
                f"relation failed: {left_path} {op} {right_path} "
                f"({left_value!r} vs {right_value!r})",
                process.return_code,
                process.duration_sec,
                prepared.report_path,
            )

    return CaseResult(
        prepared.name,
        prepared.case_dir,
        True,
        "ok",
        process.return_code,
        process.duration_sec,
        prepared.report_path,
    )


__all__ = (
    "classify_case_outcome",
    "get_current_document",
    "resolve_report_path",
)
