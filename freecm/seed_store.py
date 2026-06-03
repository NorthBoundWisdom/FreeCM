# Internal: dependency seed repository management for DependencyRootManager.

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

try:
    from .errors import SeedRepositoryError
    from .dependency_models import DependencyClosure, DependencyPin, SeedRepoPreflightProblem
    from .git_repositories import (
        fetch_remote_refs,
        git,
        git_is_work_tree,
        git_output,
        git_remote_url,
        remote_default_head,
        remove_path,
        run,
    )
except ImportError:  # pragma: no cover - supports direct script execution.
    from errors import SeedRepositoryError
    from dependency_models import DependencyClosure, DependencyPin, SeedRepoPreflightProblem
    from git_repositories import (
        fetch_remote_refs,
        git,
        git_is_work_tree,
        git_output,
        git_remote_url,
        remote_default_head,
        remove_path,
        run,
    )

class DependencySeedStoreMixin:

    def _seed_repo_root(self, repo_root: Path, repo_name: str) -> Path:
        return self._managed_child_path(
            repo_root / "build" / "dependency_seed_repos",
            repo_name,
            label="repository name",
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
            self._fetch_remote_refs(seed_root, dependency.dependency_name, dependency.remote)
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
            self._fetch_remote_refs(seed_root, dependency.dependency_name, dependency.remote)
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

__all__ = ("DependencySeedStoreMixin",)
