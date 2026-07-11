# Usage:
#   Library: from freecm.source_root_workflow import SourceRootWorkflowScript

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

from .cli_support import CLI_INIT_ERRORS, run_cli_action
from .dependency_roots import DependencyRootSpec, dependency_commit_changes
from .terminal_style import (
    format_dependency_commit_change_lines,
    format_dependency_resolution_lines,
    format_status_line,
    stderr_supports_color,
    stdout_supports_color,
)


class _ResolvedDependencyRootsLike(Protocol):
    @property
    def direct_dependency_names(self) -> tuple[str, ...]: ...


class _MaterializedSourceRootsLike(Protocol):
    @property
    def lock_data(self) -> dict[str, Any]: ...

    @property
    def dependency_roots(self) -> _ResolvedDependencyRootsLike: ...


SourceRootsT = TypeVar("SourceRootsT", bound=_MaterializedSourceRootsLike)


class SourceRootWorkflowLike(Protocol[SourceRootsT]):
    repo_root: Path

    @property
    def spec_by_dependency_name(self) -> Mapping[str, DependencyRootSpec]: ...

    def init_seed_repositories(
        self,
        repo_root: Path | None = None,
        *,
        progress: Callable[[str, str, str], None] | None = None,
        quiet: bool = False,
    ) -> tuple[Path, bool, dict[str, str]]: ...

    def materialize_source_roots(
        self,
        repo_root: Path | None = None,
        *,
        allow_network: bool = False,
        quiet: bool = False,
    ) -> SourceRootsT: ...

    def verify_source_roots(self, source_roots: SourceRootsT) -> list[str]: ...

    def dependency_resolutions(self, source_roots: SourceRootsT) -> Sequence[object]: ...

    def load_lock_file(self, repo_root: Path | None = None) -> dict[str, Any]: ...

    def seed_repo_root_for_spec(
        self,
        spec: DependencyRootSpec,
        repo_root: Path | None = None,
    ) -> Path: ...


class SourceRootWorkflowScript(Generic[SourceRootsT]):
    def __init__(
        self,
        workflow: SourceRootWorkflowLike[SourceRootsT],
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
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress verbose git output while keeping FreeCM status lines.",
        )
        return parser

    def _cmd_init(self, *, quiet: bool = False) -> int:
        self._print_status("init", f"repo={self.repo_root}")
        path, created, results = self.workflow.init_seed_repositories(
            self.repo_root,
            progress=lambda action, message, level: self._print_status(
                action,
                message,
                level=level,
            ),
            quiet=quiet,
        )
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
                seed_root = self.repo_root / "build" / "dependency_seed_repos" / dependency_name
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

    def _cmd_update(self, *, quiet: bool = False) -> int:
        self._print_status("update", f"repo={self.repo_root}")
        self._print_status(
            "update",
            "materializing source roots from the active lock; network is disabled",
        )
        before_lock_data = self.workflow.load_lock_file(self.repo_root)
        source_roots = self.workflow.materialize_source_roots(
            self.repo_root,
            allow_network=False,
            quiet=quiet,
        )
        problems = self.workflow.verify_source_roots(source_roots)
        if problems:
            details = "\n".join(f"- {problem}" for problem in problems)
            raise FileNotFoundError(
                "Workspace source roots are not ready after offline materialization:\n" f"{details}"
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
        action = (
            (lambda: self._cmd_init(quiet=args.quiet))
            if args.init
            else (lambda: self._cmd_update(quiet=args.quiet))
        )
        return run_cli_action(
            action,
            lambda result: result,
            error_types=CLI_INIT_ERRORS,
            report_error=self._print_error,
        )


__all__ = ("SourceRootWorkflowScript",)
