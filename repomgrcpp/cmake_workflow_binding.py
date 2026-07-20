from __future__ import annotations

import argparse
import copy
import os
import platform
import subprocess  # nosec B404
import sys
import traceback
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from freecm.asset_seeds import prepare_asset_seeds, require_asset_seeds
from freecm.atomic_write import atomic_write_json, atomic_write_text
from freecm.dependency_roots import dependency_commit_changes
from freecm.git_repositories import remove_path
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
from repomgrcpp import cmake_dependency_builder as _builder_module
from repomgrcpp import cmake_preset_context as _preset_context
from repomgrcpp import preset_templates as _preset_templates
from repomgrcpp.cmake_dependency_builder import (
    CMakeDependencyBuilder,
    CMakeDependencyBuilderConfig,
    CMakeDependencyBuilderServices,
    CMakeDependencyBuildSpec,
)
from repomgrcpp.cmake_preset_context import (
    CMakeDependencyBuildContext,
    load_cmake_dependency_build_context,
)
from repomgrcpp.errors import WorkflowError
from repomgrcpp.preset_templates import resolve_preset_models


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
    try:
        dependency_root.resolve().relative_to(_managed_dependency_root_parent(repo_root))
    except ValueError:
        return False
    return True


def _nested_dependency_workflow_script(dependency_root: Path) -> Path:
    return dependency_root / "configs" / "source_root_workflow.py"


def _nested_dependency_lock_template(dependency_root: Path) -> Path:
    return dependency_root / "source_roots.lock.jsonc.in"


def _has_nested_dependency_workflow(dependency_root: Path) -> bool:
    return _nested_dependency_workflow_script(dependency_root).is_file() and (
        _nested_dependency_lock_template(dependency_root).is_file()
    )


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
    atomic_write_json(repo_root / "CMakePresets.json", model)


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
    atomic_write_text(target_path, shared_clangd_template_path().read_text(encoding="utf-8"))
    return target_path.resolve(), True


def print_cli_status(action: str, message: str, *, level: str = "info") -> None:
    print(format_status_line(action, message, level=level, use_color=stdout_supports_color()))


def _command_display(cmd: Sequence[str]) -> str:
    return " ".join(str(part) for part in cmd)


def format_called_process_error(error: subprocess.CalledProcessError) -> str:
    command = error.cmd if isinstance(error.cmd, (list, tuple)) else [str(error.cmd)]
    lines = [
        f"Command failed with exit code {error.returncode}:",
        f"  {_command_display(command)}",
    ]
    if getattr(error, "stdout", None):
        lines.extend(("stdout:", str(error.stdout).strip()))
    if getattr(error, "stderr", None):
        lines.extend(("stderr:", str(error.stderr).strip()))
    return "\n".join(lines)


def format_cli_exception(error: BaseException, *, unexpected: bool = False) -> str:
    message = (
        format_called_process_error(error)
        if isinstance(error, subprocess.CalledProcessError)
        else (str(error).strip() or error.__class__.__name__)
    )
    if unexpected:
        return (
            f"Unexpected Python error ({error.__class__.__name__}): {message}\n"
            "Set FREECM_DEBUG=1 and rerun to print a traceback."
        )
    return message


def print_cli_error(error: BaseException, *, unexpected: bool = False) -> None:
    print(
        format_status_line(
            "error",
            format_cli_exception(error, unexpected=unexpected),
            level="error",
            use_color=stderr_supports_color(),
        ),
        file=sys.stderr,
    )
    if unexpected and os.environ.get("FREECM_DEBUG"):
        traceback.print_exception(type(error), error, error.__traceback__)


def _unbound_dependency_root_helper(*_: Any, **__: Any) -> Any:
    raise WorkflowError(
        "CMake dependency-root commands are unbound; run the configured "
        "configs/source_root_workflow.py entry point."
    )


@runtime_checkable
class DependencyRootWorkflowProtocol(Protocol):
    def ensure_active_lock_file(self, repo_root: Path | None = None) -> tuple[Path, bool]: ...

    def load_lock_file(self, repo_root: Path | None = None) -> dict[str, Any]: ...

    def require_dependency_roots(self, repo_root: Path | None = None) -> Any: ...

    def describe_dependency_roots(self, dependency_roots: Any) -> Sequence[Any]: ...

    def prepare_nested_dependency_workflows(
        self, dependency_roots: Any, *, repo_root: Path | None = None
    ) -> None: ...

    def _prepare_seed_repository_closure_unlocked(self, repo_root: Path, **kwargs: Any) -> Any: ...

    def _materialize_dependency_roots_unlocked(self, repo_root: Path, **kwargs: Any) -> Any: ...


