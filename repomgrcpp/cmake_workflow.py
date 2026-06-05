#!/usr/bin/env python3
# Usage:
#   python3 /path/to/FreeCM/repomgrcpp/cmake_workflow.py --init
#   python3 /path/to/FreeCM/repomgrcpp/cmake_workflow.py --update
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.cmake_workflow --help

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, MutableMapping, Sequence

_PACKAGE_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_REPO_ROOT))

from freecm.git_repositories import git_toplevel
from freecm.atomic_write import atomic_write_json, atomic_write_text

try:
    from .errors import WorkflowError
    from .preset_templates import (
        HOST_TEMPLATE_FILENAMES as HOST_TEMPLATE_FILENAMES,
        ResolvedPresetModel as ResolvedPresetModel,
        collect_template_tokens as collect_template_tokens,
        host_template_path as host_template_path,
        inject_managed_prefixes as inject_managed_prefixes,
        load_json_file,
        managed_prefix_entries as managed_prefix_entries,
        resolve_preset_model as resolve_preset_model,
        resolve_preset_models,
    )
    from freecm.terminal_style import (
        ANSI_BLUE as ANSI_BLUE,
        ANSI_BOLD as ANSI_BOLD,
        ANSI_CYAN as ANSI_CYAN,
        ANSI_DIM as ANSI_DIM,
        ANSI_GREEN as ANSI_GREEN,
        ANSI_RED as ANSI_RED,
        ANSI_RESET as ANSI_RESET,
        ANSI_YELLOW as ANSI_YELLOW,
        MODE_COLORS as MODE_COLORS,
        MODE_LABELS as MODE_LABELS,
        stderr_supports_color,
        stdout_supports_color,
        format_dependency_commit_change_lines,
        _style as _style,
        format_dependency_resolution_lines,
        format_status_line,
    )
except ImportError:  # pragma: no cover - supports direct script execution.
    from errors import WorkflowError
    from preset_templates import (
        HOST_TEMPLATE_FILENAMES as HOST_TEMPLATE_FILENAMES,
        ResolvedPresetModel as ResolvedPresetModel,
        collect_template_tokens as collect_template_tokens,
        host_template_path as host_template_path,
        inject_managed_prefixes as inject_managed_prefixes,
        load_json_file,
        managed_prefix_entries as managed_prefix_entries,
        resolve_preset_model as resolve_preset_model,
        resolve_preset_models,
    )
    from freecm.terminal_style import (
        ANSI_BLUE as ANSI_BLUE,
        ANSI_BOLD as ANSI_BOLD,
        ANSI_CYAN as ANSI_CYAN,
        ANSI_DIM as ANSI_DIM,
        ANSI_GREEN as ANSI_GREEN,
        ANSI_RED as ANSI_RED,
        ANSI_RESET as ANSI_RESET,
        ANSI_YELLOW as ANSI_YELLOW,
        MODE_COLORS as MODE_COLORS,
        MODE_LABELS as MODE_LABELS,
        stderr_supports_color,
        stdout_supports_color,
        format_dependency_commit_change_lines,
        _style as _style,
        format_dependency_resolution_lines,
        format_status_line,
    )


SCRIPT_PATH = Path(__file__).resolve()
_DEPENDENCY_ROOT_HELPER_NAMES = (
    "DependencyRootSummary",
    "describe_dependency_roots",
    "ensure_active_lock_file",
    "require_dependency_roots",
    "load_lock_file",
    "prepare_seed_repository_closure",
    "materialize_dependency_roots",
)


def _looks_like_dependency_workflow_repo(repo_root: Path) -> bool:
    return (
        (repo_root / "source_roots.lock.jsonc").exists()
        or (repo_root / "source_roots.lock.jsonc.in").exists()
        or (repo_root / "configs" / "source_roots.py").exists()
    )


def _git_toplevel_from_cwd() -> Path | None:
    try:
        return git_toplevel(Path.cwd())
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def default_repo_root(script_path: Path) -> Path:
    script_repo_root = script_path.parent.parent.resolve()
    if _looks_like_dependency_workflow_repo(script_repo_root):
        return script_repo_root

    cwd_repo_root = _git_toplevel_from_cwd()
    if cwd_repo_root is not None:
        return cwd_repo_root
    return script_repo_root


REPO_ROOT = default_repo_root(SCRIPT_PATH)
REPO_DISPLAY_NAME = "workspace"
DEPENDENCY_STATE_FILENAME = ".dependency_root_state.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

def _unbound_dependency_root_helper(*_: Any, **__: Any) -> Any:
    raise RuntimeError("dependency-root workflow helpers have not been bound")


try:
    from configs import source_roots as _bound_source_roots
