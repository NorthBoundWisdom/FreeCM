# Internal: atomic file-write helpers shared by FreeCM adapters.

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

ATOMIC_SIDECAR_DIR_NAME = ".freecm/atomic"


def _atomic_sidecar_dir(path: Path) -> Path:
    return path.parent / ATOMIC_SIDECAR_DIR_NAME


@contextmanager
def _exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            locking = cast(
                Callable[[int, int, int], None],
                vars(msvcrt)["locking"],
            )
            lock_mode = cast(int, vars(msvcrt)["LK_LOCK"])
            unlock_mode = cast(int, vars(msvcrt)["LK_UNLCK"])
            lock_file.seek(0)
            locking(lock_file.fileno(), lock_mode, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                locking(lock_file.fileno(), unlock_mode, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_dir = _atomic_sidecar_dir(path)
    lock_path = sidecar_dir / f".{path.name}.lock"
    with _exclusive_file_lock(lock_path):
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=sidecar_dir,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding=encoding) as temp_file:
                temp_file.write(text)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, path)
            _fsync_directory(path.parent)
        except BaseException:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            raise


def atomic_write_json(
    path: Path,
    data: object,
    *,
    indent: int = 2,
    encoding: str = "utf-8",
) -> None:
    import json

    atomic_write_text(
        path,
        json.dumps(data, indent=indent) + "\n",
        encoding=encoding,
    )


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


__all__ = ("atomic_write_json", "atomic_write_text")