def _captured_callable(
    namespace: Mapping[str, Any],
    manager: Any,
    name: str,
    *,
    required: bool = True,
) -> Callable[..., Any]:
    value = namespace.get(name)
    if not callable(value) and manager is not None:
        value = getattr(manager, name, None)
    if callable(value):
        return cast(Callable[..., Any], value)
    if required:
        raise WorkflowError(
            f"Cannot bind CMake workflow: dependency-root helper {name!r} is unavailable. "
            "Import configs.source_roots helpers before bind_cmake_workflow_script(...)."
        )
    return _unbound_dependency_root_helper


@dataclass(frozen=True)
class DependencyRootWorkflowBindings:
    summary_type: type[Any]
    ensure_active_lock_file: Callable[..., tuple[Path, bool]]
    load_lock_file: Callable[..., dict[str, Any]]
    require_dependency_roots: Callable[..., Any]
    describe_dependency_roots: Callable[..., Sequence[Any]]
    prepare_nested_dependency_workflows: Callable[..., None]
    prepare_seed_repository_closure: Callable[..., Any]
    materialize_dependency_roots: Callable[..., Any]
    prepare_seed_repository_closure_unlocked: Callable[..., Any]
    materialize_dependency_roots_unlocked: Callable[..., Any]

    @classmethod
    def from_namespace(
        cls, namespace: Mapping[str, Any], *, allow_unbound: bool = False
    ) -> DependencyRootWorkflowBindings:
        manager = namespace.get("workflow")
        if manager is None:
            manager = namespace.get("_WORKFLOW")
        required = not allow_unbound
        summary_type = namespace.get("DependencyRootSummary", object)
        return cls(
            summary_type=summary_type if isinstance(summary_type, type) else object,
            ensure_active_lock_file=_captured_callable(
                namespace, manager, "ensure_active_lock_file", required=required
            ),
            load_lock_file=_captured_callable(
                namespace, manager, "load_lock_file", required=required
            ),
            require_dependency_roots=_captured_callable(
                namespace, manager, "require_dependency_roots", required=required
            ),
            describe_dependency_roots=_captured_callable(
                namespace, manager, "describe_dependency_roots", required=required
            ),
            prepare_nested_dependency_workflows=_captured_callable(
                namespace, manager, "prepare_nested_dependency_workflows", required=required
            ),
            prepare_seed_repository_closure=_captured_callable(
                namespace, manager, "prepare_seed_repository_closure", required=required
            ),
            materialize_dependency_roots=_captured_callable(
                namespace, manager, "materialize_dependency_roots", required=required
            ),
            prepare_seed_repository_closure_unlocked=_captured_callable(
                namespace,
                manager,
                "_prepare_seed_repository_closure_unlocked",
                required=required,
            ),
            materialize_dependency_roots_unlocked=_captured_callable(
                namespace,
                manager,
                "_materialize_dependency_roots_unlocked",
                required=required,
            ),
        )


@dataclass(frozen=True)
class CMakeWorkflowServices:
    workspace_mutation_lock: Callable[[Path], AbstractContextManager[Any]]
    prepare_asset_seeds: Callable[[Path], Sequence[Any]]
    require_asset_seeds: Callable[[Path], Sequence[Any]]
    ensure_clangd_config: Callable[[Path], tuple[Path, bool]]
    host_os_group: Callable[[], str]
    resolve_preset_models: Callable[..., Any]
    write_generated_cmake_presets: Callable[[Path, dict[str, Any]], None]
    print_cli_status: Callable[..., None]
    print_cli_error: Callable[..., None]
    stdout_supports_color: Callable[[], bool]
    run_command: Callable[..., None]
    remove_path: Callable[[Path], None]
    write_json: Callable[[Path, Any], None]
    write_text: Callable[[Path, str], None]
    package_repo_root: Path

    @classmethod
    def from_namespace(cls, namespace: Mapping[str, Any]) -> CMakeWorkflowServices:
        defaults: dict[str, Any] = {
            "workspace_mutation_lock": workspace_mutation_lock,
            "prepare_asset_seeds": prepare_asset_seeds,
            "require_asset_seeds": require_asset_seeds,
            "ensure_clangd_config": ensure_clangd_config,
            "host_os_group": host_os_group,
            "resolve_preset_models": resolve_preset_models,
            "write_generated_cmake_presets": write_generated_cmake_presets,
            "print_cli_status": print_cli_status,
            "print_cli_error": print_cli_error,
            "stdout_supports_color": stdout_supports_color,
            "run_command": run_command,
            "remove_path": remove_path,
            "write_json": atomic_write_json,
            "write_text": atomic_write_text,
        }
        captured = {
            name: (
                namespace.get(name, default) if callable(namespace.get(name, default)) else default
            )
            for name, default in defaults.items()
        }
        return cls(
            **captured,
            package_repo_root=Path(__file__).resolve().parent.parent,
        )


