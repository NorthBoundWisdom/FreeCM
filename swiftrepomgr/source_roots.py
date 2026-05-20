#!/usr/bin/env python3
# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m swiftrepomgr.source_roots --help
#   Library: from swiftrepomgr.source_roots import SourceRootWorkflow

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from depsfixture.dependency_roots import (
    DEPENDENCY_LOCK_SCHEMA_VERSION,
    VALID_MODES,
    DependencyRootConfig,
    DependencyRootManager,
    DependencyRootSpec,
    ResolvedDependencyRoots,
)
from depsfixture.asset_seeds import prepare_asset_seeds, require_asset_seeds
from depsfixture.path_maps import (
    dedupe_dependency_specs,
    dependency_root_path_map,
    environment_map,
    print_environment_map,
)

from .swift_configs import load_swift_configs
from .terminal_style import format_status_line, stderr_supports_color, stdout_supports_color


BUILD_SETTING_KEYS = (
    "XCODE_DEVELOPMENT_TEAM",
    "MARKETING_VERSION",
    "ARCHIVE_ID",
)
SWIFT_CONFIG_KEYS = (*BUILD_SETTING_KEYS, "commercePolicy")
DEFAULT_REQUIRED_RELATIVE_PATHS: tuple[str, ...] = ("CMakeLists.txt",)
SourceRootDependencySpec = DependencyRootSpec


@dataclass(frozen=True)
class ExtraSourceRootPathSpec:
    env_key: str
    dependency_name: str
    relative_path: str
    required_relative_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceRootWorkflowConfig:
    repo_root: Path
    source_root_specs: tuple[SourceRootDependencySpec, ...]
    repo_display_name: str
    known_source_root_specs: tuple[SourceRootDependencySpec, ...] = ()
    extra_path_specs: tuple[ExtraSourceRootPathSpec, ...] = ()
    default_required_relative_paths: tuple[str, ...] = DEFAULT_REQUIRED_RELATIVE_PATHS
    swift_config_keys: tuple[str, ...] = SWIFT_CONFIG_KEYS
    swift_config_defaults: Mapping[str, str] | None = None
    xcode_manual_sync_command: str = "`python3 configs/source_root_workflow.py --update`"


@dataclass(frozen=True)
class DependencyResolution:
    dependency_name: str
    mode: str
    commit: str | None
    path: Path


@dataclass(frozen=True)
class ResolvedSourceRoots:
    dependency_roots: ResolvedDependencyRoots
    source_root_specs: tuple[SourceRootDependencySpec, ...]
    known_source_root_specs: tuple[SourceRootDependencySpec, ...]
    extra_path_specs: tuple[ExtraSourceRootPathSpec, ...]
    swift_config_keys: tuple[str, ...]
    swift_configs: dict[str, str]
    xcode_manual_sync_command: str

    @property
    def mode(self) -> str:
        return self.dependency_roots.mode

    @property
    def deps_mode(self) -> str:
        return str(self.dependency_roots.lock_data["depsMode"])

    @property
    def mode_source(self) -> str:
        return "lock"

    @property
    def mode_env_value(self) -> str | None:
        return None

    @property
    def repo_root(self) -> Path:
        return self.dependency_roots.repo_root

    @property
    def lock_data(self) -> dict[str, Any]:
        return self.dependency_roots.lock_data

    @property
    def build_settings(self) -> dict[str, str]:
        return {
            key: self.swift_configs[key]
            for key in BUILD_SETTING_KEYS
            if key in self.swift_configs
        }

    @property
    def deps_manual_path(self) -> dict[str, str]:
        deps_manual_path = self.lock_data["depsManualPath"]
        return {
            spec.dependency_name: str(deps_manual_path[spec.dependency_name])
            for spec in self.source_root_specs
        }

    @property
    def closure_order(self) -> tuple[str, ...]:
        return self.dependency_roots.closure_order

    @property
    def resolved_commits_by_dependency(self) -> dict[str, str]:
        return self.dependency_roots.resolved_commits_by_dependency

    @property
    def resolved_commits(self) -> dict[str, str]:
        return self.dependency_roots.resolved_commits

    @property
    def lock_commits(self) -> dict[str, str]:
        return self.dependency_roots.lock_commits

    def dependency_spec_for_dependency(self, dependency_name: str):
        return self.dependency_roots.dependency_pin_for(dependency_name)

    def manual_root_override_for_dependency(self, dependency_name: str) -> Path | None:
        return self.dependency_roots.manual_root_override_for(dependency_name)

    def root_for_dependency(self, dependency_name: str) -> Path:
        return self.dependency_roots.dependency_root_for(dependency_name)

    def seed_root_for_dependency(self, dependency_name: str) -> Path:
        return self.dependency_roots.seed_repository_for(dependency_name)

    def as_path_map(self) -> dict[str, Path]:
        path_map = dependency_root_path_map(
            self.known_source_root_specs,
            self.root_for_dependency,
        )
        for extra_spec in self.extra_path_specs:
            path_map[extra_spec.env_key] = (
                self.root_for_dependency(extra_spec.dependency_name)
                / extra_spec.relative_path
            ).resolve()
        return path_map

    def as_env_map(self) -> dict[str, str]:
        return environment_map(self.as_path_map())

    def as_json_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": DEPENDENCY_LOCK_SCHEMA_VERSION,
            "mode": self.mode,
            "depsMode": self.deps_mode,
            "modeSource": self.mode_source,
            "modeEnvValue": self.mode_env_value,
            "SwiftConfigs": dict(self.swift_configs),
            "depsManualPath": self.deps_manual_path,
            "roots": self.as_env_map(),
            "dependencyRoots": {
                dependency_name: str(self.root_for_dependency(dependency_name))
                for dependency_name in self.closure_order
            },
            "seedRoots": {
                dependency_name: str(self.seed_root_for_dependency(dependency_name))
                for dependency_name in self.closure_order
            },
            "lock": self.lock_commits,
            "resolvedCommits": self.resolved_commits,
            "closureOrder": list(self.closure_order),
            "dependencyNamesByParent": {
                dependency_name: list(child_names)
                for dependency_name, child_names in self.dependency_roots.dependency_names_by_parent.items()
            },
        }


