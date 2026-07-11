# Internal: dependency closure discovery for DependencyRootManager.

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .dependency_lock import TEMPLATE_LOCK_FILE_NAME
from .dependency_lock import validate_dependency_lock_data as _validate_dependency_lock_data
from .dependency_manager_contract import DependencyManagerContract
from .dependency_models import DependencyClosure, DependencyPin
from .git_repositories import git
from .jsonc import loads_jsonc

if TYPE_CHECKING:
    from .dependency_models import DependencyDeclaration, DependencyRootSpec
    from .seed_store import _OfflineSeedRepositorySnapshot


@dataclass
class _TraversalFrame:
    dependency_name: str
    child_specs: tuple[DependencyPin, ...]
    child_names: list[str]
    next_child_index: int = 0


class DependencyClosureResolverMixin(DependencyManagerContract):

    def _nested_lock_template_path(self, dependency_root: Path) -> Path:
        return dependency_root / TEMPLATE_LOCK_FILE_NAME

    def _known_spec_for_dependency(self, dependency_name: str) -> DependencyRootSpec | None:
        return self.spec_by_dependency_name.get(dependency_name)

    def _dependency_checkout_spec_from_entry(
        self,
        dependency_name: str,
        dependency_data: dict[str, Any],
        *,
        declared_by_root: bool,
        source_label: str,
        parent_dependency_name: str | None = None,
    ) -> DependencyPin:
        known_spec = self._known_spec_for_dependency(dependency_name)
        required_relative_paths = (
            known_spec.required_relative_paths
            if known_spec
            else self.config.default_required_relative_paths
        )
        repo_name = dependency_data.get("repoName") or (
            known_spec.repo_name if known_spec is not None else dependency_name
        )
        return DependencyPin(
            dependency_name=dependency_name,
            repo_name=str(repo_name),
            remote=str(dependency_data["remote"]),
            commit=str(dependency_data["commit"]),
            latest_ref=dependency_data["latestRef"],
            declared_by_root=declared_by_root,
            env_key=known_spec.env_key if known_spec else None,
            required_relative_paths=required_relative_paths,
            source_label=source_label,
            parent_dependency_name=parent_dependency_name,
        )

    def _root_dependency_specs_from_lock(
        self, lock_data: dict[str, Any]
    ) -> tuple[DependencyPin, ...]:
        return tuple(
            self._dependency_checkout_spec_from_entry(
                spec.dependency_name,
                lock_data["dependencies"][spec.dependency_name],
                declared_by_root=True,
                source_label="root lock",
            )
            for spec in self.dependency_root_specs
        )

    def _load_nested_dependency_specs(
        self,
        dependency_root: Path,
        *,
        parent_dependency_name: str,
    ) -> tuple[DependencyPin, ...]:
        template_path = self._nested_lock_template_path(dependency_root)
        if not template_path.is_file():
            return ()
        lock_data = self.load_dependency_lock_data(template_path)
        return tuple(
            self._dependency_checkout_spec_from_entry(
                dependency_name,
                dependency_data,
                declared_by_root=False,
                source_label=str(template_path),
                parent_dependency_name=parent_dependency_name,
            )
            for dependency_name, dependency_data in lock_data["dependencies"].items()
        )

    def _load_nested_dependency_specs_from_locked_commit(
        self,
        seed_root: Path,
        dependency: DependencyPin,
    ) -> tuple[DependencyPin, ...]:
        completed = git(
            seed_root,
            "show",
            f"{dependency.commit}:{TEMPLATE_LOCK_FILE_NAME}",
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            return ()
        lock_data = _validate_dependency_lock_data(
            loads_jsonc(
                completed.stdout,
                path_label=f"{seed_root}@{dependency.commit}:{TEMPLATE_LOCK_FILE_NAME}",
            ),
            path_label=f"{seed_root}@{dependency.commit}:{TEMPLATE_LOCK_FILE_NAME}",
        )
        return tuple(
            self._dependency_checkout_spec_from_entry(
                dependency_name,
                dependency_data,
                declared_by_root=False,
                source_label=f"{seed_root}@{dependency.commit}:{TEMPLATE_LOCK_FILE_NAME}",
                parent_dependency_name=dependency.dependency_name,
            )
            for dependency_name, dependency_data in lock_data["dependencies"].items()
        )

    def _discover_dependency_closure(
        self,
        lock_data: dict[str, Any],
        repo_root: Path,
        *,
        prepare_dependency_root: Callable[[DependencyPin], Path],
        load_nested_dependency_specs: Callable[[Path, DependencyPin], tuple[DependencyPin, ...]],
    ) -> DependencyClosure:
        dependency_pins_by_name: dict[str, DependencyPin] = {}
        dependency_names_by_parent: dict[str, tuple[str, ...]] = {}
        dependency_declarations_by_name: dict[str, list[DependencyDeclaration]] = {}
        topo_order: list[str] = []
        active_index_by_name: dict[str, int] = {}
        visited: set[str] = set()

        def register(spec: DependencyPin) -> DependencyPin:
            dependency_declarations_by_name.setdefault(
                spec.dependency_name,
                [],
            ).append(spec.declaration())
            merged = self._merge_dependency_specs(
                dependency_pins_by_name.get(spec.dependency_name),
                spec,
            )
            dependency_pins_by_name[spec.dependency_name] = merged
            return merged

        direct_specs = self._root_dependency_specs_from_lock(lock_data)
        for spec in direct_specs:
            register(spec)

        stack: list[_TraversalFrame] = []

        def push_dependency(dependency_name: str) -> None:
            dependency = dependency_pins_by_name[dependency_name]
            dependency_root = prepare_dependency_root(dependency)
            child_specs = load_nested_dependency_specs(dependency_root, dependency)
            active_index_by_name[dependency_name] = len(stack)
            stack.append(
                _TraversalFrame(
                    dependency_name=dependency_name,
                    child_specs=child_specs,
                    child_names=[],
                )
            )

        for direct_spec in direct_specs:
            direct_name = direct_spec.dependency_name
            if direct_name in visited:
                continue
            push_dependency(direct_name)

            while stack:
                frame = stack[-1]
                if frame.next_child_index >= len(frame.child_specs):
                    dependency_names_by_parent[frame.dependency_name] = tuple(frame.child_names)
                    active_index_by_name.pop(frame.dependency_name)
                    visited.add(frame.dependency_name)
                    topo_order.append(frame.dependency_name)
                    stack.pop()
                    continue

                child_spec = frame.child_specs[frame.next_child_index]
                frame.next_child_index += 1
                registered_child = register(child_spec)
                child_name = registered_child.dependency_name
                frame.child_names.append(child_name)

                if child_name in visited:
                    continue
                cycle_start = active_index_by_name.get(child_name)
                if cycle_start is not None:
                    cycle_names = [
                        active_frame.dependency_name for active_frame in stack[cycle_start:]
                    ]
                    cycle = " -> ".join([*cycle_names, child_name])
                    raise ValueError(f"Source-root dependency cycle detected: {cycle}")
                push_dependency(child_name)

        return DependencyClosure(
            direct_dependency_names=tuple(spec.dependency_name for spec in direct_specs),
            dependency_pins_by_name=dependency_pins_by_name,
            dependency_names_by_parent=dependency_names_by_parent,
            dependency_declarations_by_name={
                dependency_name: tuple(declarations)
                for dependency_name, declarations in dependency_declarations_by_name.items()
            },
            topo_order=tuple(topo_order),
        )

    def load_dependency_closure(
        self,
        repo_root: Path | None = None,
    ) -> DependencyClosure:
        repo_root = self._normalize_repo_root(repo_root)
        lock_data = self.load_lock_file(repo_root)
        return self._load_dependency_closure_for_lock(
            repo_root,
            lock_data,
            allow_network=False,
        )

    def _load_dependency_closure_for_lock(
        self,
        repo_root: Path,
        lock_data: dict[str, Any],
        *,
        allow_network: bool,
        quiet: bool = False,
        offline_seed_snapshots: MutableMapping[str, _OfflineSeedRepositorySnapshot] | None = None,
    ) -> DependencyClosure:
        if allow_network and offline_seed_snapshots is not None:
            raise ValueError("Offline seed snapshots cannot be collected with network access")
        mode = self._resolve_mode(lock_data)

        def prepare_dependency_root(dependency: DependencyPin) -> Path:
            manual_override = self._manual_dependency_root_for(
                repo_root, lock_data, mode, dependency
            )
            if manual_override is not None:
                self._validate_required_paths(manual_override, dependency)
                return manual_override
            if allow_network:
                return self._prepare_seed_root_for_init(
                    repo_root,
                    dependency,
                    quiet=quiet,
                )
            if offline_seed_snapshots is None:
                return self._prepare_seed_root_for_offline(repo_root, dependency)
            seed_root = self._seed_repo_root(repo_root, dependency.repo_name)
            snapshot = self._inspect_existing_seed_repo(seed_root, dependency)
            if offline_seed_snapshots is not None:
                offline_seed_snapshots[dependency.dependency_name] = snapshot
            return seed_root

        def load_nested_specs_for_dependency(
            dependency_root: Path,
            dependency: DependencyPin,
        ) -> tuple[DependencyPin, ...]:
            if self._manual_dependency_root_for(repo_root, lock_data, mode, dependency) is not None:
                return self._load_nested_dependency_specs(
                    dependency_root,
                    parent_dependency_name=dependency.dependency_name,
                )
            return self._load_nested_dependency_specs_from_locked_commit(
                dependency_root, dependency
            )

        return self._discover_dependency_closure(
            lock_data,
            repo_root,
            prepare_dependency_root=prepare_dependency_root,
            load_nested_dependency_specs=load_nested_specs_for_dependency,
        )


__all__ = ("DependencyClosureResolverMixin",)