except ModuleNotFoundError as exc:
    if exc.name != "configs":
        raise
    _bound_source_roots = None

from freecm.dependency_roots import (  # noqa: E402 - imported after repo sys.path setup.
    DependencyRootSummary,
    dependency_commit_changes,
    loads_jsonc,
)
from freecm.asset_seeds import prepare_asset_seeds, require_asset_seeds  # noqa: E402

if _bound_source_roots is None:
    describe_dependency_roots = _unbound_dependency_root_helper
    ensure_active_lock_file = _unbound_dependency_root_helper
    require_dependency_roots = _unbound_dependency_root_helper
    load_lock_file = _unbound_dependency_root_helper
    prepare_seed_repository_closure = _unbound_dependency_root_helper
    materialize_dependency_roots = _unbound_dependency_root_helper
else:
    _missing_dependency_root_helpers = [
        name
        for name in _DEPENDENCY_ROOT_HELPER_NAMES
        if not hasattr(_bound_source_roots, name)
    ]
    if _missing_dependency_root_helpers:
        describe_dependency_roots = _unbound_dependency_root_helper
        ensure_active_lock_file = _unbound_dependency_root_helper
        require_dependency_roots = _unbound_dependency_root_helper
        load_lock_file = _unbound_dependency_root_helper
        prepare_seed_repository_closure = _unbound_dependency_root_helper
        materialize_dependency_roots = _unbound_dependency_root_helper
    else:
        DependencyRootSummary = _bound_source_roots.DependencyRootSummary
        describe_dependency_roots = _bound_source_roots.describe_dependency_roots
        ensure_active_lock_file = _bound_source_roots.ensure_active_lock_file
        require_dependency_roots = _bound_source_roots.require_dependency_roots
        load_lock_file = _bound_source_roots.load_lock_file
        prepare_seed_repository_closure = (
            _bound_source_roots.prepare_seed_repository_closure
        )
        materialize_dependency_roots = _bound_source_roots.materialize_dependency_roots


@dataclass(frozen=True)
class CMakeDependencyBuildSpec:
    dependency_name: str
    uses_c_language: bool
    cmake_options: tuple[str, ...]
    uses_cxx_language: bool = True
    source_subdir: str = ""


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


CMAKE_DEPENDENCY_BUILD_ORDER: tuple[CMakeDependencyBuildSpec, ...] = ()

CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME = {
    build_spec.dependency_name: build_spec for build_spec in CMAKE_DEPENDENCY_BUILD_ORDER
}

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Manage {REPO_DISPLAY_NAME} dependency-root workflow state.")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--init",
        action="store_true",
        help="Prepare dependency seed repos and initialize the active lock file.",
    )
    mode_group.add_argument(
        "--update",
        action="store_true",
        help="Materialize locked dependency roots and generate CMakePresets.json.",
    )
    mode_group.add_argument(
        "--build-dependencies-from-cmake",
        metavar="CONTEXT_JSON",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose git output while keeping FreeCM status lines.",
    )
    return parser.parse_args()


def run_command(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    print(">>", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)


def _prepend_pythonpath(env: MutableMapping[str, str], path: Path) -> None:
    path_value = str(path.resolve())
    current = env.get("PYTHONPATH", "")
    parts = [part for part in current.split(os.pathsep) if part]
    if path_value in parts:
        return
    env["PYTHONPATH"] = os.pathsep.join([path_value, *parts])


def _managed_dependency_root_parent(repo_root: Path) -> Path:
    return (repo_root / "build" / "dependency_source_roots").resolve()


def _is_managed_dependency_root(repo_root: Path, dependency_root: Path) -> bool:
    managed_parent = _managed_dependency_root_parent(repo_root)
    try:
        dependency_root.resolve().relative_to(managed_parent)
    except ValueError:
        return False
    return True


def _nested_dependency_workflow_script(dependency_root: Path) -> Path:
    return dependency_root / "configs" / "source_root_workflow.py"


def _nested_dependency_lock_template(dependency_root: Path) -> Path:
    return dependency_root / "source_roots.lock.jsonc.in"


def _nested_dependency_lock_file(dependency_root: Path) -> Path:
    return dependency_root / "source_roots.lock.jsonc"


def _has_nested_dependency_workflow(dependency_root: Path) -> bool:
    return (
        _nested_dependency_workflow_script(dependency_root).is_file()
        and _nested_dependency_lock_template(dependency_root).is_file()
    )


def _write_nested_manual_dependency_lock(
    dependency_root: Path,
    dependency_roots: Any,
) -> None:
    template_path = _nested_dependency_lock_template(dependency_root)
    lock_data = loads_jsonc(
        template_path.read_text(encoding="utf-8"),
        path_label=str(template_path),
    )
    lock_data["depsMode"] = "manual"

    deps_manual_path = lock_data.get("depsManualPath")
    if not isinstance(deps_manual_path, dict):
        raise WorkflowError(f"Invalid depsManualPath map in nested dependency-root template: {template_path}")

    for dependency_name in deps_manual_path.keys():
        try:
            deps_manual_path[dependency_name] = str(dependency_roots.dependency_root_for(dependency_name))
        except KeyError as exc:
            raise WorkflowError(
                f"Nested dependency-root workflow requires unsupported dependency {dependency_name!r}: "
                f"{dependency_root}"
            ) from exc

    lock_path = _nested_dependency_lock_file(dependency_root)
    atomic_write_json(lock_path, lock_data)


def prepare_nested_dependency_workflows(
    dependency_roots: Any,
    *,
    repo_root: Path = REPO_ROOT,
) -> None:
    for dependency_name in dependency_roots.closure_order:
        dependency_root = dependency_roots.dependency_root_for(dependency_name)
        if not _is_managed_dependency_root(repo_root, dependency_root):
            continue
        if not _has_nested_dependency_workflow(dependency_root):
            continue

        _write_nested_manual_dependency_lock(dependency_root, dependency_roots)


def remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        _rmtree(path)
    else:
        path.unlink()


def _rmtree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_make_writable_and_retry)
    else:
        shutil.rmtree(path, onerror=_make_writable_and_retry_legacy)


