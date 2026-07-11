# Internal: regression case configuration, discovery, and selection.

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .models import (
    CaseInvocation,
    CaseMeta,
    CaseResult,
    ControlConfig,
    PreparedCase,
    RegressionAppConfig,
)


class CaseConfigError(RuntimeError):
    pass


CaseConfigError.__module__ = "tools.regression.runner"


DEFAULT_APP_CONFIG = RegressionAppConfig(
    executable_candidates=(
        "{app}",
        "{app}/{app_name}",
        "{app}/{app_name}.exe",
        "{app}/bin/{app_name}",
        "{app}/bin/{app_name}.exe",
        "{app}/Release/{app_name}.exe",
        "{app}/Debug/{app_name}.exe",
    ),
    mode_commands={
        "script": (
            "script",
            "run",
            "--file={target}",
            "--report={report}",
            "{strict_flag}",
        ),
        "scenario": (
            "scenario",
            "run",
            "--name={target}",
            "--report={report}",
        ),
        "viewer2d": (
            "viewer2d",
            "run",
            "--perf-config={target}",
            "--report={report}",
            "{backend_flag}",
        ),
    },
    prefer_substrings=(),
)


def load_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as input_file:
        data = json.load(input_file)
    if not isinstance(data, dict):
        raise CaseConfigError(f"Expected JSON object: {path}")
    return data


def load_app_config(path: Path | None) -> RegressionAppConfig:
    if path is None:
        return DEFAULT_APP_CONFIG
    data = load_json_object(path)
    candidates = data.get("executableCandidates")
    commands = data.get("modeCommands")
    prefer = data.get("preferSubstrings")
    if candidates is None:
        normalized_candidates = DEFAULT_APP_CONFIG.executable_candidates
    else:
        if not isinstance(candidates, list) or not all(
            isinstance(item, str) for item in candidates
        ):
            raise CaseConfigError("executableCandidates must be an array of strings")
        normalized_candidates = tuple(candidates)
    normalized_commands = dict(DEFAULT_APP_CONFIG.mode_commands)
    if commands is not None:
        if not isinstance(commands, dict):
            raise CaseConfigError("modeCommands must be an object")
        for mode, command in commands.items():
            if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
                raise CaseConfigError(f"modeCommands.{mode} must be an array of strings")
            normalized_commands[str(mode)] = tuple(command)
    for mode, command in normalized_commands.items():
        if not isinstance(command, tuple) or not all(isinstance(item, str) for item in command):
            raise CaseConfigError(f"modeCommands.{mode} must be an array of strings")
    if prefer is None:
        normalized_prefer = DEFAULT_APP_CONFIG.prefer_substrings
    else:
        if not isinstance(prefer, list) or not all(isinstance(item, str) for item in prefer):
            raise CaseConfigError("preferSubstrings must be an array of strings")
        normalized_prefer = tuple(prefer)
    return RegressionAppConfig(
        executable_candidates=normalized_candidates,
        mode_commands=normalized_commands,
        prefer_substrings=normalized_prefer,
    )


def _is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _expand_candidate(raw_app: Path, pattern: str) -> Path:
    value = pattern.format(app=str(raw_app), app_name=raw_app.name)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _candidate_priority(
    path: Path,
    app_config: RegressionAppConfig,
) -> tuple[int, float, str]:
    text = str(path)
    for index, needle in enumerate(app_config.prefer_substrings):
        if needle and needle in text:
            return (index, -path.stat().st_mtime, text)
    return (len(app_config.prefer_substrings), -path.stat().st_mtime, text)


def resolve_app_executable(
    app_arg: str,
    app_config: RegressionAppConfig,
) -> Path | None:
    raw = Path(app_arg).expanduser()
    if not raw.is_absolute():
        raw = (Path.cwd() / raw).resolve()

    candidates = [_expand_candidate(raw, pattern) for pattern in app_config.executable_candidates]
    if raw.suffix == ".app" and raw.is_dir():
        candidates.append(raw / "Contents" / "MacOS" / raw.stem)

    valid = [candidate for candidate in candidates if _is_executable_file(candidate)]
    if not valid:
        return None
    valid.sort(key=lambda path: _candidate_priority(path, app_config))
    return valid[0]


def load_case(case_file: Path) -> dict[str, Any]:
    return load_json_object(case_file)


def find_case_files(root: Path) -> list[Path]:
    return sorted(root.rglob("case.json"))


def load_control(control_path: Path) -> ControlConfig:
    if not control_path.exists():
        return ControlConfig([], [], [])
    data = load_json_object(control_path)
    return ControlConfig(
        only_cases=[str(item) for item in data.get("only_cases", [])],
        disabled_cases=[str(item) for item in data.get("disabled_cases", [])],
        disabled_tags=[str(item) for item in data.get("disabled_tags", [])],
    )


