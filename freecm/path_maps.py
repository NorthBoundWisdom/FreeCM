from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol, TypeVar

ENVIRONMENT_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DependencyPathSpec(Protocol):
    @property
    def dependency_name(self) -> str: ...

    @property
    def env_key(self) -> str: ...

    @property
    def required_relative_paths(self) -> tuple[str, ...]: ...


DependencyPathSpecT = TypeVar("DependencyPathSpecT", bound=DependencyPathSpec)


def validate_environment_key(value: str, *, label: str = "environment key") -> str:
    if not isinstance(value, str) or not ENVIRONMENT_KEY_PATTERN.fullmatch(value):
        raise ValueError(
            f"Invalid {label} {value!r}; expected a portable identifier matching "
            "[A-Za-z_][A-Za-z0-9_]*"
        )
    return value


def validate_dependency_relative_path(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError(f"Invalid {label} {value!r}; expected a non-empty relative path")
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if posix_path.is_absolute() or windows_path.anchor:
        raise ValueError(
            f"Invalid {label} {value!r}; path must stay relative to its dependency root"
        )

    depth = 0
    for part in value.replace("\\", "/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if depth == 0:
                raise ValueError(f"Invalid {label} {value!r}; path escapes its dependency root")
            depth -= 1
        else:
            depth += 1
    return value


def resolve_dependency_relative_path(root: Path, value: str, *, label: str) -> Path:
    validate_dependency_relative_path(value, label=label)
    resolved_root = root.resolve()
    candidate = (resolved_root / value).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"Invalid {label} {value!r}; resolved path escapes dependency root {resolved_root}"
        ) from exc
    return candidate


def validate_dependency_specs(
    specs: Iterable[DependencyPathSpecT],
    *,
    label: str = "dependency specs",
) -> tuple[DependencyPathSpecT, ...]:
    validated: list[DependencyPathSpecT] = []
    dependency_names: set[str] = set()
    environment_keys: set[str] = set()
    for spec in specs:
        if spec.dependency_name in dependency_names:
            raise ValueError(f"Duplicate dependency name {spec.dependency_name!r} in {label}")
        validate_environment_key(spec.env_key, label=f"{label} environment key")
        if spec.env_key in environment_keys:
            raise ValueError(f"Duplicate environment key {spec.env_key!r} in {label}")
        for relative_path in spec.required_relative_paths:
            validate_dependency_relative_path(
                relative_path,
                label=f"{spec.dependency_name} required path",
            )
        dependency_names.add(spec.dependency_name)
        environment_keys.add(spec.env_key)
        validated.append(spec)
    return tuple(validated)


def dependency_root_path_map(
    specs: Iterable[DependencyPathSpec],
    root_for_dependency: Callable[[str], Path],
) -> dict[str, Path]:
    validated_specs = validate_dependency_specs(specs)
    return {spec.env_key: root_for_dependency(spec.dependency_name) for spec in validated_specs}


def environment_map(path_map: Mapping[str, Path]) -> dict[str, str]:
    return {validate_environment_key(key): str(value) for key, value in path_map.items()}


def print_environment_map(env_map: Mapping[str, str], output_format: str) -> None:
    if output_format not in {"plain", "shell"}:
        raise ValueError(f"Unsupported environment map output format: {output_format}")
    for key, value in env_map.items():
        validate_environment_key(key)
        if output_format == "shell":
            print(f"export {key}={shlex.quote(value)}")
        else:
            print(f"{key}={value}")
