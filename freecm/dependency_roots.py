# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m freecm.dependency_roots --help
#   Library: from freecm.dependency_roots import bind_dependency_root_workflow

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, MutableMapping

try:
    from .errors import (
        LockfileValidationError as LockfileValidationError,
        MaterializationError,
        SeedRepositoryError,
    )
    from .dependency_names import validate_safe_dependency_path_name
    from .dependency_lock import (
        DEFAULT_REQUIRED_RELATIVE_PATHS as DEFAULT_REQUIRED_RELATIVE_PATHS,
        DEPENDENCY_LOCK_SCHEMA_VERSION,
        VALID_MODES,
    )
    from .dependency_models import (
        DependencyClosure,
        DependencyCommitChange as DependencyCommitChange,
        DependencyDeclaration,
        DependencyPin,
        DependencyRootConfig,
        DependencyRootSpec,
        DependencyRootSummary,
        ResolvedDependencyRoots,
        SeedRepoPreflightProblem,
        dependency_commit_changes as dependency_commit_changes,
        manual_root_override_path,
    )
    from .dependency_conflicts import (
        DependencyConflictDiagnostic,
        DependencyConflictError,
        DependencyConflictSide,
    )
    from .dependency_lock import (
        load_dependency_lock_data as _load_dependency_lock_data,
        validate_dependency_lock_data as _validate_dependency_lock_data,
    )
    from . import dependency_policy
    from . import dependency_reports
    from .jsonc import (
        loads_jsonc,
        strip_jsonc_comments as strip_jsonc_comments,
        strip_jsonc_trailing_commas as strip_jsonc_trailing_commas,
    )
    from .git_repositories import (
        ensure_worktree_at_commit,
        fetch_remote_refs,
        git,
        git_common_dir,
        git_has_commit,
        git_is_work_tree,
        git_output,
        git_remote_url,
        git_worktree_matches_commit,
        remote_default_head,
        remove_path,
        run,
    )
    from .path_maps import (
        print_environment_map,
    )
except ImportError:  # pragma: no cover - supports direct script execution.
    from errors import (
        LockfileValidationError as LockfileValidationError,
        MaterializationError,
        SeedRepositoryError,
    )
    from dependency_names import validate_safe_dependency_path_name
    from dependency_lock import (
        DEFAULT_REQUIRED_RELATIVE_PATHS as DEFAULT_REQUIRED_RELATIVE_PATHS,
        DEPENDENCY_LOCK_SCHEMA_VERSION,
        VALID_MODES,
    )
    from dependency_models import (
        DependencyClosure,
        DependencyCommitChange as DependencyCommitChange,
        DependencyDeclaration,
        DependencyPin,
        DependencyRootConfig,
        DependencyRootSpec,
        DependencyRootSummary,
        ResolvedDependencyRoots,
        SeedRepoPreflightProblem,
        dependency_commit_changes as dependency_commit_changes,
        manual_root_override_path,
    )
    from dependency_conflicts import (
        DependencyConflictDiagnostic,
        DependencyConflictError,
        DependencyConflictSide,
    )
    from dependency_lock import (
        load_dependency_lock_data as _load_dependency_lock_data,
        validate_dependency_lock_data as _validate_dependency_lock_data,
    )
    import dependency_policy
    import dependency_reports
    from jsonc import (
        loads_jsonc,
        strip_jsonc_comments as strip_jsonc_comments,
        strip_jsonc_trailing_commas as strip_jsonc_trailing_commas,
    )
    from git_repositories import (
        ensure_worktree_at_commit,
        fetch_remote_refs,
        git,
        git_common_dir,
        git_has_commit,
        git_is_work_tree,
        git_output,
        git_remote_url,
        git_worktree_matches_commit,
        remote_default_head,
        remove_path,
        run,
    )
    from path_maps import (
        print_environment_map,
    )

def _validate_safe_dependency_path_name(name: str, *, label: str, path_label: str) -> None:
    if not isinstance(name, str) or not name:
        raise ValueError(f"Invalid {label} in {path_label}; expected non-empty string")
    validate_safe_dependency_path_name(name, label=label, path_label=path_label)


def _managed_child_path(parent: Path, child_name: str, *, label: str) -> Path:
    _validate_safe_dependency_path_name(child_name, label=label, path_label="managed dependency roots")
    parent = parent.resolve()
    child = (parent / child_name).resolve()
    try:
        child.relative_to(parent)
    except ValueError as exc:
        raise ValueError(f"Invalid {label} {child_name!r}; resolved outside managed directory") from exc
    return child


