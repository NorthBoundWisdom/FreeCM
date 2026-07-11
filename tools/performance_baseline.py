# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m tools.performance_baseline --dependencies 50
#   PYTHONPATH=/path/to/FreeCM python3 -m tools.performance_baseline --io --io-dependencies 8
#   Library: from tools.performance_baseline import run_benchmarks

from __future__ import annotations

import argparse
import copy
import json
import statistics
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from freecm.dependency_lock import validate_dependency_lock_data
from freecm.dependency_models import DependencyPin, DependencyRootConfig, DependencyRootSpec
from freecm.dependency_roots import DependencyRootManager
from freecm.io_metrics import capture_io_operations
from freecm.jsonc import loads_jsonc
from freecm.path_maps import dependency_root_path_map, environment_map

from .performance_fixtures import create_io_performance_fixture

BenchmarkFn = Callable[[], object]


def _load_fixture_closure(fixture: Any) -> object:
    return fixture.manager.load_dependency_closure(fixture.host_root)


def _verify_fixture_roots(fixture: Any, roots: Any) -> object:
    return fixture.manager.validate_dependency_roots(roots)


def synthetic_lock_data(dependency_count: int) -> dict[str, Any]:
    dependencies = {}
    manual_paths = {}
    for index in range(dependency_count):
        name = f"Lib{index:03d}"
        dependencies[name] = {
            "remote": f"https://example.invalid/{name}.git",
            "commit": f"{index:040x}"[-40:],
            "latestRef": "main",
        }
        manual_paths[name] = ""
    return {
        "schemaVersion": 5,
        "depsMode": "pinned",
        "depsManualPath": manual_paths,
        "dependencies": dependencies,
    }


def synthetic_specs(dependency_count: int) -> tuple[DependencyRootSpec, ...]:
    return tuple(
        DependencyRootSpec(
            dependency_name=f"Lib{index:03d}",
            repo_name=f"Lib{index:03d}",
            env_key=f"LIB{index:03d}_SOURCE_ROOT",
            required_relative_paths=(),
        )
        for index in range(dependency_count)
    )


def synthetic_closure_resolution(
    lock_data: dict[str, Any],
    specs: tuple[DependencyRootSpec, ...],
    repo_root: Path,
) -> object:
    manager = DependencyRootManager(
        DependencyRootConfig(
            repo_root=repo_root,
            dependency_root_specs=specs,
            repo_display_name="SyntheticRepo",
        )
    )
    dependency_names = tuple(spec.dependency_name for spec in specs)
    nested_children = {
        dependency_name: dependency_names[index + 1]
        for index, dependency_name in enumerate(dependency_names[:-1])
    }

    def load_nested_specs(
        dependency_root: Path,
        dependency: DependencyPin,
    ) -> tuple[DependencyPin, ...]:
        del dependency_root
        child_name = nested_children.get(dependency.dependency_name)
        if child_name is None:
            return ()
        return (
            manager._dependency_checkout_spec_from_entry(
                child_name,
                lock_data["dependencies"][child_name],
                declared_by_root=False,
                source_label="synthetic-lock",
                parent_dependency_name=dependency.dependency_name,
            ),
        )

    return manager._discover_dependency_closure(
        lock_data,
        repo_root,
        prepare_dependency_root=lambda dependency: repo_root / "seeds" / dependency.repo_name,
        load_nested_dependency_specs=load_nested_specs,
    )


def _measure(name: str, iterations: int, fn: BenchmarkFn) -> dict[str, Any]:
    elapsed_ms: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        elapsed_ms.append((time.perf_counter() - start) * 1000.0)
    return {
        "name": name,
        "iterations": iterations,
        "minMs": min(elapsed_ms),
        "medianMs": statistics.median(elapsed_ms),
        "maxMs": max(elapsed_ms),
    }