@dataclass(frozen=True)
class CMakeWorkflowContext:
    repo_root: Path
    repo_display_name: str
    dependency_roots: DependencyRootWorkflowBindings
    builder: CMakeDependencyBuilder


class CMakeWorkflowScript:
    def __init__(self, context: CMakeWorkflowContext, services: CMakeWorkflowServices) -> None:
        self.context = context
        self.services = services

    @property
    def repo_root(self) -> Path:
        return self.context.repo_root

    def parse_args(self) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            description=f"Manage {self.context.repo_display_name} dependency-root workflow state."
        )
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--init", action="store_true")
        group.add_argument("--update", action="store_true")
        group.add_argument(
            "--build-dependencies-from-cmake", metavar="CONTEXT_JSON", help=argparse.SUPPRESS
        )
        parser.add_argument("--quiet", action="store_true")
        return parser.parse_args()

    def dependency_state_file_path(self, repo_root: Path, preset_name: str) -> Path:
        return self.context.builder.state_file_path(repo_root, preset_name)

    def ordered_dependency_build_specs(
        self, dependency_roots: Any
    ) -> list[CMakeDependencyBuildSpec]:
        return self.context.builder.ordered_specs(dependency_roots)

    def dependency_build_state(
        self,
        context: CMakeDependencyBuildContext,
        dependency_roots: Any,
        *,
        repo_root: Path | None = None,
    ) -> dict[str, Any]:
        return self.context.builder.build_state(context, dependency_roots, repo_root=repo_root)

    def _dependency_build_inputs(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.context.builder._dependency_build_inputs(*args, **kwargs)

    def _load_dependency_build_state(self, path: Path) -> dict[str, Any] | None:
        return self.context.builder._load_build_state(path)

    def dependency_rebuild_names(self, *args: Any, **kwargs: Any) -> set[str]:
        return self.context.builder.rebuild_names(*args, **kwargs)

    def _dependency_rebuild_names_from_state(self, *args: Any, **kwargs: Any) -> set[str]:
        return self.context.builder._rebuild_names_from_state(*args, **kwargs)

    def dependency_state_matches(self, *args: Any, **kwargs: Any) -> bool:
        return self.context.builder.state_matches(*args, **kwargs)

    def write_dependency_state_file(self, *args: Any, **kwargs: Any) -> None:
        self.context.builder.write_state_file(*args, **kwargs)

    def _write_dependency_receipts(self, *args: Any, **kwargs: Any) -> None:
        self.context.builder.write_dependency_receipts(*args, **kwargs)

    def ensure_dependency_root_active_lock(self, *args: Any, **kwargs: Any) -> None:
        self.context.builder.ensure_dependency_root_active_lock(*args, **kwargs)

    def configure_dependency_for_context(self, **kwargs: Any) -> None:
        self.context.builder.configure_dependency_for_context(**kwargs)

    def dependency_source_dir(self, dependency_root: Path, dependency_name: str) -> Path:
        return self.context.builder.dependency_source_dir(dependency_root, dependency_name)

    def _dependency_uses_c_language(self, dependency_name: str) -> bool:
        return self.context.builder.dependency_uses_c_language(dependency_name)

    def _dependency_uses_cxx_language(self, dependency_name: str) -> bool:
        return self.context.builder.dependency_uses_cxx_language(dependency_name)

    def build_dependencies_for_cmake_context(
        self, context: CMakeDependencyBuildContext, *, repo_root: Path | None = None
    ) -> None:
        root = (repo_root or self.repo_root).resolve()
        with self.services.workspace_mutation_lock(root):
            self._build_dependencies_for_cmake_context_unlocked(context, repo_root=root)

    def _build_dependencies_for_cmake_context_unlocked(
        self, context: CMakeDependencyBuildContext, *, repo_root: Path
    ) -> None:
        self.context.builder.build_dependencies_unlocked(context, repo_root=repo_root)

    def configure_dependency(self, **kwargs: Any) -> None:
        self.context.builder.configure_dependency(**kwargs)

    def prepare_nested_dependency_workflows(
        self, dependency_roots: Any, *, repo_root: Path | None = None
    ) -> None:
        self.context.dependency_roots.prepare_nested_dependency_workflows(
            dependency_roots,
            repo_root=(repo_root or self.repo_root).resolve(),
        )

    def cmd_init(self, *, quiet: bool = False) -> int:
        with self.services.workspace_mutation_lock(self.repo_root):
            return self._cmd_init_unlocked(quiet=quiet)

    def _cmd_init_unlocked(self, *, quiet: bool = False) -> int:
        status = self.services.print_cli_status
        status("init", f"repo={self.repo_root}")
        lock_path, created = self.context.dependency_roots.ensure_active_lock_file(
            repo_root=self.repo_root
        )
        status(
            "init",
            f"{'created' if created else 'using'} active dependency lock: {lock_path}",
            level="ok" if created else "info",
        )
        clangd_path, clangd_created = self.services.ensure_clangd_config(self.repo_root)
        status(
            "init",
            f"{'created' if clangd_created else 'using'} clangd config: {clangd_path}",
            level="ok" if clangd_created else "info",
        )
        status("init", "checking dependency seed repositories; network is allowed")
        closure = self._prepare_seed_repository_closure_for_command(
            progress=lambda action, message, level: status(action, message, level=level),
            quiet=quiet,
        )
        status(
            "init", f"prepared {len(closure.topo_order)} dependency seed repositories", level="ok"
        )
        for summary in self.services.prepare_asset_seeds(self.repo_root):
            status(
                "asset",
                f"{summary.asset_name}: prepared {len(summary.files)} files -> {summary.seed_root}",
                level="ok",
            )
        return 0

    def cmd_update(self) -> int:
        with self.services.workspace_mutation_lock(self.repo_root):
            return self._cmd_update_unlocked()

    def _cmd_update_unlocked(self) -> int:
        status = self.services.print_cli_status
        status("update", f"repo={self.repo_root}")
        status("update", "materializing dependency roots from the active lock; network is disabled")
        roots_api = self.context.dependency_roots
        before_lock_data = roots_api.load_lock_file(repo_root=self.repo_root)
        dependency_roots = self._materialize_dependency_roots_for_command()
        status(
            "update",
            f"materialized {len(dependency_roots.closure_order)} dependency roots",
            level="ok",
        )
        for summary in self.services.require_asset_seeds(self.repo_root):
            status(
                "asset",
                f"{summary.asset_name}: verified {len(summary.files)} files -> {summary.seed_root}",
                level="ok",
            )
        use_color = self.services.stdout_supports_color()
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
            roots_api.describe_dependency_roots(dependency_roots), use_color=use_color
        ):
            print(line)
        os_group = self.services.host_os_group()
        status("update", f"resolving {os_group} CMake preset template")
        preset_lock_data = copy.deepcopy(dependency_roots.lock_data)
        cmake_environment = preset_lock_data.setdefault("cmakeEnvironment", {})
        if not isinstance(cmake_environment, dict):
            raise WorkflowError("Invalid cmakeEnvironment map in dependency lock")
        cmake_environment.update(dependency_roots.as_environment_map())
        resolved_presets = self.services.resolve_preset_models(
            self.repo_root, preset_lock_data, os_group, dependency_roots.closure_order
        )
        status("update", "preparing nested dependency workflows")
        roots_api.prepare_nested_dependency_workflows(dependency_roots, repo_root=self.repo_root)
        self.services.write_generated_cmake_presets(
            self.repo_root, resolved_presets.generated_model
        )
        status("update", f"wrote {self.repo_root / 'CMakePresets.json'}", level="ok")
        return 0

    def _prepare_seed_repository_closure_for_command(self, *, progress: Any, quiet: bool) -> Any:
        return self.context.dependency_roots.prepare_seed_repository_closure_unlocked(
            self.repo_root, progress=progress, quiet=quiet
        )

    def _materialize_dependency_roots_for_command(self) -> Any:
        return self.context.dependency_roots.materialize_dependency_roots_unlocked(
            self.repo_root, allow_network=False
        )

    def cmd_build_dependencies_from_cmake(self, context_json_path: Path) -> int:
        context = load_cmake_dependency_build_context(context_json_path)
        self.build_dependencies_for_cmake_context(context, repo_root=self.repo_root)
        return 0

    def main(self) -> int:
        args = self.parse_args()
        try:
            if args.init:
                return self.cmd_init(quiet=getattr(args, "quiet", False))
            if args.build_dependencies_from_cmake:
                return self.cmd_build_dependencies_from_cmake(
                    Path(args.build_dependencies_from_cmake).resolve()
                )
            return self.cmd_update()
        except (
            FileNotFoundError,
            ValueError,
            RuntimeError,
            WorkflowError,
            subprocess.CalledProcessError,
        ) as exc:
            self.services.print_cli_error(exc)
            return 1
        except Exception as exc:
            self.services.print_cli_error(exc, unexpected=True)
            return 1


