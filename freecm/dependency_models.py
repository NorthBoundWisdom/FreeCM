"""Dependency root data models."""

from __future__ import annotations

import os
from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .dependency_lock import DEFAULT_REQUIRED_RELATIVE_PATHS, DEPENDENCY_LOCK_SCHEMA_VERSION
    from .path_maps import dependency_root_path_map, environment_map
except ImportError:  # pragma: no cover - supports direct script execution.
    from dependency_lock import DEFAULT_REQUIRED_RELATIVE_PATHS, DEPENDENCY_LOCK_SCHEMA_VERSION
    from path_maps import dependency_root_path_map, environment_map


def _reverse_dependency_adjacency(
    dependency_names_by_parent: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    parent_names_by_child: dict[str, list[str]] = {}
    parent_sets_by_child: dict[str, set[str]] = {}
    for parent_name, child_names in dependency_names_by_parent.items():
        for child_name in child_names:
            parent_set = parent_sets_by_child.setdefault(child_name, set())
            if parent_name in parent_set:
                continue
            parent_set.add(parent_name)
            parent_names_by_child.setdefault(child_name, []).append(parent_name)
    return {
        child_name: tuple(parent_names)
        for child_name, parent_names in parent_names_by_child.items()
    }


@dataclass(frozen=True)
class DependencyRootSpec:
    dependency_name: str
    repo_name: str
    env_key: str
    required_relative_paths: tuple[str, ...]


@dataclass(frozen=True)
class DependencyDeclaration:
    dependency_name: str
    parent_dependency_name: str | None
    source_label: str
    declared_by_root: bool
    remote: str
    commit: str

    def as_json_dict(self) -> dict[str, Any]:
        return {
            "parent": self.parent_dependency_name,
            "source": self.source_label,
            "declaredByRoot": self.declared_by_root,
            "remote": self.remote,
            "commit": self.commit,
        }


@dataclass(frozen=True)
class RootOverrideTransitivePinMismatch:
    dependency_name: str
    root_declaration: DependencyDeclaration
    transitive_declaration: DependencyDeclaration

    @property
    def code(self) -> str:
        return "root-override-transitive-pin-mismatch"

    @property
    def root_commit(self) -> str:
        return self.root_declaration.commit

    @property
    def transitive_commit(self) -> str:
        return self.transitive_declaration.commit

    @property
    def message(self) -> str:
        return (
            f"root override transitive pin mismatch for {self.dependency_name}: "
            f"root commit {self.root_commit} overrides transitive commit "
            f"{self.transitive_commit}"
        )

    def as_json_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "dependencyName": self.dependency_name,
            "message": self.message,
            "rootCommit": self.root_commit,
            "transitiveCommit": self.transitive_commit,
            "rootSource": self.root_declaration.source_label,
            "transitiveSource": self.transitive_declaration.source_label,
            "parentDependencyName": self.transitive_declaration.parent_dependency_name,
            "root": self.root_declaration.as_json_dict(),
            "transitive": self.transitive_declaration.as_json_dict(),
        }


@dataclass(frozen=True)
class DependencyPin:
    dependency_name: str
    repo_name: str
    remote: str
    commit: str
    latest_ref: str | None
    declared_by_root: bool
    env_key: str | None
    required_relative_paths: tuple[str, ...]
    source_label: str = ""
    parent_dependency_name: str | None = None

    def declaration(self) -> DependencyDeclaration:
        return DependencyDeclaration(
            dependency_name=self.dependency_name,
            parent_dependency_name=self.parent_dependency_name,
            source_label=self.source_label,
            declared_by_root=self.declared_by_root,
            remote=self.remote,
            commit=self.commit,
        )


@dataclass(frozen=True)
class DependencyClosure:
    direct_dependency_names: tuple[str, ...]
    dependency_pins_by_name: dict[str, DependencyPin]
    dependency_names_by_parent: dict[str, tuple[str, ...]]
    dependency_declarations_by_name: dict[str, tuple[DependencyDeclaration, ...]]
    topo_order: tuple[str, ...]
    dependency_parent_names_by_name: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        derived = _reverse_dependency_adjacency(self.dependency_names_by_parent)
        if self.dependency_parent_names_by_name:
            if self.dependency_parent_names_by_name != derived:
                raise ValueError("Inconsistent dependency parent adjacency")
            return
        object.__setattr__(self, "dependency_parent_names_by_name", derived)


