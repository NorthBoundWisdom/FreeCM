"""Dependency graph, audit, and policy report helpers."""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from .dependency_conflicts import DependencyConflictError
except ImportError:  # pragma: no cover - supports direct script execution.
    from dependency_conflicts import DependencyConflictError


@dataclass(frozen=True)
class DependencyPolicyViolation:
    code: str
    dependency_name: str | None
    message: str

    def as_json_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "dependencyName": self.dependency_name,
            "message": self.message,
        }


def dependency_report_record(
    manager: Any,
    dependency: Any,
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
    effective_mode = manager._effective_mode_for_dependency(
        repo_root,
        lock_data,
        mode,
        dependency,
    )
    return {
        "dependencyName": dependency.dependency_name,
        "repoName": dependency.repo_name,
        "remote": dependency.remote,
        "commit": dependency.commit,
        "mode": effective_mode,
        "direct": direct,
        "parents": list(parents),
        "children": list(children),
        "path": str(path) if path is not None else str(
            manager._concrete_dependency_root_for(repo_root, dependency, lock_data, mode)
        ),
        "seedPath": str(seed_path) if seed_path is not None else str(
            manager._seed_repo_root(repo_root, dependency.repo_name)
        ),
    }


def direct_dependency_records_for_policy(
    manager: Any,
    repo_root: Path,
    lock_data: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    mode = manager._resolve_mode(lock_data)
    return tuple(
        dependency_report_record(
            manager,
            dependency,
            repo_root=repo_root,
            lock_data=lock_data,
            mode=mode,
            direct=True,
        )
        for dependency in manager._root_dependency_specs_from_lock(lock_data)
    )


def dependency_records_for_roots(
    manager: Any,
    dependency_roots: Any,
) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for dependency_name in dependency_roots.closure_order:
        dependency = dependency_roots.dependency_pin_for(dependency_name)
        records.append(
            dependency_report_record(
                manager,
                dependency,
                repo_root=dependency_roots.repo_root,
                lock_data=dependency_roots.lock_data,
                mode=dependency_roots.mode,
                direct=dependency_roots.is_direct_dependency(dependency_name),
                parents=dependency_roots.dependency_parents_for(dependency_name),
                children=dependency_roots.dependency_names_by_parent.get(dependency_name, ()),
                path=dependency_roots.dependency_root_for(dependency_name),
                seed_path=dependency_roots.seed_repository_for(dependency_name),
            )
        )
    return tuple(records)


def policy_violations_for_records(
    policy_data: dict[str, Any],
    dependency_records: Iterable[dict[str, Any]],
) -> tuple[DependencyPolicyViolation, ...]:
    violations: list[DependencyPolicyViolation] = []
    allowed_remotes = tuple(str(entry) for entry in policy_data.get("allowedRemotes", ()))
    dependency_policies = policy_data.get("dependencyPolicies", {})
    if not isinstance(dependency_policies, dict):
        dependency_policies = {}
    dependency_catalog = policy_data.get("dependencyCatalog", {})
    if not isinstance(dependency_catalog, dict):
        dependency_catalog = {}

    for record in dependency_records:
        dependency_name = str(record["dependencyName"])
        remote = str(record["remote"])
        mode = str(record["mode"])
        dependency_policy = dependency_policies.get(dependency_name, {})
        if not isinstance(dependency_policy, dict):
            dependency_policy = {}
        catalog_entry = dependency_catalog.get(dependency_name, {})
        if not isinstance(catalog_entry, dict):
            catalog_entry = {}

        if allowed_remotes and not any(
            fnmatch.fnmatchcase(remote, pattern)
            for pattern in allowed_remotes
        ):
            violations.append(
                DependencyPolicyViolation(
                    code="remote-not-allowed",
                    dependency_name=dependency_name,
                    message=f"{dependency_name} remote is not allowed by policy: {remote}",
                )
            )
        if dependency_policy.get("pinRequired") is True and mode != "pinned":
            violations.append(
                DependencyPolicyViolation(
                    code="pin-required",
                    dependency_name=dependency_name,
                    message=f"{dependency_name} must use pinned mode, got {mode}",
                )
            )
        if dependency_policy.get("manualAllowed") is False and mode == "manual":
            violations.append(
                DependencyPolicyViolation(
                    code="manual-not-allowed",
                    dependency_name=dependency_name,
                    message=f"{dependency_name} may not use manual mode",
                )
            )
        if dependency_policy.get("latestAllowed") is False and mode == "latest":
            violations.append(
                DependencyPolicyViolation(
                    code="latest-not-allowed",
                    dependency_name=dependency_name,
                    message=f"{dependency_name} may not use latest mode",
                )
            )
        license_allowlist = dependency_policy.get("licenseAllowlist")
        catalog_license = catalog_entry.get("license")
        if (
            isinstance(license_allowlist, list)
            and isinstance(catalog_license, str)
            and catalog_license not in license_allowlist
        ):
            violations.append(
                DependencyPolicyViolation(
                    code="license-not-allowed",
                    dependency_name=dependency_name,
                    message=(
                        f"{dependency_name} license is not allowed by policy: "
                        f"{catalog_license}"
                    ),
                )
            )
    return tuple(violations)


def dependency_policy_report(
    manager: Any,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = manager._normalize_repo_root(repo_root)
    lock_data = manager.load_lock_file(repo_root)
    policy_data = manager.load_dependency_policy(repo_root)
    records = direct_dependency_records_for_policy(manager, repo_root, lock_data)
    violations = policy_violations_for_records(policy_data, records)
    return {
        "schemaVersion": 1,
        "root": str(repo_root),
        "policyPath": policy_data.get("_path"),
        "dependencyCatalog": policy_data.get("dependencyCatalog", {}),
        "dependencies": list(records),
        "policyViolations": [
            violation.as_json_dict()
            for violation in violations
        ],
    }


def dependency_audit_report(
    manager: Any,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = manager._normalize_repo_root(repo_root)
    policy_data = manager.load_dependency_policy(repo_root)
    conflict = manager.find_dependency_conflict(repo_root)
    if conflict is not None:
        return {
            "schemaVersion": 1,
            "root": str(repo_root),
            "policyPath": policy_data.get("_path"),
            "dependencyCatalog": policy_data.get("dependencyCatalog", {}),
            "dependencies": [],
            "conflicts": [conflict.as_json_dict()],
            "rootOverrideTransitivePinMismatches": [],
            "policyViolations": [],
        }
    try:
        dependency_roots = manager.load_dependency_roots(repo_root)
    except DependencyConflictError as error:
        return {
            "schemaVersion": 1,
            "root": str(repo_root),
            "policyPath": policy_data.get("_path"),
            "dependencyCatalog": policy_data.get("dependencyCatalog", {}),
            "dependencies": [],
            "conflicts": [error.diagnostic.as_json_dict()],
            "rootOverrideTransitivePinMismatches": [],
            "policyViolations": [],
        }
    records = dependency_records_for_roots(manager, dependency_roots)
    violations = policy_violations_for_records(policy_data, records)
    root_override_mismatches = [
        mismatch.as_json_dict()
        for mismatch in dependency_roots.root_override_transitive_pin_mismatches()
    ]
    return {
        "schemaVersion": 1,
        "root": str(repo_root),
        "policyPath": policy_data.get("_path"),
        "dependencyCatalog": policy_data.get("dependencyCatalog", {}),
        "dependencies": list(records),
        "conflicts": [],
        "rootOverrideTransitivePinMismatches": root_override_mismatches,
        "policyViolations": [
            violation.as_json_dict()
            for violation in violations
        ],
    }


def dependency_conflict_report(
    manager: Any,
    dependency_name: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = manager._normalize_repo_root(repo_root)
    conflict = manager.find_dependency_conflict(repo_root)
    if conflict is not None:
        conflict_data = conflict.as_json_dict()
        return {
            "schemaVersion": 1,
            "root": str(repo_root),
            "dependencyName": dependency_name,
            "found": conflict.dependency_name == dependency_name,
            "conflicts": [conflict_data] if conflict.dependency_name == dependency_name else [],
        }
    try:
        manager.load_dependency_roots(repo_root)
    except DependencyConflictError as error:
        conflict = error.diagnostic.as_json_dict()
        return {
            "schemaVersion": 1,
            "root": str(repo_root),
            "dependencyName": dependency_name,
            "found": conflict["dependencyName"] == dependency_name,
            "conflicts": [conflict] if conflict["dependencyName"] == dependency_name else [],
        }
    return {
        "schemaVersion": 1,
        "root": str(repo_root),
        "dependencyName": dependency_name,
        "found": False,
        "conflicts": [],
    }


def dependency_graph_report(
    manager: Any,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = manager._normalize_repo_root(repo_root)
    dependency_roots = manager.load_dependency_roots(repo_root)
    records = dependency_records_for_roots(manager, dependency_roots)
    return {
        "schemaVersion": 1,
        "root": str(repo_root),
        "dependencies": list(records),
        "edges": [
            {
                "from": parent_name,
                "to": child_name,
            }
            for parent_name, child_names in dependency_roots.dependency_names_by_parent.items()
            for child_name in child_names
        ],
    }


def dependency_graph_dot(
    manager: Any,
    repo_root: Path | None = None,
) -> str:
    report = dependency_graph_report(manager, repo_root)
    lines = ["digraph freecm_dependencies {"]
    for dependency in report["dependencies"]:
        dependency_name = str(dependency["dependencyName"])
        repo_name = str(dependency["repoName"])
        label = dependency_name if dependency_name == repo_name else f"{dependency_name}\\n{repo_name}"
        lines.append(f"  {json.dumps(dependency_name)} [label={json.dumps(label)}];")
    for edge in report["edges"]:
        lines.append(f"  {json.dumps(edge['from'])} -> {json.dumps(edge['to'])};")
    lines.append("}")
    return "\n".join(lines)
