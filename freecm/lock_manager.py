# Internal: dependency lock file management for DependencyRootManager.

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import dependency_policy
from .atomic_write import atomic_write_json, atomic_write_text
from .dependency_lock import ACTIVE_LOCK_FILE_NAME, TEMPLATE_LOCK_FILE_NAME, VALID_MODES
from .dependency_lock import load_dependency_lock_data as _load_dependency_lock_data
from .dependency_manager_contract import DependencyManagerContract
from .dependency_models import DependencyCommitChange, dependency_commit_changes
from .workspace_lock import workspace_mutation_lock


class DependencyLockManagerMixin(DependencyManagerContract):

    def _lock_file_path(self, repo_root: Path) -> Path:
        return repo_root / ACTIVE_LOCK_FILE_NAME

    def _lock_template_path(self, repo_root: Path) -> Path:
        return repo_root / TEMPLATE_LOCK_FILE_NAME

    def _policy_file_path(self, repo_root: Path) -> Path:
        return repo_root / "configs" / "freecm_policy.jsonc"

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

    def refresh_pinned_lock(
        self,
        repo_root: Path | None = None,
    ) -> tuple[DependencyCommitChange, ...]:
        repo_root = self._normalize_repo_root(repo_root)
        with workspace_mutation_lock(repo_root):
            active_lock_data = self.load_lock_file(repo_root)
            template_path = self._lock_template_path(repo_root)
            if not template_path.is_file():
                raise FileNotFoundError(f"Missing source-roots lock template: {template_path}")
            template_lock_data = self.load_dependency_lock_data(
                template_path,
                expected_dependency_names=self.direct_dependency_names,
            )

            active_mode = self._resolve_mode(active_lock_data)
            if active_mode != "pinned":
                raise ValueError(
                    "--refreshpin requires the active lock to use depsMode='pinned'; "
                    f"active lock uses {active_mode!r}"
                )
            template_mode = self._resolve_mode(template_lock_data)
            if template_mode != "pinned":
                raise ValueError(
                    "--refreshpin requires the lock template to use depsMode='pinned'; "
                    f"template uses {template_mode!r}"
                )

            changes = dependency_commit_changes(
                active_lock_data,
                template_lock_data,
                self.direct_dependency_names,
            )
            if not changes:
                return changes

            for dependency_name in self.direct_dependency_names:
                active_lock_data["dependencies"][dependency_name]["commit"] = template_lock_data[
                    "dependencies"
                ][dependency_name]["commit"]
            self._write_lock_file(repo_root, active_lock_data)
            return changes

    def set_latest_mode(self, repo_root: Path | None = None) -> bool:
        repo_root = self._normalize_repo_root(repo_root)
        with workspace_mutation_lock(repo_root):
            active_lock_data = self.load_lock_file(repo_root)
            if self._resolve_mode(active_lock_data) == "latest":
                return False
            active_lock_data["depsMode"] = "latest"
            self._write_lock_file(repo_root, active_lock_data)
            return True

    def load_dependency_policy(self, repo_root: Path | None = None) -> dict[str, Any]:
        repo_root = self._normalize_repo_root(repo_root)
        return dependency_policy.load_dependency_policy(self._policy_file_path(repo_root))

    def _write_lock_file(self, repo_root: Path, data: dict[str, Any]) -> None:
        atomic_write_json(self._lock_file_path(repo_root), data)

    def ensure_active_lock_file(self, repo_root: Path | None = None) -> tuple[Path, bool]:
        repo_root = self._normalize_repo_root(repo_root)
        lock_path = self._lock_file_path(repo_root)
        created = False
        if not lock_path.exists():
            template_path = self._lock_template_path(repo_root)
            if not template_path.is_file():
                raise FileNotFoundError(f"Missing source-roots lock template: {template_path}")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(lock_path, template_path.read_text(encoding="utf-8"))
            created = True
        if not lock_path.is_file():
            raise FileExistsError(f"source_roots lock path is not a file: {lock_path}")
        return lock_path.resolve(), created

    def _resolve_mode(self, lock_data: dict[str, Any]) -> str:
        deps_mode = str(lock_data["depsMode"])
        if deps_mode not in VALID_MODES:
            raise ValueError(f"Invalid depsMode {deps_mode!r}; expected one of {VALID_MODES}")
        return deps_mode


__all__ = ("DependencyLockManagerMixin",)