@dataclass(frozen=True)
class DependencyRootConfig:
    repo_root: Path
    dependency_root_specs: tuple[DependencyRootSpec, ...]
    repo_display_name: str
    default_required_relative_paths: tuple[str, ...] = DEFAULT_REQUIRED_RELATIVE_PATHS


@dataclass(frozen=True)
class ResolvedDependencyRoots:
    mode: str
    repo_root: Path
    lock_data: dict[str, Any]
    direct_dependency_names: tuple[str, ...]
    dependency_pins_by_name: dict[str, DependencyPin]
    seed_repositories_by_dependency: dict[str, Path]
    dependency_roots_by_name: dict[str, Path]
    resolved_commits_by_dependency: dict[str, str]
    dependency_names_by_parent: dict[str, tuple[str, ...]]
    dependency_declarations_by_name: dict[str, tuple[DependencyDeclaration, ...]]
    closure_order: tuple[str, ...]
    dependency_root_specs: tuple[DependencyRootSpec, ...]
    dependency_parent_names_by_name: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        derived = _reverse_dependency_adjacency(self.dependency_names_by_parent)
        if self.dependency_parent_names_by_name:
            if self.dependency_parent_names_by_name != derived:
                raise ValueError("Inconsistent dependency parent adjacency")
            return
        object.__setattr__(self, "dependency_parent_names_by_name", derived)

    @property
    def lock_commits(self) -> dict[str, str]:
        return {
            spec.dependency_name: str(
                self.lock_data["dependencies"][spec.dependency_name]["commit"]
            )
            for spec in self.dependency_root_specs
        }

    @property
    def resolved_commits(self) -> dict[str, str]:
        return dict(self.resolved_commits_by_dependency)

    def dependency_pin_for(self, dependency_name: str) -> DependencyPin:
        return self.dependency_pins_by_name[dependency_name]

    def manual_root_override_for(self, dependency_name: str) -> Path | None:
        return manual_root_override_path(
            self.lock_data,
            dependency_name,
            self.mode,
            base_root=self.repo_root,
        )

    def uses_manual_root_override_for(self, dependency_name: str) -> bool:
        return self.manual_root_override_for(dependency_name) is not None

    def dependency_root_for(self, dependency_name: str) -> Path:
        return self.dependency_roots_by_name[dependency_name]

    def seed_repository_for(self, dependency_name: str) -> Path:
        return self.seed_repositories_by_dependency[dependency_name]

    def is_direct_dependency(self, dependency_name: str) -> bool:
        return dependency_name in self.direct_dependency_names

    def dependency_declarations_for(
        self, dependency_name: str
    ) -> tuple[DependencyDeclaration, ...]:
        return self.dependency_declarations_by_name.get(dependency_name, ())

    def dependency_parents_for(self, dependency_name: str) -> tuple[str, ...]:
        return self.dependency_parent_names_by_name.get(dependency_name, ())

    def root_override_transitive_pin_mismatches(
        self,
    ) -> tuple[RootOverrideTransitivePinMismatch, ...]:
        mismatches: list[RootOverrideTransitivePinMismatch] = []
        seen: set[tuple[str, str | None, str, str]] = set()
        for dependency_name in self.closure_order:
            declarations = self.dependency_declarations_for(dependency_name)
            root_declarations = [
                declaration for declaration in declarations if declaration.declared_by_root
            ]
            if not root_declarations:
                continue
            root_declaration = root_declarations[0]
            for declaration in declarations:
                if declaration.declared_by_root:
                    continue
                if declaration.commit == root_declaration.commit:
                    continue
                key = (
                    dependency_name,
                    declaration.parent_dependency_name,
                    declaration.source_label,
                    declaration.commit,
                )
                if key in seen:
                    continue
                seen.add(key)
                mismatches.append(
                    RootOverrideTransitivePinMismatch(
                        dependency_name=dependency_name,
                        root_declaration=root_declaration,
                        transitive_declaration=declaration,
                    )
                )
        return tuple(mismatches)

    def effective_mode_for(self, dependency_name: str) -> str:
        if self.mode != "manual":
            return self.mode
        return "manual" if self.uses_manual_root_override_for(dependency_name) else "pinned"

    def as_dependency_root_path_map(self) -> dict[str, Path]:
        return dependency_root_path_map(
            self.dependency_root_specs,
            self.dependency_root_for,
        )

    def as_environment_map(self) -> dict[str, str]:
        return environment_map(self.as_dependency_root_path_map())

    def as_json_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": DEPENDENCY_LOCK_SCHEMA_VERSION,
            "mode": self.mode,
            "roots": self.as_environment_map(),
            "dependencyRoots": {
                dependency_name: str(self.dependency_root_for(dependency_name))
                for dependency_name in self.closure_order
            },
            "seedRoots": {
                dependency_name: str(self.seed_repository_for(dependency_name))
                for dependency_name in self.closure_order
            },
            "lock": self.lock_commits,
            "resolved": self.resolved_commits,
            "directDependencyNames": list(self.direct_dependency_names),
            "closureOrder": list(self.closure_order),
            "dependencyNamesByParent": {
                dependency_name: list(child_names)
                for dependency_name, child_names in self.dependency_names_by_parent.items()
            },
            "rootOverrideTransitivePinMismatches": [
                mismatch.as_json_dict()
                for mismatch in self.root_override_transitive_pin_mismatches()
            ],
            "dependencies": {
                dependency_name: self.dependency_record_for(dependency_name)
                for dependency_name in self.closure_order
            },
        }

    def dependency_record_for(self, dependency_name: str) -> dict[str, Any]:
        dependency = self.dependency_pin_for(dependency_name)
        manual_override = self.manual_root_override_for(dependency_name)
        return {
            "name": dependency_name,
            "repoName": dependency.repo_name,
            "remote": dependency.remote,
            "direct": self.is_direct_dependency(dependency_name),
            "parents": list(self.dependency_parents_for(dependency_name)),
            "children": list(self.dependency_names_by_parent.get(dependency_name, ())),
            "mode": self.effective_mode_for(dependency_name),
            "commit": self.resolved_commits_by_dependency.get(dependency_name, dependency.commit),
            "lockedCommit": dependency.commit,
            "manualOverride": str(manual_override) if manual_override is not None else None,
            "path": str(self.dependency_root_for(dependency_name)),
            "seedPath": str(self.seed_repository_for(dependency_name)),
            "declaredBy": [
                declaration.as_json_dict()
                for declaration in self.dependency_declarations_for(dependency_name)
            ],
        }


