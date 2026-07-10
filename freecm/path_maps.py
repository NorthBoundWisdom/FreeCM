from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Protocol, TypeVar


class DependencyPathSpec(Protocol):
    @property
    def dependency_name(self) -> str: ...

    @property
    def env_key(self) -> str: ...


DependencyPathSpecT = TypeVar("DependencyPathSpecT", bound=DependencyPathSpec)


def dedupe_dependency_specs(
    specs: Iterable[DependencyPathSpecT],
) -> tuple[DependencyPathSpecT, ...]:
    deduped: dict[str, DependencyPathSpecT] = {}
    for spec in specs:
        deduped.setdefault(spec.dependency_name, spec)
    return tuple(deduped.values())


def dependency_root_path_map(
    specs: Iterable[DependencyPathSpec],
    root_for_dependency: Callable[[str], Path],
) -> dict[str, Path]:
    return {spec.env_key: root_for_dependency(spec.dependency_name) for spec in specs}


def environment_map(path_map: Mapping[str, Path]) -> dict[str, str]:
    return {key: str(value) for key, value in path_map.items()}


def print_environment_map(env_map: Mapping[str, str], output_format: str) -> None:
    if output_format not in {"plain", "shell"}:
        raise ValueError(f"Unsupported environment map output format: {output_format}")
    for key, value in env_map.items():
        if output_format == "shell":
            print(f'export {key}="{value}"')
        else:
            print(f"{key}={value}")
