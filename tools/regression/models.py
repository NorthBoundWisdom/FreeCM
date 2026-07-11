# Internal: shared data models for the regression runner.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


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


__all__ = (
    "CaseInvocation",
    "CaseMeta",
    "CaseResult",
    "ControlConfig",
    "RegressionAppConfig",
)