_BOUND_METHOD_NAMES = (
    "parse_args",
    "prepare_nested_dependency_workflows",
    "dependency_state_file_path",
    "ordered_dependency_build_specs",
    "dependency_build_state",
    "_dependency_build_inputs",
    "_load_dependency_build_state",
    "dependency_rebuild_names",
    "_dependency_rebuild_names_from_state",
    "dependency_state_matches",
    "write_dependency_state_file",
    "_write_dependency_receipts",
    "ensure_dependency_root_active_lock",
    "configure_dependency_for_context",
    "dependency_source_dir",
    "_dependency_uses_c_language",
    "_dependency_uses_cxx_language",
    "build_dependencies_for_cmake_context",
    "_build_dependencies_for_cmake_context_unlocked",
    "configure_dependency",
    "cmd_init",
    "_cmd_init_unlocked",
    "cmd_update",
    "_cmd_update_unlocked",
    "_prepare_seed_repository_closure_for_command",
    "_materialize_dependency_roots_for_command",
    "cmd_build_dependencies_from_cmake",
    "main",
)

_CONSTANT_EXPORTS: dict[str, Any] = {
    "WorkflowError": WorkflowError,
    "CMakeDependencyBuildSpec": CMakeDependencyBuildSpec,
    "CMakeDependencyBuildContext": CMakeDependencyBuildContext,
    "ResolvedPresetModel": _preset_templates.ResolvedPresetModel,
    "HOST_TEMPLATE_FILENAMES": _preset_templates.HOST_TEMPLATE_FILENAMES,
    "ANSI_RESET": ANSI_RESET,
    "ANSI_BOLD": ANSI_BOLD,
    "ANSI_DIM": ANSI_DIM,
    "ANSI_RED": ANSI_RED,
    "ANSI_YELLOW": ANSI_YELLOW,
    "ANSI_GREEN": ANSI_GREEN,
    "ANSI_BLUE": ANSI_BLUE,
    "ANSI_CYAN": ANSI_CYAN,
    "MODE_LABELS": MODE_LABELS,
    "MODE_COLORS": MODE_COLORS,
}

