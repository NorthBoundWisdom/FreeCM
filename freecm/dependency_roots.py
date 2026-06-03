# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m freecm.dependency_roots --help
#   Library: from freecm.dependency_roots import bind_dependency_root_workflow

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable, MutableMapping

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
        dependency_commit_changes as dependency_commit_changes,
    )
    from .dependency_conflicts import (
        DependencyConflictDiagnostic,
        DependencyConflictError,
        DependencyConflictSide,
    )
    from .closure_resolver import DependencyClosureResolverMixin
    from .conflict_resolver import DependencyConflictResolverMixin
    from .dependency_cli import DependencyRootCli
    from .lock_manager import DependencyLockManagerMixin
    from .materializer import DependencyMaterializerMixin
    from .seed_store import DependencySeedStoreMixin
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
        git_worktree_matches_commit,
        remove_path,
        run,
    )
    from .path_maps import print_environment_map
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
        dependency_commit_changes as dependency_commit_changes,
    )
    from dependency_conflicts import (
        DependencyConflictDiagnostic,
        DependencyConflictError,
        DependencyConflictSide,
    )
    from closure_resolver import DependencyClosureResolverMixin
    from conflict_resolver import DependencyConflictResolverMixin
    from dependency_cli import DependencyRootCli
    from lock_manager import DependencyLockManagerMixin
    from materializer import DependencyMaterializerMixin
    from seed_store import DependencySeedStoreMixin
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
        git_worktree_matches_commit,
        remove_path,
        run,
    )
    from path_maps import print_environment_map

def _validate_safe_dependency_path_name(name: str, *, label: str, path_label: str) -> None:
    if not isinstance(name, str) or not name:
        raise ValueError(f"Invalid {label} in {path_label}; expected non-empty string")
    validate_safe_dependency_path_name(name, label=label, path_label=path_label)

def _managed_child_path(parent: Path, child_name: str, *, label: str) -> Path:
    _validate_safe_dependency_path_name(
        child_name,
        label=label,
        path_label="managed dependency roots",
    )
    parent = parent.resolve()
    child = (parent / child_name).resolve()
    try:
        child.relative_to(parent)
    except ValueError as exc:
        raise ValueError(f"Invalid {label} {child_name!r}; resolved outside managed directory") from exc
    return child

class DependencyRootManager(
    DependencyLockManagerMixin,
    DependencyConflictResolverMixin,
    DependencyClosureResolverMixin,
    DependencySeedStoreMixin,
    DependencyMaterializerMixin,
):
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
        self._cli = DependencyRootCli(self)

    def _normalize_repo_root(self, repo_root: Path | None) -> Path:
        return repo_root.resolve() if repo_root else self.repo_root

    def _managed_child_path(self, parent: Path, child_name: str, *, label: str) -> Path:
        return _managed_child_path(parent, child_name, label=label)

    def _fetch_remote_refs(self, seed_root: Path, dependency_name: str, remote: str) -> None:
        fetch_remote_refs(seed_root, dependency_name, remote)

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
        self._cli._print_resolve_plain(dependency_roots)

    def _print_env_map(self, dependency_roots: ResolvedDependencyRoots, output_format: str) -> None:
        print_environment_map(dependency_roots.as_environment_map(), output_format)

    def cmd_verify(self, args: argparse.Namespace) -> int:
        return self._cli.cmd_verify(args)

    def cmd_show(self, args: argparse.Namespace) -> int:
        return self._cli.cmd_show(args)

    def cmd_resolve(self, args: argparse.Namespace) -> int:
        return self._cli.cmd_resolve(args)

    def cmd_materialize(self, args: argparse.Namespace) -> int:
        return self._cli.cmd_materialize(args)

    def cmd_pin(self, args: argparse.Namespace) -> int:
        return self._cli.cmd_pin(args)

    def cmd_graph(self, args: argparse.Namespace) -> int:
        return self._cli.cmd_graph(args)

    def cmd_audit(self, args: argparse.Namespace) -> int:
        return self._cli.cmd_audit(args)

    def cmd_explain_conflict(self, args: argparse.Namespace) -> int:
        return self._cli.cmd_explain_conflict(args)

    def cmd_policy_check(self, args: argparse.Namespace) -> int:
        return self._cli.cmd_policy_check(args)

    def build_parser(self) -> argparse.ArgumentParser:
        return self._cli.build_parser()

    def main(self) -> int:
        return self._cli.main()

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