def _make_writable_and_retry(function: object, path: str, excinfo: BaseException) -> None:
    if not isinstance(excinfo, PermissionError):
        raise excinfo
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _make_writable_and_retry_legacy(
    function: object,
    path: str,
    excinfo: tuple[type[BaseException], BaseException, object],
) -> None:
    _make_writable_and_retry(function, path, excinfo[1])


def host_os_group(system_name: str | None = None) -> str:
    system_name = system_name or platform.system()
    if system_name == "Linux":
        return "linux"
    if system_name == "Darwin":
        return "mac"
    if system_name == "Windows":
        return "win"
    raise WorkflowError(
        f"Unsupported host OS '{system_name}'. Supported host OS values: Linux, Darwin, Windows."
    )


def write_generated_cmake_presets(repo_root: Path, model: dict[str, Any]) -> None:
    target_path = repo_root / "CMakePresets.json"
    target_path.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")


def shared_clangd_template_path() -> Path:
    path = Path(__file__).resolve().parent / "clangd" / ".clangd.in"
    if not path.is_file():
        raise WorkflowError(f"Missing shared clangd template: {path}")
    return path


def ensure_clangd_config(repo_root: Path) -> tuple[Path, bool]:
    target_path = repo_root / ".clangd"
    if target_path.exists():
        if not target_path.is_file():
            raise WorkflowError(f"clangd config path exists but is not a file: {target_path}")
        return target_path.resolve(), False

    template_path = shared_clangd_template_path()
    target_path.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")
    return target_path.resolve(), True


def print_cli_status(action: str, message: str, *, level: str = "info") -> None:
    print(
        format_status_line(
            action,
            message,
            level=level,
            use_color=stdout_supports_color(),
        )
    )


def _command_display(cmd: Sequence[str]) -> str:
    return " ".join(str(part) for part in cmd)


def format_called_process_error(error: subprocess.CalledProcessError) -> str:
    command = error.cmd if isinstance(error.cmd, (list, tuple)) else [str(error.cmd)]
    lines = [
        f"Command failed with exit code {error.returncode}:",
        f"  {_command_display(command)}",
    ]
    stdout = getattr(error, "stdout", None)
    stderr = getattr(error, "stderr", None)
    if stdout:
        lines.append("stdout:")
        lines.append(str(stdout).strip())
    if stderr:
        lines.append("stderr:")
        lines.append(str(stderr).strip())
    return "\n".join(lines)


def format_cli_exception(error: BaseException, *, unexpected: bool = False) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        message = format_called_process_error(error)
    else:
        message = str(error).strip() or error.__class__.__name__

    if unexpected:
        return (
            f"Unexpected Python error ({error.__class__.__name__}): {message}\n"
            "Set FREECM_DEBUG=1 and rerun to print a traceback."
        )
    return message


def print_cli_error(error: BaseException, *, unexpected: bool = False) -> None:
    use_color = stderr_supports_color()
    print(
        format_status_line(
            "error",
            format_cli_exception(error, unexpected=unexpected),
            level="error",
            use_color=use_color,
        ),
        file=sys.stderr,
    )
    if unexpected and os.environ.get("FREECM_DEBUG"):
        traceback.print_exception(type(error), error, error.__traceback__)


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
    return (
        str(value)
        .replace("${sourceDir}", str(repo_root))
        .replace("${presetName}", preset_name)
    )


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


