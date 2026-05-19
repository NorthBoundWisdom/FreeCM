from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from depsfixture.dependency_roots import DependencyRootSpec  # noqa: E402
from depsfixture.git_repositories import git_is_work_tree, remove_path  # noqa: E402
from swiftrepomgr.source_root_workflow import SourceRootWorkflowScript  # noqa: E402
from swiftrepomgr.source_roots import (  # noqa: E402
    DependencyResolution,
    ExtraSourceRootPathSpec,
    SourceRootDependencySpec,
    SourceRootWorkflow,
    SourceRootWorkflowConfig,
)
from swiftrepomgr.swift_configs import SwiftConfigError, validate_swift_configs  # noqa: E402
from swiftrepomgr.terminal_style import (  # noqa: E402
    ANSI_GREEN,
    format_dependency_commit_change_lines,
    format_dependency_resolution_lines,
)


class SwiftRepoMgrTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo_root = Path(self.tempdir.name) / "HostApp"
        self.repo_root.mkdir(parents=True)
        self.remotes_root = Path(self.tempdir.name) / "remotes"
        self.remotes_root.mkdir(parents=True)
        self.specs = (
            SourceRootDependencySpec(
                dependency_name="LibA",
                repo_name="LibA",
                env_key="LIBA_SOURCE_ROOT",
                required_relative_paths=("CMakeLists.txt", "include/LibA"),
            ),
            SourceRootDependencySpec(
                dependency_name="LibB",
                repo_name="LibB",
                env_key="LIBB_SOURCE_ROOT",
                required_relative_paths=("CMakeLists.txt", "include/LibB"),
            ),
        )
        self.extra_specs = (
            ExtraSourceRootPathSpec(
                env_key="LIBA_REGS_ROOT",
                dependency_name="LibA",
                relative_path="Regs",
                required_relative_paths=("fixture.txt",),
            ),
        )
        self.workflow = SourceRootWorkflow(
            SourceRootWorkflowConfig(
                repo_root=self.repo_root,
                source_root_specs=self.specs,
                known_source_root_specs=self.specs,
                extra_path_specs=self.extra_specs,
                repo_display_name="HostApp",
                swift_config_keys=(
                    "XCODE_DEVELOPMENT_TEAM",
                    "MARKETING_VERSION",
                    "ARCHIVE_ID",
                    "commercePolicy",
                ),
                swift_config_defaults={"commercePolicy": "appStore"},
            )
        )

    def git(self, cwd: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def _create_remote_repo(
        self,
        name: str,
        required_relative_paths: tuple[str, ...],
    ) -> tuple[Path, str]:
        repo_root = self.remotes_root / name
        repo_root.mkdir(parents=True)
        self.git(repo_root, "init")
        self.git(repo_root, "config", "user.name", "Codex")
        self.git(repo_root, "config", "user.email", "codex@example.com")
        for relative_path in required_relative_paths:
            target = repo_root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if "." in target.name:
                target.write_text(f"{name}:{relative_path}\n", encoding="utf-8")
            else:
                target.mkdir(parents=True, exist_ok=True)
                (target / ".keep").write_text("", encoding="utf-8")
        self.git(repo_root, "add", ".")
        self.git(repo_root, "commit", "-m", "init")
        return repo_root, self.git(repo_root, "rev-parse", "HEAD")

    def _commit_repo(self, repo_root: Path, message: str) -> str:
        self.git(repo_root, "add", ".")
        self.git(repo_root, "commit", "-m", message)
        return self.git(repo_root, "rev-parse", "HEAD")

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
            "SwiftConfigs": {
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
        path = self.repo_root / ("source_roots.lock.jsonc.in" if template else "source_roots.lock.jsonc")
        path.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")

    def _read_lock_data(self) -> dict[str, object]:
        return json.loads((self.repo_root / "source_roots.lock.jsonc").read_text(encoding="utf-8"))

    def test_source_root_dependency_spec_is_dependency_root_spec_alias(self) -> None:
        self.assertIs(SourceRootDependencySpec, DependencyRootSpec)
        self.assertIsInstance(self.specs[0], DependencyRootSpec)

    def test_swift_configs_validation_accepts_defaults_and_rejects_legacy_fields(self) -> None:
        configs = validate_swift_configs(
            {
                "SwiftConfigs": {
                    "XCODE_DEVELOPMENT_TEAM": "TEAMID1234",
                    "MARKETING_VERSION": "1.0.0",
                    "ARCHIVE_ID": "10000",
                    "CUSTOM_SWIFT_CONFIG": "enabled",
                }
            },
            path_label="lock",
            swift_config_keys=(
                "XCODE_DEVELOPMENT_TEAM",
                "MARKETING_VERSION",
                "ARCHIVE_ID",
                "commercePolicy",
            ),
            swift_config_defaults={"commercePolicy": "appStore"},
        )

        self.assertEqual(configs["commercePolicy"], "appStore")
        self.assertEqual(configs["CUSTOM_SWIFT_CONFIG"], "enabled")

        with self.assertRaisesRegex(SwiftConfigError, "buildSettings is no longer supported"):
            validate_swift_configs(
                {"buildSettings": {}},
                path_label="lock",
                swift_config_keys=("XCODE_DEVELOPMENT_TEAM",),
            )
        with self.assertRaisesRegex(SwiftConfigError, "commercePolicy is no longer supported"):
            validate_swift_configs(
                {"commercePolicy": "fullyUnlockedInternal"},
                path_label="lock",
                swift_config_keys=("commercePolicy",),
            )
        with self.assertRaisesRegex(SwiftConfigError, "Invalid SwiftConfigs map"):
            validate_swift_configs(
                {"SwiftConfigs": []},
                path_label="lock",
                swift_config_keys=("commercePolicy",),
            )
        with self.assertRaisesRegex(SwiftConfigError, "Invalid SwiftConfigs.commercePolicy"):
            validate_swift_configs(
                {"SwiftConfigs": {"commercePolicy": 7}},
                path_label="lock",
                swift_config_keys=("commercePolicy",),
            )
        with self.assertRaisesRegex(SwiftConfigError, "missing keys: commercePolicy"):
            validate_swift_configs(
                {"SwiftConfigs": {}},
                path_label="lock",
                swift_config_keys=("commercePolicy",),
            )

    def test_resolve_and_materialize_reuse_depsfixture_and_include_extra_paths(self) -> None:
        remotes, commits = self._bootstrap()
        self.workflow.init_seed_repositories()
        source_roots = self.workflow.materialize_source_roots(allow_network=False)

        self.assertEqual(source_roots.swift_configs["commercePolicy"], "fullyUnlockedInternal")
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
            deps_manual_path.as_json_dict()["SwiftConfigs"]["commercePolicy"],
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

    def test_verify_reports_missing_extra_path(self) -> None:
        self._bootstrap()
        self.workflow.init_seed_repositories()
        source_roots = self.workflow.materialize_source_roots(allow_network=False)
        remove_path(source_roots.root_for_dependency("LibA") / "Regs")

        problems = self.workflow.verify_source_roots(source_roots)

        self.assertTrue(any("LIBA_REGS_ROOT missing path" in problem for problem in problems))

    def test_pin_updates_lock_via_depsfixture(self) -> None:
        remotes, _ = self._bootstrap()
        self.workflow.init_seed_repositories()
        (remotes["LibA"] / "CMakeLists.txt").write_text("LibA:pinned\n", encoding="utf-8")
        advanced_head = self._commit_repo(remotes["LibA"], "advance pinned ref")
        self.git(remotes["LibA"], "tag", "swift-pin", advanced_head)

        commit = self.workflow.pin_dependency_ref("LibA", "swift-pin")

        self.assertEqual(commit, advanced_head)
        self.assertEqual(
            self._read_lock_data()["dependencies"]["LibA"]["commit"],
            advanced_head,
        )

    def test_script_update_materializes_offline_then_runs_callback(self) -> None:
        script = SourceRootWorkflowScript(
            self.workflow,
            repo_display_name="HostApp",
            update_callback=mock.Mock(return_value=0),
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

        self.assertEqual(result, 0)
        load_lock_mock.assert_called_once_with(script.repo_root)
        materialize_mock.assert_called_once_with(script.repo_root, allow_network=False)
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
        init_mock.assert_called_once_with(script.repo_root)

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
            mock.patch("swiftrepomgr.source_root_workflow.stdout_supports_color", return_value=True),
            redirect_stdout(stdout),
        ):
            result = script.main(["--init"])

        self.assertEqual(result, 0)
        self.assertIn(ANSI_GREEN, stdout.getvalue())
        self.assertIn("[repoconfigsmgr]", stdout.getvalue())

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
                )
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
            [sys.executable, "-m", "swiftrepomgr.source_roots", "--help"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Swift repo helpers are bound", completed.stdout)


if __name__ == "__main__":
    unittest.main()