def run_benchmarks(*, dependency_count: int = 50, iterations: int = 25) -> dict[str, Any]:
    lock_data = synthetic_lock_data(dependency_count)
    lock_text = json.dumps(lock_data)
    specs = synthetic_specs(dependency_count)
    repo_root = Path.cwd() / ".freecm-performance-baseline"

    return {
        "dependencyCount": dependency_count,
        "benchmarks": [
            _measure(
                "jsonc_parse",
                iterations,
                lambda: loads_jsonc(lock_text, path_label="synthetic-lock"),
            ),
            _measure(
                "lock_validation",
                iterations,
                lambda: validate_dependency_lock_data(
                    copy.deepcopy(lock_data),
                    path_label="synthetic-lock",
                    expected_dependency_names=tuple(spec.dependency_name for spec in specs),
                ),
            ),
            _measure(
                "closure_resolution",
                iterations,
                lambda: synthetic_closure_resolution(lock_data, specs, repo_root),
            ),
            _measure(
                "path_map_generation",
                iterations,
                lambda: (
                    environment_map(
                        dependency_root_path_map(
                            specs,
                            lambda dependency_name: repo_root
                            / "build"
                            / "dependency_source_roots"
                            / dependency_name,
                        )
                    ),
                ),
            ),
        ],
    }


def _measure_io_scenario(
    name: str,
    dependency_count: int,
    iterations: int,
) -> dict[str, Any]:
    elapsed_ms: list[float] = []
    git_summary: dict[str, Any] | None = None
    network_summary: dict[str, Any] | None = None
    for _ in range(iterations):
        fixture = create_io_performance_fixture(dependency_count)
        try:
            action: BenchmarkFn
            if name == "seed_preflight_init":
                fixture.prepare_seeds()
                action = fixture.prepare_seeds
            elif name == "offline_closure_discovery":
                fixture.prepare_seeds()
                action = partial(_load_fixture_closure, fixture)
            elif name == "offline_materialize_cold":
                fixture.prepare_seeds()
                action = fixture.materialize
            elif name == "offline_materialize_warm":
                fixture.prepare_seeds()
                fixture.materialize()
                action = fixture.materialize
            elif name == "dependency_root_verify":
                fixture.prepare_seeds()
                roots = fixture.materialize()
                action = partial(_verify_fixture_roots, fixture, roots)
            else:
                raise ValueError(f"Unknown I/O benchmark {name!r}")
            start = time.perf_counter()
            with capture_io_operations() as recorder:
                action()
            elapsed_ms.append((time.perf_counter() - start) * 1000.0)
            current_git = recorder.git_summary()
            current_network = recorder.git_network_summary()
            if git_summary is not None and current_git != git_summary:
                raise RuntimeError(f"Inconsistent Git command counts for {name}")
            if network_summary is not None and current_network != network_summary:
                raise RuntimeError(f"Inconsistent network command counts for {name}")
            git_summary = current_git
            network_summary = current_network
        finally:
            fixture.close()
    return {
        "name": name,
        "dependencyCount": dependency_count,
        "iterations": iterations,
        "minMs": min(elapsed_ms),
        "medianMs": statistics.median(elapsed_ms),
        "maxMs": max(elapsed_ms),
        "gitCommands": git_summary,
        "gitNetworkCommands": network_summary,
    }


def run_io_benchmarks(
    *,
    dependency_count: int = 8,
    iterations: int = 1,
) -> dict[str, Any]:
    if dependency_count < 1:
        raise ValueError("dependency_count must be positive")
    if iterations < 1:
        raise ValueError("iterations must be positive")
    names = (
        "seed_preflight_init",
        "offline_closure_discovery",
        "offline_materialize_cold",
        "offline_materialize_warm",
        "dependency_root_verify",
    )
    return {
        "dependencyCount": dependency_count,
        "topology": "chain",
        "benchmarks": [_measure_io_scenario(name, dependency_count, iterations) for name in names],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run lightweight FreeCM performance baselines.")
    parser.add_argument("--dependencies", type=int, default=50)
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--io", action="store_true")
    parser.add_argument("--io-dependencies", type=int, default=8)
    parser.add_argument("--io-iterations", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.dependencies < 1:
        raise ValueError("--dependencies must be positive")
    if args.iterations < 1:
        raise ValueError("--iterations must be positive")
    if args.io_dependencies < 1:
        raise ValueError("--io-dependencies must be positive")
    if args.io_iterations < 1:
        raise ValueError("--io-iterations must be positive")
    report = run_benchmarks(
        dependency_count=args.dependencies,
        iterations=args.iterations,
    )
    if args.io:
        report["ioBenchmarkSuite"] = run_io_benchmarks(
            dependency_count=args.io_dependencies,
            iterations=args.io_iterations,
        )
    print(
        json.dumps(
            report,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
