#!/usr/bin/env python3
# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrswift.source_roots --help
#   Library: from repomgrswift.source_roots import DependencyRootWorkflow

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from freecm.app_configs import AppConfigValue, load_app_configs
from freecm.asset_seeds import prepare_asset_seeds, require_asset_seeds
from freecm.dependency_lock import ACTIVE_LOCK_FILE_NAME
from freecm.dependency_roots import (
    DEPENDENCY_LOCK_SCHEMA_VERSION,
    VALID_MODES,
    DependencyRootConfig,
    DependencyRootManager,
    DependencyRootSpec,
    ResolvedDependencyRoots,
)
from freecm.path_maps import (
    dependency_root_path_map,
    environment_map,
    print_environment_map,
    resolve_dependency_relative_path,
    validate_dependency_relative_path,
    validate_dependency_specs,
    validate_environment_key,
)
from freecm.terminal_style import print_error, print_status

BUILD_SETTING_KEYS = (
    "XCODE_DEVELOPMENT_TEAM",
    "MARKETING_VERSION",
    "ARCHIVE_ID",
)
APP_CONFIG_KEYS = (*BUILD_SETTING_KEYS, "commercePolicy")
DEFAULT_REQUIRED_RELATIVE_PATHS: tuple[str, ...] = ()

if TYPE_CHECKING:
    from freecm.source_root_workflow import SourceRootWorkflowLike


@dataclass(frozen=True)
class ExtraDependencyPathSpec:
    env_key: str
    dependency_name: str
    relative_path: str
    required_relative_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class DependencyRootWorkflowConfig:
    repo_root: Path
    dependency_root_specs: tuple[DependencyRootSpec, ...]
    repo_display_name: str
    known_dependency_root_specs: tuple[DependencyRootSpec, ...] = ()
    extra_path_specs: tuple[ExtraDependencyPathSpec, ...] = ()
    default_required_relative_paths: tuple[str, ...] = DEFAULT_REQUIRED_RELATIVE_PATHS
    app_config_keys: tuple[str, ...] = APP_CONFIG_KEYS
    app_config_defaults: Mapping[str, AppConfigValue] | None = None
    xcode_manual_sync_command: str = "`python3 configs/source_root_workflow.py --update`"


def _validate_extra_path_specs(
    specs: tuple[ExtraDependencyPathSpec, ...],
    dependency_specs: tuple[DependencyRootSpec, ...],
) -> tuple[ExtraDependencyPathSpec, ...]:
    known_dependency_names = {spec.dependency_name for spec in dependency_specs}
    environment_keys = {spec.env_key for spec in dependency_specs}
    for spec in specs:
        validate_environment_key(spec.env_key, label="extra path environment key")
        if spec.env_key in environment_keys:
            raise ValueError(f"Duplicate environment key {spec.env_key!r} in Swift path specs")
        if spec.dependency_name not in known_dependency_names:
            raise ValueError(
                f"Unknown dependency {spec.dependency_name!r} for extra path {spec.env_key}"
            )
        validate_dependency_relative_path(
            spec.relative_path,
            label=f"{spec.env_key} extra path",
        )
        for relative_path in spec.required_relative_paths:
            validate_dependency_relative_path(
                relative_path,
                label=f"{spec.env_key} required path",
            )
        environment_keys.add(spec.env_key)
    return specs


@dataclass(frozen=True)
class DependencyResolution:
    dependency_name: str
    mode: str
    commit: str | None
    path: Path


@dataclass(frozen=True)
class ResolvedSwiftDependencyRoots:
    dependency_roots: ResolvedDependencyRoots
    dependency_root_specs: tuple[DependencyRootSpec, ...]
    known_dependency_root_specs: tuple[DependencyRootSpec, ...]
    extra_path_specs: tuple[ExtraDependencyPathSpec, ...]
    app_config_keys: tuple[str, ...]
    app_configs: dict[str, AppConfigValue]
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
            key: value
            for key in BUILD_SETTING_KEYS
            if isinstance((value := self.app_configs.get(key)), str)
        }

    @property
    def deps_manual_path(self) -> dict[str, str]:
        deps_manual_path = self.lock_data["depsManualPath"]
        return {
            spec.dependency_name: str(deps_manual_path[spec.dependency_name])
            for spec in self.dependency_root_specs
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
            self.known_dependency_root_specs,
            self.root_for_dependency,
        )
        for extra_spec in self.extra_path_specs:
            path_map[extra_spec.env_key] = resolve_dependency_relative_path(
                self.root_for_dependency(extra_spec.dependency_name),
                extra_spec.relative_path,
                label=f"{extra_spec.env_key} extra path",
            )
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
            "AppConfigs": dict(self.app_configs),
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