def dependency_build_dir(repo_root: Path, model: dict[str, Any], preset_name: str, dependency_name: str) -> Path:
    return build_dir_for_preset(repo_root, model, preset_name) / "dependency_builds" / dependency_name


def dependency_install_prefix(repo_root: Path, model: dict[str, Any], preset_name: str, dependency_name: str) -> Path:
    return build_dir_for_preset(repo_root, model, preset_name) / "dependency_installs" / dependency_name


def build_dir_for_preset_name(repo_root: Path, preset_name: str) -> Path:
    return (repo_root / "build" / preset_name).resolve()


def dependency_build_dir_for_name(repo_root: Path, preset_name: str, dependency_name: str) -> Path:
    return build_dir_for_preset_name(repo_root, preset_name) / "dependency_builds" / dependency_name


def dependency_install_prefix_for_name(repo_root: Path, preset_name: str, dependency_name: str) -> Path:
    return build_dir_for_preset_name(repo_root, preset_name) / "dependency_installs" / dependency_name


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


def dependency_state_file_path(repo_root: Path, preset_name: str) -> Path:
    return build_dir_for_preset_name(repo_root, preset_name) / "dependency_installs" / DEPENDENCY_STATE_FILENAME


def ordered_dependency_build_specs(dependency_roots: Any) -> list[CMakeDependencyBuildSpec]:
    ordered_specs: list[CMakeDependencyBuildSpec] = []
    for dependency_name in dependency_roots.closure_order:
        try:
            ordered_specs.append(CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME[dependency_name])
        except KeyError as exc:
            raise WorkflowError(
                f"Missing dependency build spec for recursive dependency-root dependency {dependency_name!r}. "
                "Configure host-specific specs with bind_cmake_workflow_script(..., dependency_build_order=...)."
            ) from exc
    return ordered_specs


def dependency_build_state(
    context: CMakeDependencyBuildContext,
    dependency_roots: Any,
) -> dict[str, Any]:
    return {
        "mode": dependency_roots.mode,
        "roots": {
            dependency_name: str(dependency_roots.dependency_root_for(dependency_name))
            for dependency_name in dependency_roots.closure_order
        },
        "resolved": dict(dependency_roots.resolved_commits),
        "context": {
            "presetName": context.preset_name,
            "generator": context.generator,
            "generatorPlatform": context.generator_platform,
            "generatorToolset": context.generator_toolset,
            "cmakeExecutable": context.cmake_executable,
            "buildConfigurations": list(context.build_configurations),
            "externalPrefixPath": context.external_prefix_path,
            "cacheVariables": dict(context.cache_variables),
        },
    }


def dependency_state_matches(
    repo_root: Path,
    context: CMakeDependencyBuildContext,
    dependency_roots: Any,
) -> bool:
    if dependency_roots.mode == "manual":
        return False

    state_path = dependency_state_file_path(repo_root, context.preset_name)
    if not state_path.is_file():
        return False

    expected = dependency_build_state(context, dependency_roots)
    actual = load_json_file(state_path)
    if actual != expected:
        return False

    for build_spec in ordered_dependency_build_specs(dependency_roots):
        install_prefix = dependency_install_prefix_for_name(
            repo_root,
            context.preset_name,
            build_spec.dependency_name,
        )
        if not install_prefix.is_dir():
            return False
    return True


def write_dependency_state_file(
    repo_root: Path,
    context: CMakeDependencyBuildContext,
    dependency_roots: Any,
) -> None:
    state_path = dependency_state_file_path(repo_root, context.preset_name)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(dependency_build_state(context, dependency_roots), indent=2) + "\n",
        encoding="utf-8",
    )


def ensure_dependency_root_active_lock(
    dependency_root: Path,
    available_dependency_roots: dict[str, Path],
) -> None:
    template_path = dependency_root / "source_roots.lock.jsonc.in"
    lock_path = dependency_root / "source_roots.lock.jsonc"
    if not template_path.is_file() or lock_path.exists():
        return
    template_text = template_path.read_text(encoding="utf-8")
    atomic_write_text(lock_path, template_text)
    lock_data = loads_jsonc(
        template_text,
        path_label=str(lock_path),
    )
    deps_manual_path = lock_data.get("depsManualPath")
    dependencies = lock_data.get("dependencies")
    if not isinstance(deps_manual_path, dict) or not isinstance(dependencies, dict):
        return

    child_dependency_names = set(dependencies.keys())
    if not child_dependency_names.issubset(available_dependency_roots.keys()):
        return

    for dependency_name in child_dependency_names:
        deps_manual_path[dependency_name] = str(available_dependency_roots[dependency_name])
    lock_data["depsMode"] = "manual"
    atomic_write_json(lock_path, lock_data)


