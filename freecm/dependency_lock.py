"""Dependency lock schema loading and validation."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    from .app_configs import APP_CONFIGS_FIELD, REMOVED_LOCK_FIELDS, validate_app_configs
    from .dependency_names import validate_safe_dependency_path_name
    from .errors import LockfileValidationError
    from .jsonc import loads_jsonc
    from .workspace_lock import WORKSPACE_LOCK_NAME
except ImportError:  # pragma: no cover - supports direct script execution.
    from app_configs import APP_CONFIGS_FIELD, REMOVED_LOCK_FIELDS, validate_app_configs
    from dependency_names import validate_safe_dependency_path_name
    from errors import LockfileValidationError
    from jsonc import loads_jsonc
    from workspace_lock import WORKSPACE_LOCK_NAME


VALID_MODES = ("pinned", "latest", "manual")
DEPENDENCY_LOCK_SCHEMA_VERSION = 5
ACTIVE_LOCK_FILE_NAME = "source_roots.lock.jsonc"
TEMPLATE_LOCK_FILE_NAME = "source_roots.lock.jsonc.in"
LOCK_SCHEMA_CONTRACT = {
    "schemaVersion": DEPENDENCY_LOCK_SCHEMA_VERSION,
    "modes": VALID_MODES,
    "activeLockFileName": ACTIVE_LOCK_FILE_NAME,
    "templateLockFileName": TEMPLATE_LOCK_FILE_NAME,
    "workspaceLockName": WORKSPACE_LOCK_NAME,
    "fields": {
        "schemaVersion": "schemaVersion",
        "depsMode": "depsMode",
        "depsManualPath": "depsManualPath",
        "dependencies": "dependencies",
        "remote": "remote",
        "commit": "commit",
        "latestRef": "latestRef",
        "repoName": "repoName",
    },
}
DEFAULT_REQUIRED_RELATIVE_PATHS: tuple[str, ...] = ()
DEPENDENCY_ENTRY_FIELDS = {
    "repoName",
    "remote",
    "commit",
    "latestRef",
}
LEGACY_DEPENDENCY_ENTRY_FIELDS = {"abiGroup"}
LEGACY_ASSET_FIELDS = ("assetSeeds", "assetDependencies")
CMAKE_PLATFORM_CACHE_VARIABLE_GROUPS = ("linux", "mac", "win")
TERMINAL_PATH_GROUPS = ("common", "linux", "mac", "win")


def _validate_string_map(
    data: dict[str, Any],
    *,
    path_label: str,
    field_name: str,
    expected_keys: set[str],
    allow_empty_values: bool,
) -> None:
    actual_keys = set(data.keys())
    missing = sorted(expected_keys - actual_keys)
    extra = sorted(actual_keys - expected_keys)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing keys: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected keys: {', '.join(extra)}")
        raise ValueError(f"Invalid {field_name} in {path_label}: {'; '.join(details)}")

    for key, value in data.items():
        if not isinstance(value, str):
            raise ValueError(f"Invalid {field_name}.{key!s} in {path_label}; expected string")
        if not allow_empty_values and not value.strip():
            raise ValueError(
                f"Invalid {field_name}.{key!s} in {path_label}; expected non-empty string"
            )


def _normalize_optional_string_map(
    data: dict[str, Any],
    *,
    path_label: str,
    field_name: str,
) -> dict[str, str]:
    value = data.get(field_name, {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {field_name} map in {path_label}")

    normalized: dict[str, str] = {}
    for key, nested_value in value.items():
        if not isinstance(key, str):
            raise ValueError(f"Invalid {field_name} key in {path_label}; expected string")
        if not isinstance(nested_value, str):
            raise ValueError(f"Invalid {field_name}.{key!s} in {path_label}; expected string")
        normalized[key] = nested_value
    return normalized


def _normalize_cmake_cache_variables(
    data: dict[str, Any],
    *,
    path_label: str,
) -> dict[str, str | dict[str, str]]:
    field_name = "cmakeCacheVariables"
    value = data.get(field_name, {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {field_name} map in {path_label}")

    normalized: dict[str, str | dict[str, str]] = {}
    for key, nested_value in value.items():
        if not isinstance(key, str):
            raise ValueError(f"Invalid {field_name} key in {path_label}; expected string")
        if isinstance(nested_value, str):
            normalized[key] = nested_value
            continue
        if not isinstance(nested_value, dict):
            raise ValueError(
                f"Invalid {field_name}.{key!s} in {path_label}; expected string or platform map"
            )
        if key not in CMAKE_PLATFORM_CACHE_VARIABLE_GROUPS:
            supported = ", ".join(CMAKE_PLATFORM_CACHE_VARIABLE_GROUPS)
            raise ValueError(
                f"Invalid {field_name}.{key!s} in {path_label}; "
                f"nested maps are only supported for platform keys: {supported}"
            )
        platform_values: dict[str, str] = {}
        for platform_key, platform_value in nested_value.items():
            if not isinstance(platform_key, str):
                raise ValueError(
                    f"Invalid {field_name}.{key!s} key in {path_label}; expected string"
                )
            if not isinstance(platform_value, str):
                raise ValueError(
                    f"Invalid {field_name}.{key!s}.{platform_key!s} in {path_label}; expected string"
                )
            platform_values[platform_key] = platform_value
        normalized[key] = platform_values
    return normalized


def _normalize_terminal_path(
    data: dict[str, Any],
    *,
    path_label: str,
) -> dict[str, list[str]]:
    field_name = "terminalPath"
    value = data.get(field_name, {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {field_name} map in {path_label}")

    normalized: dict[str, list[str]] = {}
    for key, nested_value in value.items():
        if not isinstance(key, str):
            raise ValueError(f"Invalid {field_name} key in {path_label}; expected string")
        if key not in TERMINAL_PATH_GROUPS:
            supported = ", ".join(TERMINAL_PATH_GROUPS)
            raise ValueError(
                f"Invalid {field_name}.{key!s} in {path_label}; expected one of: {supported}"
            )
        if not isinstance(nested_value, list):
            raise ValueError(f"Invalid {field_name}.{key!s} in {path_label}; expected string array")
        normalized_values: list[str] = []
        for index, entry in enumerate(nested_value):
            if not isinstance(entry, str):
                raise ValueError(
                    f"Invalid {field_name}.{key!s}[{index}] in {path_label}; expected string"
                )
            normalized_values.append(entry)
        normalized[key] = normalized_values
    return normalized


def _normalize_optional_string_field(
    dependency: dict[str, Any],
    *,
    path_label: str,
    dependency_name: str,
    field_name: str,
) -> str | None:
    value = dependency.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"Invalid field {field_name!r} for dependency {dependency_name!r} in {path_label}; expected non-empty string"
        )
    return value.strip()


def validate_dependency_lock_data(
    data: dict[str, Any],
    *,
    path_label: str,
    expected_dependency_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"Invalid dependency-roots lock file (expected object): {path_label}")
    if data.get("schemaVersion") != DEPENDENCY_LOCK_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported dependency-roots lock schemaVersion {data.get('schemaVersion')!r} in {path_label}"
        )
    if "defaultMode" in data:
        raise ValueError(f"defaultMode is no longer supported in {path_label}; use depsMode")
    if "manualRoots" in data:
        raise ValueError(f"manualRoots is no longer supported in {path_label}; use depsManualPath")
    deps_mode = str(data.get("depsMode"))
    if deps_mode not in VALID_MODES:
        raise ValueError(
            f"Invalid depsMode {deps_mode!r} in {path_label}; expected one of {VALID_MODES}"
        )

    if "cmakeSettings" in data:
        raise ValueError(
            f"cmakeSettings is no longer supported in {path_label}; "
            "use cmakeEnvironment and cmakeCacheVariables"
        )
    for legacy_asset_field in LEGACY_ASSET_FIELDS:
        if legacy_asset_field in data:
            raise ValueError(
                f"{legacy_asset_field} is no longer supported in {path_label}; use assets"
            )
    for legacy_app_config_field, replacement in REMOVED_LOCK_FIELDS.items():
        if legacy_app_config_field in data:
            raise ValueError(
                f"{legacy_app_config_field} is no longer supported in {path_label}; use {replacement}"
            )
    assets = data.get("assets", {})
    if assets is None:
        assets = {}
    if not isinstance(assets, dict):
        raise ValueError(f"Invalid assets map in {path_label}")
    data["assets"] = assets
    data["cmakeEnvironment"] = _normalize_optional_string_map(
        data,
        path_label=path_label,
        field_name="cmakeEnvironment",
    )
    data[APP_CONFIGS_FIELD] = validate_app_configs(
        data,
        path_label=path_label,
        app_config_keys=(),
    )
    data["cmakeCacheVariables"] = _normalize_cmake_cache_variables(
        data,
        path_label=path_label,
    )
    data["terminalPath"] = _normalize_terminal_path(
        data,
        path_label=path_label,
    )

    deps_manual_path = data.get("depsManualPath")
    if not isinstance(deps_manual_path, dict):
        raise ValueError(f"Invalid depsManualPath map in {path_label}")

    dependencies = data.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ValueError(f"Invalid dependencies map in {path_label}")

    expected = (
        set(expected_dependency_names)
        if expected_dependency_names is not None
        else set(dependencies.keys())
    )
    actual = set(dependencies.keys())
    for dependency_name in sorted(actual | set(deps_manual_path.keys())):
        validate_safe_dependency_path_name(
            dependency_name,
            label="dependency name",
            path_label=path_label,
        )
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing dependencies: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected dependencies: {', '.join(extra)}")
        raise ValueError(f"Invalid dependencies in {path_label}: {'; '.join(details)}")

    _validate_string_map(
        deps_manual_path,
        path_label=path_label,
        field_name="depsManualPath",
        expected_keys=expected,
        allow_empty_values=True,
    )

    for dependency_name in expected:
        dependency = dependencies[dependency_name]
        if not isinstance(dependency, dict):
            raise ValueError(f"Invalid entry for dependency {dependency_name!r} in {path_label}")
        extra_fields = sorted(
            set(dependency.keys()) - DEPENDENCY_ENTRY_FIELDS - LEGACY_DEPENDENCY_ENTRY_FIELDS
        )
        if extra_fields:
            raise ValueError(
                f"Invalid dependency {dependency_name!r} in {path_label}; "
                f"unexpected fields: {', '.join(extra_fields)}"
            )
        for legacy_field in LEGACY_DEPENDENCY_ENTRY_FIELDS:
            dependency.pop(legacy_field, None)
        for field in ("remote", "commit"):
            value = dependency.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Invalid field {field!r} for dependency {dependency_name!r} in {path_label}"
                )
            dependency[field] = value.strip()
        dependency["latestRef"] = _normalize_optional_string_field(
            dependency,
            path_label=path_label,
            dependency_name=dependency_name,
            field_name="latestRef",
        )
        repo_name = _normalize_optional_string_field(
            dependency,
            path_label=path_label,
            dependency_name=dependency_name,
            field_name="repoName",
        )
        if repo_name is not None:
            validate_safe_dependency_path_name(
                repo_name,
                label="repository name",
                path_label=path_label,
            )
        dependency["repoName"] = repo_name
    return data


def load_dependency_lock_data(
    path: Path,
    *,
    expected_dependency_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    try:
        return validate_dependency_lock_data(
            loads_jsonc(path.read_text(encoding="utf-8"), path_label=str(path)),
            path_label=str(path),
            expected_dependency_names=expected_dependency_names,
        )
    except LockfileValidationError:
        raise
    except ValueError as error:
        raise LockfileValidationError(str(error)) from error
