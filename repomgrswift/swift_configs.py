from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


SWIFT_CONFIGS_FIELD = "SwiftConfigs"
REMOVED_LOCK_FIELDS = {
    "buildSettings": "SwiftConfigs",
    "commercePolicy": "SwiftConfigs.commercePolicy",
}


class SwiftConfigError(ValueError):
    pass


def validate_swift_configs(
    lock_data: Mapping[str, Any],
    *,
    path_label: str | Path,
    swift_config_keys: tuple[str, ...],
    swift_config_defaults: Mapping[str, str] | None = None,
) -> dict[str, str]:
    path_text = str(path_label)
    for field_name, replacement in REMOVED_LOCK_FIELDS.items():
        if field_name in lock_data:
            raise SwiftConfigError(
                f"{field_name} is no longer supported in {path_text}; "
                f"use {replacement} under {SWIFT_CONFIGS_FIELD}"
            )

    raw_configs = lock_data.get(SWIFT_CONFIGS_FIELD, {})
    if raw_configs is None:
        raw_configs = {}
    if not isinstance(raw_configs, dict):
        raise SwiftConfigError(f"Invalid {SWIFT_CONFIGS_FIELD} map in {path_text}")

    defaults = dict(swift_config_defaults or {})
    normalized: dict[str, str] = {}
    for key, value in defaults.items():
        if not isinstance(key, str):
            raise SwiftConfigError(f"Invalid {SWIFT_CONFIGS_FIELD} default key in {path_text}; expected string")
        if not isinstance(value, str):
            raise SwiftConfigError(f"Invalid {SWIFT_CONFIGS_FIELD}.{key} default in {path_text}; expected string")
        normalized[key] = value

    for key, value in raw_configs.items():
        if not isinstance(key, str):
            raise SwiftConfigError(f"Invalid {SWIFT_CONFIGS_FIELD} key in {path_text}; expected string")
        if not isinstance(value, str):
            raise SwiftConfigError(f"Invalid {SWIFT_CONFIGS_FIELD}.{key} in {path_text}; expected string")
        normalized[key] = value

    missing = [key for key in swift_config_keys if key not in normalized]
    if missing:
        raise SwiftConfigError(
            f"Invalid {SWIFT_CONFIGS_FIELD} in {path_text}: missing keys: {', '.join(missing)}"
        )

    return normalized


def load_swift_configs(
    lock_data: Mapping[str, Any],
    *,
    path_label: str | Path,
    swift_config_keys: tuple[str, ...],
    swift_config_defaults: Mapping[str, str] | None = None,
) -> dict[str, str]:
    return validate_swift_configs(
        lock_data,
        path_label=path_label,
        swift_config_keys=swift_config_keys,
        swift_config_defaults=swift_config_defaults,
    )