_PURE_EXPORTS: dict[str, Any] = {
    "run_command": run_command,
    "stderr_supports_color": stderr_supports_color,
    "stdout_supports_color": stdout_supports_color,
    "_style": _style,
    "format_dependency_resolution_lines": format_dependency_resolution_lines,
    "format_status_line": format_status_line,
    "host_os_group": host_os_group,
    "write_generated_cmake_presets": write_generated_cmake_presets,
    "shared_clangd_template_path": shared_clangd_template_path,
    "ensure_clangd_config": ensure_clangd_config,
    "print_cli_status": print_cli_status,
    "format_called_process_error": format_called_process_error,
    "format_cli_exception": format_cli_exception,
    "print_cli_error": print_cli_error,
}
for _module, _names in (
    (
        _preset_context,
        (
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
        ),
    ),
    (
        _preset_templates,
        (
            "host_template_path",
            "load_json_file",
            "collect_template_tokens",
            "managed_prefix_entries",
            "inject_managed_prefixes",
            "resolve_preset_models",
            "resolve_preset_model",
        ),
    ),
    (
        _builder_module,
        (
            "_dependency_inputs_fingerprint",
            "_dependency_parent_names",
            "_dependency_source_dir_for_spec",
            "_dependency_transitive_names",
            "_dependency_uses_manual_override",
            "_effective_dependency_cache_variables",
            "_is_c_language_only_cache_key",
            "_is_cxx_language_only_cache_key",
            "_json_compatible_build_spec",
        ),
    ),
):
    for _name in _names:
        _PURE_EXPORTS[_name] = getattr(_module, _name)


