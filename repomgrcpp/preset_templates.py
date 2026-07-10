from __future__ import annotations

import copy
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from freecm.lock_schema import REMOVED_TOP_LEVEL_FIELDS

from .errors import WorkflowError

TOKEN_PATTERN = re.compile(r"@([A-Za-z0-9_]+)@")
HOST_TEMPLATE_FILENAMES = {
    "linux": "CMakePresets.json.linux.in",
    "mac": "CMakePresets.json.mac.in",
    "win": "CMakePresets.json.win.in",
}
CMAKE_PLATFORM_CACHE_VARIABLE_GROUPS = frozenset(HOST_TEMPLATE_FILENAMES)


@dataclass(frozen=True)
class ResolvedPresetModel:
    os_group: str
    template_path: Path
    resolved_model: dict[str, Any]
    generated_model: dict[str, Any]


def host_template_path(repo_root: Path, os_group: str) -> Path:
    del repo_root
    try:
        template_name = HOST_TEMPLATE_FILENAMES[os_group]
    except KeyError as exc:
        raise WorkflowError(f"Unsupported host template group '{os_group}'") from exc

    path = Path(__file__).resolve().parent / "cmake_presets" / template_name
    if not path.is_file():
        raise WorkflowError(f"Missing shared host preset template: {path}")
    return path


def load_json_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise WorkflowError(f"Invalid JSON object in {path}")
    return data


def collect_template_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    if isinstance(value, dict):
        for nested in value.values():
            tokens.update(collect_template_tokens(nested))
    elif isinstance(value, list):
        for nested in value:
            tokens.update(collect_template_tokens(nested))
    elif isinstance(value, str):
        tokens.update(match.group(1) for match in TOKEN_PATTERN.finditer(value))
    return tokens


def managed_prefix_entries(dependency_names: Sequence[str]) -> list[str]:
    return [
        f"${{sourceDir}}/build/${{presetName}}/dependency_installs/{dependency_name}"
        for dependency_name in dependency_names
    ]


def inject_managed_prefixes(
    model: dict[str, Any],
    dependency_names: Sequence[str],
    *,
    user_defined_prefix_path: bool = False,
) -> dict[str, Any]:
    if not dependency_names or user_defined_prefix_path:
        return copy.deepcopy(model)

    managed_prefix_path = ";".join(managed_prefix_entries(dependency_names))
    injected = copy.deepcopy(model)
    for preset in injected.get("configurePresets", []):
        if not isinstance(preset, dict):
            raise WorkflowError("Invalid configure preset entry in resolved preset model")
        cache_variables = preset.setdefault("cacheVariables", {})
        if not isinstance(cache_variables, dict):
            raise WorkflowError("Invalid configure preset cacheVariables in resolved preset model")
        existing_prefix = str(cache_variables.get("CMAKE_PREFIX_PATH", "")).strip()
        if existing_prefix:
            cache_variables["CMAKE_PREFIX_PATH"] = f"{managed_prefix_path};{existing_prefix}"
        else:
            cache_variables["CMAKE_PREFIX_PATH"] = managed_prefix_path
    return injected


