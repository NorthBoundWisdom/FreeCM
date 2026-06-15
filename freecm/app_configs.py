from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Union

APP_CONFIGS_FIELD = "AppConfigs"
AppConfigValue = Union[str, bool]
REMOVED_LOCK_FIELDS = {
    "buildSettings": "AppConfigs",
    "commercePolicy": "AppConfigs.commercePolicy",
    "DevMode": "AppConfigs.DevMode",
    "SwiftConfigs": "AppConfigs",
}


class AppConfigError(ValueError):
    pass


def validate_app_configs(
    lock_data: Mapping[str, Any],
    *,
    path_label: str | Path,
    app_config_keys: tuple[str, ...],
    app_config_defaults: Mapping[str, AppConfigValue] | None = None,
) -> dict[str, AppConfigValue]:
    path_text = str(path_label)
    for field_name, replacement in REMOVED_LOCK_FIELDS.items():
        if field_name in lock_data:
            raise AppConfigError(
                f"{field_name} is no longer supported in {path_text}; " f"use {replacement}"
            )

    raw_configs = lock_data.get(APP_CONFIGS_FIELD, {})
    if raw_configs is None:
        raw_configs = {}
    if not isinstance(raw_configs, dict):
        raise AppConfigError(f"Invalid {APP_CONFIGS_FIELD} map in {path_text}")

    defaults = dict(app_config_defaults or {})
    normalized: dict[str, AppConfigValue] = {}
    for key, value in defaults.items():
        if not isinstance(key, str):
            raise AppConfigError(
                f"Invalid {APP_CONFIGS_FIELD} default key in {path_text}; expected string"
            )
        if not isinstance(value, (str, bool)):
            raise AppConfigError(
                f"Invalid {APP_CONFIGS_FIELD}.{key} default in {path_text}; expected string or boolean"
            )
        normalized[key] = value

    for key, value in raw_configs.items():
        if not isinstance(key, str):
            raise AppConfigError(f"Invalid {APP_CONFIGS_FIELD} key in {path_text}; expected string")
        if not isinstance(value, (str, bool)):
            raise AppConfigError(
                f"Invalid {APP_CONFIGS_FIELD}.{key} in {path_text}; expected string or boolean"
            )
        normalized[key] = value

    missing = [key for key in app_config_keys if key not in normalized]
    if missing:
        raise AppConfigError(
            f"Invalid {APP_CONFIGS_FIELD} in {path_text}: missing keys: {', '.join(missing)}"
        )

    return normalized


def load_app_configs(
    lock_data: Mapping[str, Any],
    *,
    path_label: str | Path,
    app_config_keys: tuple[str, ...],
    app_config_defaults: Mapping[str, AppConfigValue] | None = None,
) -> dict[str, AppConfigValue]:
    return validate_app_configs(
        lock_data,
        path_label=path_label,
        app_config_keys=app_config_keys,
        app_config_defaults=app_config_defaults,
    )
