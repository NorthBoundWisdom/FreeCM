#!/usr/bin/env python3
# Usage:
#   python3 /path/to/FreeCM/repomgrcpp/cmake_workflow.py --init
#   python3 /path/to/FreeCM/repomgrcpp/cmake_workflow.py --update
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.cmake_workflow --help

from __future__ import annotations

import argparse
import os
import platform
import subprocess  # nosec B404
import sys
import traceback
from collections.abc import MutableMapping, Sequence
from pathlib import Path
from typing import Any

_PACKAGE_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_REPO_ROOT))

from freecm.atomic_write import atomic_write_json, atomic_write_text
from freecm.dependency_lock import ACTIVE_LOCK_FILE_NAME, TEMPLATE_LOCK_FILE_NAME
from freecm.git_repositories import git_toplevel
from freecm.git_repositories import remove_path as _remove_path
from freecm.materializer import (
    nested_dependency_lock_file_path,
    nested_dependency_lock_template_path,
    write_nested_manual_dependency_lock,
)
from freecm.terminal_style import (
    ANSI_BLUE as ANSI_BLUE,
)
from freecm.terminal_style import (
    ANSI_BOLD as ANSI_BOLD,
)
from freecm.terminal_style import (
    ANSI_CYAN as ANSI_CYAN,
)
from freecm.terminal_style import (
    ANSI_DIM as ANSI_DIM,
)
from freecm.terminal_style import (
    ANSI_GREEN as ANSI_GREEN,
)
from freecm.terminal_style import (
    ANSI_RED as ANSI_RED,
)
from freecm.terminal_style import (
    ANSI_RESET as ANSI_RESET,
)
from freecm.terminal_style import (
    ANSI_YELLOW as ANSI_YELLOW,
)
from freecm.terminal_style import (
    MODE_COLORS as MODE_COLORS,
)
from freecm.terminal_style import (
    MODE_LABELS as MODE_LABELS,
)
from freecm.terminal_style import (
    _style as _style,
)
from freecm.terminal_style import (
    format_dependency_commit_change_lines,
    format_dependency_resolution_lines,
    format_status_line,
    stderr_supports_color,
    stdout_supports_color,
)
from freecm.workspace_lock import workspace_mutation_lock
from repomgrcpp.cmake_dependency_builder import (
    DEPENDENCY_BUILD_STATE_SCHEMA_VERSION as DEPENDENCY_BUILD_STATE_SCHEMA_VERSION,
)
from repomgrcpp.cmake_dependency_builder import (
    CMakeDependencyBuilder,
    CMakeDependencyBuilderConfig,
    CMakeDependencyBuilderServices,
    CMakeDependencyBuildSpec,
)
from repomgrcpp.cmake_dependency_builder import (
    _dependency_inputs_fingerprint as _dependency_inputs_fingerprint,
)
from repomgrcpp.cmake_dependency_builder import (
    _dependency_parent_names as _dependency_parent_names,
)
from repomgrcpp.cmake_dependency_builder import (
    _dependency_source_dir_for_spec as _dependency_source_dir_for_spec,
)
from repomgrcpp.cmake_dependency_builder import (
    _dependency_transitive_names as _dependency_transitive_names,
)
from repomgrcpp.cmake_dependency_builder import (
    _dependency_uses_manual_override as _dependency_uses_manual_override,
)
from repomgrcpp.cmake_dependency_builder import (
    _effective_dependency_cache_variables as _effective_dependency_cache_variables,
)
from repomgrcpp.cmake_dependency_builder import (
    _is_c_language_only_cache_key as _is_c_language_only_cache_key,
)
from repomgrcpp.cmake_dependency_builder import (
    _is_cxx_language_only_cache_key as _is_cxx_language_only_cache_key,
)
from repomgrcpp.cmake_dependency_builder import (
    _json_compatible_build_spec as _json_compatible_build_spec,
)
from repomgrcpp.cmake_preset_context import (
    CMakeDependencyBuildContext,
    load_cmake_dependency_build_context,
)
from repomgrcpp.cmake_preset_context import (
    _normalized_context_build_configurations as _normalized_context_build_configurations,
)
from repomgrcpp.cmake_preset_context import (
    build_configurations_for_preset as build_configurations_for_preset,
)
from repomgrcpp.cmake_preset_context import (
    build_dir_for_preset as build_dir_for_preset,
)
from repomgrcpp.cmake_preset_context import (
    build_dir_for_preset_name as build_dir_for_preset_name,
)
from repomgrcpp.cmake_preset_context import (
    cmake_executable_for_preset as cmake_executable_for_preset,
)
from repomgrcpp.cmake_preset_context import (
    combined_prefix_path as combined_prefix_path,
)
from repomgrcpp.cmake_preset_context import (
    configure_presets as configure_presets,
)
from repomgrcpp.cmake_preset_context import (
    dependency_build_dir as dependency_build_dir,
)
from repomgrcpp.cmake_preset_context import (
    dependency_build_dir_for_name as dependency_build_dir_for_name,
)
from repomgrcpp.cmake_preset_context import (
    dependency_install_prefix as dependency_install_prefix,
)
from repomgrcpp.cmake_preset_context import (
    dependency_install_prefix_for_name as dependency_install_prefix_for_name,
)
from repomgrcpp.cmake_preset_context import (
    external_prefix_path as external_prefix_path,
)
from repomgrcpp.cmake_preset_context import (
    find_configure_preset as find_configure_preset,
)
from repomgrcpp.cmake_preset_context import (
    forwarded_cache_args as forwarded_cache_args,
)
from repomgrcpp.cmake_preset_context import (
    multi_config_generator as multi_config_generator,
)
from repomgrcpp.cmake_preset_context import (
    preset_environment as preset_environment,
)
from repomgrcpp.cmake_preset_context import (
    preset_generator_args as preset_generator_args,
)
from repomgrcpp.cmake_preset_context import (
    resolve_generator as resolve_generator,
)
from repomgrcpp.cmake_preset_context import (
    resolve_preset_string as resolve_preset_string,
)
from repomgrcpp.cmake_preset_context import (
    single_config_build_type as single_config_build_type,
)
from repomgrcpp.errors import WorkflowError
from repomgrcpp.preset_templates import (
    HOST_TEMPLATE_FILENAMES as HOST_TEMPLATE_FILENAMES,
)
from repomgrcpp.preset_templates import (
    ResolvedPresetModel as ResolvedPresetModel,
)
from repomgrcpp.preset_templates import (
    collect_template_tokens as collect_template_tokens,
)
from repomgrcpp.preset_templates import (
    host_template_path as host_template_path,
)
from repomgrcpp.preset_templates import (
    inject_managed_prefixes as inject_managed_prefixes,
)
from repomgrcpp.preset_templates import (
    load_json_file as load_json_file,
)
from repomgrcpp.preset_templates import (
    managed_prefix_entries as managed_prefix_entries,
)
from repomgrcpp.preset_templates import (
    resolve_preset_model as resolve_preset_model,
)
from repomgrcpp.preset_templates import (
    resolve_preset_models,
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
_OPTIONAL_DEPENDENCY_ROOT_HELPER_NAMES = (
    "_prepare_seed_repository_closure_unlocked",
    "_materialize_dependency_roots_unlocked",
)


def _looks_like_dependency_workflow_repo(repo_root: Path) -> bool:
    return (
        (repo_root / ACTIVE_LOCK_FILE_NAME).exists()
        or (repo_root / TEMPLATE_LOCK_FILE_NAME).exists()
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

from freecm.asset_seeds import prepare_asset_seeds, require_asset_seeds  # noqa: E402
from freecm.dependency_roots import (  # noqa: E402 - imported after repo sys.path setup.
    DependencyRootSummary as _CoreDependencyRootSummary,
)
from freecm.dependency_roots import (
    dependency_commit_changes,
)

DependencyRootSummary: type[Any] = _CoreDependencyRootSummary

if _bound_source_roots is None:
    describe_dependency_roots = _unbound_dependency_root_helper
    ensure_active_lock_file = _unbound_dependency_root_helper
    require_dependency_roots = _unbound_dependency_root_helper
    load_lock_file = _unbound_dependency_root_helper
    prepare_seed_repository_closure = _unbound_dependency_root_helper
    _prepare_seed_repository_closure_unlocked = _unbound_dependency_root_helper
    materialize_dependency_roots = _unbound_dependency_root_helper
    _materialize_dependency_roots_unlocked = _unbound_dependency_root_helper
else:
    _missing_dependency_root_helpers = [
        name for name in _DEPENDENCY_ROOT_HELPER_NAMES if not hasattr(_bound_source_roots, name)
    ]
    if _missing_dependency_root_helpers:
        describe_dependency_roots = _unbound_dependency_root_helper
        ensure_active_lock_file = _unbound_dependency_root_helper
        require_dependency_roots = _unbound_dependency_root_helper
        load_lock_file = _unbound_dependency_root_helper
        prepare_seed_repository_closure = _unbound_dependency_root_helper
        _prepare_seed_repository_closure_unlocked = _unbound_dependency_root_helper
        materialize_dependency_roots = _unbound_dependency_root_helper
        _materialize_dependency_roots_unlocked = _unbound_dependency_root_helper
    else:
        DependencyRootSummary = _bound_source_roots.DependencyRootSummary
        describe_dependency_roots = _bound_source_roots.describe_dependency_roots
        ensure_active_lock_file = _bound_source_roots.ensure_active_lock_file
        require_dependency_roots = _bound_source_roots.require_dependency_roots
        load_lock_file = _bound_source_roots.load_lock_file
        prepare_seed_repository_closure = _bound_source_roots.prepare_seed_repository_closure
        materialize_dependency_roots = _bound_source_roots.materialize_dependency_roots

_prepare_seed_repository_closure_unlocked = (
    getattr(
        _bound_source_roots,
        "_prepare_seed_repository_closure_unlocked",
        _unbound_dependency_root_helper,
    )
    if _bound_source_roots is not None
    else _unbound_dependency_root_helper
)
_materialize_dependency_roots_unlocked = (
    getattr(
        _bound_source_roots,
        "_materialize_dependency_roots_unlocked",
        _unbound_dependency_root_helper,
    )
    if _bound_source_roots is not None
    else _unbound_dependency_root_helper
)


CMAKE_DEPENDENCY_BUILD_ORDER: tuple[CMakeDependencyBuildSpec, ...] = ()

CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME = {
    build_spec.dependency_name: build_spec for build_spec in CMAKE_DEPENDENCY_BUILD_ORDER
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Manage {REPO_DISPLAY_NAME} dependency-root workflow state."
    )
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
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)  # nosec


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
    return nested_dependency_lock_template_path(dependency_root)


def _nested_dependency_lock_file(dependency_root: Path) -> Path:
    return nested_dependency_lock_file_path(dependency_root)


def _has_nested_dependency_workflow(dependency_root: Path) -> bool:
    return (
        _nested_dependency_workflow_script(dependency_root).is_file()
        and _nested_dependency_lock_template(dependency_root).is_file()
    )


def _write_nested_manual_dependency_lock(
    dependency_root: Path,
    dependency_roots: Any,
) -> None:
    def dependency_root_for(dependency_name: str) -> Path:
        try:
            return Path(dependency_roots.dependency_root_for(dependency_name))
        except KeyError as exc:
            raise WorkflowError(
                f"Nested dependency-root workflow requires unsupported dependency {dependency_name!r}: "
                f"{dependency_root}"
            ) from exc

    write_nested_manual_dependency_lock(dependency_root, dependency_root_for)


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
    _remove_path(path)


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
    atomic_write_json(target_path, model)


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


def _facade_dependency_builder() -> CMakeDependencyBuilder:
    config = CMakeDependencyBuilderConfig(
        build_order=tuple(CMAKE_DEPENDENCY_BUILD_ORDER),
        state_filename=DEPENDENCY_STATE_FILENAME,
    )
    services = CMakeDependencyBuilderServices(
        require_dependency_roots=require_dependency_roots,
        workspace_mutation_lock=workspace_mutation_lock,
        run_command=run_command,
        remove_path=remove_path,
        is_managed_dependency_root=_is_managed_dependency_root,
        has_nested_dependency_workflow=_has_nested_dependency_workflow,
        package_repo_root=_PACKAGE_REPO_ROOT,
        write_json=atomic_write_json,
        write_text=atomic_write_text,
        configure_dependency_for_context=globals().get("configure_dependency_for_context"),
        write_dependency_receipts=globals().get("_write_dependency_receipts"),
    )
    return CMakeDependencyBuilder(config, services)


def dependency_state_file_path(repo_root: Path, preset_name: str) -> Path:
    return _facade_dependency_builder().state_file_path(repo_root, preset_name)


def ordered_dependency_build_specs(dependency_roots: Any) -> list[CMakeDependencyBuildSpec]:
    return _facade_dependency_builder().ordered_specs(dependency_roots)


def dependency_build_state(
    context: CMakeDependencyBuildContext,
    dependency_roots: Any,
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    return _facade_dependency_builder().build_state(
        context,
        dependency_roots,
        repo_root=repo_root,
    )


def _dependency_build_inputs(
    context: CMakeDependencyBuildContext,
    dependency_roots: Any,
    build_spec: CMakeDependencyBuildSpec,
    dependency_states: dict[str, dict[str, Any]],
    *,
    repo_root: Path,
) -> dict[str, Any]:
    return _facade_dependency_builder()._dependency_build_inputs(
        context,
        dependency_roots,
        build_spec,
        dependency_states,
        repo_root=repo_root,
    )


def _load_dependency_build_state(path: Path) -> dict[str, Any] | None:
    return _facade_dependency_builder()._load_build_state(path)


def dependency_rebuild_names(
    repo_root: Path,
    context: CMakeDependencyBuildContext,
    dependency_roots: Any,
) -> set[str]:
    return _facade_dependency_builder().rebuild_names(
        repo_root,
        context,
        dependency_roots,
    )


def _dependency_rebuild_names_from_state(
    repo_root: Path,
    context: CMakeDependencyBuildContext,
    dependency_roots: Any,
    build_specs: Sequence[CMakeDependencyBuildSpec],
    actual_state: dict[str, Any] | None,
    expected_state: dict[str, Any],
) -> set[str]:
    return _facade_dependency_builder()._rebuild_names_from_state(
        repo_root,
        context,
        dependency_roots,
        build_specs,
        actual_state,
        expected_state,
    )


def dependency_state_matches(
    repo_root: Path,
    context: CMakeDependencyBuildContext,
    dependency_roots: Any,
) -> bool:
    return _facade_dependency_builder().state_matches(
        repo_root,
        context,
        dependency_roots,
    )


def write_dependency_state_file(
    repo_root: Path,
    context: CMakeDependencyBuildContext,
    dependency_roots: Any,
) -> None:
    _facade_dependency_builder().write_state_file(
        repo_root,
        context,
        dependency_roots,
    )


def _write_dependency_receipts(
    state_path: Path,
    *,
    mode: str,
    dependencies: dict[str, Any],
) -> None:
    _facade_dependency_builder().write_dependency_receipts(
        state_path,
        mode=mode,
        dependencies=dependencies,
    )


def ensure_dependency_root_active_lock(
    dependency_root: Path,
    available_dependency_roots: dict[str, Path],
) -> None:
    _facade_dependency_builder().ensure_dependency_root_active_lock(
        dependency_root,
        available_dependency_roots,
    )


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
    _facade_dependency_builder().configure_dependency_for_context(
        repo_root=repo_root,
        context=context,
        dependency_name=dependency_name,
        dependency_root=dependency_root,
        install_prefix=install_prefix,
        dependency_prefixes=dependency_prefixes,
        cmake_options=cmake_options,
        available_dependency_roots=available_dependency_roots,
    )


def dependency_source_dir(dependency_root: Path, dependency_name: str) -> Path:
    return _facade_dependency_builder().dependency_source_dir(
        dependency_root,
        dependency_name,
    )


def _dependency_uses_c_language(dependency_name: str) -> bool:
    return _facade_dependency_builder().dependency_uses_c_language(dependency_name)


def _dependency_uses_cxx_language(dependency_name: str) -> bool:
    return _facade_dependency_builder().dependency_uses_cxx_language(dependency_name)


def build_dependencies_for_cmake_context(
    context: CMakeDependencyBuildContext,
    *,
    repo_root: Path | None = None,
) -> None:
    resolved_root = (repo_root or REPO_ROOT).resolve()
    with workspace_mutation_lock(resolved_root):
        _build_dependencies_for_cmake_context_unlocked(
            context,
            repo_root=resolved_root,
        )


def _build_dependencies_for_cmake_context_unlocked(
    context: CMakeDependencyBuildContext,
    *,
    repo_root: Path,
) -> None:
    _facade_dependency_builder().build_dependencies_unlocked(
        context,
        repo_root=repo_root,
    )


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
    _facade_dependency_builder().configure_dependency(
        repo_root=repo_root,
        preset_model=preset_model,
        preset_name=preset_name,
        dependency_name=dependency_name,
        dependency_root=dependency_root,
        install_prefix=install_prefix,
        dependency_prefixes=dependency_prefixes,
        cmake_options=cmake_options,
    )


def cmd_init(*, quiet: bool = False) -> int:
    with workspace_mutation_lock(REPO_ROOT):
        return _cmd_init_unlocked(quiet=quiet)


def _cmd_init_unlocked(*, quiet: bool = False) -> int:
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
    closure = _prepare_seed_repository_closure_for_command(
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
    with workspace_mutation_lock(REPO_ROOT):
        return _cmd_update_unlocked()


def _cmd_update_unlocked() -> int:
    print_cli_status("update", f"repo={REPO_ROOT}")
    print_cli_status(
        "update", "materializing dependency roots from the active lock; network is disabled"
    )
    before_lock_data = load_lock_file(repo_root=REPO_ROOT)
    dependency_roots = _materialize_dependency_roots_for_command(allow_network=False)
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


def _prepare_seed_repository_closure_for_command(
    *,
    progress: Any,
    quiet: bool,
) -> Any:
    if _prepare_seed_repository_closure_unlocked is not _unbound_dependency_root_helper:
        return _prepare_seed_repository_closure_unlocked(
            REPO_ROOT,
            progress=progress,
            quiet=quiet,
        )
    return prepare_seed_repository_closure(
        repo_root=REPO_ROOT,
        progress=progress,
        quiet=quiet,
    )


def _materialize_dependency_roots_for_command(
    *,
    allow_network: bool,
) -> Any:
    if _materialize_dependency_roots_unlocked is not _unbound_dependency_root_helper:
        return _materialize_dependency_roots_unlocked(
            REPO_ROOT,
            allow_network=allow_network,
        )
    return materialize_dependency_roots(repo_root=REPO_ROOT, allow_network=allow_network)


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
    except (
        FileNotFoundError,
        ValueError,
        RuntimeError,
        WorkflowError,
        subprocess.CalledProcessError,
    ) as exc:
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
    "dependency_rebuild_names",
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

_ORIGINAL_SCRIPT_FUNCTIONS = {name: globals()[name] for name in _SCRIPT_FUNCTION_NAMES}


def _dependency_build_spec_map(
    dependency_build_order: Sequence[CMakeDependencyBuildSpec],
) -> dict[str, CMakeDependencyBuildSpec]:
    return {build_spec.dependency_name: build_spec for build_spec in dependency_build_order}


def _bind_cmake_workflow_state(
    module_globals: MutableMapping[str, Any],
    *,
    repo_root: Path,
    repo_display_name: str,
    dependency_build_order: Sequence[CMakeDependencyBuildSpec],
    dependency_state_filename: str | None,
) -> None:
    module_globals.update(
        {
            "REPO_ROOT": repo_root.resolve(),
            "REPO_DISPLAY_NAME": repo_display_name,
            "CMAKE_DEPENDENCY_BUILD_ORDER": tuple(dependency_build_order),
            "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME": _dependency_build_spec_map(
                dependency_build_order
            ),
            "DEPENDENCY_STATE_FILENAME": (
                dependency_state_filename or f".{repo_display_name.lower()}_dependency_state.json"
            ),
        }
    )


def _export_cmake_workflow_constants(module_globals: MutableMapping[str, Any]) -> None:
    for name in _SCRIPT_CONSTANT_NAMES:
        module_globals[name] = globals()[name]


def _sync_cmake_workflow_globals(
    module_globals: MutableMapping[str, Any],
    wrappers: dict[str, Any],
) -> None:
    globals()["REPO_ROOT"] = module_globals["REPO_ROOT"]
    globals()["REPO_DISPLAY_NAME"] = module_globals["REPO_DISPLAY_NAME"]
    globals()["CMAKE_DEPENDENCY_BUILD_ORDER"] = module_globals["CMAKE_DEPENDENCY_BUILD_ORDER"]
    globals()["CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME"] = module_globals[
        "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME"
    ]
    globals()["DEPENDENCY_STATE_FILENAME"] = module_globals["DEPENDENCY_STATE_FILENAME"]

    for helper_name in _DEPENDENCY_ROOT_HELPER_NAMES:
        if helper_name in module_globals:
            globals()[helper_name] = module_globals[helper_name]
    for helper_name in _OPTIONAL_DEPENDENCY_ROOT_HELPER_NAMES:
        globals()[helper_name] = module_globals.get(helper_name, _unbound_dependency_root_helper)

    for function_name, original_function in _ORIGINAL_SCRIPT_FUNCTIONS.items():
        current_value = module_globals.get(function_name)
        if current_value is not None and current_value is not wrappers.get(function_name):
            globals()[function_name] = current_value
        else:
            globals()[function_name] = original_function


def _make_cmake_workflow_wrapper(
    function_name: str,
    module_globals: MutableMapping[str, Any],
    wrappers: dict[str, Any],
) -> Any:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        _sync_cmake_workflow_globals(module_globals, wrappers)
        return globals()[function_name](*args, **kwargs)

    wrapper.__name__ = function_name
    wrapper.__qualname__ = function_name
    wrapper.__doc__ = _ORIGINAL_SCRIPT_FUNCTIONS[function_name].__doc__
    return wrapper


def bind_cmake_workflow_script(
    module_globals: MutableMapping[str, Any],
    *,
    repo_root: Path,
    repo_display_name: str,
    dependency_build_order: Sequence[CMakeDependencyBuildSpec],
    dependency_state_filename: str | None = None,
) -> None:
    wrappers: dict[str, Any] = {}

    _bind_cmake_workflow_state(
        module_globals,
        repo_root=repo_root,
        repo_display_name=repo_display_name,
        dependency_build_order=dependency_build_order,
        dependency_state_filename=dependency_state_filename,
    )
    _export_cmake_workflow_constants(module_globals)

    def sync_shared_globals() -> None:
        _sync_cmake_workflow_globals(module_globals, wrappers)

    for name in _SCRIPT_FUNCTION_NAMES:
        wrappers[name] = _make_cmake_workflow_wrapper(name, module_globals, wrappers)
        module_globals[name] = wrappers[name]

    module_globals["_sync_shared_cmake_workflow_script"] = sync_shared_globals