def _normalized_lock_string_map(lock_data: dict[str, Any], field_name: str) -> dict[str, str]:
    value = lock_data.get(field_name, {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise WorkflowError(f"Invalid {field_name} map in dependency lock")

    normalized: dict[str, str] = {}
    for key, nested_value in value.items():
        if not isinstance(key, str):
            raise WorkflowError(f"Invalid {field_name} key in dependency lock; expected string")
        if not isinstance(nested_value, str):
            raise WorkflowError(f"Invalid {field_name}.{key!s} in dependency lock; expected string")
        normalized[key] = nested_value
    return normalized


def _normalized_lock_cmake_cache_variables(
    lock_data: dict[str, Any],
    os_group: str,
) -> dict[str, str]:
    field_name = "cmakeCacheVariables"
    value = lock_data.get(field_name, {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise WorkflowError(f"Invalid {field_name} map in dependency lock")

    normalized: dict[str, str] = {}
    platform_overrides: dict[str, str] = {}
    for key, nested_value in value.items():
        if not isinstance(key, str):
            raise WorkflowError(f"Invalid {field_name} key in dependency lock; expected string")
        if isinstance(nested_value, str):
            normalized[key] = nested_value
            continue
        if not isinstance(nested_value, dict):
            raise WorkflowError(
                f"Invalid {field_name}.{key!s} in dependency lock; expected string or platform map"
            )
        if key not in CMAKE_PLATFORM_CACHE_VARIABLE_GROUPS:
            supported = ", ".join(sorted(CMAKE_PLATFORM_CACHE_VARIABLE_GROUPS))
            raise WorkflowError(
                f"Invalid {field_name}.{key!s} in dependency lock; "
                f"nested maps are only supported for platform keys: {supported}"
            )
        if key != os_group:
            continue
        for platform_key, platform_value in nested_value.items():
            if not isinstance(platform_key, str):
                raise WorkflowError(
                    f"Invalid {field_name}.{key!s} key in dependency lock; expected string"
                )
            if not isinstance(platform_value, str):
                raise WorkflowError(
                    f"Invalid {field_name}.{key!s}.{platform_key!s} in dependency lock; expected string"
                )
            platform_overrides[platform_key] = platform_value

    normalized.update(platform_overrides)
    return normalized


def _apply_cmake_preset_overrides(
    model: dict[str, Any],
    *,
    cmake_environment: dict[str, str],
    cmake_cache_variables: dict[str, str],
) -> dict[str, Any]:
    resolved = copy.deepcopy(model)
    presets = resolved.get("configurePresets")
    if not isinstance(presets, list) or not presets:
        raise WorkflowError("Resolved preset model does not contain configurePresets")

    for preset in presets:
        if not isinstance(preset, dict):
            raise WorkflowError("Resolved configure preset entry is not an object")

        preset_name = str(preset.get("name", "<unnamed>"))
        if cmake_environment:
            environment = preset.setdefault("environment", {})
            if not isinstance(environment, dict):
                raise WorkflowError(f"Preset '{preset_name}' has invalid environment map")
            environment.update(cmake_environment)
        elif "environment" in preset and not isinstance(preset["environment"], dict):
            raise WorkflowError(f"Preset '{preset_name}' has invalid environment map")

        cache_variables = preset.setdefault("cacheVariables", {})
        if not isinstance(cache_variables, dict):
            raise WorkflowError(f"Preset '{preset_name}' has invalid cacheVariables map")
        cache_variables.update(cmake_cache_variables)
    return resolved


def resolve_preset_models(
    repo_root: Path,
    lock_data: dict[str, Any],
    os_group: str,
    dependency_names: Sequence[str],
) -> ResolvedPresetModel:
    template_path = host_template_path(repo_root, os_group)
    template_data = load_json_file(template_path)
    tokens = collect_template_tokens(template_data)
    if tokens:
        token_list = ", ".join(sorted(tokens))
        raise WorkflowError(f"Unresolved preset template tokens in {template_path}: {token_list}")
    replacement = "cmakeEnvironment and cmakeCacheVariables"
    removed_field = next(
        (
            field
            for field, field_replacement in REMOVED_TOP_LEVEL_FIELDS.items()
            if field_replacement == replacement
        ),
        None,
    )
    if removed_field is not None and removed_field in lock_data:
        raise WorkflowError(f"{removed_field} is no longer supported; use {replacement}")

    cmake_environment = _normalized_lock_string_map(lock_data, "cmakeEnvironment")
    cmake_cache_variables = _normalized_lock_cmake_cache_variables(lock_data, os_group)
    resolved_model = _apply_cmake_preset_overrides(
        template_data,
        cmake_environment=cmake_environment,
        cmake_cache_variables=cmake_cache_variables,
    )
    user_defined_prefix_path = "CMAKE_PREFIX_PATH" in cmake_cache_variables
    generated_model = inject_managed_prefixes(
        resolved_model,
        dependency_names,
        user_defined_prefix_path=user_defined_prefix_path,
    )
    return ResolvedPresetModel(
        os_group=os_group,
        template_path=template_path,
        resolved_model=resolved_model,
        generated_model=generated_model,
    )


def resolve_preset_model(
    repo_root: Path,
    lock_data: dict[str, Any],
    os_group: str,
    dependency_names: Sequence[str],
) -> dict[str, Any]:
    return resolve_preset_models(
        repo_root,
        lock_data,
        os_group,
        dependency_names,
    ).generated_model
