"""Dependency and repository name validation helpers."""

from __future__ import annotations

import re

from .lock_schema import SAFE_DEPENDENCY_NAME_PATTERN_SOURCE

SAFE_DEPENDENCY_NAME_PATTERN = re.compile(SAFE_DEPENDENCY_NAME_PATTERN_SOURCE)


def validate_safe_dependency_path_name(name: str, *, label: str, path_label: str) -> None:
    if "/" in name or "\\" in name or name in {".", ".."} or ".." in name.split("."):
        raise ValueError(f"Invalid {label} {name!r} in {path_label}; expected path-safe segment")
    if not SAFE_DEPENDENCY_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"Invalid {label} {name!r} in {path_label}; expected path-safe segment")
