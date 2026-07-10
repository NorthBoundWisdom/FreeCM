"""Dependency lock schema loading and validation."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .app_configs import APP_CONFIGS_FIELD, validate_app_configs
from .dependency_names import validate_safe_dependency_path_name
from .errors import LockfileValidationError
from .jsonc import loads_jsonc
from .lock_schema import (
    ACTIVE_LOCK_FILE_NAME as _ACTIVE_LOCK_FILE_NAME,
)
from .lock_schema import (
    DEPENDENCY_ENTRY_FIELDS as _DEPENDENCY_ENTRY_FIELDS,
)
from .lock_schema import (
    LEGACY_DEPENDENCY_ENTRY_FIELDS as _LEGACY_DEPENDENCY_ENTRY_FIELDS,
)
from .lock_schema import LOCK_FIELDS as _LOCK_FIELDS
from .lock_schema import LOCK_MODES, LOCK_SCHEMA_RESOURCE, LOCK_SCHEMA_VERSION
from .lock_schema import (
    OPTIONAL_DEPENDENCY_ENTRY_FIELDS as _OPTIONAL_DEPENDENCY_ENTRY_FIELDS,
)
from .lock_schema import REMOVED_TOP_LEVEL_FIELDS as _REMOVED_TOP_LEVEL_FIELDS
from .lock_schema import (
    REQUIRED_DEPENDENCY_ENTRY_FIELDS as _REQUIRED_DEPENDENCY_ENTRY_FIELDS,
)
from .lock_schema import (
    TEMPLATE_LOCK_FILE_NAME as _TEMPLATE_LOCK_FILE_NAME,
)
from .workspace_lock import WORKSPACE_LOCK_CONTRACT, WORKSPACE_LOCK_NAME

VALID_MODES = LOCK_MODES
DEPENDENCY_LOCK_SCHEMA_VERSION = LOCK_SCHEMA_VERSION
ACTIVE_LOCK_FILE_NAME = _ACTIVE_LOCK_FILE_NAME
TEMPLATE_LOCK_FILE_NAME = _TEMPLATE_LOCK_FILE_NAME
DEPENDENCY_ENTRY_FIELDS = set(_DEPENDENCY_ENTRY_FIELDS)
LEGACY_DEPENDENCY_ENTRY_FIELDS = set(_LEGACY_DEPENDENCY_ENTRY_FIELDS)
REMOVED_TOP_LEVEL_FIELDS = dict(_REMOVED_TOP_LEVEL_FIELDS)
LEGACY_ASSET_FIELDS = tuple(
    field for field, replacement in REMOVED_TOP_LEVEL_FIELDS.items() if replacement == "assets"
)
LOCK_SCHEMA_CONTRACT = {
    "schemaVersion": DEPENDENCY_LOCK_SCHEMA_VERSION,
    "modes": VALID_MODES,
    "activeLockFileName": ACTIVE_LOCK_FILE_NAME,
    "templateLockFileName": TEMPLATE_LOCK_FILE_NAME,
    "workspaceLockName": WORKSPACE_LOCK_NAME,
    "workspaceLockProtocol": WORKSPACE_LOCK_CONTRACT,
    "legacyDependencyEntryFields": tuple(sorted(LEGACY_DEPENDENCY_ENTRY_FIELDS)),
    "dependencyEntryFields": tuple(
        str(value) for value in LOCK_SCHEMA_RESOURCE["dependencyEntryFields"]
    ),
    "requiredDependencyEntryFields": tuple(
        str(value) for value in LOCK_SCHEMA_RESOURCE["requiredDependencyEntryFields"]
    ),
    "optionalDependencyEntryFields": tuple(
        str(value) for value in LOCK_SCHEMA_RESOURCE["optionalDependencyEntryFields"]
    ),
    "removedTopLevelFields": dict(LOCK_SCHEMA_RESOURCE["removedTopLevelFields"]),
    "safeDependencyNamePattern": str(LOCK_SCHEMA_RESOURCE["safeDependencyNamePattern"]),
    "fields": dict(LOCK_SCHEMA_RESOURCE["fields"]),
}
DEFAULT_REQUIRED_RELATIVE_PATHS: tuple[str, ...] = ()
CMAKE_PLATFORM_CACHE_VARIABLE_GROUPS = ("linux", "mac", "win")
TERMINAL_PATH_GROUPS = ("common", "linux", "mac", "win")

_SCHEMA_VERSION_FIELD = _LOCK_FIELDS["schemaVersion"]
_DEPS_MODE_FIELD = _LOCK_FIELDS["depsMode"]
_DEPS_MANUAL_PATH_FIELD = _LOCK_FIELDS["depsManualPath"]
_DEPENDENCIES_FIELD = _LOCK_FIELDS["dependencies"]
_REPO_NAME_FIELD = _LOCK_FIELDS["repoName"]


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
    if data.get(_SCHEMA_VERSION_FIELD) != DEPENDENCY_LOCK_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported dependency-roots lock "
            f"{_SCHEMA_VERSION_FIELD} {data.get(_SCHEMA_VERSION_FIELD)!r} in {path_label}"
        )
    for removed_field, replacement in REMOVED_TOP_LEVEL_FIELDS.items():
        if removed_field in data:
            raise ValueError(
                f"{removed_field} is no longer supported in {path_label}; use {replacement}"
            )
    deps_mode = str(data.get(_DEPS_MODE_FIELD))
    if deps_mode not in VALID_MODES:
        raise ValueError(
            f"Invalid {_DEPS_MODE_FIELD} {deps_mode!r} in {path_label}; expected one of {VALID_MODES}"
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

    deps_manual_path = data.get(_DEPS_MANUAL_PATH_FIELD)
    if not isinstance(deps_manual_path, dict):
        raise ValueError(f"Invalid {_DEPS_MANUAL_PATH_FIELD} map in {path_label}")

    dependencies = data.get(_DEPENDENCIES_FIELD)
    if not isinstance(dependencies, dict):
        raise ValueError(f"Invalid {_DEPENDENCIES_FIELD} map in {path_label}")

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
        field_name=_DEPS_MANUAL_PATH_FIELD,
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
        for field in _REQUIRED_DEPENDENCY_ENTRY_FIELDS:
            value = dependency.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Invalid field {field!r} for dependency {dependency_name!r} in {path_label}"
                )
            dependency[field] = value.strip()
        for field in _OPTIONAL_DEPENDENCY_ENTRY_FIELDS:
            normalized = _normalize_optional_string_field(
                dependency,
                path_label=path_label,
                dependency_name=dependency_name,
                field_name=field,
            )
            if field == _REPO_NAME_FIELD and normalized is not None:
                validate_safe_dependency_path_name(
                    normalized,
                    label="repository name",
                    path_label=path_label,
                )
            dependency[field] = normalized
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
