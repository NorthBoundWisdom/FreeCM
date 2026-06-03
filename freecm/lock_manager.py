# Internal: dependency lock file management for DependencyRootManager.

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

try:
    from .atomic_write import atomic_write_json, atomic_write_text
    from . import dependency_policy
    from .dependency_lock import (
        VALID_MODES,
        load_dependency_lock_data as _load_dependency_lock_data,
    )
except ImportError:  # pragma: no cover - supports direct script execution.
    from atomic_write import atomic_write_json, atomic_write_text
    import dependency_policy
    from dependency_lock import (
        VALID_MODES,
        load_dependency_lock_data as _load_dependency_lock_data,
    )

class DependencyLockManagerMixin:

    def _lock_file_path(self, repo_root: Path) -> Path:
        return repo_root / "source_roots.lock.jsonc"

    def _lock_template_path(self, repo_root: Path) -> Path:
        return repo_root / "source_roots.lock.jsonc.in"

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
            raise ValueError(
                f"Invalid depsMode {deps_mode!r}; expected one of {VALID_MODES}"
            )
        return deps_mode

__all__ = ("DependencyLockManagerMixin",)