class DependencyRootWorkflow:
    def __init__(self, config: DependencyRootWorkflowConfig):
        self.config = config
        self.repo_root = config.repo_root.resolve()
        self.dependency_root_specs = validate_dependency_specs(
            config.dependency_root_specs,
            label=f"{config.repo_display_name} direct dependency specs",
        )
        self.known_dependency_root_specs = validate_dependency_specs(
            config.known_dependency_root_specs or self.dependency_root_specs,
            label=f"{config.repo_display_name} known dependency specs",
        )
        known_dependency_names = {spec.dependency_name for spec in self.known_dependency_root_specs}
        missing_direct_names = [
            spec.dependency_name
            for spec in self.dependency_root_specs
            if spec.dependency_name not in known_dependency_names
        ]
        if missing_direct_names:
            raise ValueError(
                "Known dependency specs are missing direct dependencies: "
                + ", ".join(missing_direct_names)
            )
        self.extra_path_specs = _validate_extra_path_specs(
            tuple(config.extra_path_specs),
            self.known_dependency_root_specs,
        )
        self.app_config_keys = tuple(config.app_config_keys)
        self.app_config_defaults = dict(config.app_config_defaults or {})
        self.direct_dependency_names = tuple(
            spec.dependency_name for spec in self.dependency_root_specs
        )
        self.spec_by_env_key = {spec.env_key: spec for spec in self.known_dependency_root_specs}
        self.spec_by_dependency_name = {
            spec.dependency_name: spec for spec in self.known_dependency_root_specs
        }
        self.direct_spec_by_dependency_name = {
            spec.dependency_name: spec for spec in self.dependency_root_specs
        }
        self._manager = DependencyRootManager(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=self.dependency_root_specs,
                repo_display_name=config.repo_display_name,
                default_required_relative_paths=config.default_required_relative_paths,
            )
        )
        self._manager.spec_by_dependency_name.update(
            {spec.dependency_name: spec for spec in self.known_dependency_root_specs}
        )

    def _repo_root(self, repo_root: Path | None = None) -> Path:
        return repo_root.resolve() if repo_root else self.repo_root

    def _lock_file_path(self, repo_root: Path) -> Path:
        return repo_root / ACTIVE_LOCK_FILE_NAME

    def _load_app_configs(
        self,
        lock_data: Mapping[str, Any],
        *,
        path_label: str | Path,
    ) -> dict[str, AppConfigValue]:
        return load_app_configs(
            lock_data,
            path_label=path_label,
            app_config_keys=self.app_config_keys,
            app_config_defaults=self.app_config_defaults,
        )

    def _wrap(self, dependency_roots: ResolvedDependencyRoots) -> ResolvedSwiftDependencyRoots:
        return ResolvedSwiftDependencyRoots(
            dependency_roots=dependency_roots,
            dependency_root_specs=self.dependency_root_specs,
            known_dependency_root_specs=self.known_dependency_root_specs,
            extra_path_specs=self.extra_path_specs,
            app_config_keys=self.app_config_keys,
            app_configs=self._load_app_configs(
                dependency_roots.lock_data,
                path_label=self._lock_file_path(dependency_roots.repo_root),
            ),
            xcode_manual_sync_command=self.config.xcode_manual_sync_command,
        )

    def seed_repo_root_for_spec(
        self,
        spec: DependencyRootSpec,
        repo_root: Path | None = None,
    ) -> Path:
        repo_root = self._repo_root(repo_root)
        repo_name = spec.repo_name
        return (repo_root / "build" / "dependency_seed_repos" / str(repo_name)).resolve()

    def init_seed_repositories(
        self,
        repo_root: Path | None = None,
        *,
        progress: Callable[[str, str, str], None] | None = None,
        quiet: bool = False,
    ) -> tuple[Path, bool, dict[str, str]]:
        repo_root = self._repo_root(repo_root)
        active_path, created = self._manager.ensure_active_lock_file(repo_root)
        lock_data = self._manager.load_lock_file(repo_root)
        self._load_app_configs(lock_data, path_label=active_path)
        closure = self._manager.prepare_seed_repository_closure(
            repo_root,
            progress=progress,
            quiet=quiet,
        )
        results = {dependency_name: "ready" for dependency_name in closure.topo_order}
        for summary in prepare_asset_seeds(repo_root):
            results[f"asset:{summary.asset_name}"] = "ready"
        return active_path.resolve(), created, results

    def resolve_dependency_roots(
        self,
        repo_root: Path | None = None,
        *,
        materialize: bool = False,
        allow_network: bool = False,
        quiet: bool = False,
    ) -> ResolvedSwiftDependencyRoots:
        if materialize:
            return self.materialize_dependency_roots(
                repo_root,
                allow_network=allow_network,
                quiet=quiet,
            )
        dependency_roots = self._manager.load_dependency_roots(self._repo_root(repo_root))
        return self._wrap(dependency_roots)

    def resolve_source_roots(
        self,
        repo_root: Path | None = None,
        *,
        materialize: bool = False,
        allow_network: bool = False,
        quiet: bool = False,
    ) -> ResolvedSwiftDependencyRoots:
        return self.resolve_dependency_roots(
            repo_root,
            materialize=materialize,
            allow_network=allow_network,
            quiet=quiet,
        )

    def load_lock_file(self, repo_root: Path | None = None) -> dict[str, Any]:
        return self._manager.load_lock_file(self._repo_root(repo_root))

    def materialize_dependency_roots(
        self,
        repo_root: Path | None = None,
        *,
        allow_network: bool = False,
        quiet: bool = False,
    ) -> ResolvedSwiftDependencyRoots:
        dependency_roots = self._manager.materialize_dependency_roots(
            self._repo_root(repo_root),
            allow_network=allow_network,
            quiet=quiet,
        )
        if not allow_network:
            require_asset_seeds(dependency_roots.repo_root)
        return self._wrap(dependency_roots)

    def materialize_source_roots(
        self,
        repo_root: Path | None = None,
        *,
        allow_network: bool = False,
        quiet: bool = False,
    ) -> ResolvedSwiftDependencyRoots:
        return self.materialize_dependency_roots(
            repo_root,
            allow_network=allow_network,
            quiet=quiet,
        )

    def verify_dependency_roots(self, dependency_roots: ResolvedSwiftDependencyRoots) -> list[str]:
        problems = self._manager.validate_dependency_roots(dependency_roots.dependency_roots)
        for extra_spec in self.extra_path_specs:
            try:
                extra_root = resolve_dependency_relative_path(
                    dependency_roots.root_for_dependency(extra_spec.dependency_name),
                    extra_spec.relative_path,
                    label=f"{extra_spec.env_key} extra path",
                )
            except ValueError as exc:
                problems.append(str(exc))
                continue
            if not extra_root.exists():
                problems.append(f"{extra_spec.env_key} missing path: {extra_root}")
                continue
            for relative_path in extra_spec.required_relative_paths:
                try:
                    candidate = resolve_dependency_relative_path(
                        extra_root,
                        relative_path,
                        label=f"{extra_spec.env_key} required path",
                    )
                except ValueError as exc:
                    problems.append(str(exc))
                    continue
                if not candidate.exists():
                    problems.append(f"{extra_spec.env_key} missing required path: {candidate}")
        return problems

    def verify_source_roots(self, source_roots: ResolvedSwiftDependencyRoots) -> list[str]:
        return self.verify_dependency_roots(source_roots)

    def require_dependency_roots(
        self,
        repo_root: Path | None = None,
        *,
        materialize: bool = False,
        allow_network: bool = False,
        quiet: bool = False,
        missing_roots_hint: str | None = None,
    ) -> ResolvedSwiftDependencyRoots:
        dependency_roots = self.resolve_dependency_roots(
            repo_root,
            materialize=materialize,
            allow_network=allow_network,
            quiet=quiet,
        )
        problems = self.verify_dependency_roots(dependency_roots)
        if problems:
            details = "\n".join(f"- {problem}" for problem in problems)
            hint = missing_roots_hint or "Run `python3 configs/source_roots.py materialize`."
            raise FileNotFoundError(
                "Workspace source roots are not ready:\n" f"{details}\n" f"{hint}"
            )
        return dependency_roots

    def dependency_resolutions(
        self,
        dependency_roots: ResolvedSwiftDependencyRoots,
    ) -> tuple[DependencyResolution, ...]:
        return tuple(
            DependencyResolution(
                dependency_name=summary.dependency_name,
                mode=summary.mode,
                commit=summary.commit,
                path=summary.path,
            )
            for summary in self._manager.describe_dependency_roots(
                dependency_roots.dependency_roots
            )
        )

    def pin_dependency_ref(
        self,
        dependency_name: str,
        ref: str,
        repo_root: Path | None = None,
        *,
        allow_fetch: bool = False,
    ) -> str:
        return self._manager.pin_dependency_ref(
            dependency_name,
            ref,
            repo_root=self._repo_root(repo_root),
            allow_fetch=allow_fetch,
        )

    def _print_env_map(
        self, dependency_roots: ResolvedSwiftDependencyRoots, output_format: str
    ) -> None:
        print_environment_map(dependency_roots.as_env_map(), output_format)

    def cmd_status(self, args: argparse.Namespace) -> int:
        try:
            dependency_roots = self.resolve_dependency_roots(
                materialize=False,
                allow_network=False,
            )
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            print_error(error)
            return 1

        if args.format == "json":
            print(json.dumps(dependency_roots.as_json_dict(), indent=2))
            return 0
        self._print_env_map(dependency_roots, args.format)
        return 0

    def cmd_verify(self, _: argparse.Namespace) -> int:
        try:
            dependency_roots = self.require_dependency_roots(
                materialize=False,
                allow_network=False,
            )
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            print_error(error)
            return 1
        self._print_env_map(dependency_roots, "plain")
        return 0

    def cmd_materialize(self, args: argparse.Namespace) -> int:
        try:
            dependency_roots = self.materialize_dependency_roots(
                allow_network=False,
                quiet=getattr(args, "quiet", False),
            )
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            print_error(error)
            return 1
        self._print_env_map(dependency_roots, "plain")
        return 0

    def cmd_init_seeds(self, args: argparse.Namespace) -> int:
        try:
            path, created, results = self.init_seed_repositories(
                progress=lambda action, message, level: print_status(
                    action,
                    message,
                    level=level,
                ),
                quiet=getattr(args, "quiet", False),
            )
        except (
            FileNotFoundError,
            FileExistsError,
            RuntimeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            print_error(error)
            return 1

        if created:
            print_status("init", f"created active source-roots lock: {path}", level="ok")
        else:
            print_status("init", f"using active source-roots lock: {path}")
        for dependency_name, result in results.items():
            spec = self.spec_by_dependency_name.get(dependency_name)
            seed_root = (
                self.repo_root / "build" / "dependency_seed_repos" / dependency_name
                if spec is None
                else self.seed_repo_root_for_spec(spec)
            )
            print_status(
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
            print_error(error)
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
        materialize.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress verbose git output while keeping FreeCM status lines.",
        )
        materialize.set_defaults(func=self.cmd_materialize)

        init_seeds = subparsers.add_parser(
            "init-seeds",
            help="Ensure source_roots.lock.jsonc exists and refresh the recursive dependency seed closure.",
        )
        init_seeds.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress verbose git output while keeping FreeCM status lines.",
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
        func: Callable[[argparse.Namespace], int] = args.func
        return func(args)


if TYPE_CHECKING:

    def _typecheck_source_root_workflow_contract(
        workflow: DependencyRootWorkflow,
    ) -> SourceRootWorkflowLike[ResolvedSwiftDependencyRoots]:
        return workflow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Swift repo helpers are bound by a repository config module. "
            "Import DependencyRootWorkflow from repomgrswift.source_roots, or run "
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
    "ExtraDependencyPathSpec",
    "ResolvedSwiftDependencyRoots",
    "DependencyRootSpec",
    "DependencyRootWorkflow",
    "DependencyRootWorkflowConfig",
    "APP_CONFIG_KEYS",
    "VALID_MODES",
)


if __name__ == "__main__":
    raise SystemExit(main())