class SourceRootWorkflow:
    def __init__(self, config: SourceRootWorkflowConfig):
        self.config = config
        self.repo_root = config.repo_root.resolve()
        self.source_root_specs = tuple(config.source_root_specs)
        self.known_source_root_specs = dedupe_dependency_specs(
            config.known_source_root_specs or self.source_root_specs
        )
        self.extra_path_specs = tuple(config.extra_path_specs)
        self.swift_config_keys = tuple(config.swift_config_keys)
        self.swift_config_defaults = dict(config.swift_config_defaults or {})
        self.direct_dependency_names = tuple(
            spec.dependency_name for spec in self.source_root_specs
        )
        self.spec_by_env_key = {
            spec.env_key: spec for spec in self.known_source_root_specs
        }
        self.spec_by_dependency_name = {
            spec.dependency_name: spec for spec in self.known_source_root_specs
        }
        self.direct_spec_by_dependency_name = {
            spec.dependency_name: spec for spec in self.source_root_specs
        }
        self._manager = DependencyRootManager(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=self.source_root_specs,
                repo_display_name=config.repo_display_name,
                default_required_relative_paths=config.default_required_relative_paths,
            )
        )
        self._manager.spec_by_dependency_name.update(
            {spec.dependency_name: spec for spec in self.known_source_root_specs}
        )

    def _repo_root(self, repo_root: Path | None = None) -> Path:
        return repo_root.resolve() if repo_root else self.repo_root

    def _lock_file_path(self, repo_root: Path) -> Path:
        return repo_root / "source_roots.lock.jsonc"

    def _load_swift_configs(
        self,
        lock_data: Mapping[str, Any],
        *,
        path_label: str | Path,
    ) -> dict[str, str]:
        return load_swift_configs(
            lock_data,
            path_label=path_label,
            swift_config_keys=self.swift_config_keys,
            swift_config_defaults=self.swift_config_defaults,
        )

    def _wrap(self, dependency_roots: ResolvedDependencyRoots) -> ResolvedSourceRoots:
        return ResolvedSourceRoots(
            dependency_roots=dependency_roots,
            source_root_specs=self.source_root_specs,
            known_source_root_specs=self.known_source_root_specs,
            extra_path_specs=self.extra_path_specs,
            swift_config_keys=self.swift_config_keys,
            swift_configs=self._load_swift_configs(
                dependency_roots.lock_data,
                path_label=self._lock_file_path(dependency_roots.repo_root),
            ),
            xcode_manual_sync_command=self.config.xcode_manual_sync_command,
        )

    def seed_repo_root_for_spec(
        self,
        spec: object,
        repo_root: Path | None = None,
    ) -> Path:
        repo_root = self._repo_root(repo_root)
        repo_name = getattr(spec, "repo_name")
        return (repo_root / "build" / "dependency_seed_repos" / str(repo_name)).resolve()

    def init_seed_repositories(
        self,
        repo_root: Path | None = None,
    ) -> tuple[Path, bool, dict[str, str]]:
        repo_root = self._repo_root(repo_root)
        active_path, created = self._manager.ensure_active_lock_file(repo_root)
        lock_data = self._manager.load_lock_file(repo_root)
        self._load_swift_configs(lock_data, path_label=active_path)
        closure = self._manager.prepare_seed_repository_closure(repo_root)
        results = {
            dependency_name: "ready"
            for dependency_name in closure.topo_order
        }
        for summary in prepare_asset_seeds(repo_root):
            results[f"asset:{summary.asset_name}"] = "ready"
        return active_path.resolve(), created, results

    def resolve_source_roots(
        self,
        repo_root: Path | None = None,
        *,
        materialize: bool = False,
        allow_network: bool = False,
    ) -> ResolvedSourceRoots:
        if materialize:
            return self.materialize_source_roots(repo_root, allow_network=allow_network)
        dependency_roots = self._manager.load_dependency_roots(self._repo_root(repo_root))
        return self._wrap(dependency_roots)

    def load_lock_file(self, repo_root: Path | None = None) -> dict[str, Any]:
        return self._manager.load_lock_file(self._repo_root(repo_root))

    def materialize_source_roots(
        self,
        repo_root: Path | None = None,
        *,
        allow_network: bool = False,
    ) -> ResolvedSourceRoots:
        dependency_roots = self._manager.materialize_dependency_roots(
            self._repo_root(repo_root),
            allow_network=allow_network,
        )
        if not allow_network:
            require_asset_seeds(dependency_roots.repo_root)
        return self._wrap(dependency_roots)

    def verify_source_roots(self, source_roots: ResolvedSourceRoots) -> list[str]:
        problems = self._manager.validate_dependency_roots(source_roots.dependency_roots)
        for extra_spec in self.extra_path_specs:
            extra_root = source_roots.root_for_dependency(extra_spec.dependency_name) / extra_spec.relative_path
            if not extra_root.exists():
                problems.append(f"{extra_spec.env_key} missing path: {extra_root}")
                continue
            for relative_path in extra_spec.required_relative_paths:
                candidate = extra_root / relative_path
                if not candidate.exists():
                    problems.append(f"{extra_spec.env_key} missing required path: {candidate}")
        return problems

    def require_source_roots(
        self,
        repo_root: Path | None = None,
        *,
        materialize: bool = False,
        allow_network: bool = False,
        missing_roots_hint: str | None = None,
    ) -> ResolvedSourceRoots:
        source_roots = self.resolve_source_roots(
            repo_root,
            materialize=materialize,
            allow_network=allow_network,
        )
        problems = self.verify_source_roots(source_roots)
        if problems:
            details = "\n".join(f"- {problem}" for problem in problems)
            hint = missing_roots_hint or "Run `python3 configs/source_roots.py materialize`."
            raise FileNotFoundError(
                "Workspace source roots are not ready:\n"
                f"{details}\n"
                f"{hint}"
            )
        return source_roots

    def dependency_resolutions(
        self,
        source_roots: ResolvedSourceRoots,
    ) -> tuple[DependencyResolution, ...]:
        return tuple(
            DependencyResolution(
                dependency_name=summary.dependency_name,
                mode=summary.mode,
                commit=summary.commit,
                path=summary.path,
            )
            for summary in self._manager.describe_dependency_roots(source_roots.dependency_roots)
        )

    def pin_dependency_ref(
        self,
        dependency_name: str,
        ref: str,
        repo_root: Path | None = None,
    ) -> str:
        return self._manager.pin_dependency_ref(
            dependency_name,
            ref,
            repo_root=self._repo_root(repo_root),
        )

    def _print_env_map(self, source_roots: ResolvedSourceRoots, output_format: str) -> None:
        print_environment_map(source_roots.as_env_map(), output_format)

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

    def cmd_status(self, args: argparse.Namespace) -> int:
        try:
            source_roots = self.resolve_source_roots(
                materialize=False,
                allow_network=False,
            )
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            self._print_error(error)
            return 1

        if args.format == "json":
            print(json.dumps(source_roots.as_json_dict(), indent=2))
            return 0
        self._print_env_map(source_roots, args.format)
        return 0

    def cmd_verify(self, _: argparse.Namespace) -> int:
        try:
            source_roots = self.require_source_roots(
                materialize=False,
                allow_network=False,
            )
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            self._print_error(error)
            return 1
        self._print_env_map(source_roots, "plain")
        return 0

    def cmd_materialize(self, _: argparse.Namespace) -> int:
        try:
            source_roots = self.materialize_source_roots(allow_network=False)
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            self._print_error(error)
            return 1
        self._print_env_map(source_roots, "plain")
        return 0

    def cmd_init_seeds(self, _: argparse.Namespace) -> int:
        try:
            path, created, results = self.init_seed_repositories()
        except (
            FileNotFoundError,
            FileExistsError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            self._print_error(error)
            return 1

        if created:
            self._print_status("init", f"created active source-roots lock: {path}", level="ok")
        else:
            self._print_status("init", f"using active source-roots lock: {path}")
        for dependency_name, result in results.items():
            spec = self.spec_by_dependency_name.get(dependency_name)
            seed_root = (
                self.repo_root / "build" / "dependency_seed_repos" / dependency_name
                if spec is None
                else self.seed_repo_root_for_spec(spec)
            )
            self._print_status(
                "seed",
                f"{dependency_name}: {result} -> {seed_root}",
                level="ok" if result == "ready" else "info",
            )
        return 0

    def cmd_pin(self, args: argparse.Namespace) -> int:
        try:
            commit = self.pin_dependency_ref(args.dep, args.ref)
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            self._print_error(error)
            return 1

        print(f"{args.dep}={commit}")
        return 0

    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description=f"Resolve, materialize, and validate {self.config.repo_display_name} dependency source roots."
        )
        subparsers = parser.add_subparsers(dest="command", required=True)

        status = subparsers.add_parser("status", help="Print final concrete source roots.")
        status.add_argument(
            "--format",
            choices=("plain", "shell", "json"),
            default="plain",
            help="Output format.",
        )
        status.set_defaults(func=self.cmd_status)

        verify = subparsers.add_parser("verify", help="Validate final concrete source roots.")
        verify.set_defaults(func=self.cmd_verify)

        materialize = subparsers.add_parser(
            "materialize",
            help="Prepare pinned/latest concrete roots under build/dependency_source_roots.",
        )
        materialize.set_defaults(func=self.cmd_materialize)

        init_seeds = subparsers.add_parser(
            "init-seeds",
            help="Ensure source_roots.lock.jsonc exists and refresh the recursive dependency seed closure.",
        )
        init_seeds.set_defaults(func=self.cmd_init_seeds)

        pin = subparsers.add_parser(
            "pin",
            help="Resolve a direct dependency ref to an exact commit and write it to source_roots.lock.jsonc.",
        )
        pin.add_argument(
            "--dep",
            required=True,
            choices=self.direct_dependency_names,
            help="Direct dependency name to pin.",
        )
        pin.add_argument("--ref", required=True, help="Git ref to resolve.")
        pin.set_defaults(func=self.cmd_pin)

        return parser

    def main(self, argv: list[str] | None = None) -> int:
        parser = self.build_parser()
        args = parser.parse_args(argv)
        return args.func(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Swift repo helpers are bound by a repository config module. "
            "Import SourceRootWorkflow from swiftrepomgr.source_roots, or run "
            "the host repository configs/source_root_workflow.py --init|--update."
        )
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"dependency lock schema {DEPENDENCY_LOCK_SCHEMA_VERSION}",
    )
    parser.parse_args(argv)
    return 0


__all__ = (
    "BUILD_SETTING_KEYS",
    "DEFAULT_REQUIRED_RELATIVE_PATHS",
    "DependencyResolution",
    "ExtraSourceRootPathSpec",
    "ResolvedSourceRoots",
    "SourceRootDependencySpec",
    "SourceRootWorkflow",
    "SourceRootWorkflowConfig",
    "SWIFT_CONFIG_KEYS",
    "VALID_MODES",
)


if __name__ == "__main__":
    raise SystemExit(main())
