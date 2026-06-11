# Internal: dependency source-root materialization for DependencyRootManager.

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from .atomic_write import atomic_write_json
    from .dependency_models import (
        DependencyPin,
        DependencyRootSummary,
        ResolvedDependencyRoots,
        manual_root_override_path,
    )
    from .errors import MaterializationError
    from .git_repositories import (
        ensure_worktree_at_commit,
        git,
        git_has_commit,
        git_is_work_tree,
        git_output,
    )
    from .jsonc import loads_jsonc
    from .workspace_lock import workspace_mutation_lock
except ImportError:  # pragma: no cover - supports direct script execution.
    from atomic_write import atomic_write_json
    from dependency_models import (
        DependencyPin,
        DependencyRootSummary,
        ResolvedDependencyRoots,
        manual_root_override_path,
    )
    from errors import MaterializationError
    from git_repositories import (
        ensure_worktree_at_commit,
        git,
        git_has_commit,
        git_is_work_tree,
        git_output,
    )
    from jsonc import loads_jsonc
    from workspace_lock import workspace_mutation_lock

if TYPE_CHECKING:
    from .dependency_models import DependencyClosure


def nested_dependency_lock_template_path(dependency_root: Path) -> Path:
    return dependency_root / "source_roots.lock.jsonc.in"


def nested_dependency_lock_file_path(dependency_root: Path) -> Path:
    return dependency_root / "source_roots.lock.jsonc"


def nested_manual_dependency_lock_data(
    template_path: Path,
    dependency_root_for: Callable[[str], Path],
) -> dict[str, Any]:
    nested_lock = loads_jsonc(
        template_path.read_text(encoding="utf-8"),
        path_label=str(template_path),
    )
    deps_manual_path = nested_lock.get("depsManualPath", {})
    if not isinstance(deps_manual_path, dict):
        raise ValueError(f"Invalid depsManualPath in nested template: {template_path}")
    nested_lock["depsMode"] = "manual"
    for nested_name in list(deps_manual_path.keys()):
        deps_manual_path[nested_name] = str(dependency_root_for(str(nested_name)))
    return nested_lock


def write_nested_manual_dependency_lock(
    dependency_root: Path,
    dependency_root_for: Callable[[str], Path],
) -> None:
    atomic_write_json(
        nested_dependency_lock_file_path(dependency_root),
        nested_manual_dependency_lock_data(
            nested_dependency_lock_template_path(dependency_root),
            dependency_root_for,
        ),
    )


