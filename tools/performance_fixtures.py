# Usage:
#   Library: from tools.performance_fixtures import create_io_performance_fixture

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from freecm.dependency_models import DependencyRootConfig, DependencyRootSpec
from freecm.dependency_roots import DependencyRootManager
from freecm.git_repositories import git, git_output


@dataclass
class IoPerformanceFixture:
    temporary_directory: tempfile.TemporaryDirectory[str]
    host_root: Path
    manager: DependencyRootManager
    dependency_count: int

    def close(self) -> None:
        self.temporary_directory.cleanup()

    def prepare_seeds(self) -> None:
        self.manager.prepare_seed_repository_closure(self.host_root, quiet=True)

    def materialize(self):
        return self.manager.materialize_dependency_roots(
            self.host_root,
            allow_network=False,
            quiet=True,
        )


def _dependency_name(index: int) -> str:
    return f"Lib{index:03d}"


def _init_repository(path: Path) -> None:
    path.mkdir(parents=True)
    git(path, "init", quiet=True)
    git(path, "config", "user.name", "FreeCM Benchmark", quiet=True)
    git(path, "config", "user.email", "benchmark@example.invalid", quiet=True)
    (path / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    git(path, "add", "fixture.txt", quiet=True)
    git(path, "commit", "-m", "initial fixture", quiet=True)


def _write_lock(path: Path, dependencies: dict[str, dict[str, object]]) -> None:
    data = {
        "schemaVersion": 5,
        "depsMode": "pinned",
        "depsManualPath": {name: "" for name in dependencies},
        "dependencies": dependencies,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def create_io_performance_fixture(dependency_count: int) -> IoPerformanceFixture:
    if dependency_count < 1:
        raise ValueError("dependency_count must be positive")
    temporary_directory = tempfile.TemporaryDirectory(prefix="freecm-io-benchmark-")
    root = Path(temporary_directory.name)
    remotes_root = root / "remotes"
    host_root = root / "host"
    host_root.mkdir()
    repositories = [remotes_root / _dependency_name(index) for index in range(dependency_count)]
    for repository in repositories:
        _init_repository(repository)

    commits = ["" for _ in repositories]
    for index in range(dependency_count - 1, -1, -1):
        repository = repositories[index]
        if index + 1 < dependency_count:
            child_name = _dependency_name(index + 1)
            _write_lock(
                repository / "source_roots.lock.jsonc.in",
                {
                    child_name: {
                        "remote": str(repositories[index + 1]),
                        "commit": commits[index + 1],
                        "latestRef": None,
                    }
                },
            )
            git(repository, "add", "source_roots.lock.jsonc.in", quiet=True)
            git(repository, "commit", "-m", "add nested dependency", quiet=True)
        commits[index] = git_output(repository, "rev-parse", "HEAD")

    direct_name = _dependency_name(0)
    _write_lock(
        host_root / "source_roots.lock.jsonc",
        {
            direct_name: {
                "remote": str(repositories[0]),
                "commit": commits[0],
                "latestRef": None,
            }
        },
    )
    specs = tuple(
        DependencyRootSpec(
            dependency_name=_dependency_name(index),
            repo_name=_dependency_name(index),
            env_key=f"LIB{index:03d}_SOURCE_ROOT",
            required_relative_paths=("fixture.txt",),
        )
        for index in range(dependency_count)
    )
    manager = DependencyRootManager(
        DependencyRootConfig(
            repo_root=host_root,
            dependency_root_specs=(specs[0],),
            known_dependency_root_specs=specs,
            repo_display_name="BenchmarkHost",
        )
    )
    return IoPerformanceFixture(
        temporary_directory=temporary_directory,
        host_root=host_root,
        manager=manager,
        dependency_count=dependency_count,
    )
