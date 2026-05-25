"""Dependency policy loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from .dependency_names import validate_safe_dependency_path_name
    from .errors import LockfileValidationError
    from .jsonc import loads_jsonc
except ImportError:  # pragma: no cover - supports direct script execution.
    from dependency_names import validate_safe_dependency_path_name
    from errors import LockfileValidationError
    from jsonc import loads_jsonc


def default_dependency_policy() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "allowedRemotes": [],
        "dependencyPolicies": {},
        "conflictPolicy": {},
        "dependencyCatalog": {},
        "_path": None,
    }


def load_dependency_policy(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return default_dependency_policy()
    try:
        data = loads_jsonc(path.read_text(encoding="utf-8"), path_label=str(path))
        if not isinstance(data, dict):
            raise ValueError(f"Invalid FreeCM policy file (expected object): {path}")
        if data.get("schemaVersion") != 1:
            raise ValueError(
                f"Unsupported FreeCM policy schemaVersion {data.get('schemaVersion')!r} in {path}"
            )
        allowed_remotes = data.get("allowedRemotes", [])
        if not isinstance(allowed_remotes, list) or not all(
            isinstance(entry, str) and entry.strip() for entry in allowed_remotes
        ):
            raise ValueError(f"Invalid allowedRemotes in {path}; expected non-empty string array")
        dependency_policies = data.get("dependencyPolicies", {})
        if not isinstance(dependency_policies, dict):
            raise ValueError(f"Invalid dependencyPolicies in {path}; expected object")
        for dependency_name, dependency_policy in dependency_policies.items():
            if not isinstance(dependency_name, str):
                raise ValueError(f"Invalid dependencyPolicies key in {path}; expected string")
            validate_safe_dependency_path_name(
                dependency_name,
                label="dependency name",
                path_label=str(path),
            )
            if not isinstance(dependency_policy, dict):
                raise ValueError(
                    f"Invalid dependencyPolicies.{dependency_name} in {path}; expected object"
                )
            for field_name in ("pinRequired", "manualAllowed", "latestAllowed"):
                value = dependency_policy.get(field_name)
                if value is not None and not isinstance(value, bool):
                    raise ValueError(
                        f"Invalid dependencyPolicies.{dependency_name}.{field_name} in {path}; expected boolean"
                    )
            abi_group = dependency_policy.get("abiGroup")
            if abi_group is not None and (
                not isinstance(abi_group, str) or not abi_group.strip()
            ):
                raise ValueError(
                    f"Invalid dependencyPolicies.{dependency_name}.abiGroup in {path}; expected non-empty string"
                )
            license_allowlist = dependency_policy.get("licenseAllowlist")
            if license_allowlist is not None and (
                not isinstance(license_allowlist, list)
                or not all(isinstance(entry, str) and entry.strip() for entry in license_allowlist)
            ):
                raise ValueError(
                    f"Invalid dependencyPolicies.{dependency_name}.licenseAllowlist in {path}; expected non-empty string array"
                )
        conflict_policy = data.get("conflictPolicy", {})
        if not isinstance(conflict_policy, dict):
            raise ValueError(f"Invalid conflictPolicy in {path}; expected object")
        dependency_catalog = data.get("dependencyCatalog", {})
        if not isinstance(dependency_catalog, dict):
            raise ValueError(f"Invalid dependencyCatalog in {path}; expected object")
        for dependency_name, catalog_entry in dependency_catalog.items():
            if not isinstance(dependency_name, str):
                raise ValueError(f"Invalid dependencyCatalog key in {path}; expected string")
            validate_safe_dependency_path_name(
                dependency_name,
                label="dependency name",
                path_label=str(path),
            )
            if not isinstance(catalog_entry, dict):
                raise ValueError(
                    f"Invalid dependencyCatalog.{dependency_name} in {path}; expected object"
                )
            for field_name in ("owner", "tier", "license"):
                value = catalog_entry.get(field_name)
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    raise ValueError(
                        f"Invalid dependencyCatalog.{dependency_name}.{field_name} in {path}; expected non-empty string"
                    )
            approval_required = catalog_entry.get("approvalRequired")
            if approval_required is not None and not isinstance(approval_required, bool):
                raise ValueError(
                    f"Invalid dependencyCatalog.{dependency_name}.approvalRequired in {path}; expected boolean"
                )
        return {
            "schemaVersion": 1,
            "allowedRemotes": tuple(allowed_remotes),
            "dependencyPolicies": dependency_policies,
            "conflictPolicy": conflict_policy,
            "dependencyCatalog": dependency_catalog,
            "_path": str(path),
        }
    except LockfileValidationError:
        raise
    except ValueError as error:
        raise LockfileValidationError(str(error)) from error