def collect_case_meta(case_file: Path, suite_root: Path) -> CaseMeta:
    case = load_case(case_file)
    rel = case_file.parent.relative_to(suite_root)
    return CaseMeta(
        case_file=case_file,
        case_dir=case_file.parent,
        case_id=str(rel).replace("\\", "/"),
        name=str(case.get("name", case_file.parent.name)),
        tags=[str(tag) for tag in case.get("tags", [])],
        enabled=bool(case.get("enabled", True)),
    )


def is_case_selected(meta: CaseMeta, control: ControlConfig) -> bool:
    if not meta.enabled:
        return False
    if control.only_cases and meta.case_id not in control.only_cases:
        return False
    if meta.case_id in control.disabled_cases:
        return False
    if control.disabled_tags and any(tag in control.disabled_tags for tag in meta.tags):
        return False
    return True


def parse_case_invocation(
    case: Mapping[str, Any],
    case_file: Path,
    validate_paths: bool,
) -> CaseInvocation:
    invoke = case.get("invoke")
    if not isinstance(invoke, dict):
        raise CaseConfigError("invoke object is required")

    allowed_keys = {"mode", "target", "strict", "backend"}
    unknown_keys = sorted(set(invoke.keys()) - allowed_keys)
    if unknown_keys:
        raise CaseConfigError(f"unsupported invoke keys: {', '.join(unknown_keys)}")

    mode = str(invoke.get("mode", "")).strip().lower()
    if not mode:
        raise CaseConfigError("invoke.mode is required")

    raw_target = invoke.get("target", "")
    if not isinstance(raw_target, str) or not raw_target.strip():
        raise CaseConfigError("invoke.target must be a non-empty string")
    target = raw_target.strip()

    strict = invoke.get("strict", False)
    if "strict" in invoke and not isinstance(strict, bool):
        raise CaseConfigError("invoke.strict must be a boolean")

    raw_backend = invoke.get("backend", "")
    if raw_backend is None:
        raw_backend = ""
    if not isinstance(raw_backend, str):
        raise CaseConfigError("invoke.backend must be a string")
    backend = raw_backend.strip().lower()

    if validate_paths and mode in {"script", "viewer2d"}:
        target_path = (case_file.parent / target).resolve()
        if not target_path.exists():
            raise CaseConfigError(f"{mode} target not found: {target_path}")

    return CaseInvocation(mode=mode, target=target, strict=bool(strict), backend=backend)


def validate_selected_cases(
    selected_meta: Sequence[CaseMeta],
    app_config: RegressionAppConfig,
) -> list[str]:
    validation_errors: list[str] = []
    for meta in selected_meta:
        case = load_case(meta.case_file)
        try:
            invocation = parse_case_invocation(
                case,
                meta.case_file,
                validate_paths=True,
            )
            if invocation.mode not in app_config.mode_commands:
                raise CaseConfigError(f"unsupported invoke.mode: {invocation.mode!r}")
        except CaseConfigError as exc:
            validation_errors.append(f"{meta.case_id}: {exc}")
    return validation_errors


def prepare_case(
    case_file: Path,
    case_id: str,
    out_root: Path,
    default_timeout: float,
    app_config: RegressionAppConfig,
) -> PreparedCase | CaseResult:
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

    timeout_sec = float(case.get("timeout_sec", default_timeout))
    assert_config = case.get("assert", {})
    if not isinstance(assert_config, dict):
        return CaseResult(
            name,
            case_dir,
            False,
            "assert must be an object",
            None,
            0.0,
            out_root / "unknown_report.json",
        )
    expected_outcome = str(assert_config.get("outcome", "pass")).lower()
    valid_outcomes = {
        "pass",
        "assert_fail",
        "timeout",
        "scenario_fail",
        "process_crash",
    }
    case_out_dir = out_root / case_id.replace("/", "__")
    report_path = case_out_dir / "report.json"
    if expected_outcome not in valid_outcomes:
        return CaseResult(
            name,
            case_dir,
            False,
            f"invalid assert.outcome: {expected_outcome}",
            None,
            0.0,
            report_path,
        )

    return PreparedCase(
        name=name,
        case_file=case_file,
        case_dir=case_dir,
        case_id=case_id,
        invocation=invocation,
        target_path=target_path,
        timeout_sec=timeout_sec,
        assert_config=assert_config,
        expected_outcome=expected_outcome,
        case_out_dir=case_out_dir,
        report_path=report_path,
        stdout_path=case_out_dir / "stdout.log",
        stderr_path=case_out_dir / "stderr.log",
    )


__all__ = (
    "CaseConfigError",
    "DEFAULT_APP_CONFIG",
    "collect_case_meta",
    "find_case_files",
    "is_case_selected",
    "load_app_config",
    "load_case",
    "load_control",
    "load_json_object",
    "parse_case_invocation",
    "resolve_app_executable",
    "validate_selected_cases",
)
