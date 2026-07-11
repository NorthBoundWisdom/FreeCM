from __future__ import annotations

import argparse
import hashlib
import inspect
import io
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import get_type_hints
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from freecm.app_configs import AppConfigError, validate_app_configs  # noqa: E402
from freecm.dependency_models import DependencyPin, ResolvedDependencyRoots  # noqa: E402
from freecm.dependency_roots import DependencyRootSpec  # noqa: E402
from freecm.git_repositories import git_is_work_tree, remove_path  # noqa: E402
from freecm.source_root_workflow import SourceRootWorkflowScript  # noqa: E402
from repomgrswift.source_roots import (  # noqa: E402
    DEFAULT_REQUIRED_RELATIVE_PATHS,
    DependencyResolution,
    DependencyRootWorkflow,
    DependencyRootWorkflowConfig,
    ExtraDependencyPathSpec,
    ResolvedSwiftDependencyRoots,
)
from repomgrswift.terminal_style import (  # noqa: E402
    ANSI_GREEN,
    format_dependency_commit_change_lines,
    format_dependency_resolution_lines,
)
from tests.git_test_helpers import (  # noqa: E402
    commit_git_fixture_repo,
    create_git_fixture_repo,
    run_git_fixture,
)


class SwiftFreeCMTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo_root = Path(self.tempdir.name) / "HostApp"
        self.repo_root.mkdir(parents=True)
        self.remotes_root = Path(self.tempdir.name) / "remotes"
        self.remotes_root.mkdir(parents=True)
        self.specs = (
            DependencyRootSpec(
                dependency_name="LibA",
                repo_name="LibA",
                env_key="LIBA_SOURCE_ROOT",
                required_relative_paths=("CMakeLists.txt", "include/LibA"),
            ),
            DependencyRootSpec(
                dependency_name="LibB",
                repo_name="LibB",
                env_key="LIBB_SOURCE_ROOT",
                required_relative_paths=("CMakeLists.txt", "include/LibB"),
            ),
        )
        self.extra_specs = (
            ExtraDependencyPathSpec(
                env_key="LIBA_REGS_ROOT",
                dependency_name="LibA",
                relative_path="Regs",
                required_relative_paths=("fixture.txt",),
            ),
        )
        self.workflow = DependencyRootWorkflow(
            DependencyRootWorkflowConfig(
                repo_root=self.repo_root,
                dependency_root_specs=self.specs,
                known_dependency_root_specs=self.specs,
                extra_path_specs=self.extra_specs,
                repo_display_name="HostApp",
                app_config_keys=(
                    "XCODE_DEVELOPMENT_TEAM",
                    "MARKETING_VERSION",
                    "ARCHIVE_ID",
                    "commercePolicy",
                ),
                app_config_defaults={"commercePolicy": "appStore"},
            )
        )

    def git(self, cwd: Path, *args: str) -> str:
        return run_git_fixture(cwd, *args)

    def _create_remote_repo(
        self,
        name: str,
        required_relative_paths: tuple[str, ...],
    ) -> tuple[Path, str]:
        return create_git_fixture_repo(self.remotes_root, name, required_relative_paths)

    def _commit_repo(self, repo_root: Path, message: str) -> str:
        return commit_git_fixture_repo(repo_root, message)

    def _bootstrap(self) -> tuple[dict[str, Path], dict[str, str]]:
        remotes: dict[str, Path] = {}
        commits: dict[str, str] = {}
        for spec in self.specs:
            remote, commit = self._create_remote_repo(spec.repo_name, spec.required_relative_paths)
            remotes[spec.dependency_name] = remote
            commits[spec.dependency_name] = commit
        (remotes["LibA"] / "Regs").mkdir()
        (remotes["LibA"] / "Regs" / "fixture.txt").write_text("fixture\n", encoding="utf-8")
        commits["LibA"] = self._commit_repo(remotes["LibA"], "add regs")
        self._write_lock_data(self._lock_data(remotes, commits))
        return remotes, commits

    def _lock_data(self, remotes: dict[str, Path], commits: dict[str, str]) -> dict[str, object]:
        return {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "AppConfigs": {
                "XCODE_DEVELOPMENT_TEAM": "TEAMID1234",
                "MARKETING_VERSION": "1.0.0",
                "ARCHIVE_ID": "10000",
                "commercePolicy": "fullyUnlockedInternal",
            },
            "depsManualPath": {spec.dependency_name: "" for spec in self.specs},
            "dependencies": {
                spec.dependency_name: {
                    "remote": str(remotes[spec.dependency_name]),
                    "commit": commits[spec.dependency_name],
                }
                for spec in self.specs
            },
        }

    def _write_lock_data(self, lock_data: dict[str, object], *, template: bool = False) -> None:
        path = self.repo_root / (
            "source_roots.lock.jsonc.in" if template else "source_roots.lock.jsonc"
        )
        path.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")

    def _read_lock_data(self) -> dict[str, object]:
        return json.loads((self.repo_root / "source_roots.lock.jsonc").read_text(encoding="utf-8"))

    def test_swift_adapter_default_required_paths_are_not_cmake_specific(self) -> None:
        self.assertEqual(DEFAULT_REQUIRED_RELATIVE_PATHS, ())
        self.assertEqual(
            DependencyRootWorkflowConfig(
                repo_root=self.repo_root,
                dependency_root_specs=(),
                repo_display_name="HostApp",
            ).default_required_relative_paths,
            (),
        )

    def test_swift_public_api_snapshot_is_preserved(self) -> None:
        import repomgrswift
        import repomgrswift.source_roots as source_roots

        public_names = (
            "DependencyResolution",
            "ExtraDependencyPathSpec",
            "ResolvedSwiftDependencyRoots",
            "DependencyRootSpec",
            "DependencyRootWorkflow",
            "DependencyRootWorkflowConfig",
        )
        for name in public_names:
            with self.subTest(name=name):
                self.assertIs(getattr(repomgrswift, name), getattr(source_roots, name))

        self.assertEqual(
            tuple(field.name for field in fields(DependencyRootWorkflowConfig)),
            (
                "repo_root",
                "dependency_root_specs",
                "repo_display_name",
                "known_dependency_root_specs",
                "extra_path_specs",
                "default_required_relative_paths",
                "app_config_keys",
                "app_config_defaults",
                "xcode_manual_sync_command",
            ),
        )
        constructor_parameters = {
            ExtraDependencyPathSpec: (
                "env_key",
                "dependency_name",
                "relative_path",
                "required_relative_paths",
            ),
            DependencyResolution: ("dependency_name", "mode", "commit", "path"),
            ResolvedSwiftDependencyRoots: (
                "dependency_roots",
                "dependency_root_specs",
                "known_dependency_root_specs",
                "extra_path_specs",
                "app_config_keys",
                "app_configs",
                "xcode_manual_sync_command",
            ),
            DependencyRootWorkflow: ("config",),
        }
        for value, expected in constructor_parameters.items():
            with self.subTest(value=value.__name__):
                self.assertEqual(tuple(inspect.signature(value).parameters), expected)

        method_parameters = {
            "seed_repo_root_for_spec": ("self", "spec", "repo_root"),
            "init_seed_repositories": ("self", "repo_root", "progress", "quiet"),
            "resolve_dependency_roots": (
                "self",
                "repo_root",
                "materialize",
                "allow_network",
                "quiet",
            ),
            "resolve_source_roots": (
                "self",
                "repo_root",
                "materialize",
                "allow_network",
                "quiet",
            ),
            "load_lock_file": ("self", "repo_root"),
            "materialize_dependency_roots": (
                "self",
                "repo_root",
                "allow_network",
                "quiet",
            ),
            "materialize_source_roots": (
                "self",
                "repo_root",
                "allow_network",
                "quiet",
            ),
            "verify_dependency_roots": ("self", "dependency_roots"),
            "verify_source_roots": ("self", "source_roots"),
            "require_dependency_roots": (
                "self",
                "repo_root",
                "materialize",
                "allow_network",
                "quiet",
                "missing_roots_hint",
            ),
            "dependency_resolutions": ("self", "dependency_roots"),
            "pin_dependency_ref": (
                "self",
                "dependency_name",
                "ref",
                "repo_root",
                "allow_fetch",
            ),
            "cmd_status": ("self", "args"),
            "cmd_verify": ("self", "_"),
            "cmd_materialize": ("self", "args"),
            "cmd_init_seeds": ("self", "args"),
            "cmd_pin": ("self", "args"),
            "build_parser": ("self",),
            "main": ("self", "argv"),
        }
        for name, expected in method_parameters.items():
            with self.subTest(method=name):
                self.assertEqual(
                    tuple(inspect.signature(getattr(DependencyRootWorkflow, name)).parameters),
                    expected,
                )

        typed_root_methods = (
            "resolve_dependency_roots",
            "resolve_source_roots",
            "materialize_dependency_roots",
            "materialize_source_roots",
            "verify_dependency_roots",
            "verify_source_roots",
            "require_dependency_roots",
        )
        for name in typed_root_methods:
            with self.subTest(typed_method=name):
                signature = inspect.signature(getattr(DependencyRootWorkflow, name))
                hints = get_type_hints(getattr(DependencyRootWorkflow, name))
                expected_return = (
                    list[str]
                    if name in {"verify_dependency_roots", "verify_source_roots"}
                    else ResolvedSwiftDependencyRoots
                )
                self.assertEqual(hints["return"], expected_return)
                for parameter in signature.parameters.values():
                    if parameter.name != "self":
                        self.assertNotEqual(
                            parameter.annotation,
                            inspect.Parameter.empty,
                        )
        exact_signatures = {
            "resolve_dependency_roots": "(self, repo_root: 'Path | None' = None, *, materialize: 'bool' = False, allow_network: 'bool' = False, quiet: 'bool' = False) -> 'ResolvedSwiftDependencyRoots'",
            "resolve_source_roots": "(self, repo_root: 'Path | None' = None, *, materialize: 'bool' = False, allow_network: 'bool' = False, quiet: 'bool' = False) -> 'ResolvedSwiftDependencyRoots'",
            "materialize_dependency_roots": "(self, repo_root: 'Path | None' = None, *, allow_network: 'bool' = False, quiet: 'bool' = False) -> 'ResolvedSwiftDependencyRoots'",
            "materialize_source_roots": "(self, repo_root: 'Path | None' = None, *, allow_network: 'bool' = False, quiet: 'bool' = False) -> 'ResolvedSwiftDependencyRoots'",
            "verify_dependency_roots": "(self, dependency_roots: 'ResolvedSwiftDependencyRoots') -> 'list[str]'",
            "verify_source_roots": "(self, source_roots: 'ResolvedSwiftDependencyRoots') -> 'list[str]'",
            "require_dependency_roots": "(self, repo_root: 'Path | None' = None, *, materialize: 'bool' = False, allow_network: 'bool' = False, quiet: 'bool' = False, missing_roots_hint: 'str | None' = None) -> 'ResolvedSwiftDependencyRoots'",
        }
        for name, expected in exact_signatures.items():
            with self.subTest(exact_signature=name):
                self.assertEqual(
                    str(inspect.signature(getattr(DependencyRootWorkflow, name))),
                    expected,
                )
        self.assertIs(
            get_type_hints(DependencyRootWorkflow.verify_dependency_roots)["dependency_roots"],
            ResolvedSwiftDependencyRoots,
        )
        self.assertIs(
            get_type_hints(DependencyRootWorkflow.verify_source_roots)["source_roots"],
            ResolvedSwiftDependencyRoots,
        )

    def test_swift_workflows_keep_known_specs_and_presentation_isolated(self) -> None:
        transitive_spec = DependencyRootSpec(
            dependency_name="LibTransitive",
            repo_name="TransitiveRepo",
            env_key="TRANSITIVE_ROOT",
            required_relative_paths=(),
        )
        workflow_a = DependencyRootWorkflow(
            DependencyRootWorkflowConfig(
                repo_root=self.repo_root / "A",
                dependency_root_specs=(self.specs[0],),
                repo_display_name="A",
                known_dependency_root_specs=(self.specs[0], transitive_spec),
                extra_path_specs=(ExtraDependencyPathSpec("A_EXTRA_ROOT", "LibA", "Extra"),),
                app_config_keys=("Channel",),
                app_config_defaults={"Channel": "A-default"},
            )
        )
        workflow_b = DependencyRootWorkflow(
            DependencyRootWorkflowConfig(
                repo_root=self.repo_root / "B",
                dependency_root_specs=(self.specs[1],),
                repo_display_name="B",
                app_config_keys=("Flavor",),
                app_config_defaults={"Flavor": "B-default"},
            )
        )

        def resolved(
            workflow: DependencyRootWorkflow,
            names: tuple[str, ...],
            app_configs: dict[str, str],
        ) -> ResolvedDependencyRoots:
            pins = {
                name: DependencyPin(
                    dependency_name=name,
                    repo_name=workflow.spec_by_dependency_name[name].repo_name,
                    remote=f"https://example.invalid/{name}.git",
                    commit=name.lower() * 8,
                    latest_ref=None,
                    declared_by_root=name in workflow.direct_dependency_names,
                    env_key=workflow.spec_by_dependency_name[name].env_key,
                    required_relative_paths=(),
                )
                for name in names
            }
            roots = {name: workflow.repo_root / "roots" / name for name in names}
            return ResolvedDependencyRoots(
                mode="pinned",
                repo_root=workflow.repo_root,
                lock_data={
                    "depsMode": "pinned",
                    "AppConfigs": app_configs,
                    "depsManualPath": {name: "" for name in workflow.direct_dependency_names},
                    "dependencies": {
                        name: {"commit": pins[name].commit}
                        for name in workflow.direct_dependency_names
                    },
                },
                direct_dependency_names=workflow.direct_dependency_names,
                dependency_pins_by_name=pins,
                seed_repositories_by_dependency={
                    name: workflow.repo_root / "seeds" / name for name in names
                },
                dependency_roots_by_name=roots,
                resolved_commits_by_dependency={name: pins[name].commit for name in names},
                dependency_names_by_parent={name: () for name in names},
                dependency_declarations_by_name={name: () for name in names},
                closure_order=names,
                dependency_root_specs=workflow.dependency_root_specs,
            )

        roots_a = resolved(workflow_a, ("LibTransitive", "LibA"), {"Channel": "A"})
        roots_b = resolved(workflow_b, ("LibB",), {"Flavor": "B"})
        with (
            mock.patch.object(
                workflow_a._manager,
                "load_dependency_roots",
                side_effect=(roots_a, roots_a),
            ),
            mock.patch.object(
                workflow_b._manager,
                "load_dependency_roots",
                return_value=roots_b,
            ),
        ):
            first_a = workflow_a.resolve_dependency_roots()
            only_b = workflow_b.resolve_dependency_roots()
            second_a = workflow_a.resolve_dependency_roots()

        self.assertEqual(first_a.as_env_map(), second_a.as_env_map())
        self.assertEqual(first_a.app_configs, {"Channel": "A"})
        self.assertEqual(only_b.app_configs, {"Flavor": "B"})
        self.assertIn("TRANSITIVE_ROOT", first_a.as_env_map())
        self.assertIn("A_EXTRA_ROOT", first_a.as_env_map())
        self.assertNotIn("TRANSITIVE_ROOT", only_b.as_env_map())
        self.assertEqual(workflow_a.direct_dependency_names, ("LibA",))
        self.assertEqual(workflow_b.direct_dependency_names, ("LibB",))
        self.assertEqual(
            tuple(workflow_a.spec_by_dependency_name),
            ("LibA", "LibTransitive"),
        )
        self.assertEqual(tuple(workflow_b.spec_by_dependency_name), ("LibB",))

    def test_optional_known_root_is_omitted_when_absent_from_closure(self) -> None:
        optional_spec = DependencyRootSpec(
            dependency_name="LibOptional",
            repo_name="OptionalRepo",
            env_key="LIBOPTIONAL_ROOT",
            required_relative_paths=(),
        )
        workflow = DependencyRootWorkflow(
            DependencyRootWorkflowConfig(
                repo_root=self.repo_root,
                dependency_root_specs=(self.specs[0],),
                repo_display_name="HostApp",
                known_dependency_root_specs=(self.specs[0], optional_spec),
                extra_path_specs=(
                    ExtraDependencyPathSpec(
                        "LIBOPTIONAL_EXTRA_ROOT",
                        "LibOptional",
                        "Extra",
                    ),
                ),
                app_config_keys=("Channel",),
                app_config_defaults={"Channel": "stable"},
            )
        )
        pin = DependencyPin(
            dependency_name="LibA",
            repo_name="LibA",
            remote="https://example.invalid/LibA.git",
            commit="a" * 40,
            latest_ref=None,
            declared_by_root=True,
            env_key="LIBA_SOURCE_ROOT",
            required_relative_paths=(),
        )
        core_roots = ResolvedDependencyRoots(
            mode="pinned",
            repo_root=self.repo_root,
            lock_data={
                "depsMode": "pinned",
                "AppConfigs": {"Channel": "stable"},
                "depsManualPath": {"LibA": ""},
                "dependencies": {"LibA": {"commit": "a" * 40}},
            },
            direct_dependency_names=("LibA",),
            dependency_pins_by_name={"LibA": pin},
            seed_repositories_by_dependency={"LibA": self.repo_root / "seed" / "LibA"},
            dependency_roots_by_name={"LibA": self.repo_root / "roots" / "LibA"},
            resolved_commits_by_dependency={"LibA": "a" * 40},
            dependency_names_by_parent={"LibA": ()},
            dependency_declarations_by_name={"LibA": ()},
            closure_order=("LibA",),
            dependency_root_specs=(self.specs[0],),
        )
        roots = workflow._wrap_dependency_roots(core_roots)

        self.assertEqual(
            roots.as_env_map(),
            {"LIBA_SOURCE_ROOT": str(self.repo_root / "roots" / "LibA")},
        )
        self.assertNotIn("LIBOPTIONAL_ROOT", roots.as_json_dict()["roots"])
        self.assertNotIn("LIBOPTIONAL_EXTRA_ROOT", roots.as_json_dict()["roots"])
        with mock.patch.object(
            workflow._manager,
            "validate_dependency_roots",
            return_value=[],
        ):
            problems = workflow.verify_dependency_roots(roots)
        self.assertEqual(
            problems,
            ["LIBOPTIONAL_EXTRA_ROOT missing dependency root: LibOptional"],
        )

    def test_swift_workflow_rejects_duplicate_and_unsafe_path_specs(self) -> None:
        duplicate_dependency = DependencyRootSpec(
            dependency_name="LibA",
            repo_name="OtherRepo",
            env_key="OTHER_ROOT",
            required_relative_paths=(),
        )
        duplicate_environment = DependencyRootSpec(
            dependency_name="LibC",
            repo_name="LibC",
            env_key="LIBA_SOURCE_ROOT",
            required_relative_paths=(),
        )
        for known_specs, message in (
            ((*self.specs, duplicate_dependency), "Duplicate dependency name"),
            ((*self.specs, duplicate_environment), "Duplicate environment key"),
            ((self.specs[0],), "missing direct dependencies"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    DependencyRootWorkflow(
                        DependencyRootWorkflowConfig(
                            repo_root=self.repo_root,
                            dependency_root_specs=self.specs,
                            known_dependency_root_specs=known_specs,
                            repo_display_name="HostApp",
                        )
                    )

        invalid_extra_sets = (
            (
                (
                    ExtraDependencyPathSpec(
                        env_key="LIBA_SOURCE_ROOT",
                        dependency_name="LibA",
                        relative_path="Regs",
                    ),
                ),
                "Duplicate environment key",
            ),
            (
                (
                    ExtraDependencyPathSpec("EXTRA_ROOT", "LibA", "Regs"),
                    ExtraDependencyPathSpec("EXTRA_ROOT", "LibB", "Data"),
                ),
                "Duplicate environment key",
            ),
            (
                (ExtraDependencyPathSpec("EXTRA_ROOT", "Unknown", "Regs"),),
                "Unknown dependency",
            ),
            (
                (ExtraDependencyPathSpec("EXTRA_ROOT", "LibA", "../escape"),),
                "escapes its dependency root",
            ),
            (
                (
                    ExtraDependencyPathSpec(
                        "EXTRA_ROOT",
                        "LibA",
                        "Regs",
                        required_relative_paths=("/absolute",),
                    ),
                ),
                "stay relative",
            ),
            (
                (ExtraDependencyPathSpec("INVALID-KEY", "LibA", "Regs"),),
                "portable identifier",
            ),
        )
        for extra_specs, message in invalid_extra_sets:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    DependencyRootWorkflow(
                        DependencyRootWorkflowConfig(
                            repo_root=self.repo_root,
                            dependency_root_specs=self.specs,
                            known_dependency_root_specs=self.specs,
                            extra_path_specs=extra_specs,
                            repo_display_name="HostApp",
                        )
                    )

    def test_swift_status_shell_quotes_environment_values(self) -> None:
        special_value = "root with space/'quote'/$HOME/`command`\nnext"
        source_roots = SimpleNamespace(as_env_map=lambda: {"LIBA_ROOT": special_value})
        args = self.workflow.build_parser().parse_args(["status", "--format", "shell"])
        stdout = io.StringIO()
        with (
            mock.patch.object(
                self.workflow,
                "resolve_dependency_roots",
                return_value=source_roots,
            ),
            redirect_stdout(stdout),
        ):
            result = self.workflow.cmd_status(args)

        self.assertEqual(result, 0)
        shell_line = stdout.getvalue().removesuffix("\n")
        self.assertEqual(shell_line, f"export LIBA_ROOT={shlex.quote(special_value)}")
        self.assertEqual(shlex.split(shell_line), ["export", f"LIBA_ROOT={special_value}"])

    def test_swift_status_plain_and_json_output_shapes_are_stable(self) -> None:
        roots = SimpleNamespace(
            as_env_map=lambda: {
                "LIBA_SOURCE_ROOT": "/workspace/LibA",
                "LIBA_REGS_ROOT": "/workspace/LibA/Regs",
            },
            as_json_dict=lambda: {
                "schemaVersion": 5,
                "mode": "pinned",
                "AppConfigs": {"commercePolicy": "appStore"},
                "roots": {"LIBA_SOURCE_ROOT": "/workspace/LibA"},
            },
        )
        expected = {
            "plain": ("LIBA_SOURCE_ROOT=/workspace/LibA\n" "LIBA_REGS_ROOT=/workspace/LibA/Regs\n"),
            "json": json.dumps(roots.as_json_dict(), indent=2) + "\n",
        }
        for output_format, expected_output in expected.items():
            with self.subTest(output_format=output_format):
                stdout = io.StringIO()
                with (
                    mock.patch.object(
                        self.workflow,
                        "resolve_dependency_roots",
                        return_value=roots,
                    ),
                    redirect_stdout(stdout),
                ):
                    result = self.workflow.cmd_status(argparse.Namespace(format=output_format))
                self.assertEqual(result, 0)
                self.assertEqual(stdout.getvalue(), expected_output)

    def test_swift_commands_delegate_to_one_core_command_adapter(self) -> None:
        commands = mock.Mock()
        self.workflow._commands = commands
        args = argparse.Namespace(format="plain", quiet=False, dep="LibA", ref="main")

        for method_name, command_name in (
            ("cmd_status", "cmd_status"),
            ("cmd_verify", "cmd_verify"),
            ("cmd_materialize", "cmd_materialize"),
            ("cmd_pin", "cmd_pin"),
        ):
            with self.subTest(method=method_name):
                getattr(commands, command_name).return_value = 7
                self.assertEqual(getattr(self.workflow, method_name)(args), 7)
                getattr(commands, command_name).assert_called_once_with(args)

    def test_swift_command_bindings_keep_materialize_and_pin_offline(self) -> None:
        roots = SimpleNamespace(as_env_map=lambda: {"LIBA_ROOT": "/tmp/LibA"})
        with (
            mock.patch.object(
                self.workflow,
                "materialize_dependency_roots",
                return_value=roots,
            ) as materialize,
            mock.patch.object(
                self.workflow,
                "pin_dependency_ref",
                return_value="a" * 40,
            ) as pin,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                self.workflow.cmd_materialize(argparse.Namespace(quiet=True)),
                0,
            )
            self.assertEqual(
                self.workflow.cmd_pin(argparse.Namespace(dep="LibA", ref="main")),
                0,
            )

        materialize.assert_called_once_with(allow_network=False, quiet=True)
        pin.assert_called_once_with("LibA", "main", allow_fetch=False)

    def test_swift_cli_reports_process_errors_and_propagates_interrupts(self) -> None:
        stderr = io.StringIO()
        args = argparse.Namespace(format="plain")
        with (
            mock.patch.object(
                self.workflow,
                "resolve_dependency_roots",
                side_effect=subprocess.CalledProcessError(2, ["git", "status"]),
            ),
            redirect_stderr(stderr),
        ):
            self.assertEqual(self.workflow.cmd_status(args), 1)
        self.assertIn("[freecm]", stderr.getvalue())

        with (
            mock.patch.object(
                self.workflow,
                "resolve_dependency_roots",
                side_effect=ValueError("late error"),
            ),
            mock.patch("repomgrswift.source_roots.print_error") as late_reporter,
        ):
            self.assertEqual(self.workflow.cmd_status(args), 1)
        late_reporter.assert_called_once()

        with (
            mock.patch.object(
                self.workflow,
                "resolve_dependency_roots",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.workflow.cmd_status(args)

        script = SourceRootWorkflowScript(self.workflow, repo_display_name="HostApp")
        with (
            mock.patch.object(
                script.workflow,
                "init_seed_repositories",
                side_effect=SystemExit(4),
            ),
            self.assertRaisesRegex(SystemExit, "4"),
        ):
            script.main(["--init"])

    def test_swift_adapter_does_not_import_cpp_adapter(self) -> None:
        for source_path in (REPO_ROOT / "repomgrswift").rglob("*.py"):
            with self.subTest(source=source_path.name):
                self.assertNotIn(
                    "repomgrcpp",
                    source_path.read_text(encoding="utf-8"),
                )

    def test_swift_extra_path_rejects_resolved_symlink_escape(self) -> None:
        self._bootstrap()
        self.workflow.init_seed_repositories()
        source_roots = self.workflow.materialize_source_roots(allow_network=False)
        extra_root = source_roots.root_for_dependency("LibA") / "Regs"
        remove_path(extra_root)
        outside_root = self.repo_root / "outside-regs"
        outside_root.mkdir()
        try:
            extra_root.symlink_to(outside_root, target_is_directory=True)
        except OSError as exc:
            if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege is not available")
            raise

        problems = self.workflow.verify_source_roots(source_roots)

        self.assertTrue(any("resolved path escapes" in problem for problem in problems))

    def test_app_configs_validation_accepts_defaults_and_rejects_legacy_fields(self) -> None:
        configs = validate_app_configs(
            {
                "AppConfigs": {
                    "XCODE_DEVELOPMENT_TEAM": "TEAMID1234",
                    "MARKETING_VERSION": "1.0.0",
                    "ARCHIVE_ID": "10000",
                    "CUSTOM_APP_CONFIG": "enabled",
                    "DevMode": False,
                }
            },
            path_label="lock",
            app_config_keys=(
                "XCODE_DEVELOPMENT_TEAM",
                "MARKETING_VERSION",
                "ARCHIVE_ID",
                "DevMode",
                "commercePolicy",
            ),
            app_config_defaults={"commercePolicy": "appStore"},
        )

        self.assertEqual(configs["commercePolicy"], "appStore")
        self.assertEqual(configs["CUSTOM_APP_CONFIG"], "enabled")
        self.assertIs(configs["DevMode"], False)

        with self.assertRaisesRegex(AppConfigError, "buildSettings is no longer supported"):
            validate_app_configs(
                {"buildSettings": {}},
                path_label="lock",
                app_config_keys=("XCODE_DEVELOPMENT_TEAM",),
            )
        with self.assertRaisesRegex(AppConfigError, "commercePolicy is no longer supported"):
            validate_app_configs(
                {"commercePolicy": "fullyUnlockedInternal"},
                path_label="lock",
                app_config_keys=("commercePolicy",),
            )
        with self.assertRaisesRegex(AppConfigError, "DevMode is no longer supported"):
            validate_app_configs(
                {"DevMode": False},
                path_label="lock",
                app_config_keys=("DevMode",),
            )
        with self.assertRaisesRegex(AppConfigError, "Invalid AppConfigs map"):
            validate_app_configs(
                {"AppConfigs": []},
                path_label="lock",
                app_config_keys=("commercePolicy",),
            )
        with self.assertRaisesRegex(AppConfigError, "Invalid AppConfigs.commercePolicy"):
            validate_app_configs(
                {"AppConfigs": {"commercePolicy": 7}},
                path_label="lock",
                app_config_keys=("commercePolicy",),
            )
        with self.assertRaisesRegex(AppConfigError, "missing keys: commercePolicy"):
            validate_app_configs(
                {"AppConfigs": {}},
                path_label="lock",
                app_config_keys=("commercePolicy",),
            )

    def test_resolve_and_materialize_reuse_freecm_core_and_include_extra_paths(self) -> None:
        remotes, commits = self._bootstrap()
        self.workflow.init_seed_repositories()
        source_roots = self.workflow.materialize_source_roots(allow_network=False)

        self.assertEqual(source_roots.app_configs["commercePolicy"], "fullyUnlockedInternal")
        self.assertEqual(source_roots.resolved_commits["LibA"], commits["LibA"])
        self.assertTrue(git_is_work_tree(source_roots.root_for_dependency("LibA")))
        self.assertEqual(
            source_roots.as_env_map()["LIBA_REGS_ROOT"],
            str(source_roots.root_for_dependency("LibA") / "Regs"),
        )
        self.assertEqual(
            source_roots.as_env_map()["LIBA_SOURCE_ROOT"],
            str(source_roots.root_for_dependency("LibA")),
        )
        self.assertEqual(self.workflow.verify_source_roots(source_roots), [])

        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "manual"
        lock_data["depsManualPath"]["LibA"] = str(remotes["LibA"])  # type: ignore[index]
        self._write_lock_data(lock_data)

        deps_manual_path = self.workflow.resolve_source_roots(allow_network=False)
        self.assertEqual(deps_manual_path.root_for_dependency("LibA"), remotes["LibA"].resolve())
        self.assertEqual(
            deps_manual_path.as_json_dict()["AppConfigs"]["commercePolicy"],
            "fullyUnlockedInternal",
        )
        self.assertNotIn("buildSettings", deps_manual_path.as_json_dict())

    def test_init_seed_repositories_copies_active_lock_once(self) -> None:
        remotes, commits = self._bootstrap()
        active_path = self.repo_root / "source_roots.lock.jsonc"
        template_data = self._lock_data(remotes, commits)
        self._write_lock_data(template_data, template=True)
        active_path.unlink()

        created_path, created, _ = self.workflow.init_seed_repositories()
        active_path.write_text(
            active_path.read_text(encoding="utf-8").replace('"pinned"', '"manual"'),
            encoding="utf-8",
        )
        existing_path, existing_created, _ = self.workflow.init_seed_repositories()

        self.assertTrue(created)
        self.assertEqual(created_path, active_path.resolve())
        self.assertFalse(existing_created)
        self.assertEqual(existing_path, active_path.resolve())
        self.assertIn('"manual"', active_path.read_text(encoding="utf-8"))

    def test_init_seed_repositories_prepares_asset_seeds(self) -> None:
        remotes, commits = self._bootstrap()
        asset_payload = b"asset"
        asset_source = self.repo_root / "local-asset.bin"
        asset_source.write_bytes(asset_payload)
        lock_data = self._lock_data(remotes, commits)
        lock_data["assets"] = {
            "AssetBundle": {
                "seedPath": "build/dependency_seed_repos/AssetBundle",
                "files": [
                    {
                        "id": "asset",
                        "type": "file",
                        "url": asset_source.as_uri(),
                        "fileName": "asset.bin",
                        "sha256": hashlib.sha256(asset_payload).hexdigest(),
                        "sizeBytes": len(asset_payload),
                    }
                ],
            }
        }
        self._write_lock_data(lock_data)

        _, _, results = self.workflow.init_seed_repositories()

        self.assertEqual("ready", results["asset:AssetBundle"])
        self.assertEqual(
            asset_payload,
            (
                self.repo_root / "build" / "dependency_seed_repos" / "AssetBundle" / "asset.bin"
            ).read_bytes(),
        )

    def test_verify_reports_missing_extra_path(self) -> None:
        self._bootstrap()
        self.workflow.init_seed_repositories()
        source_roots = self.workflow.materialize_source_roots(allow_network=False)
        remove_path(source_roots.root_for_dependency("LibA") / "Regs")

        problems = self.workflow.verify_source_roots(source_roots)

        self.assertTrue(any("LIBA_REGS_ROOT missing path" in problem for problem in problems))

    def test_pin_updates_lock_via_freecm_core_from_local_seed(self) -> None:
        self._bootstrap()
        self.workflow.init_seed_repositories()
        seed_root = self.workflow.seed_repo_root_for_spec(
            self.workflow.spec_by_dependency_name["LibA"],
        )
        seed_head = self.git(seed_root, "rev-parse", "HEAD")
        self.git(seed_root, "tag", "swift-pin", seed_head)

        commit = self.workflow.pin_dependency_ref("LibA", "swift-pin")

        self.assertEqual(commit, seed_head)
        self.assertEqual(
            self._read_lock_data()["dependencies"]["LibA"]["commit"],
            seed_head,
        )

    def test_script_update_materializes_offline_then_runs_callback(self) -> None:
        script = SourceRootWorkflowScript(
            self.workflow,
            repo_display_name="HostApp",
            update_callback=mock.Mock(return_value=7),
        )
        source_roots = SimpleNamespace(
            lock_data={
                "dependencies": {
                    "LibA": {"commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                    "LibB": {"commit": "2222222222222222222222222222222222222222"},
                }
            },
            dependency_roots=SimpleNamespace(direct_dependency_names=("LibA", "LibB")),
        )
        with (
            mock.patch.object(
                script.workflow,
                "load_lock_file",
                return_value={
                    "dependencies": {
                        "LibA": {"commit": "1111111111111111111111111111111111111111"},
                        "LibB": {"commit": "2222222222222222222222222222222222222222"},
                    }
                },
            ) as load_lock_mock,
            mock.patch.object(
                script.workflow,
                "materialize_source_roots",
                return_value=source_roots,
            ) as materialize_mock,
            mock.patch.object(
                script.workflow,
                "verify_source_roots",
                return_value=[],
            ) as verify_mock,
            mock.patch.object(
                script.workflow,
                "dependency_resolutions",
                return_value=[],
            ) as resolutions_mock,
        ):
            result = script.main(["--update"])

        self.assertEqual(result, 7)
        load_lock_mock.assert_called_once_with(script.repo_root)
        materialize_mock.assert_called_once_with(
            script.repo_root,
            allow_network=False,
            quiet=False,
        )
        verify_mock.assert_called_once_with(source_roots)
        resolutions_mock.assert_called_once_with(source_roots)
        script.update_callback.assert_called_once_with()  # type: ignore[union-attr]

    def test_script_update_can_defer_resolution_summary_to_callback(self) -> None:
        script = SourceRootWorkflowScript(
            self.workflow,
            repo_display_name="HostApp",
            update_callback=mock.Mock(return_value=0),
            print_update_resolutions=False,
        )
        source_roots = SimpleNamespace(
            lock_data={"dependencies": {}},
            dependency_roots=SimpleNamespace(direct_dependency_names=()),
        )
        with (
            mock.patch.object(script.workflow, "load_lock_file", return_value={}),
            mock.patch.object(
                script.workflow,
                "materialize_source_roots",
                return_value=source_roots,
            ),
            mock.patch.object(script.workflow, "verify_source_roots", return_value=[]),
            mock.patch.object(script.workflow, "dependency_resolutions") as resolutions_mock,
            mock.patch("builtins.print"),
        ):
            result = script.main(["--update"])

        self.assertEqual(result, 0)
        resolutions_mock.assert_not_called()
        script.update_callback.assert_called_once_with()  # type: ignore[union-attr]

    def test_script_init_bootstraps_seed_repositories(self) -> None:
        script = SourceRootWorkflowScript(self.workflow, repo_display_name="HostApp")
        with (
            mock.patch.object(
                script.workflow,
                "init_seed_repositories",
                return_value=(Path("/tmp/source_roots.lock.jsonc"), True, {"LibA": "ready"}),
            ) as init_mock,
            mock.patch.object(
                script.workflow,
                "seed_repo_root_for_spec",
                return_value=Path("/tmp/build/dependency_seed_repos/LibA"),
            ),
            mock.patch("builtins.print"),
        ):
            result = script.main(["--init"])

        self.assertEqual(result, 0)
        init_mock.assert_called_once_with(
            script.repo_root,
            progress=mock.ANY,
            quiet=False,
        )

    def test_script_init_quiet_suppresses_verbose_git_output(self) -> None:
        script = SourceRootWorkflowScript(self.workflow, repo_display_name="HostApp")
        with (
            mock.patch.object(
                script.workflow,
                "init_seed_repositories",
                return_value=(Path("/tmp/source_roots.lock.jsonc"), True, {}),
            ) as init_mock,
            mock.patch("builtins.print"),
        ):
            result = script.main(["--init", "--quiet"])

        self.assertEqual(result, 0)
        init_mock.assert_called_once_with(
            script.repo_root,
            progress=mock.ANY,
            quiet=True,
        )

    def test_script_known_init_error_keeps_styled_error_boundary(self) -> None:
        script = SourceRootWorkflowScript(self.workflow, repo_display_name="HostApp")
        stderr = io.StringIO()
        with (
            mock.patch.object(
                script.workflow,
                "init_seed_repositories",
                side_effect=ValueError("invalid lock"),
            ),
            redirect_stderr(stderr),
        ):
            self.assertEqual(script.main(["--init"]), 1)

        self.assertIn("[freecm]", stderr.getvalue())
        self.assertIn("invalid lock", stderr.getvalue())

    def test_script_init_uses_colored_status_output_when_supported(self) -> None:
        script = SourceRootWorkflowScript(self.workflow, repo_display_name="HostApp")
        stdout = io.StringIO()
        with (
            mock.patch.object(
                script.workflow,
                "init_seed_repositories",
                return_value=(Path("/tmp/source_roots.lock.jsonc"), True, {"LibA": "ready"}),
            ),
            mock.patch.object(
                script.workflow,
                "seed_repo_root_for_spec",
                return_value=Path("/tmp/build/dependency_seed_repos/LibA"),
            ),
            mock.patch("freecm.source_root_workflow.stdout_supports_color", return_value=True),
            redirect_stdout(stdout),
        ):
            result = script.main(["--init"])

        self.assertEqual(result, 0)
        self.assertIn(ANSI_GREEN, stdout.getvalue())
        self.assertIn("[freecm]", stdout.getvalue())

    def test_terminal_style_matches_dependency_summary_shape(self) -> None:
        lines = format_dependency_resolution_lines(
            [
                DependencyResolution(
                    dependency_name="LibA",
                    mode="pinned",
                    commit="abc123",
                    path=Path("/tmp/LibA"),
                ),
                DependencyResolution(
                    dependency_name="LibB",
                    mode="manual",
                    commit=None,
                    path=Path("/tmp/LibB"),
                ),
            ],
            use_color=False,
        )
        self.assertEqual(
            lines,
            [
                "resolved direct dependencies:",
                "  LibA: pin sha=abc123",
                f"  LibB: manual path={Path('/tmp/LibB')}",
            ],
        )

    def test_terminal_style_exposes_dependency_commit_change_lines(self) -> None:
        self.assertEqual(
            format_dependency_commit_change_lines([], use_color=False),
            ["dependency lock commits unchanged"],
        )

    def test_source_roots_help_entrypoint(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "repomgrswift.source_roots", "--help"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Swift repo helpers are bound", completed.stdout)


if __name__ == "__main__":
    unittest.main()
