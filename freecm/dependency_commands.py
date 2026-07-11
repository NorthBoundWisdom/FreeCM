# Usage:
#   Library: from freecm.dependency_commands import DependencyRootCommands

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .cli_support import run_cli_action
from .path_maps import print_environment_map


@dataclass(frozen=True)
class DependencyRootCommandBindings:
    load_roots: Callable[[], Any]
    require_roots: Callable[[], Any]
    materialize_roots: Callable[[bool], Any]
    pin_ref: Callable[[str, str], str]
    environment_map: Callable[[Any], Mapping[str, str]]
    json_dict: Callable[[Any], Mapping[str, Any]]
    report_error: Callable[[BaseException], None]
    read_error_types: tuple[type[Exception], ...]
    mutation_error_types: tuple[type[Exception], ...]


class DependencyRootCommands:
    def __init__(self, bindings: DependencyRootCommandBindings) -> None:
        self.bindings = bindings

    def _render_environment(self, roots: Any, output_format: str) -> int:
        print_environment_map(self.bindings.environment_map(roots), output_format)
        return 0

    def cmd_status(self, args: argparse.Namespace) -> int:
        def render(roots: Any) -> int:
            if args.format == "json":
                print(json.dumps(self.bindings.json_dict(roots), indent=2))
                return 0
            return self._render_environment(roots, args.format)

        return run_cli_action(
            self.bindings.load_roots,
            render,
            error_types=self.bindings.read_error_types,
            report_error=self.bindings.report_error,
        )

    def cmd_verify(self, _: argparse.Namespace) -> int:
        return run_cli_action(
            self.bindings.require_roots,
            lambda roots: self._render_environment(roots, "plain"),
            error_types=self.bindings.read_error_types,
            report_error=self.bindings.report_error,
        )

    def cmd_materialize(self, args: argparse.Namespace) -> int:
        return run_cli_action(
            lambda: self.bindings.materialize_roots(getattr(args, "quiet", False)),
            lambda roots: self._render_environment(roots, "plain"),
            error_types=self.bindings.mutation_error_types,
            report_error=self.bindings.report_error,
        )

    def cmd_pin(self, args: argparse.Namespace) -> int:
        return run_cli_action(
            lambda: self.bindings.pin_ref(args.dep, args.ref),
            lambda commit: self._render_pin(args.dep, commit),
            error_types=self.bindings.mutation_error_types,
            report_error=self.bindings.report_error,
        )

    @staticmethod
    def _render_pin(dependency_name: str, commit: str) -> int:
        print(f"{dependency_name}={commit}")
        return 0


__all__ = ("DependencyRootCommandBindings", "DependencyRootCommands")