@dataclass(frozen=True)
class DependencyRootSummary:
    dependency_name: str
    mode: str
    commit: str | None
    path: Path


@dataclass(frozen=True)
class DependencyCommitChange:
    dependency_name: str
    old_commit: str
    new_commit: str


@dataclass(frozen=True)
class SeedRepoPreflightProblem:
    dependency_name: str
    seed_root: Path
    reason: str


def manual_root_override_path(
    lock_data: dict[str, Any],
    dependency_name: str,
    mode: str,
    *,
    base_root: Path | None = None,
) -> Path | None:
    if mode != "manual":
        return None
    deps_manual_path = lock_data.get("depsManualPath", {})
    manual_path = str(deps_manual_path.get(dependency_name, "")).strip()
    if not manual_path:
        return None
    path = Path(os.path.expandvars(manual_path)).expanduser()
    if base_root is not None and not path.is_absolute():
        path = base_root / path
    return path.resolve()


def dependency_commit_changes(
    before_lock_data: MutableMapping[str, Any],
    after_lock_data: MutableMapping[str, Any],
    dependency_names: Iterable[str],
) -> tuple[DependencyCommitChange, ...]:
    before_dependencies = before_lock_data.get("dependencies", {})
    after_dependencies = after_lock_data.get("dependencies", {})
    changes: list[DependencyCommitChange] = []
    for dependency_name in dependency_names:
        if not isinstance(before_dependencies, MutableMapping):
            continue
        if not isinstance(after_dependencies, MutableMapping):
            continue
        before_dependency = before_dependencies.get(dependency_name, {})
        after_dependency = after_dependencies.get(dependency_name, {})
        if not isinstance(before_dependency, MutableMapping):
            continue
        if not isinstance(after_dependency, MutableMapping):
            continue
        old_commit = str(before_dependency.get("commit", "")).strip()
        new_commit = str(after_dependency.get("commit", "")).strip()
        if not old_commit or not new_commit or old_commit == new_commit:
            continue
        changes.append(
            DependencyCommitChange(
                dependency_name=dependency_name,
                old_commit=old_commit,
                new_commit=new_commit,
            )
        )
    return tuple(changes)
