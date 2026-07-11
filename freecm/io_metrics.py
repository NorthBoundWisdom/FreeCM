# Internal: context-local observation of Git commands issued by FreeCM.

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GitCommandObservation:
    category: str
    network_capable: bool


class IoOperationRecorder:
    def __init__(self) -> None:
        self._git_categories: Counter[str] = Counter()
        self._network_categories: Counter[str] = Counter()

    def record_git_command(self, cmd: Sequence[str]) -> None:
        observation = classify_git_command(cmd)
        if observation is None:
            return
        self._git_categories[observation.category] += 1
        if observation.network_capable:
            self._network_categories[observation.category] += 1

    @staticmethod
    def _summary(counter: Counter[str]) -> dict[str, Any]:
        by_category = dict(sorted(counter.items()))
        return {"total": sum(by_category.values()), "byCategory": by_category}

    def git_summary(self) -> dict[str, Any]:
        return self._summary(self._git_categories)

    def git_network_summary(self) -> dict[str, Any]:
        return self._summary(self._network_categories)


_ACTIVE_RECORDER: ContextVar[IoOperationRecorder | None] = ContextVar(
    "freecm_active_io_recorder",
    default=None,
)


@contextmanager
def capture_io_operations() -> Iterator[IoOperationRecorder]:
    recorder = IoOperationRecorder()
    token = _ACTIVE_RECORDER.set(recorder)
    try:
        yield recorder
    finally:
        _ACTIVE_RECORDER.reset(token)


def record_git_command(cmd: Sequence[str]) -> None:
    recorder = _ACTIVE_RECORDER.get()
    if recorder is not None:
        recorder.record_git_command(cmd)


def _git_subcommand_args(cmd: Sequence[str]) -> tuple[str, tuple[str, ...]] | None:
    if not cmd or os.path.basename(cmd[0]).lower() not in {"git", "git.exe"}:
        return None
    index = 1
    while index < len(cmd):
        value = cmd[index]
        if value == "-C":
            index += 2
            continue
        if value.startswith("-"):
            index += 1
            continue
        return value, tuple(cmd[index + 1 :])
    return None


def classify_git_command(cmd: Sequence[str]) -> GitCommandObservation | None:
    parsed = _git_subcommand_args(cmd)
    if parsed is None:
        return None
    subcommand, args = parsed
    network_capable = subcommand in {"clone", "fetch", "ls-remote"}
    if subcommand == "status":
        category = "status"
    elif subcommand == "rev-parse":
        if "--is-inside-work-tree" in args:
            category = "rev_parse_worktree"
        elif "--git-common-dir" in args:
            category = "rev_parse_common_dir"
        elif "--verify" in args:
            category = "rev_parse_verify"
        elif "HEAD" in args:
            category = "rev_parse_head"
        else:
            category = "rev_parse_other"
    elif subcommand == "remote" and args[:1] == ("get-url",):
        category = "remote_get_url"
    elif subcommand == "show":
        category = "show"
    elif subcommand == "worktree":
        operation = args[0] if args else ""
        category = {
            "prune": "worktree_prune",
            "add": "worktree_add",
        }.get(operation, "worktree_other")
    elif subcommand == "submodule":
        operation = args[0] if args else ""
        category = "submodule_update" if operation == "update" else "submodule_other"
        network_capable = operation == "update"
    else:
        category = {
            "clone": "clone",
            "fetch": "fetch",
            "ls-remote": "ls_remote",
            "checkout": "checkout",
            "reset": "reset",
            "clean": "clean",
        }.get(subcommand, "other")
    return GitCommandObservation(category, network_capable)
