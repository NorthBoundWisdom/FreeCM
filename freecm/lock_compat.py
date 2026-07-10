"""Read-only compatibility checks for FreeCM dependency lock files."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .app_configs import REMOVED_LOCK_FIELDS
from .dependency_lock import (
    DEPENDENCY_ENTRY_FIELDS,
    DEPENDENCY_LOCK_SCHEMA_VERSION,
    LEGACY_ASSET_FIELDS,
    LEGACY_DEPENDENCY_ENTRY_FIELDS,
    validate_dependency_lock_data,
)
from .errors import LockfileValidationError
from .jsonc import loads_jsonc


@dataclass(frozen=True)
class LockCompatibilityProblem:
    path: str
    severity: str
    code: str
    message: str
    suggestion: str

    def to_json(self) -> dict[str, str]:
        return asdict(self)


def default_lock_compatibility_paths(repo_root: Path) -> tuple[Path, ...]:
    candidates = (
        repo_root / "source_roots.lock.jsonc.in",
        repo_root / "source_roots.lock.jsonc",
    )
    return tuple(path for path in candidates if path.exists())


def _problem(
    *,
    path_label: str,
    severity: str,
    code: str,
    message: str,
    suggestion: str,
) -> LockCompatibilityProblem:
    return LockCompatibilityProblem(
        path=path_label,
        severity=severity,
        code=code,
        message=message,
        suggestion=suggestion,
    )


def lock_compatibility_problems(
    data: dict[str, Any],
    *,
    path_label: str,
) -> tuple[LockCompatibilityProblem, ...]:
    problems: list[LockCompatibilityProblem] = []
    schema_version = data.get("schemaVersion")
    if schema_version != DEPENDENCY_LOCK_SCHEMA_VERSION:
        problems.append(
            _problem(
                path_label=path_label,
                severity="error",
                code="unsupported-schema-version",
                message=(
                    "Unsupported dependency lock schemaVersion "
                    f"{schema_version!r}; expected {DEPENDENCY_LOCK_SCHEMA_VERSION}."
                ),
                suggestion=(
                    "Regenerate or migrate this lock with the current FreeCM workflow before "
                    "using it in CI."
                ),
            )
        )

    legacy_top_level_replacements = {
        "defaultMode": "depsMode",
        "manualRoots": "depsManualPath",
        "cmakeSettings": "cmakeEnvironment and cmakeCacheVariables",
        **{field: "assets" for field in LEGACY_ASSET_FIELDS},
        **REMOVED_LOCK_FIELDS,
    }
    for field_name, replacement in sorted(legacy_top_level_replacements.items()):
        if field_name in data:
            problems.append(
                _problem(
                    path_label=path_label,
                    severity="error",
                    code="legacy-top-level-field",
                    message=f"{field_name} is no longer supported.",
                    suggestion=f"Replace {field_name} with {replacement}.",
                )
            )

    dependencies = data.get("dependencies", {})
    if isinstance(dependencies, dict):
        for dependency_name, dependency_data in sorted(dependencies.items()):
            if not isinstance(dependency_data, dict):
                continue
            legacy_entry_fields = sorted(
                set(dependency_data.keys()) & LEGACY_DEPENDENCY_ENTRY_FIELDS
            )
            if legacy_entry_fields:
                problems.append(
                    _problem(
                        path_label=path_label,
                        severity="warning",
                        code="legacy-dependency-field",
                        message=(
                            f"Dependency {dependency_name!r} uses legacy fields: "
                            f"{', '.join(legacy_entry_fields)}."
                        ),
                        suggestion="Remove these fields from the reviewed lock template.",
                    )
                )
            unknown_fields = sorted(
                set(dependency_data.keys())
                - DEPENDENCY_ENTRY_FIELDS
                - LEGACY_DEPENDENCY_ENTRY_FIELDS
            )
            if unknown_fields:
                problems.append(
                    _problem(
                        path_label=path_label,
                        severity="error",
                        code="unknown-dependency-field",
                        message=(
                            f"Dependency {dependency_name!r} has unknown fields: "
                            f"{', '.join(unknown_fields)}."
                        ),
                        suggestion="Remove unknown fields or update FreeCM if the schema changed.",
                    )
                )

    try:
        validate_dependency_lock_data(copy.deepcopy(data), path_label=path_label)
    except (LockfileValidationError, ValueError) as exc:
        problems.append(
            _problem(
                path_label=path_label,
                severity="error",
                code="validation-error",
                message=str(exc),
                suggestion=(
                    "Run `repo-tool check-lock-compat --format json` for a structured "
                    "report, then update the lock template before materializing."
                ),
            )
        )

    deduped: dict[tuple[str, str, str], LockCompatibilityProblem] = {}
    for problem in problems:
        deduped[(problem.severity, problem.code, problem.message)] = problem
    return tuple(deduped.values())


def check_lock_compatibility_file(path: Path) -> tuple[LockCompatibilityProblem, ...]:
    path_label = str(path)
    try:
        data = loads_jsonc(path.read_text(encoding="utf-8"), path_label=path_label)
    except (OSError, LockfileValidationError, ValueError) as exc:
        return (
            _problem(
                path_label=path_label,
                severity="error",
                code="read-error",
                message=str(exc),
                suggestion="Fix the file path or JSONC syntax before validating compatibility.",
            ),
        )
    if not isinstance(data, dict):
        return (
            _problem(
                path_label=path_label,
                severity="error",
                code="invalid-root",
                message="Dependency lock root must be a JSON object.",
                suggestion="Rewrite the lock file as an object using the current schema.",
            ),
        )
    return lock_compatibility_problems(data, path_label=path_label)


def lock_compatibility_report(paths: tuple[Path, ...]) -> dict[str, Any]:
    file_reports = []
    for path in paths:
        problems = check_lock_compatibility_file(path)
        file_reports.append(
            {
                "path": str(path),
                "ok": not any(problem.severity == "error" for problem in problems),
                "problems": [problem.to_json() for problem in problems],
            }
        )
    return {
        "schemaVersion": DEPENDENCY_LOCK_SCHEMA_VERSION,
        "ok": not any(not report["ok"] for report in file_reports),
        "files": file_reports,
    }
