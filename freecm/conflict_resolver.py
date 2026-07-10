# Internal: dependency closure conflict resolution for DependencyRootManager.

from __future__ import annotations

from collections import deque
from pathlib import Path

from .dependency_conflicts import (
    DependencyConflictDiagnostic,
    DependencyConflictError,
    DependencyConflictSide,
)
from .dependency_manager_contract import DependencyManagerContract
from .dependency_models import DependencyPin
from .git_repositories import git_is_work_tree


class DependencyConflictResolverMixin(DependencyManagerContract):

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

    def find_dependency_conflict(
        self,
        repo_root: Path | None = None,
    ) -> DependencyConflictDiagnostic | None:
        repo_root = self._normalize_repo_root(repo_root)
        lock_data = self.load_lock_file(repo_root)
        mode = self._resolve_mode(lock_data)
        dependency_pins_by_name: dict[str, DependencyPin] = {}
        queue = deque(self._root_dependency_specs_from_lock(lock_data))
        processed: set[tuple[str, str, str]] = set()

        while queue:
            spec = queue.popleft()
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
                queue.extend(
                    self._load_nested_dependency_specs_from_locked_commit(seed_root, merged)
                )
        return None


__all__ = ("DependencyConflictResolverMixin",)
