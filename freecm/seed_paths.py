"""Host-local selection of public dependency seeds and editable checkouts."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .workspace_lock import directory_mutation_lock


@dataclass
class _PublicSeedLocks:
    pools: set[Path]
    stack: ExitStack
    held: set[Path] = field(default_factory=set)

    def acquire(self, seed: Path) -> None:
        if seed.parent not in self.pools or seed in self.held:
            return
        self.stack.enter_context(_seed_lock(seed.parent / f".{seed.name}.freecm-seed.lock"))
        self.held.add(seed)


_PUBLIC_SEED_LOCKS: ContextVar[_PublicSeedLocks | None] = ContextVar(
    "freecm_public_seed_locks", default=None
)


def _seed_lock(path: Path) -> Any:
    return directory_mutation_lock(
        path,
        timeout_seconds=600,
        on_wait=lambda waiting: print(
            f"[freecm] seed: waiting for shared seed lock: {waiting}", flush=True
        ),
    )


def lock_selected_public_seed(seed: Path) -> None:
    """Hold a discovered transitive seed for the surrounding mutation operation."""
    locks = _PUBLIC_SEED_LOCKS.get()
    if locks is not None:
        locks.acquire(seed)


def resolve_dependency_path(value: str, repo_root: Path) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    return (repo_root / path).resolve()


def public_seed_paths(lock_data: dict[str, Any], repo_root: Path) -> dict[str, Path]:
    return {
        name: resolve_dependency_path(entry["publicSeedPath"], repo_root)
        for name, entry in lock_data.get("dependencies", {}).items()
        if not entry.get("disabled", False) and entry.get("publicSeedPath")
    }


def validate_manual_seed_path(
    lock_data: dict[str, Any], manual_path: Path, repo_root: Path
) -> None:
    for name, seed in public_seed_paths(lock_data, repo_root).items():
        if manual_path.is_relative_to(seed):
            raise ValueError(
                f"Manual path {manual_path} is inside the declared publicSeedPath for {name}: "
                f"{seed}. Use a separate checkout, such as build/dependency_seed_repos/{name}."
            )


def validate_manual_seed_paths(lock_data: dict[str, Any], repo_root: Path) -> None:
    if lock_data.get("depsMode") != "manual":
        return
    for name, value in lock_data.get("depsManualPath", {}).items():
        if value and not lock_data["dependencies"][name].get("disabled", False):
            validate_manual_seed_path(
                lock_data, resolve_dependency_path(value, repo_root), repo_root
            )


def dependency_seed_path(lock_data: dict[str, Any], dependency_name: str, repo_root: Path) -> Path:
    local_seed = (repo_root / "build" / "dependency_seed_repos" / dependency_name).resolve()
    # The normal local seed remains available for integration work, even when
    # this dependency also declares a public seed for pinned/latest use.
    if lock_data.get("depsMode") == "manual":
        manual = lock_data.get("depsManualPath", {}).get(dependency_name)
        if manual and resolve_dependency_path(manual, repo_root) == local_seed:
            return local_seed
    return public_seed_paths(lock_data, repo_root).get(dependency_name, local_seed)


@contextmanager
def public_seed_mutation_locks(lock_data: dict[str, Any], repo_root: Path) -> Iterator[None]:
    # Lock pools before traversing their dependency graphs: transitive seed names
    # may only become known after fetching a parent. This also prevents hosts
    # with different direct dependencies from mutating the same inherited seed.
    seeds = set(public_seed_paths(lock_data, repo_root).values())
    with ExitStack() as stack:
        locks = _PublicSeedLocks({seed.parent for seed in seeds}, stack)
        for pool in sorted(locks.pools):
            stack.enter_context(_seed_lock(pool / ".freecm-public-seeds.lock"))
        for seed in sorted(seeds):
            locks.acquire(seed)
        token = _PUBLIC_SEED_LOCKS.set(locks)
        try:
            yield
        finally:
            _PUBLIC_SEED_LOCKS.reset(token)
