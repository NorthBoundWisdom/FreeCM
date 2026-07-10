# Internal: dependency-root CLI adapter used by DependencyRootManager.

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from typing import Any

from .dependency_models import ResolvedDependencyRoots
from .dependency_reports import has_error_policy_violations
from .path_maps import print_environment_map
from .terminal_style import (
    format_root_override_transitive_pin_mismatch_lines,
    stderr_supports_color,
)


class DependencyRootCli:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

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

    def _print_env_map(
        self,
        dependency_roots: ResolvedDependencyRoots,
        output_format: str,
    ) -> None:
        print_environment_map(dependency_roots.as_environment_map(), output_format)

    def cmd_verify(self, _: argparse.Namespace) -> int:
        try:
            dependency_roots = self.manager.require_dependency_roots()
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        self._print_env_map(dependency_roots, "plain")
        return 0

    def cmd_show(self, args: argparse.Namespace) -> int:
        try:
            dependency_roots = self.manager.load_dependency_roots()
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(dependency_roots.as_json_dict(), indent=2))
            return 0
        self._print_env_map(dependency_roots, args.format)
        return 0

    def cmd_resolve(self, args: argparse.Namespace) -> int:
        try:
            dependency_roots = self.manager.load_dependency_roots()
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(dependency_roots.as_json_dict(), indent=2))
            return 0
        self._print_resolve_plain(dependency_roots)
        return 0

    def cmd_materialize(self, _: argparse.Namespace) -> int:
        try:
            dependency_roots = self.manager.materialize_dependency_roots(
                allow_network=False,
            )
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            print(str(error), file=sys.stderr)
            return 1
        self._print_env_map(dependency_roots, "plain")
        return 0

    def cmd_pin(self, args: argparse.Namespace) -> int:
        try:
            commit = self.manager.pin_dependency_ref(args.dep, args.ref)
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            print(str(error), file=sys.stderr)
            return 1
        print(f"{args.dep}={commit}")
        return 0

    def cmd_graph(self, args: argparse.Namespace) -> int:
        try:
            if args.format == "dot":
                print(self.manager.dependency_graph_dot())
                return 0
            print(json.dumps(self.manager.dependency_graph_report(), indent=2))
            return 0
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            print(str(error), file=sys.stderr)
            return 1

    def cmd_audit(self, args: argparse.Namespace) -> int:
        try:
            report = self.manager.dependency_audit_report()
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            print(str(error), file=sys.stderr)
            return 1
        if args.format == "json":
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
        try:
            report = self.manager.dependency_conflict_report(args.dep)
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            print(str(error), file=sys.stderr)
            return 1
        if args.format == "json":
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
            print(f"No dependency conflict found for {args.dep}.")
        return 0 if report["found"] else 1

    def cmd_policy_check(self, args: argparse.Namespace) -> int:
        try:
            report = self.manager.dependency_policy_report()
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            print(str(error), file=sys.stderr)
            return 1
        if args.format == "json":
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
