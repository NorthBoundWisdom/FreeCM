from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repomgrcpp.errors import WorkflowError
from repomgrcpp.preset_templates import load_json_file


@dataclass(frozen=True)
class CMakeDependencyBuildContext:
    preset_name: str
    generator: str
    generator_platform: str
    generator_toolset: str
    cmake_executable: str
    build_configurations: tuple[str, ...]
    external_prefix_path: str
    cache_variables: dict[str, str]


def configure_presets(model: dict[str, Any]) -> list[dict[str, Any]]:
    presets = model.get("configurePresets")
    if not isinstance(presets, list) or not presets:
        raise WorkflowError("Resolved preset model does not contain configurePresets")
    result: list[dict[str, Any]] = []
    for preset in presets:
        if not isinstance(preset, dict):
            raise WorkflowError("Resolved configure preset entry is not an object")
        result.append(preset)
    return result


def find_configure_preset(model: dict[str, Any], preset_name: str) -> dict[str, Any]:
    for preset in configure_presets(model):
        if preset.get("name") == preset_name:
            return preset
    raise WorkflowError(f"Unsupported configure preset '{preset_name}' in resolved preset model")


def cmake_executable_for_preset(model: dict[str, Any], preset_name: str) -> str:
    find_configure_preset(model, preset_name)
    return "cmake"


def resolve_preset_string(repo_root: Path, preset_name: str, value: str) -> str:
    return str(value).replace("${sourceDir}", str(repo_root)).replace("${presetName}", preset_name)


def resolve_generator(model: dict[str, Any], preset_name: str) -> str:
    preset = find_configure_preset(model, preset_name)
    generator = str(preset.get("generator", "")).strip()
    if not generator:
        raise WorkflowError(f"Preset '{preset_name}' is missing generator")
    return generator


def preset_environment(model: dict[str, Any], preset_name: str) -> dict[str, str]:
    preset = find_configure_preset(model, preset_name)
    result = dict(os.environ)
    environment = preset.get("environment", {})
    if not isinstance(environment, dict):
        raise WorkflowError(f"Preset '{preset_name}' has invalid environment map")
    for key, value in environment.items():
        result[str(key)] = str(value)
    return result


def build_dir_for_preset(repo_root: Path, model: dict[str, Any], preset_name: str) -> Path:
    preset = find_configure_preset(model, preset_name)
    binary_dir = str(preset.get("binaryDir", "")).strip()
    if not binary_dir:
        raise WorkflowError(f"Preset '{preset_name}' is missing binaryDir")
    return Path(resolve_preset_string(repo_root, preset_name, binary_dir))


def dependency_build_dir(
    repo_root: Path, model: dict[str, Any], preset_name: str, dependency_name: str
) -> Path:
    return (
        build_dir_for_preset(repo_root, model, preset_name) / "dependency_builds" / dependency_name
    )


def dependency_install_prefix(
    repo_root: Path, model: dict[str, Any], preset_name: str, dependency_name: str
) -> Path:
    return (
        build_dir_for_preset(repo_root, model, preset_name)
        / "dependency_installs"
        / dependency_name
    )


def build_dir_for_preset_name(repo_root: Path, preset_name: str) -> Path:
    return (repo_root / "build" / preset_name).resolve()


def dependency_build_dir_for_name(repo_root: Path, preset_name: str, dependency_name: str) -> Path:
    return build_dir_for_preset_name(repo_root, preset_name) / "dependency_builds" / dependency_name


def dependency_install_prefix_for_name(
    repo_root: Path, preset_name: str, dependency_name: str
) -> Path:
    return (
        build_dir_for_preset_name(repo_root, preset_name) / "dependency_installs" / dependency_name
    )


def multi_config_generator(generator: str) -> bool:
    return generator == "Xcode" or generator.startswith("Visual Studio")


def preset_generator_args(model: dict[str, Any], preset_name: str) -> list[str]:
    preset = find_configure_preset(model, preset_name)
    args: list[str] = []
    architecture = preset.get("architecture")
    toolset = preset.get("toolset")

    architecture_value = ""
    if isinstance(architecture, dict):
        if str(architecture.get("strategy", "set")).strip() != "external":
            architecture_value = str(architecture.get("value", "")).strip()
    elif architecture is not None:
        architecture_value = str(architecture).strip()

    toolset_value = ""
    if isinstance(toolset, dict):
        if str(toolset.get("strategy", "set")).strip() != "external":
            toolset_value = str(toolset.get("value", "")).strip()
    elif toolset is not None:
        toolset_value = str(toolset).strip()

    if architecture_value:
        args.extend(["-A", architecture_value])
    if toolset_value:
        args.extend(["-T", toolset_value])
    return args