def configure_dependency_for_context(
    *,
    repo_root: Path,
    context: CMakeDependencyBuildContext,
    dependency_name: str,
    dependency_root: Path,
    install_prefix: Path,
    dependency_prefixes: Sequence[Path],
    cmake_options: Sequence[str],
    available_dependency_roots: dict[str, Path],
) -> None:
    build_dir = dependency_build_dir_for_name(repo_root, context.preset_name, dependency_name)
    build_dir.mkdir(parents=True, exist_ok=True)
    source_dir = dependency_source_dir(dependency_root, dependency_name)
    env = dict(os.environ)
    if _is_managed_dependency_root(repo_root, dependency_root):
        ensure_dependency_root_active_lock(dependency_root, available_dependency_roots)
        _prepend_pythonpath(env, _PACKAGE_REPO_ROOT)

    managed_by_parent_args: list[str] = []
    if _has_nested_dependency_workflow(dependency_root):
        managed_by_parent_args.append("-DSOURCE_ROOT_WORKFLOW_MANAGED_BY_PARENT=ON")

    configure_cmd = [
        context.cmake_executable,
        "-S",
        str(source_dir),
        "-B",
        str(build_dir),
        "-G",
        context.generator,
        f"-DCMAKE_INSTALL_PREFIX={install_prefix}",
        *managed_by_parent_args,
        *cmake_options,
    ]

    if context.generator_platform:
        configure_cmd.extend(["-A", context.generator_platform])
    if context.generator_toolset:
        configure_cmd.extend(["-T", context.generator_toolset])

    for key, value in context.cache_variables.items():
        if key in {"CMAKE_PREFIX_PATH", "CMAKE_INSTALL_PREFIX", "CMAKE_BUILD_TYPE"}:
            continue
        if _is_c_language_only_cache_key(key) and not _dependency_uses_c_language(dependency_name):
            continue
        if _is_cxx_language_only_cache_key(key) and not _dependency_uses_cxx_language(dependency_name):
            continue
        if value in ("",):
            continue
        configure_cmd.append(f"-D{key}={value}")

    if not multi_config_generator(context.generator):
        configure_cmd.append(
            f"-DCMAKE_BUILD_TYPE={context.cache_variables.get('CMAKE_BUILD_TYPE', 'Release') or 'Release'}"
        )

    prefix_parts = [str(path) for path in dependency_prefixes]
    if context.external_prefix_path:
        prefix_parts.append(context.external_prefix_path)
    if prefix_parts:
        configure_cmd.append(f"-DCMAKE_PREFIX_PATH={';'.join(prefix_parts)}")

    run_command(configure_cmd, cwd=source_dir, env=env)

    for configuration in context.build_configurations:
        build_cmd = [context.cmake_executable, "--build", str(build_dir)]
        install_cmd = [context.cmake_executable, "--install", str(build_dir)]
        if multi_config_generator(context.generator):
            build_cmd.extend(["--config", configuration])
            install_cmd.extend(["--config", configuration])
        run_command(build_cmd, cwd=source_dir, env=env)
        run_command(install_cmd, cwd=source_dir, env=env)


def dependency_source_dir(dependency_root: Path, dependency_name: str) -> Path:
    source_subdir = ""
    for build_spec in CMAKE_DEPENDENCY_BUILD_ORDER:
        if build_spec.dependency_name == dependency_name:
            source_subdir = build_spec.source_subdir
            break
    if not source_subdir:
        return dependency_root
    subdir_path = Path(source_subdir)
    if subdir_path.is_absolute():
        raise WorkflowError(
            f"Dependency build spec {dependency_name!r} uses absolute source_subdir: {source_subdir}"
        )
    source_dir = (dependency_root / subdir_path).resolve()
    try:
        source_dir.relative_to(dependency_root.resolve())
    except ValueError as exc:
        raise WorkflowError(
            f"Dependency build spec {dependency_name!r} source_subdir escapes dependency root: {source_subdir}"
        ) from exc
    return source_dir


def _dependency_uses_c_language(dependency_name: str) -> bool:
    for build_spec in CMAKE_DEPENDENCY_BUILD_ORDER:
        if build_spec.dependency_name == dependency_name:
            return build_spec.uses_c_language
    raise WorkflowError(f"Unknown dependency build spec: {dependency_name}")


