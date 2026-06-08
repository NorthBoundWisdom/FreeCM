"""Dependency policy loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
        "remoteAliases": {},
        "dependencyPolicies": {},
        "violationSeverities": {},
        "conflictPolicy": {},
        "dependencyCatalog": {},
        "signaturePolicy": {},
        "refPolicy": {},
        "sbomPolicy": {},
        "licensePolicy": {},
        "ownerApprovalPolicy": {},
        "vulnerabilityPolicy": {},
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
        remote_aliases = data.get("remoteAliases", {})
        if not isinstance(remote_aliases, dict) or not all(
            isinstance(key, str)
            and key.strip()
            and isinstance(value, str)
            and value.strip()
            for key, value in remote_aliases.items()
        ):
            raise ValueError(f"Invalid remoteAliases in {path}; expected non-empty string map")
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
            license_allowlist = dependency_policy.get("licenseAllowlist")
            if license_allowlist is not None and (
                not isinstance(license_allowlist, list)
                or not all(isinstance(entry, str) and entry.strip() for entry in license_allowlist)
            ):
                raise ValueError(
                    f"Invalid dependencyPolicies.{dependency_name}.licenseAllowlist in {path}; expected non-empty string array"
                )
        violation_severities = data.get("violationSeverities", {})
        if not isinstance(violation_severities, dict):
            raise ValueError(f"Invalid violationSeverities in {path}; expected object")
        for violation_code, severity in violation_severities.items():
            if not isinstance(violation_code, str) or not violation_code.strip():
                raise ValueError(f"Invalid violationSeverities key in {path}; expected non-empty string")
            if severity not in {"error", "warning"}:
                raise ValueError(
                    f"Invalid violationSeverities.{violation_code} in {path}; expected error or warning"
                )
        conflict_policy = data.get("conflictPolicy", {})
        if not isinstance(conflict_policy, dict):
            raise ValueError(f"Invalid conflictPolicy in {path}; expected object")
        reserved_policies: dict[str, dict[str, Any]] = {}
        for field_name in (
            "signaturePolicy",
            "refPolicy",
            "sbomPolicy",
            "licensePolicy",
            "ownerApprovalPolicy",
            "vulnerabilityPolicy",
        ):
            value = data.get(field_name, {})
            if not isinstance(value, dict):
                raise ValueError(f"Invalid {field_name} in {path}; expected object")
            reserved_policies[field_name] = value
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
            "normalizedAllowedRemotes": tuple(normalize_remote_url(entry) for entry in allowed_remotes),
            "remoteAliases": {
                normalize_remote_url(key): normalize_remote_url(value)
                for key, value in remote_aliases.items()
            },
            "dependencyPolicies": dependency_policies,
            "violationSeverities": violation_severities,
            "conflictPolicy": conflict_policy,
            "dependencyCatalog": dependency_catalog,
            **reserved_policies,
            "_path": str(path),
        }
    except LockfileValidationError:
        raise
    except ValueError as error:
        raise LockfileValidationError(str(error)) from error


def normalize_remote_url(remote: str) -> str:
    value = remote.strip()
    if value.startswith("git@"):
        host_path = value.removeprefix("git@")
        if ":" in host_path:
            host, repo_path = host_path.split(":", 1)
            return _normalized_host_path(host, repo_path)

    split = urlsplit(value)
    if split.scheme in {"http", "https", "ssh", "git"} and split.netloc:
        repo_path = split.path.lstrip("/")
        username, _, host = split.netloc.rpartition("@")
        del username
        return _normalized_host_path(host or split.netloc, repo_path)

    return value.removesuffix("/")


def canonical_policy_remote(remote: str, policy_data: dict[str, Any]) -> str:
    normalized = normalize_remote_url(remote)
    aliases = policy_data.get("remoteAliases", {})
    if isinstance(aliases, dict):
        return str(aliases.get(normalized, normalized))
    return normalized


def _normalized_host_path(host: str, repo_path: str) -> str:
    normalized_path = repo_path.strip("/").removesuffix(".git")
    return f"{host.lower()}/{normalized_path}"
