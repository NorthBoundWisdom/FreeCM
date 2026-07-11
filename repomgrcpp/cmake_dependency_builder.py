from __future__ import annotations

import hashlib
import json
import os
from collections import deque
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any

from freecm.dependency_lock import ACTIVE_LOCK_FILE_NAME, TEMPLATE_LOCK_FILE_NAME
from freecm.jsonc import loads_jsonc
from repomgrcpp.cmake_preset_context import (
    CMakeDependencyBuildContext,
    build_configurations_for_preset,
    build_dir_for_preset_name,
    cmake_executable_for_preset,
    combined_prefix_path,
    dependency_build_dir,
    dependency_build_dir_for_name,
    dependency_install_prefix_for_name,
    forwarded_cache_args,
    multi_config_generator,
    preset_environment,
    preset_generator_args,
    resolve_generator,
    single_config_build_type,
)
from repomgrcpp.errors import WorkflowError
from repomgrcpp.preset_templates import load_json_file

DEPENDENCY_BUILD_STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CMakeDependencyBuildSpec:
    dependency_name: str
    uses_c_language: bool
    cmake_options: tuple[str, ...]
    uses_cxx_language: bool = True
    source_subdir: str = ""


@dataclass(frozen=True)
class CMakeDependencyBuilderConfig:
    build_order: tuple[CMakeDependencyBuildSpec, ...]
    state_filename: str


@dataclass(frozen=True)
class CMakeDependencyBuilderServices:
    require_dependency_roots: Callable[..., Any]
    workspace_mutation_lock: Callable[[Path], AbstractContextManager[Any]]
    run_command: Callable[..., None]
    remove_path: Callable[[Path], None]
    is_managed_dependency_root: Callable[[Path, Path], bool]
    has_nested_dependency_workflow: Callable[[Path], bool]
    package_repo_root: Path
    write_json: Callable[[Path, Any], None]
    write_text: Callable[[Path, str], None]
    configure_dependency_for_context: Callable[..., None] | None = None
    write_dependency_receipts: Callable[..., None] | None = None


def _prepend_pythonpath(env: MutableMapping[str, str], path: Path) -> None:
    path_value = str(path.resolve())
    current = env.get("PYTHONPATH", "")
    parts = [part for part in current.split(os.pathsep) if part]
    if path_value in parts:
        return
    env["PYTHONPATH"] = os.pathsep.join([path_value, *parts])


def _json_compatible_build_spec(build_spec: CMakeDependencyBuildSpec) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for build_field in fields(build_spec):
        value = getattr(build_spec, build_field.name)
        result[build_field.name] = list(value) if isinstance(value, tuple) else value
    return result


def _is_c_language_only_cache_key(key: str) -> bool:
    if key in {"CMAKE_C_COMPILER", "CMAKE_C_COMPILER_LAUNCHER"}:
        return True
    if key == "CMAKE_C_STANDARD":
        return True
    if key.startswith("CMAKE_C_FLAGS"):
        return True
    return False


def _is_cxx_language_only_cache_key(key: str) -> bool:
    if key in {"CMAKE_CXX_COMPILER", "CMAKE_CXX_COMPILER_LAUNCHER"}:
        return True
    if key == "CMAKE_CXX_STANDARD":
        return True
    if key.startswith("CMAKE_CXX_FLAGS"):
        return True
    return False