def _dependency_uses_cxx_language(dependency_name: str) -> bool:
    for build_spec in CMAKE_DEPENDENCY_BUILD_ORDER:
        if build_spec.dependency_name == dependency_name:
            return build_spec.uses_cxx_language
    raise WorkflowError(f"Unknown dependency build spec: {dependency_name}")


def _is_c_language_only_cache_key(key: str) -> bool:
    if key == "CMAKE_C_COMPILER":
        return True
    if key == "CMAKE_C_STANDARD":
        return True
    if key.startswith("CMAKE_C_FLAGS"):
        return True
    return False


def _is_cxx_language_only_cache_key(key: str) -> bool:
    if key == "CMAKE_CXX_COMPILER":
        return True
    if key == "CMAKE_CXX_STANDARD":
        return True
    if key.startswith("CMAKE_CXX_FLAGS"):
        return True
    return False


def build_dependencies_for_cmake_context(
    context: CMakeDependencyBuildContext,
    *,
    repo_root: Path = REPO_ROOT,
) -> None:
    dependency_roots = require_dependency_roots(repo_root=repo_root)
    build_specs = ordered_dependency_build_specs(dependency_roots)
    if dependency_state_matches(repo_root, context, dependency_roots):
        return

    for build_spec in build_specs:
        remove_path(
            dependency_build_dir_for_name(repo_root, context.preset_name, build_spec.dependency_name)
        )
        remove_path(
            dependency_install_prefix_for_name(repo_root, context.preset_name, build_spec.dependency_name)
        )

    installed_prefixes: list[Path] = []
    available_dependency_roots = {
        dependency_name: dependency_roots.dependency_root_for(dependency_name)
        for dependency_name in dependency_roots.closure_order
    }
    for build_spec in build_specs:
        dependency_root = dependency_roots.dependency_root_for(build_spec.dependency_name)
        install_prefix = dependency_install_prefix_for_name(
            repo_root,
            context.preset_name,
            build_spec.dependency_name,
        )
        install_prefix.mkdir(parents=True, exist_ok=True)
        configure_dependency_for_context(
            repo_root=repo_root,
            context=context,
            dependency_name=build_spec.dependency_name,
            dependency_root=dependency_root,
            install_prefix=install_prefix,
            dependency_prefixes=installed_prefixes,
            cmake_options=build_spec.cmake_options,
            available_dependency_roots=available_dependency_roots,
        )
        installed_prefixes.append(install_prefix)

    write_dependency_state_file(repo_root, context, dependency_roots)


def configure_dependency(
    *,
    repo_root: Path,
    preset_model: dict[str, Any],
    preset_name: str,
    dependency_name: str,
    dependency_root: Path,
    install_prefix: Path,
    dependency_prefixes: Sequence[Path],
    cmake_options: Sequence[str],
) -> None:
    build_dir = dependency_build_dir(repo_root, preset_model, preset_name, dependency_name)
    build_dir.mkdir(parents=True, exist_ok=True)
    generator = resolve_generator(preset_model, preset_name)
    env = preset_environment(preset_model, preset_name)
    cmake_executable = cmake_executable_for_preset(preset_model, preset_name)

    configure_cmd = [
        cmake_executable,
        "-S",
        str(dependency_root),
        "-B",
        str(build_dir),
        "-G",
        generator,
        *preset_generator_args(preset_model, preset_name),
        f"-DCMAKE_INSTALL_PREFIX={install_prefix}",
        *forwarded_cache_args(preset_model, preset_name),
        *cmake_options,
    ]
    if not multi_config_generator(generator):
        configure_cmd.append(
            f"-DCMAKE_BUILD_TYPE={single_config_build_type(preset_model, preset_name)}"
        )

    prefix_path = combined_prefix_path(
        preset_model,
        repo_root,
        preset_name,
        dependency_prefixes,
    )
    if prefix_path:
        configure_cmd.append(f"-DCMAKE_PREFIX_PATH={prefix_path}")

    run_command(configure_cmd, cwd=dependency_root, env=env)

    for configuration in build_configurations_for_preset(preset_model, preset_name):
        build_cmd = [cmake_executable, "--build", str(build_dir)]
        install_cmd = [cmake_executable, "--install", str(build_dir)]
        if multi_config_generator(generator):
            build_cmd.extend(["--config", configuration])
            install_cmd.extend(["--config", configuration])
        run_command(build_cmd, cwd=dependency_root, env=env)
        run_command(install_cmd, cwd=dependency_root, env=env)