def bind_cmake_workflow_script(
    module_globals: MutableMapping[str, Any],
    *,
    repo_root: Path,
    repo_display_name: str,
    dependency_build_order: Sequence[CMakeDependencyBuildSpec],
    dependency_state_filename: str | None = None,
) -> CMakeWorkflowScript:
    dependency_roots = DependencyRootWorkflowBindings.from_namespace(module_globals)
    services = CMakeWorkflowServices.from_namespace(module_globals)
    config = CMakeDependencyBuilderConfig(
        build_order=tuple(dependency_build_order),
        state_filename=dependency_state_filename
        or f".{repo_display_name.lower()}_dependency_state.json",
    )
    builder = CMakeDependencyBuilder(
        config,
        CMakeDependencyBuilderServices(
            require_dependency_roots=dependency_roots.require_dependency_roots,
            workspace_mutation_lock=services.workspace_mutation_lock,
            run_command=services.run_command,
            remove_path=services.remove_path,
            is_managed_dependency_root=_is_managed_dependency_root,
            has_nested_dependency_workflow=_has_nested_dependency_workflow,
            package_repo_root=services.package_repo_root,
            write_json=services.write_json,
            write_text=services.write_text,
        ),
    )
    context = CMakeWorkflowContext(
        repo_root=repo_root.resolve(),
        repo_display_name=repo_display_name,
        dependency_roots=dependency_roots,
        builder=builder,
    )
    script = CMakeWorkflowScript(context, services)
    module_globals.update(
        {
            "REPO_ROOT": context.repo_root,
            "REPO_DISPLAY_NAME": repo_display_name,
            "CMAKE_DEPENDENCY_BUILD_ORDER": config.build_order,
            "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME": dict(builder.spec_by_name),
            "DEPENDENCY_STATE_FILENAME": config.state_filename,
            "_CMAKE_WORKFLOW_SCRIPT": script,
            "DependencyRootWorkflowProtocol": DependencyRootWorkflowProtocol,
            "DependencyRootWorkflowBindings": DependencyRootWorkflowBindings,
            "CMakeWorkflowServices": CMakeWorkflowServices,
            "CMakeWorkflowContext": CMakeWorkflowContext,
            "CMakeWorkflowScript": CMakeWorkflowScript,
        }
    )
    module_globals.update(_CONSTANT_EXPORTS)
    for name, value in _PURE_EXPORTS.items():
        module_globals.setdefault(name, value)
    for name in _BOUND_METHOD_NAMES:
        module_globals[name] = getattr(script, name)
    return script


def create_unbound_cmake_workflow_script(repo_root: Path) -> CMakeWorkflowScript:
    namespace: dict[str, Any] = {}
    dependency_roots = DependencyRootWorkflowBindings.from_namespace(namespace, allow_unbound=True)
    services = CMakeWorkflowServices.from_namespace(namespace)
    config = CMakeDependencyBuilderConfig(
        build_order=(), state_filename=".dependency_root_state.json"
    )
    builder = CMakeDependencyBuilder(
        config,
        CMakeDependencyBuilderServices(
            require_dependency_roots=dependency_roots.require_dependency_roots,
            workspace_mutation_lock=services.workspace_mutation_lock,
            run_command=services.run_command,
            remove_path=services.remove_path,
            is_managed_dependency_root=_is_managed_dependency_root,
            has_nested_dependency_workflow=_has_nested_dependency_workflow,
            package_repo_root=services.package_repo_root,
            write_json=services.write_json,
            write_text=services.write_text,
        ),
    )
    return CMakeWorkflowScript(
        CMakeWorkflowContext(repo_root.resolve(), "workspace", dependency_roots, builder),
        services,
    )


__all__ = (
    "CMakeWorkflowContext",
    "CMakeWorkflowScript",
    "CMakeWorkflowServices",
    "DependencyRootWorkflowBindings",
    "DependencyRootWorkflowProtocol",
    "bind_cmake_workflow_script",
    "create_unbound_cmake_workflow_script",
    "ensure_clangd_config",
    "format_called_process_error",
    "format_cli_exception",
    "host_os_group",
    "print_cli_error",
    "print_cli_status",
    "run_command",
    "shared_clangd_template_path",
    "write_generated_cmake_presets",
)
