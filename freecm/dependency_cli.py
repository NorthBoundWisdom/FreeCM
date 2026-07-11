# Internal: dependency-root CLI adapter used by DependencyRootManager.

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from .cli_support import CLI_DATA_ERRORS, CLI_PROCESS_ERRORS, run_cli_action
from .dependency_commands import DependencyRootCommandBindings, DependencyRootCommands
from .dependency_models import ResolvedDependencyRoots
from .dependency_reports import has_error_policy_violations
from .terminal_style import (
    format_root_override_transitive_pin_mismatch_lines,
    stderr_supports_color,
)


class DependencyRootCli:
    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self._commands = DependencyRootCommands(
            DependencyRootCommandBindings(
                load_roots=lambda: self.manager.load_dependency_roots(),
                require_roots=lambda: self.manager.require_dependency_roots(),
                materialize_roots=lambda _quiet: self.manager.materialize_dependency_roots(
                    allow_network=False
                ),
                pin_ref=lambda dependency_name, ref: self.manager.pin_dependency_ref(
                    dependency_name,
                    ref,
                    allow_fetch=False,
                ),
                environment_map=lambda roots: roots.as_environment_map(),
                json_dict=lambda roots: roots.as_json_dict(),
                report_error=lambda error: print(str(error), file=sys.stderr),
                read_error_types=CLI_DATA_ERRORS,
                mutation_error_types=CLI_PROCESS_ERRORS,
            )
        )

    def _print_resolve_plain(
        self,
        dependency_roots: ResolvedDependencyRoots,
    ) -> None:
        print(f"mode={dependency_roots.mode}")
        print("closureOrder=" + ",".join(dependency_roots.closure_order))
        for dependency_name in dependency_roots.closure_order:
            record = dependency_roots.dependency_record_for(dependency_name)
            parents = ",".join(record["parents"]) or "<root>"
            children = ",".join(record["children"]) or "-"
            commit = str(record["commit"])
            print(
                f"{dependency_name}: repo={record['repoName']} "
                f"mode={record['mode']} direct={str(record['direct']).lower()} "
                f"commit={commit} path={record['path']} seed={record['seedPath']} "
                f"parents={parents} children={children}"
            )

    def cmd_verify(self, _: argparse.Namespace) -> int:
        return self._commands.cmd_verify(_)

    def cmd_show(self, args: argparse.Namespace) -> int:
        return self._commands.cmd_status(args)

    def cmd_resolve(self, args: argparse.Namespace) -> int:
        def render(dependency_roots: ResolvedDependencyRoots) -> int:
            if args.format == "json":
                print(json.dumps(dependency_roots.as_json_dict(), indent=2))
            else:
                self._print_resolve_plain(dependency_roots)
            return 0

        return run_cli_action(
            self.manager.load_dependency_roots,
            render,
            error_types=CLI_DATA_ERRORS,
            report_error=lambda error: print(str(error), file=sys.stderr),
        )

    def cmd_materialize(self, _: argparse.Namespace) -> int:
        return self._commands.cmd_materialize(_)

    def cmd_pin(self, args: argparse.Namespace) -> int:
        return self._commands.cmd_pin(args)

    def cmd_graph(self, args: argparse.Namespace) -> int:
        def action() -> str:
            if args.format == "dot":
                return str(self.manager.dependency_graph_dot())
            return json.dumps(self.manager.dependency_graph_report(), indent=2)

        def render(output: str) -> int:
            print(output)
            return 0

        return run_cli_action(
            action,
            render,
            error_types=CLI_PROCESS_ERRORS,
            report_error=lambda error: print(str(error), file=sys.stderr),
        )

    def cmd_audit(self, args: argparse.Namespace) -> int:
        return run_cli_action(
            self.manager.dependency_audit_report,
            lambda report: self._render_audit(report, args.format),
            error_types=CLI_PROCESS_ERRORS,
            report_error=lambda error: print(str(error), file=sys.stderr),
        )

    @staticmethod
    def _render_audit(report: dict[str, Any], output_format: str) -> int:
        if output_format == "json":
            print(json.dumps(report, indent=2))
            return 0 if not has_error_policy_violations(report) and not report["conflicts"] else 1
        if report["conflicts"]:
            for conflict in report["conflicts"]:
                print(conflict["message"], file=sys.stderr)
                for action in conflict.get("suggestedActions", ()):
                    print(f"- {action}", file=sys.stderr)
            return 1
        for line in format_root_override_transitive_pin_mismatch_lines(
            report.get("rootOverrideTransitivePinMismatches", ()),
            use_color=stderr_supports_color(),
        ):
            print(line, file=sys.stderr)
        for warning in report.get("modeWarnings", ()):
            if isinstance(warning, dict):
                print(warning.get("message", ""), file=sys.stderr)
        if has_error_policy_violations(report):
            for violation in report["policyViolations"]:
                if violation.get("severity", "error") == "error":
                    print(violation["message"], file=sys.stderr)
            return 1
        print("audit ok")
        return 0

    def cmd_explain_conflict(self, args: argparse.Namespace) -> int:
        return run_cli_action(
            lambda: self.manager.dependency_conflict_report(args.dep),
            lambda report: self._render_conflict(report, args.dep, args.format),
            error_types=CLI_PROCESS_ERRORS,
            report_error=lambda error: print(str(error), file=sys.stderr),
        )

    @staticmethod
    def _render_conflict(report: dict[str, Any], dependency_name: str, output_format: str) -> int:
        if output_format == "json":
            print(json.dumps(report, indent=2))
        elif report["conflicts"]:
            conflict = report["conflicts"][0]
            print(conflict["message"])
            print(
                f"- existing: {conflict['existing']['source'] or '<unknown>'} "
                f"({conflict['existing']['parentDependencyName'] or 'root'}) "
                f"{conflict['existing']['value']!r}"
            )
            print(
                f"- candidate: {conflict['candidate']['source'] or '<unknown>'} "
                f"({conflict['candidate']['parentDependencyName'] or 'root'}) "
                f"{conflict['candidate']['value']!r}"
            )
            print("Suggested actions:")
            for action in conflict.get("suggestedActions", ()):
                print(f"- {action}")
        else:
            print(f"No dependency conflict found for {dependency_name}.")
        return 0 if report["found"] else 1

    def cmd_policy_check(self, args: argparse.Namespace) -> int:
        return run_cli_action(
            self.manager.dependency_policy_report,
            lambda report: self._render_policy(report, args.format),
            error_types=CLI_PROCESS_ERRORS,
            report_error=lambda error: print(str(error), file=sys.stderr),
        )

    @staticmethod
    def _render_policy(report: dict[str, Any], output_format: str) -> int:
        if output_format == "json":
            print(json.dumps(report, indent=2))
        elif report["policyViolations"]:
            for violation in report["policyViolations"]:
                print(violation["message"], file=sys.stderr)
        else:
            print("policy ok")
        return 0 if not has_error_policy_violations(report) else 1

    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description=(
                f"Resolve, materialize, and validate "
                f"{self.manager.config.repo_display_name} dependency roots."
            )
        )
        subparsers = parser.add_subparsers(dest="command", required=True)

        show = subparsers.add_parser("show", help="Print final concrete dependency roots.")
        show.add_argument(
            "--format",
            choices=("plain", "shell", "json"),
            default="plain",
            help="Output format.",
        )
        show.set_defaults(func=self.cmd_show)

        resolve = subparsers.add_parser(
            "resolve",
            help="Print the fully resolved dependency closure.",
        )
        resolve.add_argument(
            "--format",
            choices=("plain", "json"),
            default="plain",
            help="Output format.",
        )
        resolve.set_defaults(func=self.cmd_resolve)

        verify = subparsers.add_parser("verify", help="Validate final concrete dependency roots.")
        verify.set_defaults(func=self.cmd_verify)

        materialize = subparsers.add_parser(
            "materialize",
            help=(
                "Materialize concrete roots from local seed repos under "
                "build/dependency_source_roots."
            ),
        )
        materialize.set_defaults(func=self.cmd_materialize)

        pin = subparsers.add_parser(
            "pin",
            help="Resolve a dependency ref from the local seed repo and write it to the lock file.",
        )
        pin.add_argument(
            "--dep",
            required=True,
            choices=self.manager.direct_dependency_names,
            help="Dependency name to pin.",
        )
        pin.add_argument("--ref", required=True, help="Git ref to resolve.")
        pin.set_defaults(func=self.cmd_pin)

        graph = subparsers.add_parser(
            "graph",
            help="Print the resolved dependency graph from local seed repos.",
        )
        graph.add_argument(
            "--format",
            choices=("json", "dot"),
            default="json",
            help="Output format.",
        )
        graph.set_defaults(func=self.cmd_graph)

        audit = subparsers.add_parser(
            "audit",
            help="Print a machine-readable dependency audit report.",
        )
        audit.add_argument(
            "--format",
            choices=("plain", "json"),
            default="plain",
            help="Output format.",
        )
        audit.set_defaults(func=self.cmd_audit)

        policy_check = subparsers.add_parser(
            "policy-check",
            help="Validate direct dependency lock entries against configs/freecm_policy.jsonc.",
        )
        policy_check.add_argument(
            "--format",
            choices=("plain", "json"),
            default="plain",
            help="Output format.",
        )
        policy_check.set_defaults(func=self.cmd_policy_check)

        explain_conflict = subparsers.add_parser(
            "explain-conflict",
            help="Explain a dependency closure conflict for a dependency name.",
        )
        explain_conflict.add_argument("dep", help="Dependency name to explain.")
        explain_conflict.add_argument(
            "--format",
            choices=("plain", "json"),
            default="plain",
            help="Output format.",
        )
        explain_conflict.set_defaults(func=self.cmd_explain_conflict)
        return parser

    def main(self) -> int:
        parser = self.build_parser()
        args = parser.parse_args()
        func: Callable[[argparse.Namespace], int] = args.func
        return func(args)


__all__ = ("DependencyRootCli",)
