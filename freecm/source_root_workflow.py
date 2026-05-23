# Usage:
#   Library: from freecm.source_root_workflow import SourceRootWorkflowScript

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, Protocol

from .dependency_roots import dependency_commit_changes

from .terminal_style import (
    format_dependency_commit_change_lines,
    format_dependency_resolution_lines,
    format_status_line,
    stderr_supports_color,
    stdout_supports_color,
)


class SourceRootWorkflowLike(Protocol):
    repo_root: Path
    spec_by_dependency_name: dict[str, object]

    def init_seed_repositories(
        self,
        repo_root: Path | None = None,
    ) -> tuple[Path, bool, dict[str, str]]:
        ...

    def materialize_source_roots(
        self,
        repo_root: Path | None = None,
        *,
        allow_network: bool = False,
    ) -> object:
        ...

    def verify_source_roots(self, source_roots: object) -> list[str]:
        ...

    def dependency_resolutions(self, source_roots: object) -> Iterable[object]:
        ...

    def load_lock_file(self, repo_root: Path | None = None) -> dict[str, object]:
        ...

    def seed_repo_root_for_spec(
        self,
        spec: object,
        repo_root: Path | None = None,
    ) -> Path:
        ...


class SourceRootWorkflowScript:
    def __init__(
        self,
        workflow: SourceRootWorkflowLike,
        *,
        repo_display_name: str,
        update_callback: Callable[[], int] | None = None,
        print_update_resolutions: bool = True,
    ) -> None:
        self.workflow = workflow
        self.repo_root = workflow.repo_root
        self.repo_display_name = repo_display_name
        self.update_callback = update_callback
        self.print_update_resolutions = print_update_resolutions

    def _print_status(self, action: str, message: str, *, level: str = "info") -> None:
        print(
            format_status_line(
                action,
                message,
                level=level,
                use_color=stdout_supports_color(),
            )
        )

    def _print_error(self, error: BaseException) -> None:
        print(
            format_status_line(
                "error",
                str(error),
                level="error",
                use_color=stderr_supports_color(),
            ),
            file=sys.stderr,
        )

    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description=f"Manage {self.repo_display_name} source-root workflow state."
        )
        mode_group = parser.add_mutually_exclusive_group(required=True)
        mode_group.add_argument(
            "--init",
            action="store_true",
            help="Ensure source_roots.lock.jsonc exists and refresh the recursive dependency seed closure.",
        )
        mode_group.add_argument(
            "--update",
            action="store_true",
            help="Materialize locked source roots offline and run the host update callback.",
        )
        return parser

    def _cmd_init(self) -> int:
        self._print_status("init", f"repo={self.repo_root}")
        path, created, results = self.workflow.init_seed_repositories(self.repo_root)
        if created:
            self._print_status("init", f"created active source-roots lock: {path}", level="ok")
        else:
            self._print_status("init", f"using active source-roots lock: {path}")
        for dependency_name, result in results.items():
            if dependency_name.startswith("asset:"):
                self._print_status(
                    "asset",
                    f"{dependency_name.removeprefix('asset:')}: {result}",
                    level="ok" if result == "ready" else "info",
                )
                continue
            spec = self.workflow.spec_by_dependency_name.get(dependency_name)
            if spec is None:
                seed_root = (
                    self.repo_root / "build" / "dependency_seed_repos" / dependency_name
                )
            else:
                seed_root = self.workflow.seed_repo_root_for_spec(
                    spec,
                    self.repo_root,
                )
            self._print_status(
                "seed",
                f"{dependency_name}: {result} -> {seed_root}",
                level="ok" if result == "ready" else "info",
            )
        return 0

    def _cmd_update(self) -> int:
        self._print_status("update", f"repo={self.repo_root}")
        self._print_status(
            "update",
            "materializing source roots from the active lock; network is disabled",
        )
        before_lock_data = self.workflow.load_lock_file(self.repo_root)
        source_roots = self.workflow.materialize_source_roots(
            self.repo_root,
            allow_network=False,
        )
        problems = self.workflow.verify_source_roots(source_roots)
        if problems:
            details = "\n".join(f"- {problem}" for problem in problems)
            raise FileNotFoundError(
                "Workspace source roots are not ready after offline materialization:\n"
                f"{details}"
            )
        self._print_status("update", "materialized source roots", level="ok")
        use_color = stdout_supports_color()
        for line in format_dependency_commit_change_lines(
            dependency_commit_changes(
                before_lock_data,
                source_roots.lock_data,
                source_roots.dependency_roots.direct_dependency_names,
            ),
            use_color=use_color,
        ):
            print(line)
        if self.print_update_resolutions:
            for line in format_dependency_resolution_lines(
                self.workflow.dependency_resolutions(source_roots),
                use_color=use_color,
            ):
                print(line)
        if self.update_callback is None:
            return 0
        self._print_status("update", "running host update callback")
        return self.update_callback()

    def main(self, argv: list[str] | None = None) -> int:
        parser = self.build_parser()
        args = parser.parse_args(argv)
        try:
            if args.init:
                return self._cmd_init()
            return self._cmd_update()
        except (
            FileNotFoundError,
            FileExistsError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            self._print_error(error)
            return 1


__all__ = ("SourceRootWorkflowScript",)
