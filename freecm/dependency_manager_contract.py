# Internal: shared type contract for DependencyRootManager mixins.

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .dependency_models import (
    DependencyClosure,
    DependencyPin,
    DependencyRootConfig,
    DependencyRootSpec,
)

SeedProgressCallback = Callable[[str, str, str], None]


if TYPE_CHECKING:

    class DependencyManagerContract:
        config: DependencyRootConfig
        repo_root: Path
        dependency_root_specs: tuple[DependencyRootSpec, ...]
        direct_dependency_root_specs: tuple[DependencyRootSpec, ...]
        known_dependency_root_specs: tuple[DependencyRootSpec, ...]
        direct_dependency_names: tuple[str, ...]
        spec_by_dependency_name: dict[str, DependencyRootSpec]
        direct_spec_by_dependency_name: dict[str, DependencyRootSpec]
        spec_by_env_key: dict[str, DependencyRootSpec]

        def _normalize_repo_root(self, repo_root: Path | None) -> Path: ...

        def _managed_child_path(
            self,
            parent: Path,
            child_name: str,
            *,
            label: str,
        ) -> Path: ...

        def _fetch_remote_refs(
            self,
            seed_root: Path,
            dependency_name: str,
            remote: str,
            *,
            quiet: bool = False,
        ) -> None: ...

        def load_dependency_lock_data(
            self,
            path: Path,
            *,
            expected_dependency_names: Iterable[str] | None = None,
        ) -> dict[str, Any]: ...

        def load_lock_file(self, repo_root: Path | None = None) -> dict[str, Any]: ...

        def _write_lock_file(self, repo_root: Path, data: dict[str, Any]) -> None: ...

        def _resolve_mode(self, lock_data: dict[str, Any]) -> str: ...

        def _dependency_checkout_spec_from_entry(
            self,
            dependency_name: str,
            dependency_data: dict[str, Any],
            *,
            declared_by_root: bool,
            source_label: str,
            parent_dependency_name: str | None = None,
        ) -> DependencyPin: ...

        def _root_dependency_specs_from_lock(
            self,
            lock_data: dict[str, Any],
        ) -> tuple[DependencyPin, ...]: ...

        def _load_nested_dependency_specs(
            self,
            dependency_root: Path,
            *,
            parent_dependency_name: str,
        ) -> tuple[DependencyPin, ...]: ...

        def _load_nested_dependency_specs_from_locked_commit(
            self,
            seed_root: Path,
            dependency: DependencyPin,
        ) -> tuple[DependencyPin, ...]: ...

        def _discover_dependency_closure(
            self,
            lock_data: dict[str, Any],
            repo_root: Path,
            *,
            prepare_dependency_root: Callable[[DependencyPin], Path],
            load_nested_dependency_specs: Callable[
                [Path, DependencyPin], tuple[DependencyPin, ...]
            ],
        ) -> DependencyClosure: ...

        def load_dependency_closure(
            self,
            repo_root: Path | None = None,
        ) -> DependencyClosure: ...

        def _load_dependency_closure_for_lock(
            self,
            repo_root: Path,
            lock_data: dict[str, Any],
            *,
            allow_network: bool,
            quiet: bool = False,
        ) -> DependencyClosure: ...

        def _merge_dependency_specs(
            self,
            existing: DependencyPin | None,
            candidate: DependencyPin,
        ) -> DependencyPin: ...

        def _seed_repo_root(self, repo_root: Path, repo_name: str) -> Path: ...

        def _ensure_seed_repo(
            self,
            seed_root: Path,
            remote: str,
            *,
            quiet: bool = False,
        ) -> bool: ...

        def _ensure_existing_seed_repo(
            self,
            seed_root: Path,
            dependency: DependencyPin,
        ) -> None: ...

        def _prepare_seed_repository_closure_unlocked(
            self,
            repo_root: Path,
            *,
            progress: SeedProgressCallback | None = None,
            quiet: bool = False,
        ) -> DependencyClosure: ...

        def _prepare_seed_root_for_init(
            self,
            repo_root: Path,
            dependency: DependencyPin,
            *,
            quiet: bool = False,
        ) -> Path: ...

        def _prepare_seed_root_for_offline(
            self,
            repo_root: Path,
            dependency: DependencyPin,
        ) -> Path: ...

        def _refresh_latest_direct_dependency_commits(
            self,
            repo_root: Path,
            lock_data: dict[str, Any],
            *,
            allow_network: bool,
            quiet: bool = False,
        ) -> bool: ...

        def _resolve_ref_to_commit(
            self,
            seed_root: Path,
            dependency_name: str,
            remote: str,
            ref: str,
            *,
            allow_fetch: bool = False,
        ) -> str: ...

        def _manual_dependency_root_for(
            self,
            repo_root: Path,
            lock_data: dict[str, Any],
            mode: str,
            dependency: DependencyPin,
        ) -> Path | None: ...

        def _external_manual_dependency_root_for(
            self,
            repo_root: Path,
            lock_data: dict[str, Any],
            mode: str,
            dependency: DependencyPin,
        ) -> Path | None: ...

        def _validate_required_paths(
            self,
            root: Path,
            dependency: DependencyPin,
        ) -> None: ...

else:

    class DependencyManagerContract:
        pass


__all__ = ("DependencyManagerContract",)
