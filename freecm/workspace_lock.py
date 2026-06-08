# Internal: workspace-level mutation locking shared by FreeCM workflows.

from __future__ import annotations

import time
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

WORKSPACE_LOCK_NAME = ".freecm.workspace.lock"
_WORKSPACE_LOCK_POLL_SECONDS = 0.05
_WORKSPACE_LOCK_TIMEOUT_SECONDS = 300.0
_HELD_WORKSPACE_LOCKS: dict[Path, tuple[int, int]] = {}
_HELD_WORKSPACE_LOCKS_MUTEX = threading.Lock()


def workspace_lock_path(repo_root: Path) -> Path:
    return repo_root.resolve() / WORKSPACE_LOCK_NAME


@contextmanager
def workspace_mutation_lock(
    repo_root: Path,
    *,
    timeout_seconds: float = _WORKSPACE_LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    lock_path = workspace_lock_path(repo_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _acquire_workspace_lock(lock_path, timeout_seconds=timeout_seconds)
    try:
        yield
    finally:
        _release_workspace_lock(lock_path)


def _acquire_workspace_lock(lock_path: Path, *, timeout_seconds: float) -> None:
    owner = threading.get_ident()
    deadline = time.monotonic() + timeout_seconds
    while True:
        with _HELD_WORKSPACE_LOCKS_MUTEX:
            held = _HELD_WORKSPACE_LOCKS.get(lock_path)
            if held is not None and held[0] == owner:
                _HELD_WORKSPACE_LOCKS[lock_path] = (owner, held[1] + 1)
                return
        try:
            lock_path.mkdir()
            with _HELD_WORKSPACE_LOCKS_MUTEX:
                _HELD_WORKSPACE_LOCKS[lock_path] = (owner, 1)
            return
        except FileExistsError:
            if lock_path.is_dir():
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Unable to acquire workspace lock: {lock_path}")
                time.sleep(_WORKSPACE_LOCK_POLL_SECONDS)
                continue
            lock_path.unlink()


def _release_workspace_lock(lock_path: Path) -> None:
    owner = threading.get_ident()
    with _HELD_WORKSPACE_LOCKS_MUTEX:
        held = _HELD_WORKSPACE_LOCKS.get(lock_path)
        if held is None or held[0] != owner:
            raise RuntimeError(f"Workspace lock is not held by this thread: {lock_path}")
        if held[1] > 1:
            _HELD_WORKSPACE_LOCKS[lock_path] = (owner, held[1] - 1)
            return
        del _HELD_WORKSPACE_LOCKS[lock_path]
    lock_path.rmdir()


__all__ = ("WORKSPACE_LOCK_NAME", "workspace_lock_path", "workspace_mutation_lock")
