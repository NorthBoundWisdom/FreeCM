#!/usr/bin/env python3
# Usage:
#   python3 /path/to/FreeCM/repomgrcpp/cmake_workflow.py --help
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.cmake_workflow --help
# ruff: noqa: F401

from __future__ import annotations

import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

_PACKAGE_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_REPO_ROOT))

from freecm.atomic_write import atomic_write_json, atomic_write_text
from freecm.dependency_lock import ACTIVE_LOCK_FILE_NAME, TEMPLATE_LOCK_FILE_NAME
from freecm.dependency_models import DependencyRootSummary
from freecm.git_repositories import git_toplevel, remove_path
from freecm.materializer import (
    nested_dependency_lock_file_path,
    nested_dependency_lock_template_path,
    write_nested_manual_dependency_lock,
)
from freecm.terminal_style import (
    ANSI_BLUE,
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_RED,
    ANSI_RESET,
    ANSI_YELLOW,
    MODE_COLORS,
    MODE_LABELS,
    _style,
    format_dependency_commit_change_lines,
    format_dependency_resolution_lines,
    format_status_line,
    stderr_supports_color,
    stdout_supports_color,
)
from freecm.workspace_lock import workspace_mutation_lock
from repomgrcpp.cmake_dependency_builder import (
    DEPENDENCY_BUILD_STATE_SCHEMA_VERSION,
    CMakeDependencyBuilder,
    CMakeDependencyBuilderConfig,
    CMakeDependencyBuilderServices,
    CMakeDependencyBuildSpec,
    _dependency_inputs_fingerprint,
    _dependency_parent_names,
    _dependency_source_dir_for_spec,
    _dependency_transitive_names,
    _dependency_uses_manual_override,
    _effective_dependency_cache_variables,
    _is_c_language_only_cache_key,
    _is_cxx_language_only_cache_key,
    _json_compatible_build_spec,
)
from repomgrcpp.cmake_preset_context import (
    CMakeDependencyBuildContext,
    _normalized_context_build_configurations,
    build_configurations_for_preset,
    build_dir_for_preset,
    build_dir_for_preset_name,
    cmake_executable_for_preset,
    combined_prefix_path,
    configure_presets,
    dependency_build_dir,
    dependency_build_dir_for_name,
    dependency_install_prefix,
    dependency_install_prefix_for_name,
    external_prefix_path,
    find_configure_preset,
    forwarded_cache_args,
    load_cmake_dependency_build_context,
    multi_config_generator,
    preset_environment,
    preset_generator_args,
    resolve_generator,
    resolve_preset_string,
    single_config_build_type,
)
from repomgrcpp.cmake_workflow_binding import (
    CMakeWorkflowContext,
    CMakeWorkflowScript,
    CMakeWorkflowServices,
    DependencyRootWorkflowBindings,
    DependencyRootWorkflowProtocol,
    bind_cmake_workflow_script,
    create_unbound_cmake_workflow_script,
    ensure_clangd_config,
    format_called_process_error,
    format_cli_exception,
    host_os_group,
    print_cli_error,
    print_cli_status,
    run_command,
    shared_clangd_template_path,
    write_generated_cmake_presets,
)
from repomgrcpp.errors import WorkflowError
from repomgrcpp.preset_templates import (
    HOST_TEMPLATE_FILENAMES,
    ResolvedPresetModel,
    collect_template_tokens,
    host_template_path,
    inject_managed_prefixes,
    load_json_file,
    managed_prefix_entries,
    resolve_preset_model,
    resolve_preset_models,
)

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = _PACKAGE_REPO_ROOT
REPO_DISPLAY_NAME = "workspace"
DEPENDENCY_STATE_FILENAME = ".dependency_root_state.json"
CMAKE_DEPENDENCY_BUILD_ORDER: tuple[CMakeDependencyBuildSpec, ...] = ()
CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME: dict[str, CMakeDependencyBuildSpec] = {}
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


def _unbound_dependency_root_helper(*_: Any, **__: Any) -> Any:
    raise WorkflowError(
        "dependency-root workflow helpers have not been bound; use "
        "configs/source_root_workflow.py"
    )


describe_dependency_roots = _unbound_dependency_root_helper
ensure_active_lock_file = _unbound_dependency_root_helper
require_dependency_roots = _unbound_dependency_root_helper
load_lock_file = _unbound_dependency_root_helper
prepare_seed_repository_closure = _unbound_dependency_root_helper
_prepare_seed_repository_closure_unlocked = _unbound_dependency_root_helper
materialize_dependency_roots = _unbound_dependency_root_helper
_materialize_dependency_roots_unlocked = _unbound_dependency_root_helper


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
    return cwd_repo_root if cwd_repo_root is not None else script_repo_root


def _managed_dependency_root_parent(repo_root: Path) -> Path:
    return (repo_root / "build" / "dependency_source_roots").resolve()


def _is_managed_dependency_root(repo_root: Path, dependency_root: Path) -> bool:
    try:
        dependency_root.resolve().relative_to(_managed_dependency_root_parent(repo_root))
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
    return _nested_dependency_workflow_script(dependency_root).is_file() and (
        _nested_dependency_lock_template(dependency_root).is_file()
    )