def _effective_dependency_cache_variables(
    context: CMakeDependencyBuildContext,
    *,
    uses_c_language: bool,
    uses_cxx_language: bool,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in context.cache_variables.items():
        if key in {"CMAKE_PREFIX_PATH", "CMAKE_INSTALL_PREFIX", "CMAKE_BUILD_TYPE"}:
            continue
        if _is_c_language_only_cache_key(key) and not uses_c_language:
            continue
        if _is_cxx_language_only_cache_key(key) and not uses_cxx_language:
            continue
        if value == "":
            continue
        result[key] = value
    return result


def _dependency_transitive_names(
    dependency_roots: Any,
    dependency_name: str,
) -> tuple[str, ...]:
    dependency_names_by_parent = getattr(dependency_roots, "dependency_names_by_parent", {})
    transitive_names: set[str] = set()
    pending = list(dependency_names_by_parent.get(dependency_name, ()))
    while pending:
        child_name = str(pending.pop())
        if child_name in transitive_names:
            continue
        transitive_names.add(child_name)
        pending.extend(dependency_names_by_parent.get(child_name, ()))
    return tuple(name for name in dependency_roots.closure_order if name in transitive_names)


def _dependency_uses_manual_override(dependency_roots: Any, dependency_name: str) -> bool:
    uses_manual_override = getattr(dependency_roots, "uses_manual_root_override_for", None)
    if callable(uses_manual_override):
        return bool(uses_manual_override(dependency_name))
    return str(dependency_roots.mode) == "manual"


def _dependency_inputs_fingerprint(inputs: dict[str, Any]) -> str:
    canonical = json.dumps(
        inputs,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dependency_parent_names(dependency_roots: Any) -> dict[str, tuple[str, ...]]:
    configured = getattr(dependency_roots, "dependency_parent_names_by_name", None)
    if isinstance(configured, dict):
        return {
            str(dependency_name): tuple(str(parent_name) for parent_name in parent_names)
            for dependency_name, parent_names in configured.items()
        }

    result: dict[str, list[str]] = {}
    dependency_names_by_parent = getattr(dependency_roots, "dependency_names_by_parent", {})
    for parent_name, child_names in dependency_names_by_parent.items():
        for child_name in child_names:
            result.setdefault(str(child_name), []).append(str(parent_name))
    return {
        dependency_name: tuple(parent_names) for dependency_name, parent_names in result.items()
    }


def _dependency_source_dir_for_spec(
    dependency_root: Path,
    build_spec: CMakeDependencyBuildSpec,
) -> Path:
    if not build_spec.source_subdir:
        return dependency_root
    subdir_path = Path(build_spec.source_subdir)
    if subdir_path.is_absolute():
        raise WorkflowError(
            f"Dependency build spec {build_spec.dependency_name!r} uses absolute source_subdir: "
            f"{build_spec.source_subdir}"
        )
    source_dir = (dependency_root / subdir_path).resolve()
    try:
        source_dir.relative_to(dependency_root.resolve())
    except ValueError as exc:
        raise WorkflowError(
            f"Dependency build spec {build_spec.dependency_name!r} source_subdir escapes "
            f"dependency root: {build_spec.source_subdir}"
        ) from exc
    return source_dir


class CMakeDependencyBuilder:
    def __init__(
        self,
        config: CMakeDependencyBuilderConfig,
        services: CMakeDependencyBuilderServices,
    ) -> None:
        self.config = config
        self.services = services
        self._spec_by_name: Mapping[str, CMakeDependencyBuildSpec] = MappingProxyType(
            {spec.dependency_name: spec for spec in config.build_order}
        )

    @property
    def spec_by_name(self) -> Mapping[str, CMakeDependencyBuildSpec]:
        return self._spec_by_name

    def state_file_path(self, repo_root: Path, preset_name: str) -> Path:
        return (
            build_dir_for_preset_name(repo_root, preset_name)
            / "dependency_installs"
            / self.config.state_filename
        )

    def ordered_specs(self, dependency_roots: Any) -> list[CMakeDependencyBuildSpec]:
        ordered_specs: list[CMakeDependencyBuildSpec] = []
        for dependency_name in dependency_roots.closure_order:
            try:
                ordered_specs.append(self._spec_by_name[dependency_name])
            except KeyError as exc:
                raise WorkflowError(
                    "Missing dependency build spec for recursive dependency-root dependency "
                    f"{dependency_name!r}. Configure host-specific specs with "
                    "bind_cmake_workflow_script(..., dependency_build_order=...)."
                ) from exc
        return ordered_specs

    def build_state(
        self,
        context: CMakeDependencyBuildContext,
        dependency_roots: Any,
        *,
        repo_root: Path | None = None,
    ) -> dict[str, Any]:
        if repo_root is None:
            repo_root = Path(dependency_roots.repo_root)
        build_specs = self.ordered_specs(dependency_roots)
        dependency_states: dict[str, dict[str, Any]] = {}
        for build_spec in build_specs:
            inputs = self._dependency_build_inputs(
                context,
                dependency_roots,
                build_spec,
                dependency_states,
                repo_root=repo_root,
            )
            dependency_states[build_spec.dependency_name] = {
                "fingerprint": _dependency_inputs_fingerprint(inputs),
                "inputs": inputs,
            }
        return {
            "schemaVersion": DEPENDENCY_BUILD_STATE_SCHEMA_VERSION,
            "mode": dependency_roots.mode,
            "dependencies": dependency_states,
        }

    def _dependency_build_inputs(
        self,
        context: CMakeDependencyBuildContext,
        dependency_roots: Any,
        build_spec: CMakeDependencyBuildSpec,
        dependency_states: dict[str, dict[str, Any]],
        *,
        repo_root: Path,
    ) -> dict[str, Any]:
        dependency_name = build_spec.dependency_name
        dependency_root = dependency_roots.dependency_root_for(dependency_name)
        transitive_names = _dependency_transitive_names(dependency_roots, dependency_name)
        missing_states = [name for name in transitive_names if name not in dependency_states]
        if missing_states:
            raise WorkflowError(
                f"Dependency closure order must list dependencies before {dependency_name!r}: "
                f"{', '.join(missing_states)}"
            )
        dependency_names_by_parent = getattr(dependency_roots, "dependency_names_by_parent", {})
        return {
            "buildSpec": _json_compatible_build_spec(build_spec),
            "root": str(dependency_root),
            "sourceDir": str(_dependency_source_dir_for_spec(dependency_root, build_spec)),
            "buildDir": str(
                dependency_build_dir_for_name(repo_root, context.preset_name, dependency_name)
            ),
            "installPrefix": str(
                dependency_install_prefix_for_name(repo_root, context.preset_name, dependency_name)
            ),
            "resolvedCommit": dict(dependency_roots.resolved_commits).get(dependency_name),
            "manualOverride": _dependency_uses_manual_override(dependency_roots, dependency_name),
            "managedRoot": self.services.is_managed_dependency_root(repo_root, dependency_root),
            "managedByParent": self.services.has_nested_dependency_workflow(dependency_root),
            "directDependencies": list(dependency_names_by_parent.get(dependency_name, ())),
            "transitiveDependencies": list(transitive_names),
            "dependencyPrefixes": [
                {
                    "dependencyName": transitive_name,
                    "path": str(
                        dependency_install_prefix_for_name(
                            repo_root, context.preset_name, transitive_name
                        )
                    ),
                    "fingerprint": dependency_states[transitive_name]["fingerprint"],
                }
                for transitive_name in transitive_names
            ],
            "context": {
                "presetName": context.preset_name,
                "generator": context.generator,
                "generatorPlatform": context.generator_platform,
                "generatorToolset": context.generator_toolset,
                "cmakeExecutable": context.cmake_executable,
                "buildConfigurations": list(context.build_configurations),
                "externalPrefixPath": context.external_prefix_path,
                "cacheVariables": _effective_dependency_cache_variables(
                    context,
                    uses_c_language=build_spec.uses_c_language,
                    uses_cxx_language=build_spec.uses_cxx_language,
                ),
                "buildType": (
                    None
                    if multi_config_generator(context.generator)
                    else context.cache_variables.get("CMAKE_BUILD_TYPE", "Release") or "Release"
                ),
            },
        }

    def _load_build_state(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            state = load_json_file(path)
        except (OSError, ValueError, WorkflowError):
            return None
        if state.get("schemaVersion") != DEPENDENCY_BUILD_STATE_SCHEMA_VERSION:
            return None
        if not isinstance(state.get("dependencies"), dict):
            return None
        return state

    def rebuild_names(
        self,
        repo_root: Path,
        context: CMakeDependencyBuildContext,
        dependency_roots: Any,
    ) -> set[str]:
        build_specs = self.ordered_specs(dependency_roots)
        state_path = self.state_file_path(repo_root, context.preset_name)
        actual_state = self._load_build_state(state_path)
        expected_state = self.build_state(context, dependency_roots, repo_root=repo_root)
        return self._rebuild_names_from_state(
            repo_root,
            context,
            dependency_roots,
            build_specs,
            actual_state,
            expected_state,
        )

    def _rebuild_names_from_state(
        self,
        repo_root: Path,
        context: CMakeDependencyBuildContext,
        dependency_roots: Any,
        build_specs: Sequence[CMakeDependencyBuildSpec],
        actual_state: dict[str, Any] | None,
        expected_state: dict[str, Any],
    ) -> set[str]:
        all_dependency_names = {build_spec.dependency_name for build_spec in build_specs}
        actual_dependencies = actual_state["dependencies"] if actual_state is not None else {}
        expected_dependencies = expected_state["dependencies"]
        changed_names = {
            build_spec.dependency_name
            for build_spec in build_specs
            if actual_dependencies.get(build_spec.dependency_name)
            != expected_dependencies[build_spec.dependency_name]
            or _dependency_uses_manual_override(dependency_roots, build_spec.dependency_name)
            or not dependency_install_prefix_for_name(
                repo_root, context.preset_name, build_spec.dependency_name
            ).is_dir()
        }

        parent_names_by_dependency = _dependency_parent_names(dependency_roots)
        pending = deque(changed_names)
        while pending:
            changed_name = pending.popleft()
            for parent_name in parent_names_by_dependency.get(changed_name, ()):
                if parent_name not in all_dependency_names or parent_name in changed_names:
                    continue
                changed_names.add(parent_name)
                pending.append(parent_name)
        return changed_names

    def state_matches(
        self,
        repo_root: Path,
        context: CMakeDependencyBuildContext,
        dependency_roots: Any,
    ) -> bool:
        return not self.rebuild_names(repo_root, context, dependency_roots)

    def write_state_file(
        self,
        repo_root: Path,
        context: CMakeDependencyBuildContext,
        dependency_roots: Any,
    ) -> None:
        self.services.write_json(
            self.state_file_path(repo_root, context.preset_name),
            self.build_state(context, dependency_roots, repo_root=repo_root),
        )

    def write_dependency_receipts(
        self,
        state_path: Path,
        *,
        mode: str,
        dependencies: dict[str, Any],
    ) -> None:
        self.services.write_json(
            state_path,
            {
                "schemaVersion": DEPENDENCY_BUILD_STATE_SCHEMA_VERSION,
                "mode": mode,
                "dependencies": dependencies,
            },
        )

    def ensure_dependency_root_active_lock(
        self,
        dependency_root: Path,
        available_dependency_roots: dict[str, Path],
    ) -> None:
        template_path = dependency_root / TEMPLATE_LOCK_FILE_NAME
        lock_path = dependency_root / ACTIVE_LOCK_FILE_NAME
        if not template_path.is_file() or lock_path.exists():
            return
        template_text = template_path.read_text(encoding="utf-8")
        self.services.write_text(lock_path, template_text)
        lock_data = loads_jsonc(template_text, path_label=str(lock_path))
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
        self.services.write_json(lock_path, lock_data)

    def configure_dependency_for_context(
        self,
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
        build_spec = self._spec_by_name.get(dependency_name)
        source_dir = (
            _dependency_source_dir_for_spec(dependency_root, build_spec)
            if build_spec is not None
            else dependency_root
        )
        env = dict(os.environ)
        if self.services.is_managed_dependency_root(repo_root, dependency_root):
            self.ensure_dependency_root_active_lock(dependency_root, available_dependency_roots)
            _prepend_pythonpath(env, self.services.package_repo_root)

        managed_by_parent_args: list[str] = []
        if self.services.has_nested_dependency_workflow(dependency_root):
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

        has_c_variables = any(_is_c_language_only_cache_key(key) for key in context.cache_variables)
        has_cxx_variables = any(
            _is_cxx_language_only_cache_key(key) for key in context.cache_variables
        )
        for key, value in _effective_dependency_cache_variables(
            context,
            uses_c_language=(
                (
                    build_spec.uses_c_language
                    if build_spec is not None
                    else self.dependency_uses_c_language(dependency_name)
                )
                if has_c_variables
                else True
            ),
            uses_cxx_language=(
                (
                    build_spec.uses_cxx_language
                    if build_spec is not None
                    else self.dependency_uses_cxx_language(dependency_name)
                )
                if has_cxx_variables
                else True
            ),
        ).items():
            configure_cmd.append(f"-D{key}={value}")

        if not multi_config_generator(context.generator):
            configure_cmd.append(
                "-DCMAKE_BUILD_TYPE="
                f"{context.cache_variables.get('CMAKE_BUILD_TYPE', 'Release') or 'Release'}"
            )

        prefix_parts = [str(path) for path in dependency_prefixes]
        if context.external_prefix_path:
            prefix_parts.append(context.external_prefix_path)
        if prefix_parts:
            configure_cmd.append(f"-DCMAKE_PREFIX_PATH={';'.join(prefix_parts)}")

        self.services.run_command(configure_cmd, cwd=source_dir, env=env)

        for configuration in context.build_configurations:
            build_cmd = [context.cmake_executable, "--build", str(build_dir)]
            install_cmd = [context.cmake_executable, "--install", str(build_dir)]
            if multi_config_generator(context.generator):
                build_cmd.extend(["--config", configuration])
                install_cmd.extend(["--config", configuration])
            self.services.run_command(build_cmd, cwd=source_dir, env=env)
            self.services.run_command(install_cmd, cwd=source_dir, env=env)

    def dependency_source_dir(self, dependency_root: Path, dependency_name: str) -> Path:
        build_spec = self._spec_by_name.get(dependency_name)
        if build_spec is None:
            return dependency_root
        return _dependency_source_dir_for_spec(dependency_root, build_spec)

    def dependency_uses_c_language(self, dependency_name: str) -> bool:
        try:
            return self._spec_by_name[dependency_name].uses_c_language
        except KeyError as exc:
            raise WorkflowError(f"Unknown dependency build spec: {dependency_name}") from exc

    def dependency_uses_cxx_language(self, dependency_name: str) -> bool:
        try:
            return self._spec_by_name[dependency_name].uses_cxx_language
        except KeyError as exc:
            raise WorkflowError(f"Unknown dependency build spec: {dependency_name}") from exc

    def build_dependencies(
        self,
        context: CMakeDependencyBuildContext,
        *,
        repo_root: Path,
    ) -> None:
        resolved_root = repo_root.resolve()
        with self.services.workspace_mutation_lock(resolved_root):
            self.build_dependencies_unlocked(context, repo_root=resolved_root)

    def build_dependencies_unlocked(
        self,
        context: CMakeDependencyBuildContext,
        *,
        repo_root: Path,
    ) -> None:
        dependency_roots = self.services.require_dependency_roots(repo_root=repo_root)
        build_specs = self.ordered_specs(dependency_roots)
        expected_state = self.build_state(context, dependency_roots, repo_root=repo_root)
        state_path = self.state_file_path(repo_root, context.preset_name)
        actual_state = self._load_build_state(state_path)
        rebuild_names = self._rebuild_names_from_state(
            repo_root,
            context,
            dependency_roots,
            build_specs,
            actual_state,
            expected_state,
        )
        actual_dependencies = actual_state["dependencies"] if actual_state is not None else {}
        expected_dependencies = expected_state["dependencies"]
        valid_receipts = {
            build_spec.dependency_name: expected_dependencies[build_spec.dependency_name]
            for build_spec in build_specs
            if build_spec.dependency_name not in rebuild_names
            and actual_dependencies.get(build_spec.dependency_name)
            == expected_dependencies[build_spec.dependency_name]
        }

        receipt_writer = self.services.write_dependency_receipts or self.write_dependency_receipts
        if not rebuild_names:
            if actual_state != expected_state:
                receipt_writer(
                    state_path,
                    mode=dependency_roots.mode,
                    dependencies=valid_receipts,
                )
            return

        receipt_writer(
            state_path,
            mode=dependency_roots.mode,
            dependencies=valid_receipts,
        )
        for build_spec in build_specs:
            if build_spec.dependency_name not in rebuild_names:
                continue
            self.services.remove_path(
                dependency_build_dir_for_name(
                    repo_root, context.preset_name, build_spec.dependency_name
                )
            )
            self.services.remove_path(
                dependency_install_prefix_for_name(
                    repo_root, context.preset_name, build_spec.dependency_name
                )
            )

        available_dependency_roots = {
            dependency_name: dependency_roots.dependency_root_for(dependency_name)
            for dependency_name in dependency_roots.closure_order
        }
        configure = (
            self.services.configure_dependency_for_context or self.configure_dependency_for_context
        )
        for build_spec in build_specs:
            dependency_name = build_spec.dependency_name
            install_prefix = dependency_install_prefix_for_name(
                repo_root, context.preset_name, dependency_name
            )
            if dependency_name not in rebuild_names:
                continue
            dependency_root = dependency_roots.dependency_root_for(dependency_name)
            dependency_prefixes = [
                dependency_install_prefix_for_name(repo_root, context.preset_name, transitive_name)
                for transitive_name in _dependency_transitive_names(
                    dependency_roots, dependency_name
                )
            ]
            install_prefix.mkdir(parents=True, exist_ok=True)
            configure(
                repo_root=repo_root,
                context=context,
                dependency_name=dependency_name,
                dependency_root=dependency_root,
                install_prefix=install_prefix,
                dependency_prefixes=dependency_prefixes,
                cmake_options=build_spec.cmake_options,
                available_dependency_roots=available_dependency_roots,
            )
            valid_receipts[dependency_name] = expected_dependencies[dependency_name]
            receipt_writer(
                state_path,
                mode=dependency_roots.mode,
                dependencies=valid_receipts,
            )

    def configure_dependency(
        self,
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
            preset_model, repo_root, preset_name, dependency_prefixes
        )
        if prefix_path:
            configure_cmd.append(f"-DCMAKE_PREFIX_PATH={prefix_path}")

        self.services.run_command(configure_cmd, cwd=dependency_root, env=env)

        for configuration in build_configurations_for_preset(preset_model, preset_name):
            build_cmd = [cmake_executable, "--build", str(build_dir)]
            install_cmd = [cmake_executable, "--install", str(build_dir)]
            if multi_config_generator(generator):
                build_cmd.extend(["--config", configuration])
                install_cmd.extend(["--config", configuration])
            self.services.run_command(build_cmd, cwd=dependency_root, env=env)
            self.services.run_command(install_cmd, cwd=dependency_root, env=env)
