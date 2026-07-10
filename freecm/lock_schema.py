"""Load and validate the language-neutral lock schema contract resource."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

LOCK_SCHEMA_RESOURCE_NAME = "lock-schema-contract.json"


def _string_list(data: dict[str, Any], field_name: str) -> list[str]:
    value = data.get(field_name)
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and item for item in value)
        or len(value) != len(set(value))
    ):
        raise RuntimeError(
            f"Invalid {LOCK_SCHEMA_RESOURCE_NAME} field {field_name!r}; "
            "expected unique non-empty string array"
        )
    return value


def _string_map(data: dict[str, Any], field_name: str) -> dict[str, str]:
    value = data.get(field_name)
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise RuntimeError(
            f"Invalid {LOCK_SCHEMA_RESOURCE_NAME} field {field_name!r}; expected string map"
        )
    return value


def _load_lock_schema_contract() -> dict[str, Any]:
    resource = files("freecm").joinpath(LOCK_SCHEMA_RESOURCE_NAME)
    try:
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to load {LOCK_SCHEMA_RESOURCE_NAME}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid {LOCK_SCHEMA_RESOURCE_NAME}; expected object")
    if not isinstance(value.get("schemaVersion"), int) or isinstance(
        value.get("schemaVersion"), bool
    ):
        raise RuntimeError(
            f"Invalid {LOCK_SCHEMA_RESOURCE_NAME} field 'schemaVersion'; expected integer"
        )
    for field_name in (
        "activeLockFileName",
        "templateLockFileName",
        "workspaceLockName",
        "safeDependencyNamePattern",
    ):
        if not isinstance(value.get(field_name), str) or not value[field_name]:
            raise RuntimeError(
                f"Invalid {LOCK_SCHEMA_RESOURCE_NAME} field {field_name!r}; expected non-empty string"
            )
    for field_name in (
        "modes",
        "legacyDependencyEntryFields",
        "dependencyEntryFields",
        "requiredDependencyEntryFields",
        "optionalDependencyEntryFields",
    ):
        _string_list(value, field_name)
    for field_name in ("modes", "dependencyEntryFields", "requiredDependencyEntryFields"):
        if not _string_list(value, field_name):
            raise RuntimeError(
                f"Invalid {LOCK_SCHEMA_RESOURCE_NAME} field {field_name!r}; expected non-empty string array"
            )
    for field_name in ("fields", "removedTopLevelFields"):
        _string_map(value, field_name)
    required_field_names = {
        "schemaVersion",
        "depsMode",
        "depsManualPath",
        "dependencies",
        "repoName",
        "remote",
        "commit",
        "latestRef",
    }
    missing_field_names = required_field_names - set(_string_map(value, "fields"))
    if missing_field_names:
        raise RuntimeError(
            f"Invalid {LOCK_SCHEMA_RESOURCE_NAME} field 'fields'; missing keys: "
            f"{', '.join(sorted(missing_field_names))}"
        )
    protocol = value.get("workspaceLockProtocol")
    if not isinstance(protocol, dict):
        raise RuntimeError(
            f"Invalid {LOCK_SCHEMA_RESOURCE_NAME} field 'workspaceLockProtocol'; expected object"
        )
    expected_protocol_types = {
        "schemaVersion": int,
        "ownerFileName": str,
        "timeoutMs": int,
        "retryDelayMs": int,
        "initializationGraceMs": int,
    }
    for field_name, expected_type in expected_protocol_types.items():
        item = protocol.get(field_name)
        valid = (
            isinstance(item, int) and not isinstance(item, bool) and item > 0
            if expected_type is int
            else isinstance(item, str) and bool(item)
        )
        if not valid:
            raise RuntimeError(
                f"Invalid {LOCK_SCHEMA_RESOURCE_NAME} workspace lock field {field_name!r}"
            )
    dependency_fields = set(_string_list(value, "dependencyEntryFields"))
    required_dependency_fields = set(_string_list(value, "requiredDependencyEntryFields"))
    optional_dependency_fields = set(_string_list(value, "optionalDependencyEntryFields"))
    if required_dependency_fields & optional_dependency_fields:
        raise RuntimeError(
            f"Invalid {LOCK_SCHEMA_RESOURCE_NAME}; required and optional dependency fields overlap"
        )
    classified_fields = required_dependency_fields | optional_dependency_fields
    if dependency_fields != classified_fields:
        raise RuntimeError(
            f"Invalid {LOCK_SCHEMA_RESOURCE_NAME}; required and optional dependency fields must partition dependencyEntryFields"
        )
    return value


LOCK_SCHEMA_RESOURCE = _load_lock_schema_contract()
LOCK_SCHEMA_VERSION = int(LOCK_SCHEMA_RESOURCE["schemaVersion"])
LOCK_MODES = tuple(str(value) for value in LOCK_SCHEMA_RESOURCE["modes"])
ACTIVE_LOCK_FILE_NAME = str(LOCK_SCHEMA_RESOURCE["activeLockFileName"])
TEMPLATE_LOCK_FILE_NAME = str(LOCK_SCHEMA_RESOURCE["templateLockFileName"])
WORKSPACE_LOCK_NAME = str(LOCK_SCHEMA_RESOURCE["workspaceLockName"])
LOCK_FIELDS = {str(field): str(value) for field, value in LOCK_SCHEMA_RESOURCE["fields"].items()}
DEPENDENCY_ENTRY_FIELDS = tuple(
    str(value) for value in LOCK_SCHEMA_RESOURCE["dependencyEntryFields"]
)
REQUIRED_DEPENDENCY_ENTRY_FIELDS = tuple(
    str(value) for value in LOCK_SCHEMA_RESOURCE["requiredDependencyEntryFields"]
)
OPTIONAL_DEPENDENCY_ENTRY_FIELDS = tuple(
    str(value) for value in LOCK_SCHEMA_RESOURCE["optionalDependencyEntryFields"]
)
LEGACY_DEPENDENCY_ENTRY_FIELDS = tuple(
    str(value) for value in LOCK_SCHEMA_RESOURCE["legacyDependencyEntryFields"]
)
REMOVED_TOP_LEVEL_FIELDS = {
    str(field): str(replacement)
    for field, replacement in LOCK_SCHEMA_RESOURCE["removedTopLevelFields"].items()
}
SAFE_DEPENDENCY_NAME_PATTERN_SOURCE = str(LOCK_SCHEMA_RESOURCE["safeDependencyNamePattern"])


__all__ = [
    "ACTIVE_LOCK_FILE_NAME",
    "DEPENDENCY_ENTRY_FIELDS",
    "LEGACY_DEPENDENCY_ENTRY_FIELDS",
    "LOCK_FIELDS",
    "LOCK_MODES",
    "LOCK_SCHEMA_RESOURCE",
    "LOCK_SCHEMA_RESOURCE_NAME",
    "LOCK_SCHEMA_VERSION",
    "OPTIONAL_DEPENDENCY_ENTRY_FIELDS",
    "REMOVED_TOP_LEVEL_FIELDS",
    "REQUIRED_DEPENDENCY_ENTRY_FIELDS",
    "SAFE_DEPENDENCY_NAME_PATTERN_SOURCE",
    "TEMPLATE_LOCK_FILE_NAME",
    "WORKSPACE_LOCK_NAME",
]