class DependencyMaterializerMixin:

    def _manual_dependency_root_for(
        self,
        repo_root: Path,
        lock_data: dict[str, Any],
        mode: str,
        dependency: DependencyPin,
    ) -> Path | None:
        if not dependency.declared_by_root:
            return None
        return manual_root_override_path(
            lock_data,
            dependency.dependency_name,
            mode,
            base_root=self._normalize_repo_root(repo_root),
        )

    def _is_managed_seed_root(
        self,
        repo_root: Path,
        dependency: DependencyPin,
        root: Path | None,
    ) -> bool:
        if root is None:
            return False
        return root == self._seed_repo_root(repo_root, dependency.repo_name)

    def _external_manual_dependency_root_for(
        self,
        repo_root: Path,
        lock_data: dict[str, Any],
        mode: str,
        dependency: DependencyPin,
    ) -> Path | None:
        manual_override = self._manual_dependency_root_for(repo_root, lock_data, mode, dependency)
        if self._is_managed_seed_root(repo_root, dependency, manual_override):
            return None
        return manual_override

    def _validate_required_paths(self, root: Path, dependency: DependencyPin) -> None:
        if not root.is_dir():
            raise FileNotFoundError(f"{dependency.dependency_name} missing directory: {root}")
        for relative_path in dependency.required_relative_paths:
            candidate = root / relative_path
            if not candidate.exists():
                raise FileNotFoundError(
                    f"{dependency.dependency_name} missing required path: {candidate}"
                )

    def _packaged_source_root_metadata_path(self, root: Path) -> Path:
        return root / ".freecm" / "dependency_source_root.json"

    def _packaged_source_root_problem(
        self,
        root: Path,
        dependency: DependencyPin,
    ) -> str | None:
        metadata_path = self._packaged_source_root_metadata_path(root)
        if not metadata_path.is_file():
            return f"{dependency.dependency_name} is not a git checkout: {root}"

        try:
            metadata = loads_jsonc(
                metadata_path.read_text(encoding="utf-8"),
                path_label=str(metadata_path),
            )
        except ValueError as exc:
            return f"{dependency.dependency_name} invalid packaged source root metadata: {exc}"
        if not isinstance(metadata, dict):
            return (
                f"{dependency.dependency_name} invalid packaged source root metadata: "
                f"{metadata_path}"
            )

        expected = {
            "dependencyName": dependency.dependency_name,
            "repoName": dependency.repo_name,
            "remote": dependency.remote,
            "commit": dependency.commit,
        }
        for key, expected_value in expected.items():
            actual_value = metadata.get(key)
            if actual_value != expected_value:
                return (
                    f"{dependency.dependency_name} packaged source root metadata mismatch "
                    f"for {key}: expected {expected_value}, got {actual_value}"
                )
        return None

    def _managed_dependency_root_for(self, repo_root: Path, dependency: DependencyPin) -> Path:
        return self._managed_child_path(
            repo_root / "build" / "dependency_source_roots",
            dependency.repo_name,
            label="repository name",
        )

    def _concrete_dependency_root_for(
        self,
        repo_root: Path,
        dependency: DependencyPin,
        lock_data: dict[str, Any],
        mode: str,
    ) -> Path:
        manual_override = self._manual_dependency_root_for(repo_root, lock_data, mode, dependency)
        if manual_override is not None:
            return manual_override
        return self._managed_dependency_root_for(repo_root, dependency)

    def _dependency_roots_from_state(
        self,
        repo_root: Path,
        lock_data: dict[str, Any],
        mode: str,
        closure: DependencyClosure,
        resolved_commits_by_dependency: dict[str, str] | None = None,
    ) -> ResolvedDependencyRoots:
        dependency_pins_by_name = dict(closure.dependency_pins_by_name)
        seed_repositories_by_dependency: dict[str, Path] = {}
        dependency_roots_by_name: dict[str, Path] = {}
        for dependency_name, dependency in dependency_pins_by_name.items():
            manual_override = self._manual_dependency_root_for(
                repo_root,
                lock_data,
                mode,
                dependency,
            )
            if manual_override is not None:
                seed_repositories_by_dependency[dependency_name] = manual_override
                dependency_roots_by_name[dependency_name] = manual_override
            else:
                seed_repositories_by_dependency[dependency_name] = self._seed_repo_root(
                    repo_root,
                    dependency.repo_name,
                )
                dependency_roots_by_name[dependency_name] = self._managed_dependency_root_for(
                    repo_root,
                    dependency,
                )

        if resolved_commits_by_dependency is None:
            resolved_commits_by_dependency = {
                dependency_name: dependency.commit
                for dependency_name, dependency in dependency_pins_by_name.items()
                if self._manual_dependency_root_for(repo_root, lock_data, mode, dependency) is None
            }

        return ResolvedDependencyRoots(
            mode=mode,
            repo_root=repo_root,
            lock_data=lock_data,
            direct_dependency_names=closure.direct_dependency_names,
            dependency_pins_by_name=dependency_pins_by_name,
            seed_repositories_by_dependency=seed_repositories_by_dependency,
            dependency_roots_by_name=dependency_roots_by_name,
            resolved_commits_by_dependency=resolved_commits_by_dependency,
            dependency_names_by_parent=dict(closure.dependency_names_by_parent),
            dependency_declarations_by_name=dict(closure.dependency_declarations_by_name),
            closure_order=closure.topo_order,
            dependency_root_specs=self.dependency_root_specs,
        )

    def describe_dependency_roots(
        self,
        dependency_roots: ResolvedDependencyRoots,
    ) -> tuple[DependencyRootSummary, ...]:
        resolutions: list[DependencyRootSummary] = []
        for dependency_name in self.direct_dependency_names:
            mode = dependency_roots.mode
            if mode == "manual":
                mode = (
                    "manual"
                    if manual_root_override_path(
                        dependency_roots.lock_data,
                        dependency_name,
                        dependency_roots.mode,
                        base_root=dependency_roots.repo_root,
                    )
                    is not None
                    else "pinned"
                )
            resolutions.append(
                DependencyRootSummary(
                    dependency_name=dependency_name,
                    mode=mode,
                    commit=dependency_roots.resolved_commits_by_dependency.get(dependency_name),
                    path=dependency_roots.dependency_root_for(dependency_name),
                )
            )
        return tuple(resolutions)

    def _effective_mode_for_dependency(
        self,
        repo_root: Path,
        lock_data: dict[str, Any],
        mode: str,
        dependency: DependencyPin,
    ) -> str:
        if mode != "manual":
            return mode
        if self._manual_dependency_root_for(repo_root, lock_data, mode, dependency) is not None:
            return "manual"
        return "pinned"

    def _ensure_commit_available(
        self,
        seed_root: Path,
        dependency: DependencyPin,
        commit: str,
        *,
        allow_network: bool,
        quiet: bool = False,
    ) -> None:
        if git_has_commit(seed_root, commit):
            return
        if not allow_network:
            raise MaterializationError(
                f"Missing locked commit {commit} for {dependency.dependency_name} in local seed repo: {seed_root}"
            )
        self._fetch_remote_refs(
            seed_root,
            dependency.dependency_name,
            dependency.remote,
            quiet=quiet,
        )
        if not git_has_commit(seed_root, commit):
            raise MaterializationError(
                f"Unable to resolve locked commit {commit} for {dependency.dependency_name} from {dependency.remote}"
            )

    def materialize_dependency_roots(
        self,
        repo_root: Path | None = None,
        *,
        allow_network: bool = False,
        quiet: bool = False,
    ) -> ResolvedDependencyRoots:
        repo_root = self._normalize_repo_root(repo_root)
        with workspace_mutation_lock(repo_root):
            return self._materialize_dependency_roots_unlocked(
                repo_root,
                allow_network=allow_network,
                quiet=quiet,
            )

    def _materialize_dependency_roots_unlocked(
        self,
        repo_root: Path,
        *,
        allow_network: bool = False,
        quiet: bool = False,
    ) -> ResolvedDependencyRoots:
        lock_data = self.load_lock_file(repo_root)
        mode = self._resolve_mode(lock_data)

        if mode == "latest":
            if self._refresh_latest_direct_dependency_commits(
                repo_root,
                lock_data,
                allow_network=allow_network,
            ):
                self._write_lock_file(repo_root, lock_data)
            lock_data = self.load_lock_file(repo_root)
            closure = self._load_dependency_closure_for_lock(
                repo_root,
                lock_data,
                allow_network=allow_network,
                quiet=quiet,
            )
        else:
            closure = (
                self._prepare_seed_repository_closure_unlocked(repo_root, quiet=quiet)
                if allow_network
                else self.load_dependency_closure(repo_root)
            )

        resolved_commits_by_dependency: dict[str, str] = {}
        for dependency_name in closure.topo_order:
            dependency = closure.dependency_pins_by_name[dependency_name]
            if self._manual_dependency_root_for(repo_root, lock_data, mode, dependency) is not None:
                continue

            seed_root = self._seed_repo_root(repo_root, dependency.repo_name)
            if allow_network:
                self._ensure_seed_repo(seed_root, dependency.remote, quiet=quiet)
            else:
                self._ensure_existing_seed_repo(seed_root, dependency)

            commit = dependency.commit
            _fetch_allowed = allow_network
            self._ensure_commit_available(
                seed_root,
                dependency,
                commit,
                allow_network=_fetch_allowed,
                quiet=quiet,
            )
            target_root = self._managed_dependency_root_for(repo_root, dependency)
            ensure_worktree_at_commit(seed_root, target_root, commit, quiet=quiet)
            resolved_commits_by_dependency[dependency_name] = commit

        return self._dependency_roots_from_state(
            repo_root,
            lock_data,
            mode,
            closure,
            resolved_commits_by_dependency=resolved_commits_by_dependency,
        )

    def load_dependency_roots(
        self,
        repo_root: Path | None = None,
    ) -> ResolvedDependencyRoots:
        repo_root = self._normalize_repo_root(repo_root)
        lock_data = self.load_lock_file(repo_root)
        mode = self._resolve_mode(lock_data)
        closure = self.load_dependency_closure(repo_root)
        return self._dependency_roots_from_state(repo_root, lock_data, mode, closure)

    def validate_dependency_roots(self, dependency_roots: ResolvedDependencyRoots) -> list[str]:
        problems: list[str] = []
        resolved_commits = dependency_roots.resolved_commits
        for dependency_name in dependency_roots.closure_order:
            dependency = dependency_roots.dependency_pin_for(dependency_name)
            root = dependency_roots.dependency_root_for(dependency_name)
            if not root.is_dir():
                problems.append(f"{dependency_name} missing directory: {root}")
                continue
            for relative_path in dependency.required_relative_paths:
                candidate = root / relative_path
                if not candidate.exists():
                    problems.append(f"{dependency_name} missing required path: {candidate}")
            if not git_is_work_tree(root):
                problem = self._packaged_source_root_problem(root, dependency)
                if problem is not None:
                    problems.append(problem)
                continue
            if dependency_name in resolved_commits:
                expected_commit = resolved_commits[dependency_name]
                actual_commit = git_output(root, "rev-parse", "HEAD")
                if actual_commit != expected_commit:
                    problems.append(
                        f"{dependency_name} checkout commit mismatch: expected {expected_commit}, got {actual_commit}"
                    )
        return problems

    def require_dependency_roots(
        self,
        repo_root: Path | None = None,
    ) -> ResolvedDependencyRoots:
        dependency_roots = self.load_dependency_roots(repo_root=repo_root)
        problems = self.validate_dependency_roots(dependency_roots)
        if problems:
            details = "\n".join(f"- {problem}" for problem in problems)
            raise FileNotFoundError(
                "Workspace dependency roots are not ready:\n"
                f"{details}\n"
                "Run `python3 configs/source_root_workflow.py --update` or "
                "`python3 configs/source_roots.py materialize`."
            )
        return dependency_roots

    def prepare_nested_dependency_workflows(
        self,
        dependency_roots: ResolvedDependencyRoots,
        *,
        repo_root: Path | None = None,
    ) -> None:
        del repo_root
        for dependency_name in dependency_roots.closure_order:
            dependency_root = dependency_roots.dependency_root_for(dependency_name)
            if dependency_root == dependency_roots.seed_repository_for(dependency_name):
                continue
            template_path = nested_dependency_lock_template_path(dependency_root)
            if not template_path.is_file():
                continue

            def nested_root_for(
                nested_name: str,
                *,
                parent_dependency_name: str = dependency_name,
            ) -> Path:
                if nested_name not in dependency_roots.dependency_roots_by_name:
                    raise KeyError(
                        "Nested workflow dependency "
                        f"{nested_name} not available while preparing {parent_dependency_name}"
                    )
                return dependency_roots.dependency_root_for(nested_name)

            write_nested_manual_dependency_lock(dependency_root, nested_root_for)

    def _resolve_ref_to_commit(
        self,
        seed_root: Path,
        dependency_name: str,
        remote: str,
        ref: str,
        *,
        allow_fetch: bool = False,
    ) -> str:
        candidate_refs = [ref, f"refs/tags/{ref}"]
        if not ref.startswith("origin/"):
            candidate_refs.append(f"origin/{ref}")
        for candidate_ref in candidate_refs:
            completed = git(
                seed_root,
                "rev-parse",
                "--verify",
                f"{candidate_ref}^{{commit}}",
                capture_output=True,
                check=False,
            )
            if completed.returncode == 0:
                return completed.stdout.strip()
        if allow_fetch:
            self._fetch_remote_refs(seed_root, dependency_name, remote)
            for candidate_ref in candidate_refs:
                completed = git(
                    seed_root,
                    "rev-parse",
                    "--verify",
                    f"{candidate_ref}^{{commit}}",
                    capture_output=True,
                    check=False,
                )
                if completed.returncode == 0:
                    return completed.stdout.strip()
        raise RuntimeError(f"Unable to resolve ref {ref!r} for {dependency_name} from {remote}")

    def pin_dependency_ref(
        self,
        dependency_name: str,
        ref: str,
        repo_root: Path | None = None,
        *,
        allow_fetch: bool = False,
    ) -> str:
        repo_root = self._normalize_repo_root(repo_root)
        with workspace_mutation_lock(repo_root):
            return self._pin_dependency_ref_unlocked(
                dependency_name,
                ref,
                repo_root,
                allow_fetch=allow_fetch,
            )

    def _pin_dependency_ref_unlocked(
        self,
        dependency_name: str,
        ref: str,
        repo_root: Path,
        *,
        allow_fetch: bool = False,
    ) -> str:
        spec = self.spec_by_dependency_name[dependency_name]
        lock_data = self.load_lock_file(repo_root)
        dependency = lock_data["dependencies"][spec.dependency_name]
        seed_root = self._seed_repo_root(repo_root, spec.repo_name)
        dependency_pin = self._dependency_checkout_spec_from_entry(
            spec.dependency_name,
            dependency,
            declared_by_root=True,
            source_label="root lock",
        )
        if allow_fetch:
            self._ensure_seed_repo(seed_root, str(dependency["remote"]))
        else:
            self._ensure_existing_seed_repo(seed_root, dependency_pin)
        commit = self._resolve_ref_to_commit(
            seed_root,
            spec.dependency_name,
            str(dependency["remote"]),
            ref,
            allow_fetch=allow_fetch,
        )
        dependency["commit"] = commit
        self._write_lock_file(repo_root, lock_data)
        return commit


__all__ = (
    "DependencyMaterializerMixin",
    "nested_dependency_lock_file_path",
    "nested_dependency_lock_template_path",
    "nested_manual_dependency_lock_data",
    "write_nested_manual_dependency_lock",
)