def forwarded_cache_args(model: dict[str, Any], preset_name: str) -> list[str]:
    preset = find_configure_preset(model, preset_name)
    cache_variables = preset.get("cacheVariables", {})
    if not isinstance(cache_variables, dict):
        raise WorkflowError(f"Preset '{preset_name}' has invalid cacheVariables map")

    args: list[str] = []
    for key, value in cache_variables.items():
        if key in {"CMAKE_PREFIX_PATH", "CMAKE_INSTALL_PREFIX", "CMAKE_BUILD_TYPE"}:
            continue
        if value in (None, ""):
            continue
        args.append(f"-D{key}={value}")
    return args


def external_prefix_path(model: dict[str, Any], repo_root: Path, preset_name: str) -> str:
    preset = find_configure_preset(model, preset_name)
    cache_variables = preset.get("cacheVariables", {})
    if not isinstance(cache_variables, dict):
        raise WorkflowError(f"Preset '{preset_name}' has invalid cacheVariables map")
    value = str(cache_variables.get("CMAKE_PREFIX_PATH", "")).strip()
    if not value:
        return ""
    return resolve_preset_string(repo_root, preset_name, value)


def combined_prefix_path(
    model: dict[str, Any],
    repo_root: Path,
    preset_name: str,
    dependency_prefixes: Sequence[Path],
) -> str:
    parts = [str(path) for path in dependency_prefixes]
    external_prefix = external_prefix_path(model, repo_root, preset_name)
    if external_prefix:
        parts.append(external_prefix)
    return ";".join(parts)


def single_config_build_type(model: dict[str, Any], preset_name: str) -> str:
    preset = find_configure_preset(model, preset_name)
    cache_variables = preset.get("cacheVariables", {})
    if not isinstance(cache_variables, dict):
        raise WorkflowError(f"Preset '{preset_name}' has invalid cacheVariables map")
    build_type = str(cache_variables.get("CMAKE_BUILD_TYPE", "")).strip()
    return build_type or "Release"


def build_configurations_for_preset(model: dict[str, Any], preset_name: str) -> list[str]:
    generator = resolve_generator(model, preset_name)
    if not multi_config_generator(generator):
        return [single_config_build_type(model, preset_name)]

    configurations: list[str] = []
    for build_preset in model.get("buildPresets", []):
        if not isinstance(build_preset, dict):
            continue
        if build_preset.get("configurePreset") != preset_name:
            continue
        configuration = str(build_preset.get("configuration", "")).strip()
        if configuration and configuration not in configurations:
            configurations.append(configuration)

    return configurations or ["Release"]


def _normalized_context_build_configurations(configurations: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in configurations if str(value).strip())
    return normalized or ("Release",)


def load_cmake_dependency_build_context(path: Path) -> CMakeDependencyBuildContext:
    data = load_json_file(path)

    preset_name = str(data.get("presetName", "")).strip()
    generator = str(data.get("generator", "")).strip()
    if not preset_name:
        raise WorkflowError(f"Missing presetName in dependency build context: {path}")
    if not generator:
        raise WorkflowError(f"Missing generator in dependency build context: {path}")

    cache_variables = data.get("cacheVariables", {})
    if not isinstance(cache_variables, dict):
        raise WorkflowError(f"Invalid cacheVariables in dependency build context: {path}")

    normalized_cache_variables: dict[str, str] = {}
    for key, value in cache_variables.items():
        if not isinstance(value, str):
            raise WorkflowError(
                f"Invalid cacheVariables.{key!s} in dependency build context: {path}"
            )
        normalized_cache_variables[str(key)] = value

    build_configurations = data.get("buildConfigurations", [])
    if not isinstance(build_configurations, list):
        raise WorkflowError(f"Invalid buildConfigurations in dependency build context: {path}")

    return CMakeDependencyBuildContext(
        preset_name=preset_name,
        generator=generator,
        generator_platform=str(data.get("generatorPlatform", "")).strip(),
        generator_toolset=str(data.get("generatorToolset", "")).strip(),
        cmake_executable="cmake",
        build_configurations=_normalized_context_build_configurations(build_configurations),
        external_prefix_path=str(data.get("externalPrefixPath", "")).strip(),
        cache_variables=normalized_cache_variables,
    )