def _write_nested_manual_dependency_lock(dependency_root: Path, dependency_roots: Any) -> None:
    def dependency_root_for(dependency_name: str) -> Path:
        try:
            return Path(dependency_roots.dependency_root_for(dependency_name))
        except KeyError as exc:
            raise WorkflowError(
                "Nested dependency-root workflow requires unsupported dependency "
                f"{dependency_name!r}: {dependency_root}"
            ) from exc

    write_nested_manual_dependency_lock(dependency_root, dependency_root_for)


def prepare_nested_dependency_workflows(
    dependency_roots: Any, *, repo_root: Path = REPO_ROOT
) -> None:
    for dependency_name in dependency_roots.closure_order:
        dependency_root = dependency_roots.dependency_root_for(dependency_name)
        if _is_managed_dependency_root(repo_root, dependency_root) and (
            _has_nested_dependency_workflow(dependency_root)
        ):
            _write_nested_manual_dependency_lock(dependency_root, dependency_roots)


_DEFAULT_SCRIPT = create_unbound_cmake_workflow_script(REPO_ROOT)


def _facade_dependency_builder() -> CMakeDependencyBuilder:
    return CMakeDependencyBuilder(
        CMakeDependencyBuilderConfig(
            build_order=tuple(CMAKE_DEPENDENCY_BUILD_ORDER),
            state_filename=DEPENDENCY_STATE_FILENAME,
        ),
        CMakeDependencyBuilderServices(
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
        ),
    )


def parse_args():
    return _DEFAULT_SCRIPT.parse_args()


def dependency_state_file_path(repo_root: Path, preset_name: str) -> Path:
    return _facade_dependency_builder().state_file_path(repo_root, preset_name)


def ordered_dependency_build_specs(dependency_roots: Any) -> list[CMakeDependencyBuildSpec]:
    return _facade_dependency_builder().ordered_specs(dependency_roots)


def dependency_build_state(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _facade_dependency_builder().build_state(*args, **kwargs)


def _dependency_build_inputs(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _facade_dependency_builder()._dependency_build_inputs(*args, **kwargs)


def _load_dependency_build_state(path: Path) -> dict[str, Any] | None:
    return _facade_dependency_builder()._load_build_state(path)


def dependency_rebuild_names(*args: Any, **kwargs: Any) -> set[str]:
    return _facade_dependency_builder().rebuild_names(*args, **kwargs)


def _dependency_rebuild_names_from_state(*args: Any, **kwargs: Any) -> set[str]:
    return _facade_dependency_builder()._rebuild_names_from_state(*args, **kwargs)


def dependency_state_matches(*args: Any, **kwargs: Any) -> bool:
    return _facade_dependency_builder().state_matches(*args, **kwargs)


def write_dependency_state_file(*args: Any, **kwargs: Any) -> None:
    _facade_dependency_builder().write_state_file(*args, **kwargs)


def _write_dependency_receipts(*args: Any, **kwargs: Any) -> None:
    _facade_dependency_builder().write_dependency_receipts(*args, **kwargs)


def ensure_dependency_root_active_lock(*args: Any, **kwargs: Any) -> None:
    _facade_dependency_builder().ensure_dependency_root_active_lock(*args, **kwargs)


def configure_dependency_for_context(**kwargs: Any) -> None:
    _facade_dependency_builder().configure_dependency_for_context(**kwargs)


def dependency_source_dir(dependency_root: Path, dependency_name: str) -> Path:
    return _facade_dependency_builder().dependency_source_dir(dependency_root, dependency_name)


def _dependency_uses_c_language(dependency_name: str) -> bool:
    return _facade_dependency_builder().dependency_uses_c_language(dependency_name)


def _dependency_uses_cxx_language(dependency_name: str) -> bool:
    return _facade_dependency_builder().dependency_uses_cxx_language(dependency_name)


def build_dependencies_for_cmake_context(
    context: CMakeDependencyBuildContext, *, repo_root: Path | None = None
) -> None:
    root = (repo_root or REPO_ROOT).resolve()
    with workspace_mutation_lock(root):
        _build_dependencies_for_cmake_context_unlocked(context, repo_root=root)


def _build_dependencies_for_cmake_context_unlocked(
    context: CMakeDependencyBuildContext, *, repo_root: Path
) -> None:
    _facade_dependency_builder().build_dependencies_unlocked(context, repo_root=repo_root)


def configure_dependency(**kwargs: Any) -> None:
    _facade_dependency_builder().configure_dependency(**kwargs)


def cmd_init(*, quiet: bool = False) -> int:
    return _DEFAULT_SCRIPT.cmd_init(quiet=quiet)


def cmd_update() -> int:
    return _DEFAULT_SCRIPT.cmd_update()


def cmd_build_dependencies_from_cmake(context_json_path: Path) -> int:
    return _DEFAULT_SCRIPT.cmd_build_dependencies_from_cmake(context_json_path)


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
