# Internal: shared data models for the regression runner.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CaseResult:
    name: str
    case_dir: Path
    passed: bool
    reason: str
    exit_code: int | None
    duration_sec: float
    report_path: Path


@dataclass(frozen=True)
class CaseInvocation:
    mode: str
    target: str
    strict: bool
    backend: str


@dataclass(frozen=True)
class CaseMeta:
    case_file: Path
    case_dir: Path
    case_id: str
    name: str
    tags: list[str]
    enabled: bool


@dataclass(frozen=True)
class ControlConfig:
    only_cases: list[str]
    disabled_cases: list[str]
    disabled_tags: list[str]


@dataclass(frozen=True)
class RegressionAppConfig:
    executable_candidates: tuple[str, ...]
    mode_commands: Mapping[str, tuple[str, ...]]
    prefer_substrings: tuple[str, ...]


for _public_type in (
    CaseResult,
    CaseInvocation,
    CaseMeta,
    ControlConfig,
    RegressionAppConfig,
):
    _public_type.__module__ = "tools.regression.runner"


@dataclass(frozen=True)
class PreparedCase:
    name: str
    case_file: Path
    case_dir: Path
    case_id: str
    invocation: CaseInvocation
    target_path: Path | None
    timeout_sec: float
    assert_config: dict[str, Any]
    expected_outcome: str
    case_out_dir: Path
    report_path: Path
    stdout_path: Path
    stderr_path: Path


@dataclass(frozen=True)
class CaseProcessResult:
    command: tuple[str, ...]
    cwd: Path
    timed_out: bool
    return_code: int | None
    duration_sec: float
    stdout_path: Path
    stderr_path: Path


__all__ = (
    "CaseInvocation",
    "CaseMeta",
    "CaseResult",
    "ControlConfig",
    "RegressionAppConfig",
)