def cmd_init(*, quiet: bool = False) -> int:
    print_cli_status("init", f"repo={REPO_ROOT}")
    lock_path, created = ensure_active_lock_file(repo_root=REPO_ROOT)
    if created:
        print_cli_status("init", f"created active dependency lock: {lock_path}", level="ok")
    else:
        print_cli_status("init", f"using active dependency lock: {lock_path}")
    clangd_path, clangd_created = ensure_clangd_config(REPO_ROOT)
    if clangd_created:
        print_cli_status("init", f"created clangd config: {clangd_path}", level="ok")
    else:
        print_cli_status("init", f"using clangd config: {clangd_path}")
    print_cli_status("init", "checking dependency seed repositories; network is allowed")
    closure = prepare_seed_repository_closure(
        repo_root=REPO_ROOT,
        progress=lambda action, message, level: print_cli_status(
            action,
            message,
            level=level,
        ),
        quiet=quiet,
    )
    print_cli_status(
        "init",
        f"prepared {len(closure.topo_order)} dependency seed repositories",
        level="ok",
    )
    asset_summaries = prepare_asset_seeds(REPO_ROOT)
    for summary in asset_summaries:
        print_cli_status(
            "asset",
            f"{summary.asset_name}: prepared {len(summary.files)} files -> {summary.seed_root}",
            level="ok",
        )
    return 0


def cmd_update() -> int:
    print_cli_status("update", f"repo={REPO_ROOT}")
    print_cli_status("update", "materializing dependency roots from the active lock; network is disabled")
    before_lock_data = load_lock_file(repo_root=REPO_ROOT)
    dependency_roots = materialize_dependency_roots(repo_root=REPO_ROOT, allow_network=False)
    print_cli_status(
        "update",
        f"materialized {len(dependency_roots.closure_order)} dependency roots",
        level="ok",
    )
    asset_summaries = require_asset_seeds(REPO_ROOT)
    for summary in asset_summaries:
        print_cli_status(
            "asset",
            f"{summary.asset_name}: verified {len(summary.files)} files -> {summary.seed_root}",
            level="ok",
        )
    use_color = stdout_supports_color()
    for line in format_dependency_commit_change_lines(
        dependency_commit_changes(
            before_lock_data,
            dependency_roots.lock_data,
            dependency_roots.direct_dependency_names,
        ),
        use_color=use_color,
    ):
        print(line)
    for line in format_dependency_resolution_lines(
        describe_dependency_roots(dependency_roots),
        use_color=use_color,
    ):
        print(line)
    os_group = host_os_group()
    print_cli_status("update", f"resolving {os_group} CMake preset template")
    resolved_presets = resolve_preset_models(
        REPO_ROOT,
        dependency_roots.lock_data,
        os_group,
        dependency_roots.closure_order,
    )
    print_cli_status("update", "preparing nested dependency workflows")
    prepare_nested_dependency_workflows(dependency_roots, repo_root=REPO_ROOT)
    write_generated_cmake_presets(REPO_ROOT, resolved_presets.generated_model)
    print_cli_status("update", f"wrote {REPO_ROOT / 'CMakePresets.json'}", level="ok")
    return 0


def cmd_build_dependencies_from_cmake(context_json_path: Path) -> int:
    context = load_cmake_dependency_build_context(context_json_path)
    build_dependencies_for_cmake_context(context, repo_root=REPO_ROOT)
    return 0