class DependencyRootManager:
    def __init__(self, config: DependencyRootConfig):
        self.config = config
        self.repo_root = config.repo_root.resolve()
        self.dependency_root_specs = config.dependency_root_specs
        for spec in self.dependency_root_specs:
            _validate_safe_dependency_path_name(
                spec.dependency_name,
                label="dependency name",
                path_label=f"{config.repo_display_name} dependency specs",
            )
            _validate_safe_dependency_path_name(
                spec.repo_name,
                label="repository name",
                path_label=f"{config.repo_display_name} dependency specs",
            )
        self.direct_dependency_names = tuple(
            spec.dependency_name for spec in self.dependency_root_specs
        )
        self.spec_by_dependency_name = {
            spec.dependency_name: spec for spec in self.dependency_root_specs
        }

    def _seed_repo_root(self, repo_root: Path, repo_name: str) -> Path:
        return _managed_child_path(
            repo_root / "build" / "dependency_seed_repos",
            repo_name,
            label="repository name",
        )

    def _lock_file_path(self, repo_root: Path) -> Path:
        return repo_root / "source_roots.lock.jsonc"

    def _lock_template_path(self, repo_root: Path) -> Path:
        return repo_root / "source_roots.lock.jsonc.in"

    def _nested_lock_template_path(self, dependency_root: Path) -> Path:
        return dependency_root / "source_roots.lock.jsonc.in"

    def _policy_file_path(self, repo_root: Path) -> Path:
        return repo_root / "configs" / "freecm_policy.jsonc"

    def _normalize_repo_root(self, repo_root: Path | None) -> Path:
        return repo_root.resolve() if repo_root else self.repo_root

    def load_dependency_lock_data(
        self,
        path: Path,
        *,
        expected_dependency_names: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        return _load_dependency_lock_data(
            path,
            expected_dependency_names=expected_dependency_names,
        )

    def load_lock_file(self, repo_root: Path | None = None) -> dict[str, Any]:
        repo_root = self._normalize_repo_root(repo_root)
        path = self._lock_file_path(repo_root)
        if not path.is_file():
            raise FileNotFoundError(
                f"Missing active dependency-roots lock file: {path}\n"
                "Run `python3 configs/source_root_workflow.py --init` first."
            )
        return self.load_dependency_lock_data(
            path,
            expected_dependency_names=self.direct_dependency_names,
        )

    def load_dependency_policy(self, repo_root: Path | None = None) -> dict[str, Any]:
        repo_root = self._normalize_repo_root(repo_root)
        return dependency_policy.load_dependency_policy(self._policy_file_path(repo_root))

    def _write_lock_file(self, repo_root: Path, data: dict[str, Any]) -> None:
        self._lock_file_path(repo_root).write_text(
            json.dumps(data, indent=2) + "\n",
            encoding="utf-8",
        )

    def ensure_active_lock_file(self, repo_root: Path | None = None) -> tuple[Path, bool]:
        repo_root = self._normalize_repo_root(repo_root)
        lock_path = self._lock_file_path(repo_root)
        created = False
        if not lock_path.exists():
            template_path = self._lock_template_path(repo_root)
            if not template_path.is_file():
                raise FileNotFoundError(f"Missing source-roots lock template: {template_path}")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(template_path, lock_path)
            created = True
        if not lock_path.is_file():
            raise FileExistsError(f"source_roots lock path is not a file: {lock_path}")
        return lock_path.resolve(), created

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
        repo_name = (
            dependency_data.get("repoName")
            or (known_spec.repo_name if known_spec is not None else dependency_name)
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

    def _root_dependency_specs_from_lock(self, lock_data: dict[str, Any]) -> tuple[DependencyPin, ...]:
        return tuple(
            self._dependency_checkout_spec_from_entry(
                spec.dependency_name,
                lock_data["dependencies"][spec.dependency_name],
                declared_by_root=True,
                source_label="root lock",
            )
            for spec in self.dependency_root_specs
        )

    def _format_conflict(
        self,
        existing: DependencyPin,
        candidate: DependencyPin,
        *,
        field_name: str,
        existing_value: str,
        candidate_value: str,
    ) -> str:
        return self._conflict_diagnostic(
            existing,
            candidate,
            field_name=field_name,
            existing_value=existing_value,
            candidate_value=candidate_value,
        ).as_text()

    def _conflict_diagnostic(
        self,
        existing: DependencyPin,
        candidate: DependencyPin,
        *,
        field_name: str,
        existing_value: str,
        candidate_value: str,
    ) -> DependencyConflictDiagnostic:
        return DependencyConflictDiagnostic(
            dependency_name=candidate.dependency_name,
            field_name=field_name,
            existing=DependencyConflictSide(
                source=existing.source_label,
                parent_dependency_name=existing.parent_dependency_name,
                value=existing_value,
            ),
            candidate=DependencyConflictSide(
                source=candidate.source_label,
                parent_dependency_name=candidate.parent_dependency_name,
                value=candidate_value,
            ),
        )

    def _merge_dependency_specs(
        self,
        existing: DependencyPin | None,
        candidate: DependencyPin,
    ) -> DependencyPin:
        if existing is None:
            return candidate

        for field_name in ("repo_name", "remote"):
            if getattr(existing, field_name) != getattr(candidate, field_name):
                raise DependencyConflictError(
                    self._conflict_diagnostic(
                        existing,
                        candidate,
                        field_name=field_name,
                        existing_value=str(getattr(existing, field_name)),
                        candidate_value=str(getattr(candidate, field_name)),
                    )
                )

        if existing.commit != candidate.commit:
            if existing.declared_by_root and not candidate.declared_by_root:
                return existing
            if candidate.declared_by_root and not existing.declared_by_root:
                return candidate
            raise DependencyConflictError(
                self._conflict_diagnostic(
                    existing,
                    candidate,
                    field_name="commit",
                    existing_value=existing.commit,
                    candidate_value=candidate.commit,
                )
            )

        if existing.env_key is not None or candidate.env_key is None:
            return existing

        return DependencyPin(
            dependency_name=existing.dependency_name,
            repo_name=existing.repo_name,
            remote=existing.remote,
            commit=existing.commit,
            latest_ref=existing.latest_ref or candidate.latest_ref,
            declared_by_root=existing.declared_by_root or candidate.declared_by_root,
            env_key=candidate.env_key,
            required_relative_paths=candidate.required_relative_paths,
            source_label=candidate.source_label,
            parent_dependency_name=candidate.parent_dependency_name,
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
            f"{dependency.commit}:source_roots.lock.jsonc.in",
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            return ()
        lock_data = _validate_dependency_lock_data(
            loads_jsonc(
                completed.stdout,
                path_label=f"{seed_root}@{dependency.commit}:source_roots.lock.jsonc.in",
            ),
            path_label=f"{seed_root}@{dependency.commit}:source_roots.lock.jsonc.in",
        )
        return tuple(
            self._dependency_checkout_spec_from_entry(
                dependency_name,
                dependency_data,
                declared_by_root=False,
                source_label=f"{seed_root}@{dependency.commit}:source_roots.lock.jsonc.in",
                parent_dependency_name=dependency.dependency_name,
            )
            for dependency_name, dependency_data in lock_data["dependencies"].items()
        )

    def _ensure_seed_repo(self, seed_root: Path, remote: str) -> bool:
        if seed_root.exists():
            if not git_is_work_tree(seed_root):
                remove_path(seed_root)
            else:
                current_remote = git_remote_url(seed_root, "origin")
                if current_remote == remote:
                    return False
                remove_path(seed_root)
        seed_root.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", remote, str(seed_root)])
        return True

    def _remote_default_branch(self, seed_root: Path, remote: str) -> str:
        del seed_root
        return remote_default_head(remote).branch

    def _clone_missing_seed_repo_to_default_branch(
        self,
        seed_root: Path,
        dependency: DependencyPin,
    ) -> None:
        if seed_root.exists():
            return
        seed_root.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", dependency.remote, str(seed_root)])
        default_branch = self._remote_default_branch(seed_root, dependency.remote)
        default_ref = f"origin/{default_branch}"
        git(seed_root, "checkout", "--force", "-B", default_branch, default_ref)
        git(seed_root, "reset", "--hard", default_ref)
        git(seed_root, "clean", "-ffdqx")

    def _sync_seed_repo_to_default_branch(
        self,
        seed_root: Path,
        dependency: DependencyPin,
        *,
        skip_fetch: bool = False,
    ) -> None:
        problems = self._seed_repo_preflight_problems(seed_root, dependency)
        if problems:
            raise SeedRepositoryError(self._format_seed_repo_preflight_error(problems))
        created = self._ensure_seed_repo(seed_root, dependency.remote)
        if not created and not skip_fetch:
            fetch_remote_refs(seed_root, dependency.dependency_name, dependency.remote)
        default_branch = self._remote_default_branch(seed_root, dependency.remote)
        default_ref = f"origin/{default_branch}"
        git(seed_root, "checkout", "--force", "-B", default_branch, default_ref)
        git(seed_root, "reset", "--hard", default_ref)
        git(seed_root, "clean", "-ffdqx")

    def _ensure_existing_seed_repo(self, seed_root: Path, dependency: DependencyPin) -> None:
        if not git_is_work_tree(seed_root):
            raise FileNotFoundError(
                "Missing dependency seed repo path:\n"
                f"- {seed_root}\n"
                "Run `python3 configs/source_root_workflow.py --init` first."
            )
        current_remote = git_remote_url(seed_root, "origin")
        if current_remote != dependency.remote:
            raise FileNotFoundError(
                "Dependency seed repo remote mismatch:\n"
                f"- path: {seed_root}\n"
                f"- expected: {dependency.remote}\n"
                f"- actual: {current_remote or '<missing>'}\n"
                "Fix or move the existing seed repo, then rerun `python3 configs/source_root_workflow.py --init`."
            )

    def _seed_repo_preflight_problems(
        self,
        seed_root: Path,
        dependency: DependencyPin,
    ) -> list[SeedRepoPreflightProblem]:
        if not seed_root.exists():
            return []

        def problem(reason: str) -> SeedRepoPreflightProblem:
            return SeedRepoPreflightProblem(
                dependency_name=dependency.dependency_name,
                seed_root=seed_root,
                reason=reason,
            )

        problems: list[SeedRepoPreflightProblem] = []
        if not seed_root.is_dir():
            return [problem("path exists but is not a directory")]
        if not git_is_work_tree(seed_root):
            return [problem("path is not a git worktree")]

        status = git(
            seed_root,
            "status",
            "--porcelain",
            "-z",
            "--untracked-files=all",
            capture_output=True,
            check=False,
        )
        if status.returncode != 0:
            problems.append(problem("unable to read worktree status"))
        elif status.stdout.strip():
            if self._discard_dirty_submodule_pointers(seed_root, status.stdout):
                status = git(
                    seed_root,
                    "status",
                    "--porcelain",
                    "-z",
                    "--untracked-files=all",
                    capture_output=True,
                    check=False,
                )
            if status.returncode != 0:
                problems.append(problem("unable to read worktree status"))
            elif status.stdout.strip():
                problems.append(problem("worktree is dirty"))
        return problems

    def _discard_dirty_submodule_pointers(self, seed_root: Path, status_output: str) -> bool:
        paths = self._porcelain_status_paths(status_output)
        if not paths or not all(self._is_gitlink_path(seed_root, path) for path in paths):
            return False
        for path in paths:
            updated = git(
                seed_root,
                "submodule",
                "update",
                "--init",
                "--checkout",
                "--",
                path,
                capture_output=True,
                check=False,
            )
            if updated.returncode != 0:
                return False
        return True

    def _porcelain_status_paths(self, status_output: str) -> list[str]:
        paths: list[str] = []
        entries = [entry for entry in status_output.split("\0") if entry]
        index = 0
        while index < len(entries):
            entry = entries[index]
            if len(entry) >= 4:
                paths.append(entry[3:])
                if entry[0] in ("R", "C"):
                    index += 1
            index += 1
        return paths

    def _is_gitlink_path(self, seed_root: Path, relative_path: str) -> bool:
        listed = git(
            seed_root,
            "ls-files",
            "--stage",
            "--",
            relative_path,
            capture_output=True,
            check=False,
        )
        if listed.returncode != 0:
            return False
        return any(line.startswith("160000 ") for line in listed.stdout.splitlines())

    def _format_seed_repo_preflight_error(
        self,
        problems: Iterable[SeedRepoPreflightProblem],
    ) -> str:
        lines = [
            "`--init` cannot safely sync existing dependency seed repos."
        ]
        for problem in problems:
            lines.append(f"- {problem.dependency_name}: {problem.seed_root}")
            lines.append(f"  reason: {problem.reason}")
        return "\n".join(lines)

    def _dependency_closure_seed_signature(
        self,
        repo_root: Path,
        lock_data: dict[str, Any],
        mode: str,
        closure: DependencyClosure,
    ) -> tuple[tuple[str, ...], ...]:
        records: list[tuple[str, ...]] = []
        for dependency_name in sorted(closure.dependency_pins_by_name):
            dependency = closure.dependency_pins_by_name[dependency_name]
            manual_override = self._external_manual_dependency_root_for(
                repo_root,
                lock_data,
                mode,
                dependency,
            )
            if manual_override is not None:
                records.append(
                    (
                        dependency_name,
                        dependency.remote,
                        dependency.commit,
                        "manual",
                        str(manual_override),
                    )
                )
                continue

            seed_root = self._seed_repo_root(repo_root, dependency.repo_name)
            head = ""
            if git_is_work_tree(seed_root):
                completed = git(
                    seed_root,
                    "rev-parse",
                    "HEAD",
                    capture_output=True,
                    check=False,
                )
                if completed.returncode == 0:
                    head = completed.stdout.strip()
            records.append(
                (
                    dependency_name,
                    dependency.remote,
                    dependency.commit,
                    "seed",
                    str(seed_root),
                    head,
                )
            )
        return tuple(records)

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
        visiting: list[str] = []
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

        def visit(spec: DependencyPin) -> None:
            registered_spec = register(spec)
            dependency_name = registered_spec.dependency_name
            if dependency_name in visited:
                return
            if dependency_name in visiting:
                cycle = " -> ".join([*visiting, dependency_name])
                raise ValueError(f"Source-root dependency cycle detected: {cycle}")

            visiting.append(dependency_name)
            dependency_root = prepare_dependency_root(registered_spec)
            child_specs = load_nested_dependency_specs(dependency_root, registered_spec)
            child_names: list[str] = []
            for child_spec in child_specs:
                child_spec = register(child_spec)
                child_names.append(child_spec.dependency_name)
                visit(child_spec)

            dependency_names_by_parent[dependency_name] = tuple(child_names)
            visiting.pop()
            visited.add(dependency_name)
            topo_order.append(dependency_name)

        direct_specs = self._root_dependency_specs_from_lock(lock_data)
        for spec in direct_specs:
            visit(spec)

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

    def _resolve_mode(self, lock_data: dict[str, Any]) -> str:
        deps_mode = str(lock_data["depsMode"])
        if deps_mode not in VALID_MODES:
            raise ValueError(
                f"Invalid depsMode {deps_mode!r}; expected one of {VALID_MODES}"
            )
        return deps_mode

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

    def prepare_seed_repository_closure(
        self,
        repo_root: Path | None = None,
    ) -> DependencyClosure:
        repo_root = self._normalize_repo_root(repo_root)
        lock_data = self.load_lock_file(repo_root)
        mode = self._resolve_mode(lock_data)
        synced_closure_signature: tuple[tuple[str, ...], ...] | None = None
        cloned_seed_roots: set[Path] = set()

        while True:
            problems: list[SeedRepoPreflightProblem] = []
            missing_dependencies: list[DependencyPin] = []
            seen_missing_seed_roots: set[Path] = set()

            def prepare_dependency_root(dependency: DependencyPin) -> Path:
                manual_override = self._external_manual_dependency_root_for(
                    repo_root,
                    lock_data,
                    mode,
                    dependency,
                )
                if manual_override is not None:
                    self._validate_required_paths(manual_override, dependency)
                    return manual_override

                seed_root = self._seed_repo_root(repo_root, dependency.repo_name)
                if seed_root.exists():
                    problems.extend(self._seed_repo_preflight_problems(seed_root, dependency))
                elif seed_root not in seen_missing_seed_roots:
                    missing_dependencies.append(dependency)
                    seen_missing_seed_roots.add(seed_root)
                return seed_root

            def load_nested_dependency_specs(
                dependency_root: Path,
                dependency: DependencyPin,
            ) -> tuple[DependencyPin, ...]:
                try:
                    if (
                        self._external_manual_dependency_root_for(
                            repo_root,
                            lock_data,
                            mode,
                            dependency,
                        )
                        is not None
                    ):
                        return self._load_nested_dependency_specs(
                            dependency_root,
                            parent_dependency_name=dependency.dependency_name,
                        )
                    if not git_is_work_tree(dependency_root):
                        return ()
                    return self._load_nested_dependency_specs(
                        dependency_root,
                        parent_dependency_name=dependency.dependency_name,
                    )
                except ValueError:
                    return ()

            closure = self._discover_dependency_closure(
                lock_data,
                repo_root,
                prepare_dependency_root=prepare_dependency_root,
                load_nested_dependency_specs=load_nested_dependency_specs,
            )

            if problems:
                raise SeedRepositoryError(self._format_seed_repo_preflight_error(problems))
            if not missing_dependencies:
                closure_signature = self._dependency_closure_seed_signature(
                    repo_root,
                    lock_data,
                    mode,
                    closure,
                )
                if closure_signature == synced_closure_signature:
                    return closure
                for dependency in closure.dependency_pins_by_name.values():
                    if (
                        self._external_manual_dependency_root_for(
                            repo_root,
                            lock_data,
                            mode,
                            dependency,
                        )
                        is None
                    ):
                        seed_root = self._seed_repo_root(repo_root, dependency.repo_name)
                        self._sync_seed_repo_to_default_branch(
                            seed_root,
                            dependency,
                            skip_fetch=seed_root in cloned_seed_roots,
                        )
                synced_closure_signature = self._dependency_closure_seed_signature(
                    repo_root,
                    lock_data,
                    mode,
                    closure,
                )
                continue

            for dependency in missing_dependencies:
                seed_root = self._seed_repo_root(repo_root, dependency.repo_name)
                self._clone_missing_seed_repo_to_default_branch(seed_root, dependency)
                cloned_seed_roots.add(seed_root)

    def _prepare_seed_root_for_init(self, repo_root: Path, dependency: DependencyPin) -> Path:
        seed_root = self._seed_repo_root(repo_root, dependency.repo_name)
        self._sync_seed_repo_to_default_branch(seed_root, dependency)
        return seed_root

    def _prepare_seed_root_for_offline(self, repo_root: Path, dependency: DependencyPin) -> Path:
        seed_root = self._seed_repo_root(repo_root, dependency.repo_name)
        self._ensure_existing_seed_repo(seed_root, dependency)
        return seed_root

    def load_dependency_closure(
        self,
        repo_root: Path | None = None,
    ) -> DependencyClosure:
        repo_root = self._normalize_repo_root(repo_root)
        lock_data = self.load_lock_file(repo_root)
        mode = self._resolve_mode(lock_data)

        def prepare_dependency_root(dependency: DependencyPin) -> Path:
            manual_override = self._manual_dependency_root_for(repo_root, lock_data, mode, dependency)
            if manual_override is not None:
                self._validate_required_paths(manual_override, dependency)
                return manual_override
            return self._prepare_seed_root_for_offline(repo_root, dependency)

        def load_nested_specs_for_dependency(
            dependency_root: Path,
            dependency: DependencyPin,
        ) -> tuple[DependencyPin, ...]:
            if self._manual_dependency_root_for(repo_root, lock_data, mode, dependency) is not None:
                return self._load_nested_dependency_specs(
                    dependency_root,
                    parent_dependency_name=dependency.dependency_name,
                )
            return self._load_nested_dependency_specs_from_locked_commit(dependency_root, dependency)

        return self._discover_dependency_closure(
            lock_data,
            repo_root,
            prepare_dependency_root=prepare_dependency_root,
            load_nested_dependency_specs=load_nested_specs_for_dependency,
        )

    def find_dependency_conflict(
        self,
        repo_root: Path | None = None,
    ) -> DependencyConflictDiagnostic | None:
        repo_root = self._normalize_repo_root(repo_root)
        lock_data = self.load_lock_file(repo_root)
        mode = self._resolve_mode(lock_data)
        dependency_pins_by_name: dict[str, DependencyPin] = {}
        queue = list(self._root_dependency_specs_from_lock(lock_data))
        processed: set[tuple[str, str, str]] = set()

        while queue:
            spec = queue.pop(0)
            try:
                merged = self._merge_dependency_specs(
                    dependency_pins_by_name.get(spec.dependency_name),
                    spec,
                )
            except DependencyConflictError as error:
                return error.diagnostic
            dependency_pins_by_name[spec.dependency_name] = merged

            visit_key = (merged.dependency_name, merged.remote, merged.commit)
            if visit_key in processed:
                continue
            processed.add(visit_key)

            manual_override = self._manual_dependency_root_for(repo_root, lock_data, mode, merged)
            if manual_override is not None:
                if not manual_override.is_dir():
                    continue
                queue.extend(
                    self._load_nested_dependency_specs(
                        manual_override,
                        parent_dependency_name=merged.dependency_name,
                    )
                )
                continue

            seed_root = self._seed_repo_root(repo_root, merged.repo_name)
            if git_is_work_tree(seed_root):
                queue.extend(self._load_nested_dependency_specs_from_locked_commit(seed_root, merged))
        return None

    def _load_dependency_closure_for_lock(
        self,
        repo_root: Path,
        lock_data: dict[str, Any],
        *,
        allow_network: bool,
    ) -> DependencyClosure:
        mode = self._resolve_mode(lock_data)

        def prepare_dependency_root(dependency: DependencyPin) -> Path:
            manual_override = self._manual_dependency_root_for(repo_root, lock_data, mode, dependency)
            if manual_override is not None:
                self._validate_required_paths(manual_override, dependency)
                return manual_override
            if allow_network:
                return self._prepare_seed_root_for_init(repo_root, dependency)
            return self._prepare_seed_root_for_offline(repo_root, dependency)

        def load_nested_specs_for_dependency(
            dependency_root: Path,
            dependency: DependencyPin,
        ) -> tuple[DependencyPin, ...]:
            if self._manual_dependency_root_for(repo_root, lock_data, mode, dependency) is not None:
                return self._load_nested_dependency_specs(dependency_root)
            return self._load_nested_dependency_specs_from_locked_commit(dependency_root, dependency)

        return self._discover_dependency_closure(
            lock_data,
            repo_root,
            prepare_dependency_root=prepare_dependency_root,
            load_nested_dependency_specs=load_nested_specs_for_dependency,
        )

    def _resolve_latest_commit(
        self,
        seed_root: Path,
        dependency: DependencyPin,
        *,
        allow_network: bool,
    ) -> str:
        if dependency.latest_ref is None:
            if allow_network:
                self._sync_seed_repo_to_default_branch(seed_root, dependency)
            return git_output(seed_root, "rev-parse", "HEAD")

        if allow_network:
            self._ensure_seed_repo(seed_root, dependency.remote)
            fetch_remote_refs(seed_root, dependency.dependency_name, dependency.remote)
        return self._resolve_ref_to_commit(
            seed_root,
            dependency.dependency_name,
            dependency.remote,
            dependency.latest_ref,
            allow_fetch=False,
        )

    def _refresh_latest_direct_dependency_commits(
        self,
        repo_root: Path,
        lock_data: dict[str, Any],
        *,
        allow_network: bool,
    ) -> bool:
        lock_changed = False
        for dependency in self._root_dependency_specs_from_lock(lock_data):
            seed_root = self._seed_repo_root(repo_root, dependency.repo_name)
            if not allow_network:
                self._ensure_existing_seed_repo(seed_root, dependency)

            commit = self._resolve_latest_commit(seed_root, dependency, allow_network=allow_network)
            if str(lock_data["dependencies"][dependency.dependency_name]["commit"]) == commit:
                continue
            lock_data["dependencies"][dependency.dependency_name]["commit"] = commit
            lock_changed = True
        return lock_changed

    def _managed_dependency_root_for(self, repo_root: Path, dependency: DependencyPin) -> Path:
        return _managed_child_path(
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
        if (
            self._manual_dependency_root_for(repo_root, lock_data, mode, dependency)
            is not None
        ):
            return "manual"
        return "pinned"

    def _ensure_commit_available(
        self,
        seed_root: Path,
        dependency: DependencyPin,
        commit: str,
        *,
        allow_network: bool,
    ) -> None:
        if git_has_commit(seed_root, commit):
            return
        if not allow_network:
            raise MaterializationError(
                f"Missing locked commit {commit} for {dependency.dependency_name} in local seed repo: {seed_root}"
            )
        fetch_remote_refs(seed_root, dependency.dependency_name, dependency.remote)
        if not git_has_commit(seed_root, commit):
            raise MaterializationError(
                f"Unable to resolve locked commit {commit} for {dependency.dependency_name} from {dependency.remote}"
            )

    def materialize_dependency_roots(
        self,
        repo_root: Path | None = None,
        *,
        allow_network: bool = False,
    ) -> ResolvedDependencyRoots:
        repo_root = self._normalize_repo_root(repo_root)
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
            )
        else:
            closure = (
                self.prepare_seed_repository_closure(repo_root)
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
                self._ensure_seed_repo(seed_root, dependency.remote)
            else:
                self._ensure_existing_seed_repo(seed_root, dependency)

            commit = dependency.commit
            _fetch_allowed = allow_network
            self._ensure_commit_available(seed_root, dependency, commit, allow_network=_fetch_allowed)
            target_root = self._managed_dependency_root_for(repo_root, dependency)
            ensure_worktree_at_commit(seed_root, target_root, commit)
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
            template_path = dependency_root / "source_roots.lock.jsonc.in"
            if not template_path.is_file():
                continue
            nested_lock = loads_jsonc(
                template_path.read_text(encoding="utf-8"),
                path_label=str(template_path),
            )
            deps_manual_path = nested_lock.get("depsManualPath", {})
            if not isinstance(deps_manual_path, dict):
                raise ValueError(f"Invalid depsManualPath in nested template: {template_path}")
            nested_lock["depsMode"] = "manual"
            for nested_name in list(deps_manual_path.keys()):
                if nested_name not in dependency_roots.dependency_roots_by_name:
                    raise KeyError(
                        f"Nested workflow dependency {nested_name} not available while preparing {dependency_name}"
                    )
                deps_manual_path[nested_name] = str(dependency_roots.dependency_root_for(nested_name))
            (dependency_root / "source_roots.lock.jsonc").write_text(
                json.dumps(nested_lock, indent=2) + "\n",
                encoding="utf-8",
            )

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
            fetch_remote_refs(seed_root, dependency_name, remote)
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

    def _dependency_report_record(
        self,
        dependency: DependencyPin,
        *,
        repo_root: Path,
        lock_data: dict[str, Any],
        mode: str,
        direct: bool,
        parents: Iterable[str] = (),
        children: Iterable[str] = (),
        path: Path | None = None,
        seed_path: Path | None = None,
    ) -> dict[str, Any]:
        return dependency_reports.dependency_report_record(
            self,
            dependency,
            repo_root=repo_root,
            lock_data=lock_data,
            mode=mode,
            direct=direct,
            parents=parents,
            children=children,
            path=path,
            seed_path=seed_path,
        )

    def _direct_dependency_records_for_policy(
        self,
        repo_root: Path,
        lock_data: dict[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        return dependency_reports.direct_dependency_records_for_policy(
            self,
            repo_root,
            lock_data,
        )

    def _dependency_records_for_roots(
        self,
        dependency_roots: ResolvedDependencyRoots,
    ) -> tuple[dict[str, Any], ...]:
        return dependency_reports.dependency_records_for_roots(self, dependency_roots)

    def _policy_violations_for_records(
        self,
        policy_data: dict[str, Any],
        dependency_records: Iterable[dict[str, Any]],
    ) -> tuple[dependency_reports.DependencyPolicyViolation, ...]:
        return dependency_reports.policy_violations_for_records(policy_data, dependency_records)

    def dependency_policy_report(
        self,
        repo_root: Path | None = None,
    ) -> dict[str, Any]:
        return dependency_reports.dependency_policy_report(self, repo_root)

    def dependency_audit_report(
        self,
        repo_root: Path | None = None,
    ) -> dict[str, Any]:
        return dependency_reports.dependency_audit_report(self, repo_root)

    def dependency_graph_report(
        self,
        repo_root: Path | None = None,
    ) -> dict[str, Any]:
        return dependency_reports.dependency_graph_report(self, repo_root)

    def dependency_graph_dot(
        self,
        repo_root: Path | None = None,
    ) -> str:
        return dependency_reports.dependency_graph_dot(self, repo_root)

    def dependency_conflict_report(
        self,
        dependency_name: str,
        repo_root: Path | None = None,
    ) -> dict[str, Any]:
        return dependency_reports.dependency_conflict_report(self, dependency_name, repo_root)

    def _print_resolve_plain(
        self,
        dependency_roots: ResolvedDependencyRoots,
    ) -> None:
        print(f"mode={dependency_roots.mode}")
        print("closureOrder=" + ",".join(dependency_roots.closure_order))
        for dependency_name in dependency_roots.closure_order:
            record = dependency_roots.dependency_record_for(dependency_name)
            parents = ",".join(record["parents"]) or "<root>"
            children = ",".join(record["children"]) or "-"
            commit = str(record["commit"])
            print(
                f"{dependency_name}: repo={record['repoName']} "
                f"mode={record['mode']} direct={str(record['direct']).lower()} "
                f"commit={commit} path={record['path']} seed={record['seedPath']} "
                f"parents={parents} children={children}"
            )

    def _print_env_map(self, dependency_roots: ResolvedDependencyRoots, output_format: str) -> None:
        print_environment_map(dependency_roots.as_environment_map(), output_format)

    def cmd_verify(self, _: argparse.Namespace) -> int:
        try:
            dependency_roots = self.require_dependency_roots()
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        self._print_env_map(dependency_roots, "plain")
        return 0

    def cmd_show(self, args: argparse.Namespace) -> int:
        try:
            dependency_roots = self.load_dependency_roots()
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(dependency_roots.as_json_dict(), indent=2))
            return 0
        self._print_env_map(dependency_roots, args.format)
        return 0

    def cmd_resolve(self, args: argparse.Namespace) -> int:
        try:
            dependency_roots = self.load_dependency_roots()
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(dependency_roots.as_json_dict(), indent=2))
            return 0
        self._print_resolve_plain(dependency_roots)
        return 0

    def cmd_materialize(self, _: argparse.Namespace) -> int:
        try:
            dependency_roots = self.materialize_dependency_roots(allow_network=False)
        except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
            print(str(error), file=sys.stderr)
            return 1
        self._print_env_map(dependency_roots, "plain")
        return 0

    def cmd_pin(self, args: argparse.Namespace) -> int:
        try:
            commit = self.pin_dependency_ref(args.dep, args.ref)
        except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
            print(str(error), file=sys.stderr)
            return 1
        print(f"{args.dep}={commit}")
        return 0

    def cmd_graph(self, args: argparse.Namespace) -> int:
        try:
            if args.format == "dot":
                print(self.dependency_graph_dot())
                return 0
            print(json.dumps(self.dependency_graph_report(), indent=2))
            return 0
        except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
            print(str(error), file=sys.stderr)
            return 1

    def cmd_audit(self, args: argparse.Namespace) -> int:
        try:
            report = self.dependency_audit_report()
        except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
            print(str(error), file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(report, indent=2))
            return 0 if not report["policyViolations"] and not report["conflicts"] else 1
        if report["conflicts"]:
            for conflict in report["conflicts"]:
                print(conflict["message"], file=sys.stderr)
                for action in conflict.get("suggestedActions", ()):
                    print(f"- {action}", file=sys.stderr)
            return 1
        if report["policyViolations"]:
            for violation in report["policyViolations"]:
                print(violation["message"], file=sys.stderr)
            return 1
        print("audit ok")
        return 0

    def cmd_explain_conflict(self, args: argparse.Namespace) -> int:
        try:
            report = self.dependency_conflict_report(args.dep)
        except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
            print(str(error), file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(report, indent=2))
        elif report["conflicts"]:
            conflict = report["conflicts"][0]
            print(conflict["message"])
            print(
                f"- existing: {conflict['existing']['source'] or '<unknown>'} "
                f"({conflict['existing']['parentDependencyName'] or 'root'}) "
                f"{conflict['existing']['value']!r}"
            )
            print(
                f"- candidate: {conflict['candidate']['source'] or '<unknown>'} "
                f"({conflict['candidate']['parentDependencyName'] or 'root'}) "
                f"{conflict['candidate']['value']!r}"
            )
            print("Suggested actions:")
            for action in conflict.get("suggestedActions", ()):
                print(f"- {action}")
        else:
            print(f"No dependency conflict found for {args.dep}.")
        return 0 if report["found"] else 1

    def cmd_policy_check(self, args: argparse.Namespace) -> int:
        try:
            report = self.dependency_policy_report()
        except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
            print(str(error), file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(report, indent=2))
        elif report["policyViolations"]:
            for violation in report["policyViolations"]:
                print(violation["message"], file=sys.stderr)
        else:
            print("policy ok")
        return 0 if not report["policyViolations"] else 1

    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description=f"Resolve, materialize, and validate {self.config.repo_display_name} dependency roots."
        )
        subparsers = parser.add_subparsers(dest="command", required=True)

        show = subparsers.add_parser("show", help="Print final concrete dependency roots.")
        show.add_argument(
            "--format",
            choices=("plain", "shell", "json"),
            default="plain",
            help="Output format.",
        )
        show.set_defaults(func=self.cmd_show)

        resolve = subparsers.add_parser(
            "resolve",
            help="Print the fully resolved dependency closure.",
        )
        resolve.add_argument(
            "--format",
            choices=("plain", "json"),
            default="plain",
            help="Output format.",
        )
        resolve.set_defaults(func=self.cmd_resolve)

        verify = subparsers.add_parser("verify", help="Validate final concrete dependency roots.")
        verify.set_defaults(func=self.cmd_verify)

        materialize = subparsers.add_parser(
            "materialize",
            help="Materialize concrete roots from local seed repos under build/dependency_source_roots.",
        )
        materialize.set_defaults(func=self.cmd_materialize)

        pin = subparsers.add_parser(
            "pin",
            help="Resolve a dependency ref from the local seed repo and write it to the lock file.",
        )
        pin.add_argument(
            "--dep",
            required=True,
            choices=self.direct_dependency_names,
            help="Dependency name to pin.",
        )
        pin.add_argument("--ref", required=True, help="Git ref to resolve.")
        pin.set_defaults(func=self.cmd_pin)

        graph = subparsers.add_parser(
            "graph",
            help="Print the resolved dependency graph from local seed repos.",
        )
        graph.add_argument(
            "--format",
            choices=("json", "dot"),
            default="json",
            help="Output format.",
        )
        graph.set_defaults(func=self.cmd_graph)

        audit = subparsers.add_parser(
            "audit",
            help="Print a machine-readable dependency audit report.",
        )
        audit.add_argument(
            "--format",
            choices=("plain", "json"),
            default="plain",
            help="Output format.",
        )
        audit.set_defaults(func=self.cmd_audit)

        policy_check = subparsers.add_parser(
            "policy-check",
            help="Validate direct dependency lock entries against configs/freecm_policy.jsonc.",
        )
        policy_check.add_argument(
            "--format",
            choices=("plain", "json"),
            default="plain",
            help="Output format.",
        )
        policy_check.set_defaults(func=self.cmd_policy_check)

        explain_conflict = subparsers.add_parser(
            "explain-conflict",
            help="Explain a dependency closure conflict for a dependency name.",
        )
        explain_conflict.add_argument("dep", help="Dependency name to explain.")
        explain_conflict.add_argument(
            "--format",
            choices=("plain", "json"),
            default="plain",
            help="Output format.",
        )
        explain_conflict.set_defaults(func=self.cmd_explain_conflict)
        return parser

    def main(self) -> int:
        parser = self.build_parser()
        args = parser.parse_args()
        return args.func(args)


def bind_dependency_root_workflow(
    module_globals: MutableMapping[str, Any],
    config: DependencyRootConfig,
) -> DependencyRootManager:
    workflow = DependencyRootManager(config)
    module_globals.update(
        {
            "VALID_MODES": VALID_MODES,
            "DEPENDENCY_LOCK_SCHEMA_VERSION": DEPENDENCY_LOCK_SCHEMA_VERSION,
            "DEFAULT_REQUIRED_RELATIVE_PATHS": config.default_required_relative_paths,
            "DIRECT_DEPENDENCY_NAMES": workflow.direct_dependency_names,
            "SPEC_BY_DEPENDENCY_NAME": workflow.spec_by_dependency_name,
            "DependencyRootSpec": DependencyRootSpec,
            "DependencyRootConfig": DependencyRootConfig,
            "DependencyRootManager": DependencyRootManager,
            "DependencyDeclaration": DependencyDeclaration,
            "DependencyPin": DependencyPin,
            "DependencyClosure": DependencyClosure,
            "ResolvedDependencyRoots": ResolvedDependencyRoots,
            "DependencyRootSummary": DependencyRootSummary,
            "load_dependency_lock_data": workflow.load_dependency_lock_data,
            "load_lock_file": workflow.load_lock_file,
            "load_dependency_policy": workflow.load_dependency_policy,
            "ensure_active_lock_file": workflow.ensure_active_lock_file,
            "prepare_seed_repository_closure": workflow.prepare_seed_repository_closure,
            "load_dependency_closure": workflow.load_dependency_closure,
            "find_dependency_conflict": workflow.find_dependency_conflict,
            "materialize_dependency_roots": workflow.materialize_dependency_roots,
            "load_dependency_roots": workflow.load_dependency_roots,
            "validate_dependency_roots": workflow.validate_dependency_roots,
            "require_dependency_roots": workflow.require_dependency_roots,
            "prepare_nested_dependency_workflows": workflow.prepare_nested_dependency_workflows,
            "describe_dependency_roots": workflow.describe_dependency_roots,
            "pin_dependency_ref": workflow.pin_dependency_ref,
            "dependency_policy_report": workflow.dependency_policy_report,
            "dependency_audit_report": workflow.dependency_audit_report,
            "dependency_graph_report": workflow.dependency_graph_report,
            "dependency_graph_dot": workflow.dependency_graph_dot,
            "dependency_conflict_report": workflow.dependency_conflict_report,
            "build_parser": workflow.build_parser,
            "main": workflow.main,
            "_WORKFLOW": workflow,
            "run": run,
            "git": git,
            "git_output": git_output,
            "git_is_work_tree": git_is_work_tree,
            "git_common_dir": git_common_dir,
            "git_has_commit": git_has_commit,
            "git_worktree_matches_commit": git_worktree_matches_commit,
            "remove_path": remove_path,
            "ensure_worktree_at_commit": ensure_worktree_at_commit,
            "_seed_repo_root": workflow._seed_repo_root,
        }
    )
    return workflow


def _build_unbound_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dependency Roots helpers are bound by a repository config module. "
            "Import bind_dependency_root_workflow from freecm.dependency_roots, "
            "or run configs/source_root_workflow.py --init|--update from a configured workspace."
        )
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"dependency lock schema {DEPENDENCY_LOCK_SCHEMA_VERSION}",
    )
    return parser


def _main_unbound() -> int:
    _build_unbound_parser().parse_args()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main_unbound())
