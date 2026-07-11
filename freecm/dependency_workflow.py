# Usage:
#   Library: from freecm.dependency_workflow import DependencyRootWorkflowFacade

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

from .asset_seeds import prepare_asset_seeds, require_asset_seeds
from .dependency_lock import ACTIVE_LOCK_FILE_NAME
from .dependency_models import (
    DependencyRootConfig,
    DependencyRootSpec,
    ResolvedDependencyRoots,
)
from .dependency_roots import DependencyRootManager


class WrappedDependencyRoots(Protocol):
    @property
    def dependency_roots(self) -> ResolvedDependencyRoots: ...


ResolvedRootsT = TypeVar("ResolvedRootsT", bound=WrappedDependencyRoots)


@dataclass(frozen=True)
class DependencyRootWorkflowServices:
    prepare_asset_seeds: Callable[[Path], Sequence[Any]] = field(
        default_factory=lambda: prepare_asset_seeds
    )
    require_asset_seeds: Callable[[Path], Sequence[Any]] = field(
        default_factory=lambda: require_asset_seeds
    )


class DependencyRootWorkflowFacade(Generic[ResolvedRootsT]):
    def __init__(
        self,
        manager_config: DependencyRootConfig,
        *,
        services: DependencyRootWorkflowServices | None = None,
    ) -> None:
        self._manager = DependencyRootManager(manager_config)
        self._services = services or DependencyRootWorkflowServices()
        self.repo_root = self._manager.repo_root
        self.dependency_root_specs = self._manager.dependency_root_specs
        self.direct_dependency_root_specs = self._manager.direct_dependency_root_specs
        self.known_dependency_root_specs = self._manager.known_dependency_root_specs
        self.direct_dependency_names = self._manager.direct_dependency_names
        self.spec_by_dependency_name = self._manager.spec_by_dependency_name
        self.direct_spec_by_dependency_name = self._manager.direct_spec_by_dependency_name
        self.spec_by_env_key = self._manager.spec_by_env_key

    def _repo_root(self, repo_root: Path | None = None) -> Path:
        return repo_root.resolve() if repo_root else self.repo_root

    def _lock_file_path(self, repo_root: Path) -> Path:
        return repo_root / ACTIVE_LOCK_FILE_NAME

    def _wrap_dependency_roots(
        self,
        dependency_roots: ResolvedDependencyRoots,
    ) -> ResolvedRootsT:
        raise NotImplementedError

    def _validate_workflow_lock_data(
        self,
        lock_data: Mapping[str, Any],
        *,
        path_label: str | Path,
    ) -> None:
        del lock_data, path_label

    def _additional_dependency_root_problems(
        self,
        dependency_roots: ResolvedRootsT,
    ) -> Sequence[str]:
        del dependency_roots
        return ()

    def seed_repo_root_for_spec(
        self,
        spec: DependencyRootSpec,
        repo_root: Path | None = None,
    ) -> Path:
        root = self._repo_root(repo_root)
        return (root / "build" / "dependency_seed_repos" / spec.repo_name).resolve()

    def init_seed_repositories(
        self,
        repo_root: Path | None = None,
        *,
        progress: Callable[[str, str, str], None] | None = None,
        quiet: bool = False,
    ) -> tuple[Path, bool, dict[str, str]]:
        root = self._repo_root(repo_root)
        active_path, created = self._manager.ensure_active_lock_file(root)
        lock_data = self._manager.load_lock_file(root)
        self._validate_workflow_lock_data(lock_data, path_label=active_path)
        closure = self._manager.prepare_seed_repository_closure(
            root,
            progress=progress,
            quiet=quiet,
        )
        results = {dependency_name: "ready" for dependency_name in closure.topo_order}
        for summary in self._services.prepare_asset_seeds(root):
            results[f"asset:{summary.asset_name}"] = "ready"
        return active_path.resolve(), created, results

    def resolve_dependency_roots(
        self,
        repo_root: Path | None = None,
        *,
        materialize: bool = False,
        allow_network: bool = False,
        quiet: bool = False,
    ) -> ResolvedRootsT:
        if materialize:
            return self.materialize_dependency_roots(
                repo_root,
                allow_network=allow_network,
                quiet=quiet,
            )
        dependency_roots = self._manager.load_dependency_roots(self._repo_root(repo_root))
        return self._wrap_dependency_roots(dependency_roots)

    def resolve_source_roots(
        self,
        repo_root: Path | None = None,
        *,
        materialize: bool = False,
        allow_network: bool = False,
        quiet: bool = False,
    ) -> ResolvedRootsT:
        return self.resolve_dependency_roots(
            repo_root,
            materialize=materialize,
            allow_network=allow_network,
            quiet=quiet,
        )

    def load_lock_file(self, repo_root: Path | None = None) -> dict[str, Any]:
        return self._manager.load_lock_file(self._repo_root(repo_root))

    def materialize_dependency_roots(
        self,
        repo_root: Path | None = None,
        *,
        allow_network: bool = False,
        quiet: bool = False,
    ) -> ResolvedRootsT:
        dependency_roots = self._manager.materialize_dependency_roots(
            self._repo_root(repo_root),
            allow_network=allow_network,
            quiet=quiet,
        )
        if not allow_network:
            self._services.require_asset_seeds(dependency_roots.repo_root)
        return self._wrap_dependency_roots(dependency_roots)

    def materialize_source_roots(
        self,
        repo_root: Path | None = None,
        *,
        allow_network: bool = False,
        quiet: bool = False,
    ) -> ResolvedRootsT:
        return self.materialize_dependency_roots(
            repo_root,
            allow_network=allow_network,
            quiet=quiet,
        )

    def verify_dependency_roots(self, dependency_roots: ResolvedRootsT) -> list[str]:
        problems = self._manager.validate_dependency_roots(dependency_roots.dependency_roots)
        problems.extend(self._additional_dependency_root_problems(dependency_roots))
        return problems

    def verify_source_roots(self, source_roots: ResolvedRootsT) -> list[str]:
        return self.verify_dependency_roots(source_roots)

    def require_dependency_roots(
        self,
        repo_root: Path | None = None,
        *,
        materialize: bool = False,
        allow_network: bool = False,
        quiet: bool = False,
        missing_roots_hint: str | None = None,
    ) -> ResolvedRootsT:
        dependency_roots = self.resolve_dependency_roots(
            repo_root,
            materialize=materialize,
            allow_network=allow_network,
            quiet=quiet,
        )
        problems = self.verify_dependency_roots(dependency_roots)
        if problems:
            details = "\n".join(f"- {problem}" for problem in problems)
            hint = missing_roots_hint or "Run `python3 configs/source_roots.py materialize`."
            raise FileNotFoundError(
                "Workspace source roots are not ready:\n" f"{details}\n" f"{hint}"
            )
        return dependency_roots

    def dependency_resolutions(
        self,
        dependency_roots: ResolvedRootsT,
    ) -> tuple[Any, ...]:
        return tuple(self._manager.describe_dependency_roots(dependency_roots.dependency_roots))

    def pin_dependency_ref(
        self,
        dependency_name: str,
        ref: str,
        repo_root: Path | None = None,
        *,
        allow_fetch: bool = False,
    ) -> str:
        return self._manager.pin_dependency_ref(
            dependency_name,
            ref,
            repo_root=self._repo_root(repo_root),
            allow_fetch=allow_fetch,
        )


__all__ = (
    "DependencyRootWorkflowFacade",
    "DependencyRootWorkflowServices",
)