def main() -> int:
    args = parse_args()
    try:
        if args.init:
            return cmd_init(quiet=getattr(args, "quiet", False))
        if args.build_dependencies_from_cmake:
            return cmd_build_dependencies_from_cmake(
                Path(args.build_dependencies_from_cmake).resolve()
            )
        return cmd_update()
    except (FileNotFoundError, ValueError, RuntimeError, WorkflowError, subprocess.CalledProcessError) as exc:
        print_cli_error(exc)
        return 1
    except Exception as exc:
        print_cli_error(exc, unexpected=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


_SCRIPT_FUNCTION_NAMES = (
    "parse_args",
    "run_command",
    "stderr_supports_color",
    "stdout_supports_color",
    "_style",
    "format_dependency_resolution_lines",
    "format_status_line",
    "_managed_dependency_root_parent",
    "_is_managed_dependency_root",
    "_nested_dependency_workflow_script",
    "_nested_dependency_lock_template",
    "_nested_dependency_lock_file",
    "_has_nested_dependency_workflow",
    "_write_nested_manual_dependency_lock",
    "prepare_nested_dependency_workflows",
    "remove_path",
    "host_os_group",
    "host_template_path",
    "load_json_file",
    "collect_template_tokens",
    "managed_prefix_entries",
    "inject_managed_prefixes",
    "resolve_preset_models",
    "resolve_preset_model",
    "write_generated_cmake_presets",
    "shared_clangd_template_path",
    "ensure_clangd_config",
    "print_cli_status",
    "_command_display",
    "format_called_process_error",
    "format_cli_exception",
    "print_cli_error",
    "configure_presets",
    "find_configure_preset",
    "cmake_executable_for_preset",
    "resolve_preset_string",
    "resolve_generator",
    "preset_environment",
    "build_dir_for_preset",
    "dependency_build_dir",
    "dependency_install_prefix",
    "build_dir_for_preset_name",
    "dependency_build_dir_for_name",
    "dependency_install_prefix_for_name",
    "multi_config_generator",
    "preset_generator_args",
    "forwarded_cache_args",
    "external_prefix_path",
    "combined_prefix_path",
    "single_config_build_type",
    "build_configurations_for_preset",
    "_normalized_context_build_configurations",
    "load_cmake_dependency_build_context",
    "dependency_state_file_path",
    "ordered_dependency_build_specs",
    "dependency_build_state",
    "dependency_state_matches",
    "write_dependency_state_file",
    "ensure_dependency_root_active_lock",
    "configure_dependency_for_context",
    "dependency_source_dir",
    "_dependency_uses_c_language",
    "_dependency_uses_cxx_language",
    "_is_c_language_only_cache_key",
    "_is_cxx_language_only_cache_key",
    "build_dependencies_for_cmake_context",
    "configure_dependency",
    "cmd_init",
    "cmd_update",
    "cmd_build_dependencies_from_cmake",
    "main",
)

_SCRIPT_CONSTANT_NAMES = (
    "WorkflowError",
    "CMakeDependencyBuildSpec",
    "ResolvedPresetModel",
    "CMakeDependencyBuildContext",
    "HOST_TEMPLATE_FILENAMES",
    "ANSI_RESET",
    "ANSI_BOLD",
    "ANSI_DIM",
    "ANSI_RED",
    "ANSI_YELLOW",
    "ANSI_GREEN",
    "ANSI_BLUE",
    "ANSI_CYAN",
    "MODE_LABELS",
    "MODE_COLORS",
)

_ORIGINAL_SCRIPT_FUNCTIONS = {
    name: globals()[name]
    for name in _SCRIPT_FUNCTION_NAMES
}


def bind_cmake_workflow_script(
    module_globals: MutableMapping[str, Any],
    *,
    repo_root: Path,
    repo_display_name: str,
    dependency_build_order: Sequence[CMakeDependencyBuildSpec],
    dependency_state_filename: str | None = None,
) -> None:
    wrappers: dict[str, Any] = {}

    module_globals.update(
        {
            "REPO_ROOT": repo_root.resolve(),
            "REPO_DISPLAY_NAME": repo_display_name,
            "CMAKE_DEPENDENCY_BUILD_ORDER": tuple(dependency_build_order),
            "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME": {
                build_spec.dependency_name: build_spec
                for build_spec in dependency_build_order
            },
            "DEPENDENCY_STATE_FILENAME": (
                dependency_state_filename
                or f".{repo_display_name.lower()}_dependency_state.json"
            ),
        }
    )

    for name in _SCRIPT_CONSTANT_NAMES:
        module_globals[name] = globals()[name]

    def sync_shared_globals() -> None:
        globals()["REPO_ROOT"] = module_globals["REPO_ROOT"]
        globals()["REPO_DISPLAY_NAME"] = module_globals["REPO_DISPLAY_NAME"]
        globals()["CMAKE_DEPENDENCY_BUILD_ORDER"] = module_globals["CMAKE_DEPENDENCY_BUILD_ORDER"]
        globals()["CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME"] = module_globals["CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME"]
        globals()["DEPENDENCY_STATE_FILENAME"] = module_globals["DEPENDENCY_STATE_FILENAME"]

        for helper_name in _DEPENDENCY_ROOT_HELPER_NAMES:
            if helper_name in module_globals:
                globals()[helper_name] = module_globals[helper_name]

        for function_name, original_function in _ORIGINAL_SCRIPT_FUNCTIONS.items():
            current_value = module_globals.get(function_name)
            if current_value is not None and current_value is not wrappers.get(function_name):
                globals()[function_name] = current_value
            else:
                globals()[function_name] = original_function

    def make_wrapper(function_name: str) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            sync_shared_globals()
            return globals()[function_name](*args, **kwargs)

        wrapper.__name__ = function_name
        wrapper.__qualname__ = function_name
        wrapper.__doc__ = _ORIGINAL_SCRIPT_FUNCTIONS[function_name].__doc__
        return wrapper

    for name in _SCRIPT_FUNCTION_NAMES:
        wrappers[name] = make_wrapper(name)
        module_globals[name] = wrappers[name]

    module_globals["_sync_shared_cmake_workflow_script"] = sync_shared_globals
