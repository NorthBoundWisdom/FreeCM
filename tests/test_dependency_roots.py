from __future__ import annotations

import argparse
import copy
import io
import json
import shlex
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from freecm.dependency_conflicts import DependencyConflictError  # noqa: E402
from freecm.dependency_models import DependencyClosure, DependencyPin  # noqa: E402
from freecm.dependency_roots import (  # noqa: E402
    DEFAULT_REQUIRED_RELATIVE_PATHS,
    DependencyRootConfig,
    DependencyRootManager,
    DependencyRootSpec,
    bind_dependency_root_workflow,
    loads_jsonc,
)
from freecm.errors import (  # noqa: E402
    FreeCMError,
    LockfileValidationError,
    MaterializationError,
    SeedRepositoryError,
)
from freecm.git_repositories import fetch_remote_refs, git_is_work_tree, remove_path  # noqa: E402
from freecm.materializer import write_nested_manual_dependency_lock  # noqa: E402
from freecm.path_maps import (  # noqa: E402
    dependency_root_path_map,
    environment_map,
    print_environment_map,
    resolve_dependency_relative_path,
    validate_dependency_specs,
)
from freecm.terminal_style import (  # noqa: E402
    ANSI_GREEN,
    ANSI_RED,
    format_root_override_transitive_pin_mismatch_lines,
)
from freecm.workspace_lock import workspace_lock_path  # noqa: E402
from tests.git_test_helpers import (  # noqa: E402
    commit_git_fixture_repo,
    create_git_fixture_repo,
    run_git_fixture,
)


def atomic_sidecar_dir(path: Path) -> Path:
    return path.parent / ".freecm" / "atomic"


def assert_atomic_write_sidecars(testcase: unittest.TestCase, path: Path) -> None:
    sidecar_dir = atomic_sidecar_dir(path)
    testcase.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])
    testcase.assertFalse((path.parent / f".{path.name}.lock").exists())
    testcase.assertEqual(list(sidecar_dir.glob(f".{path.name}.*.tmp")), [])
    testcase.assertTrue((sidecar_dir / f".{path.name}.lock").is_file())


class DependencyRootManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo_root = Path(self.tempdir.name) / "HostRepo"
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
        self.workflow = DependencyRootManager(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=self.specs,
                repo_display_name="HostRepo",
            )
        )

    def git(self, cwd: Path, *args: str) -> str:
        return run_git_fixture(cwd, *args)

    def _create_remote_repo(
        self, name: str, required_relative_paths: tuple[str, ...]
    ) -> tuple[Path, str]:
        return create_git_fixture_repo(self.remotes_root, name, required_relative_paths)

    def _write_nested_template(
        self,
        repo_root: Path,
        *,
        dependencies: dict[str, dict[str, object]],
    ) -> str:
        lock_data: dict[str, object] = {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "cmakeEnvironment": {},
            "cmakeCacheVariables": {},
            "depsManualPath": {dependency_name: "" for dependency_name in dependencies},
            "dependencies": dependencies,
        }
        (repo_root / "source_roots.lock.jsonc.in").write_text(
            json.dumps(lock_data, indent=2) + "\n",
            encoding="utf-8",
        )
        return self._commit_repo(repo_root, "add nested dependency template")

    def _commit_repo(self, repo_root: Path, message: str) -> str:
        return commit_git_fixture_repo(repo_root, message)

    def _bootstrap(self) -> tuple[dict[str, Path], dict[str, str]]:
        remotes: dict[str, Path] = {}
        commits: dict[str, str] = {}
        for spec in self.specs:
            remote, commit = self._create_remote_repo(spec.repo_name, spec.required_relative_paths)
            remotes[spec.dependency_name] = remote
            commits[spec.dependency_name] = commit
        self._write_lock_file(remotes, commits)
        return remotes, commits

    def _synthetic_dependency_closure(
        self,
        adjacency: dict[str, tuple[str, ...]],
        *,
        direct_dependency_names: tuple[str, ...] = ("Lib0",),
        edge_commits: dict[tuple[str, str], str] | None = None,
        operation_counts: dict[tuple[str, str], int] | None = None,
    ) -> DependencyClosure:
        specs = tuple(
            DependencyRootSpec(
                dependency_name=name,
                repo_name=name,
                env_key=f"{name.upper()}_SOURCE_ROOT",
                required_relative_paths=(),
            )
            for name in direct_dependency_names
        )
        workflow = DependencyRootManager(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=specs,
                repo_display_name="HostRepo",
            )
        )

        def entry(name: str, *, commit: str | None = None) -> dict[str, object]:
            return {
                "remote": f"https://example.invalid/{name}.git",
                "commit": commit or f"commit-{name}",
                "latestRef": None,
            }

        lock_data = {"dependencies": {name: entry(name) for name in direct_dependency_names}}

        def nested_specs(
            _dependency_root: Path,
            dependency: DependencyPin,
        ) -> tuple[DependencyPin, ...]:
            if operation_counts is not None:
                key = ("load", dependency.dependency_name)
                operation_counts[key] = operation_counts.get(key, 0) + 1
            return tuple(
                workflow._dependency_checkout_spec_from_entry(
                    child_name,
                    entry(
                        child_name,
                        commit=(edge_commits or {}).get((dependency.dependency_name, child_name)),
                    ),
                    declared_by_root=False,
                    source_label=f"{dependency.dependency_name} template",
                    parent_dependency_name=dependency.dependency_name,
                )
                for child_name in adjacency.get(dependency.dependency_name, ())
            )

        def prepare_dependency_root(dependency: DependencyPin) -> Path:
            if operation_counts is not None:
                key = ("prepare", dependency.dependency_name)
                operation_counts[key] = operation_counts.get(key, 0) + 1
            return self.repo_root / dependency.dependency_name

        return workflow._discover_dependency_closure(
            lock_data,
            self.repo_root,
            prepare_dependency_root=prepare_dependency_root,
            load_nested_dependency_specs=nested_specs,
        )

    def test_fetch_remote_refs_updates_moved_tags(self) -> None:
        remote, first_commit = self._create_remote_repo("MovingTagLib", ("CMakeLists.txt",))
        self.git(remote, "tag", "Prerelease-Alpha", first_commit)
        seed_root = self.repo_root / "build" / "dependency_seed_repos" / "MovingTagLib"
        seed_root.parent.mkdir(parents=True)
        self.git(seed_root.parent, "clone", str(remote), str(seed_root))
        self.git(seed_root, "fetch", "--tags", "origin")
        self.assertEqual(
            first_commit,
            self.git(seed_root, "rev-parse", "refs/tags/Prerelease-Alpha"),
        )

        (remote / "CMakeLists.txt").write_text("advanced\n", encoding="utf-8")
        second_commit = self._commit_repo(remote, "advance moving tag")
        self.git(remote, "tag", "-f", "Prerelease-Alpha", second_commit)

        fetch_remote_refs(seed_root, "MovingTagLib", str(remote))

        self.assertEqual(
            second_commit,
            self.git(seed_root, "rev-parse", "refs/tags/Prerelease-Alpha"),
        )

    def _write_lock_file(self, remotes: dict[str, Path], commits: dict[str, str]) -> None:
        lock_data = self._lock_data(remotes, commits)
        self._write_lock_data(lock_data)

    def _lock_data(self, remotes: dict[str, Path], commits: dict[str, str]) -> dict[str, object]:
        return {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "cmakeEnvironment": {},
            "cmakeCacheVariables": {},
            "depsManualPath": {spec.dependency_name: "" for spec in self.specs},
            "dependencies": {
                spec.dependency_name: {
                    "remote": str(remotes[spec.dependency_name]),
                    "commit": commits[spec.dependency_name],
                }
                for spec in self.specs
            },
        }

    def _write_lock_data(self, lock_data: dict[str, object]) -> None:
        (self.repo_root / "source_roots.lock.jsonc").write_text(
            json.dumps(lock_data, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_policy_data(self, policy_data: dict[str, object]) -> None:
        policy_path = self.repo_root / "configs" / "freecm_policy.jsonc"
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_text(json.dumps(policy_data, indent=2) + "\n", encoding="utf-8")

    def _seed_root(self, dependency_name: str) -> Path:
        spec = self.workflow.spec_by_dependency_name[dependency_name]
        return self.workflow._seed_repo_root(self.repo_root, spec.repo_name)

    def _managed_source_root(self, dependency_name: str) -> Path:
        spec = self.workflow.spec_by_dependency_name[dependency_name]
        return self.workflow._managed_dependency_root_for(self.repo_root, spec)

    def _head(self, repo_root: Path) -> str:
        return self.git(repo_root, "rev-parse", "HEAD")

    def _copy_plain_dependency_root(
        self,
        source_root: Path,
        dependency_name: str,
        commit: str,
    ) -> Path:
        target_root = self._managed_source_root(dependency_name)
        shutil.copytree(
            source_root,
            target_root,
            ignore=shutil.ignore_patterns(".git"),
        )
        metadata_path = target_root / ".freecm" / "dependency_source_root.json"
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(
                {
                    "dependencyName": dependency_name,
                    "repoName": dependency_name,
                    "remote": str(source_root),
                    "commit": commit,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return target_root

    def _alias_workflow(self) -> DependencyRootManager:
        specs = (
            DependencyRootSpec(
                dependency_name="LibAlias",
                repo_name="RepoAlias",
                env_key="LIB_ALIAS_SOURCE_ROOT",
                required_relative_paths=("CMakeLists.txt", "include/RepoAlias"),
            ),
        )
        return DependencyRootManager(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=specs,
                repo_display_name="HostRepo",
            )
        )

    def test_path_map_helpers_build_env_maps_and_quote_shell_values(self) -> None:
        validated = validate_dependency_specs(self.specs)
        roots = {
            "LibA": Path("/tmp/LibA"),
            "LibB": Path("/tmp/LibB"),
        }

        path_map = dependency_root_path_map(validated, roots.__getitem__)
        env_map = environment_map(path_map)
        plain_stdout = io.StringIO()
        shell_stdout = io.StringIO()

        with redirect_stdout(plain_stdout):
            print_environment_map(env_map, "plain")
        with redirect_stdout(shell_stdout):
            print_environment_map(env_map, "shell")

        self.assertEqual(
            [spec.env_key for spec in validated], ["LIBA_SOURCE_ROOT", "LIBB_SOURCE_ROOT"]
        )
        self.assertEqual(path_map["LIBA_SOURCE_ROOT"], Path("/tmp/LibA"))
        self.assertEqual(env_map["LIBB_SOURCE_ROOT"], str(Path("/tmp/LibB")))
        self.assertIn(f"LIBA_SOURCE_ROOT={Path('/tmp/LibA')}", plain_stdout.getvalue())
        self.assertEqual(
            shell_stdout.getvalue(),
            f"export LIBA_SOURCE_ROOT={shlex.quote(str(Path('/tmp/LibA')))}\n"
            f"export LIBB_SOURCE_ROOT={shlex.quote(str(Path('/tmp/LibB')))}\n",
        )

        special_value = "space 'quote' $HOME `command`\nnext"
        special_stdout = io.StringIO()
        with redirect_stdout(special_stdout):
            print_environment_map({"SAFE_KEY": special_value}, "shell")
        shell_line = special_stdout.getvalue().removesuffix("\n")
        self.assertEqual(shell_line, f"export SAFE_KEY={shlex.quote(special_value)}")
        self.assertEqual(shlex.split(shell_line), ["export", f"SAFE_KEY={special_value}"])

        with self.assertRaisesRegex(ValueError, "Unsupported environment map output format"):
            print_environment_map(env_map, "json")

        with self.assertRaisesRegex(ValueError, "portable identifier"):
            print_environment_map({"BAD-KEY": "value"}, "shell")

    def test_dependency_specs_reject_duplicate_keys_and_unsafe_relative_paths(self) -> None:
        duplicate_name = DependencyRootSpec(
            dependency_name="LibA",
            repo_name="OtherRepo",
            env_key="OTHER_ROOT",
            required_relative_paths=(),
        )
        duplicate_env = DependencyRootSpec(
            dependency_name="LibC",
            repo_name="LibC",
            env_key="LIBA_SOURCE_ROOT",
            required_relative_paths=(),
        )
        for extra_spec, message in (
            (duplicate_name, "Duplicate dependency name"),
            (duplicate_env, "Duplicate environment key"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    DependencyRootManager(
                        DependencyRootConfig(
                            repo_root=self.repo_root,
                            dependency_root_specs=(*self.specs, extra_spec),
                            repo_display_name="HostRepo",
                        )
                    )

        for env_key in ("1INVALID", "INVALID-KEY", "BAD KEY", "BAD=KEY"):
            with self.subTest(env_key=env_key):
                with self.assertRaisesRegex(ValueError, "portable identifier"):
                    DependencyRootManager(
                        DependencyRootConfig(
                            repo_root=self.repo_root,
                            dependency_root_specs=(
                                DependencyRootSpec(
                                    dependency_name="LibA",
                                    repo_name="LibA",
                                    env_key=env_key,
                                    required_relative_paths=(),
                                ),
                            ),
                            repo_display_name="HostRepo",
                        )
                    )

        for relative_path in (
            "/absolute",
            "../escape",
            "nested/../../escape",
            "C:\\absolute",
            "C:drive-relative",
            "\\rooted",
            "\\\\server\\share\\path",
        ):
            with self.subTest(relative_path=relative_path):
                with self.assertRaisesRegex(ValueError, "dependency root"):
                    DependencyRootManager(
                        DependencyRootConfig(
                            repo_root=self.repo_root,
                            dependency_root_specs=(
                                DependencyRootSpec(
                                    dependency_name="LibA",
                                    repo_name="LibA",
                                    env_key="LIBA_ROOT",
                                    required_relative_paths=(relative_path,),
                                ),
                            ),
                            repo_display_name="HostRepo",
                        )
                    )

        with self.assertRaisesRegex(ValueError, "default required path"):
            DependencyRootManager(
                DependencyRootConfig(
                    repo_root=self.repo_root,
                    dependency_root_specs=(),
                    repo_display_name="HostRepo",
                    default_required_relative_paths=("../escape",),
                )
            )

    def test_known_dependency_specs_are_distinct_from_direct_specs(self) -> None:
        transitive_spec = DependencyRootSpec(
            dependency_name="LibTransitive",
            repo_name="TransitiveRepo",
            env_key="TRANSITIVE_SOURCE_ROOT",
            required_relative_paths=("include/Transitive",),
        )
        default_manager = DependencyRootManager(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=self.specs,
                repo_display_name="HostRepo",
            )
        )
        manager = DependencyRootManager(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=self.specs,
                repo_display_name="HostRepo",
                default_required_relative_paths=("fallback.txt",),
                known_dependency_root_specs=(*self.specs, transitive_spec),
            )
        )

        self.assertEqual(default_manager.known_dependency_root_specs, self.specs)
        self.assertEqual(manager.dependency_root_specs, self.specs)
        self.assertEqual(manager.direct_dependency_root_specs, self.specs)
        self.assertEqual(manager.direct_dependency_names, ("LibA", "LibB"))
        self.assertEqual(manager.spec_by_dependency_name["LibTransitive"], transitive_spec)
        self.assertNotIn("LibTransitive", manager.direct_spec_by_dependency_name)
        self.assertEqual(manager.spec_by_env_key[transitive_spec.env_key], transitive_spec)

        known_pin = manager._dependency_checkout_spec_from_entry(
            "LibTransitive",
            {
                "remote": "https://example.invalid/transitive.git",
                "commit": "a" * 40,
                "latestRef": None,
            },
            declared_by_root=False,
            source_label="nested lock",
        )
        unknown_pin = manager._dependency_checkout_spec_from_entry(
            "UnknownLib",
            {
                "remote": "https://example.invalid/unknown.git",
                "commit": "b" * 40,
                "latestRef": None,
            },
            declared_by_root=False,
            source_label="nested lock",
        )
        self.assertEqual(known_pin.repo_name, "TransitiveRepo")
        self.assertEqual(known_pin.env_key, "TRANSITIVE_SOURCE_ROOT")
        self.assertEqual(known_pin.required_relative_paths, ("include/Transitive",))
        self.assertEqual(unknown_pin.repo_name, "UnknownLib")
        self.assertIsNone(unknown_pin.env_key)
        self.assertEqual(unknown_pin.required_relative_paths, ("fallback.txt",))

    def test_dependency_root_config_preserves_old_positional_fields(self) -> None:
        config = DependencyRootConfig(
            self.repo_root,
            self.specs,
            "HostRepo",
            ("fallback.txt",),
        )

        self.assertEqual(config.default_required_relative_paths, ("fallback.txt",))
        self.assertEqual(config.known_dependency_root_specs, ())

    def test_bound_workflow_exports_direct_and_known_spec_views(self) -> None:
        transitive_spec = DependencyRootSpec(
            dependency_name="LibTransitive",
            repo_name="TransitiveRepo",
            env_key="TRANSITIVE_SOURCE_ROOT",
            required_relative_paths=(),
        )
        namespace: dict[str, object] = {}
        workflow = bind_dependency_root_workflow(
            namespace,
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=self.specs,
                repo_display_name="HostRepo",
                known_dependency_root_specs=(*self.specs, transitive_spec),
            ),
        )

        self.assertIs(namespace["DIRECT_DEPENDENCY_ROOT_SPECS"], workflow.dependency_root_specs)
        self.assertIs(
            namespace["KNOWN_DEPENDENCY_ROOT_SPECS"], workflow.known_dependency_root_specs
        )
        self.assertIs(
            namespace["DIRECT_SPEC_BY_DEPENDENCY_NAME"],
            workflow.direct_spec_by_dependency_name,
        )
        self.assertIs(namespace["SPEC_BY_DEPENDENCY_NAME"], workflow.spec_by_dependency_name)

    def test_known_dependency_specs_require_matching_direct_specs(self) -> None:
        changed_direct = DependencyRootSpec(
            dependency_name="LibA",
            repo_name="OtherLibA",
            env_key="OTHER_LIBA_ROOT",
            required_relative_paths=(),
        )
        cases = (
            ((self.specs[0],), "missing direct dependencies"),
            ((changed_direct, self.specs[1]), "differ from direct dependency specs"),
        )
        for known_specs, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    DependencyRootManager(
                        DependencyRootConfig(
                            repo_root=self.repo_root,
                            dependency_root_specs=self.specs,
                            repo_display_name="HostRepo",
                            known_dependency_root_specs=known_specs,
                        )
                    )

    def test_pin_rejects_known_transitive_dependency(self) -> None:
        transitive_spec = DependencyRootSpec(
            dependency_name="LibTransitive",
            repo_name="LibTransitive",
            env_key="TRANSITIVE_SOURCE_ROOT",
            required_relative_paths=(),
        )
        manager = DependencyRootManager(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=self.specs,
                repo_display_name="HostRepo",
                known_dependency_root_specs=(*self.specs, transitive_spec),
            )
        )
        with self.assertRaisesRegex(ValueError, "non-direct dependency 'LibTransitive'"):
            manager._pin_dependency_ref_unlocked(
                "LibTransitive",
                "main",
                self.repo_root,
            )

    def test_root_lock_validation_only_requires_direct_dependencies(self) -> None:
        remotes, commits = self._bootstrap()
        transitive_spec = DependencyRootSpec(
            dependency_name="LibTransitive",
            repo_name="LibTransitive",
            env_key="TRANSITIVE_SOURCE_ROOT",
            required_relative_paths=(),
        )
        manager = DependencyRootManager(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=self.specs,
                repo_display_name="HostRepo",
                known_dependency_root_specs=(*self.specs, transitive_spec),
            )
        )

        lock_data = manager.load_lock_file()

        self.assertEqual(set(lock_data["dependencies"]), set(remotes))
        self.assertEqual(
            {name: lock_data["dependencies"][name]["commit"] for name in remotes},
            commits,
        )

    def test_show_shell_cli_quotes_environment_values(self) -> None:
        special_value = "root with space/'quote'/$HOME/`command`\nnext"
        dependency_roots = SimpleNamespace(as_environment_map=lambda: {"LIBA_ROOT": special_value})
        stdout = io.StringIO()
        with (
            mock.patch.object(
                self.workflow,
                "load_dependency_roots",
                return_value=dependency_roots,
            ),
            redirect_stdout(stdout),
        ):
            result = self.workflow.cmd_show(argparse.Namespace(format="shell"))

        self.assertEqual(result, 0)
        shell_line = stdout.getvalue().removesuffix("\n")
        self.assertEqual(shell_line, f"export LIBA_ROOT={shlex.quote(special_value)}")
        self.assertEqual(shlex.split(shell_line), ["export", f"LIBA_ROOT={special_value}"])

    def test_dependency_relative_path_rejects_symlink_escape(self) -> None:
        dependency_root = self.repo_root / "dependency"
        outside_root = self.repo_root / "outside"
        dependency_root.mkdir()
        outside_root.mkdir()
        link = dependency_root / "linked"
        try:
            link.symlink_to(outside_root, target_is_directory=True)
        except OSError as exc:
            if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege is not available")
            raise

        with self.assertRaisesRegex(ValueError, "resolved path escapes"):
            resolve_dependency_relative_path(
                dependency_root,
                "linked/file.txt",
                label="LibA required path",
            )

    def test_dependency_specs_reject_path_unsafe_names(self) -> None:
        for dependency_name, repo_name in (("../LibA", "LibA"), ("LibA", "LibA/evil")):
            with self.subTest(dependency_name=dependency_name, repo_name=repo_name):
                with self.assertRaisesRegex(ValueError, "path-safe segment"):
                    DependencyRootManager(
                        DependencyRootConfig(
                            repo_root=self.repo_root,
                            dependency_root_specs=(
                                DependencyRootSpec(
                                    dependency_name=dependency_name,
                                    repo_name=repo_name,
                                    env_key="LIBA_SOURCE_ROOT",
                                    required_relative_paths=("CMakeLists.txt",),
                                ),
                            ),
                            repo_display_name="HostRepo",
                        )
                    )

    def test_core_default_required_paths_are_not_cmake_specific(self) -> None:
        self.assertEqual(DEFAULT_REQUIRED_RELATIVE_PATHS, ())
        workflow = DependencyRootManager(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=(),
                repo_display_name="HostRepo",
            )
        )

        self.assertEqual(workflow.config.default_required_relative_paths, ())

    def test_ensure_active_lock_file_copies_template_once(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        template_path = self.repo_root / "source_roots.lock.jsonc.in"
        active_path = self.repo_root / "source_roots.lock.jsonc"
        template_path.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")
        active_path.unlink()

        created_path, created = self.workflow.ensure_active_lock_file(self.repo_root)
        first_content = active_path.read_text(encoding="utf-8")
        active_path.write_text(first_content.replace('"pinned"', '"manual"'), encoding="utf-8")
        existing_path, existing_created = self.workflow.ensure_active_lock_file(self.repo_root)

        self.assertTrue(created)
        self.assertEqual(created_path, active_path.resolve())
        self.assertFalse(existing_created)
        self.assertEqual(existing_path, active_path.resolve())
        self.assertIn('"manual"', active_path.read_text(encoding="utf-8"))

    def test_ensure_active_lock_file_does_not_accept_legacy_json_names(self) -> None:
        remotes, commits = self._bootstrap()
        legacy_active_path = self.repo_root / "source_roots.lock.json"
        legacy_template_path = self.repo_root / "source_roots.lock.json.in"
        current_active_path = self.repo_root / "source_roots.lock.jsonc"
        current_active_path.unlink()
        legacy_active_path.write_text(
            json.dumps(self._lock_data(remotes, commits), indent=2) + "\n",
            encoding="utf-8",
        )
        legacy_template_path.write_text(
            json.dumps(self._lock_data(remotes, commits), indent=2) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(FileNotFoundError, "source-roots lock template"):
            self.workflow.ensure_active_lock_file(self.repo_root)

    def test_ensure_active_lock_file_rejects_directory_active_lock(self) -> None:
        self._bootstrap()
        active_path = self.repo_root / "source_roots.lock.jsonc"
        active_path.unlink()
        active_path.mkdir()

        with self.assertRaisesRegex(FileExistsError, "source_roots lock path is not a file"):
            self.workflow.ensure_active_lock_file(self.repo_root)

    def test_load_lock_rejects_legacy_asset_fields(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["assetSeeds"] = {}
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(LockfileValidationError, "assetSeeds is no longer supported"):
            self.workflow.load_lock_file(self.repo_root)

    def _assert_init_fails_without_changing_seed(
        self,
        dependency_name: str,
        expected_reason: str,
    ) -> RuntimeError:
        seed_root = self._seed_root(dependency_name)
        original_head = self._head(seed_root) if git_is_work_tree(seed_root) else None
        with self.assertRaisesRegex(SeedRepositoryError, expected_reason) as raised:
            self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        if original_head is not None:
            self.assertEqual(self._head(seed_root), original_head)
        return raised.exception

    def test_init_syncs_existing_seed_when_remote_head_advanced(self) -> None:
        remotes, _ = self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        seed_root = self._seed_root("LibA")
        original_head = self._head(seed_root)

        (remotes["LibA"] / "CMakeLists.txt").write_text("LibA:updated\n", encoding="utf-8")
        advanced_head = self._commit_repo(remotes["LibA"], "advance remote")

        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        self.assertEqual(self._head(seed_root), advanced_head)
        self.assertNotEqual(original_head, advanced_head)
        self.assertFalse(workspace_lock_path(self.repo_root).exists())

    def test_latest_mode_can_track_dependency_latest_ref(self) -> None:
        remotes, commits = self._bootstrap()
        default_branch = self.git(remotes["LibA"], "symbolic-ref", "--short", "HEAD")
        self.git(remotes["LibA"], "checkout", "-b", "stable")
        (remotes["LibA"] / "CMakeLists.txt").write_text("LibA:stable\n", encoding="utf-8")
        stable_head = self._commit_repo(remotes["LibA"], "advance stable")
        self.git(remotes["LibA"], "checkout", default_branch)
        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "latest"
        lock_data["dependencies"]["LibA"]["latestRef"] = "stable"  # type: ignore[index]
        self._write_lock_data(lock_data)

        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        dependency_roots = self.workflow.materialize_dependency_roots(
            self.repo_root,
            allow_network=False,
        )

        self.assertEqual(
            self.workflow.load_lock_file(self.repo_root)["dependencies"]["LibA"]["commit"],
            stable_head,
        )
        self.assertEqual(dependency_roots.resolved_commits["LibA"], stable_head)
        self.assertEqual(
            (dependency_roots.dependency_root_for("LibA") / "CMakeLists.txt").read_text(
                encoding="utf-8",
            ),
            "LibA:stable\n",
        )

    def test_pinned_mode_materializes_locked_commit_even_when_remote_advances(self) -> None:
        remotes, commits = self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        (remotes["LibA"] / "CMakeLists.txt").write_text("LibA:after-lock\n", encoding="utf-8")
        advanced_head = self._commit_repo(remotes["LibA"], "advance remote after lock")
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        dependency_roots = self.workflow.materialize_dependency_roots(
            repo_root=self.repo_root,
            allow_network=False,
        )

        self.assertNotEqual(commits["LibA"], advanced_head)
        self.assertEqual(dependency_roots.resolved_commits["LibA"], commits["LibA"])
        self.assertEqual(
            self._head(dependency_roots.dependency_root_for("LibA")),
            commits["LibA"],
        )
        self.assertFalse(workspace_lock_path(self.repo_root).exists())

    def test_manual_mode_uses_override_path_without_materializing_managed_root(self) -> None:
        remotes, commits = self._bootstrap()
        manual_root = remotes["LibA"]
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "manual"
        lock_data["depsManualPath"]["LibA"] = str(manual_root)  # type: ignore[index]
        self._write_lock_data(lock_data)
        managed_root = self.repo_root / "build" / "dependency_source_roots" / "LibA"
        remove_path(managed_root)

        dependency_roots = self.workflow.materialize_dependency_roots(
            repo_root=self.repo_root,
            allow_network=False,
        )

        self.assertEqual(dependency_roots.dependency_root_for("LibA"), manual_root.resolve())
        self.assertFalse(managed_root.exists())

    def test_latest_mode_offline_uses_existing_seed_refs_without_fetching(self) -> None:
        remotes, commits = self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        seed_root = self._seed_root("LibA")
        self.git(seed_root, "checkout", "-b", "local-latest")
        (seed_root / "CMakeLists.txt").write_text("LibA:local latest\n", encoding="utf-8")
        local_head = self._commit_repo(seed_root, "advance local seed ref")
        self.git(seed_root, "checkout", "master")
        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "latest"
        lock_data["dependencies"]["LibA"]["latestRef"] = "local-latest"  # type: ignore[index]
        self._write_lock_data(lock_data)

        with mock.patch.object(self.workflow, "_fetch_remote_refs") as fetch_refs:
            dependency_roots = self.workflow.materialize_dependency_roots(
                repo_root=self.repo_root,
                allow_network=False,
            )

        self.assertEqual(dependency_roots.resolved_commits["LibA"], local_head)
        self.assertEqual(
            self.workflow.load_lock_file(self.repo_root)["dependencies"]["LibA"]["commit"],
            local_head,
        )
        fetch_refs.assert_not_called()

    def test_materialize_defaults_to_offline_mode(self) -> None:
        self._bootstrap()

        with self.assertRaisesRegex(FileNotFoundError, "Missing dependency seed repo path"):
            self.workflow.materialize_dependency_roots(repo_root=self.repo_root)

    def test_materialize_offline_does_not_clone_fetch_or_sync_seed_repos(self) -> None:
        self._bootstrap()

        with (
            mock.patch.object(self.workflow, "_ensure_seed_repo") as ensure_seed,
            mock.patch.object(self.workflow, "_sync_seed_repo_to_default_branch") as sync_seed,
            mock.patch.object(self.workflow, "_fetch_remote_refs") as fetch_refs,
        ):
            with self.assertRaisesRegex(FileNotFoundError, "Missing dependency seed repo path"):
                self.workflow.materialize_dependency_roots(
                    repo_root=self.repo_root,
                    allow_network=False,
                )

        ensure_seed.assert_not_called()
        sync_seed.assert_not_called()
        fetch_refs.assert_not_called()

    def test_load_dependency_roots_does_not_clone_fetch_or_sync_seed_repos(self) -> None:
        self._bootstrap()

        with (
            mock.patch.object(self.workflow, "_ensure_seed_repo") as ensure_seed,
            mock.patch.object(self.workflow, "_sync_seed_repo_to_default_branch") as sync_seed,
            mock.patch.object(self.workflow, "_fetch_remote_refs") as fetch_refs,
        ):
            with self.assertRaisesRegex(FileNotFoundError, "Missing dependency seed repo path"):
                self.workflow.load_dependency_roots(repo_root=self.repo_root)

        ensure_seed.assert_not_called()
        sync_seed.assert_not_called()
        fetch_refs.assert_not_called()

    def test_manual_path_relative_to_repo_root_accepts_packaged_plain_source_root(self) -> None:
        remotes, commits = self._bootstrap()
        liba_root = self._copy_plain_dependency_root(remotes["LibA"], "LibA", commits["LibA"])
        libb_root = self._copy_plain_dependency_root(remotes["LibB"], "LibB", commits["LibB"])
        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "manual"
        liba_path = "build/dependency_source_roots/LibA"
        libb_path = "build/dependency_source_roots/LibB"
        lock_data["depsManualPath"]["LibA"] = liba_path  # type: ignore[index]
        lock_data["depsManualPath"]["LibB"] = libb_path  # type: ignore[index]
        self._write_lock_data(lock_data)

        dependency_roots = self.workflow.load_dependency_roots(repo_root=self.repo_root)

        self.assertEqual(dependency_roots.dependency_root_for("LibA"), liba_root.resolve())
        self.assertEqual(dependency_roots.dependency_root_for("LibB"), libb_root.resolve())
        self.assertEqual(self.workflow.validate_dependency_roots(dependency_roots), [])

    def test_plain_source_root_requires_matching_packaged_metadata(self) -> None:
        remotes, commits = self._bootstrap()
        liba_root = self._copy_plain_dependency_root(remotes["LibA"], "LibA", commits["LibA"])
        self._copy_plain_dependency_root(remotes["LibB"], "LibB", commits["LibB"])
        metadata_path = liba_root / ".freecm" / "dependency_source_root.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["commit"] = "f" * 40
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "manual"
        liba_path = "build/dependency_source_roots/LibA"
        libb_path = "build/dependency_source_roots/LibB"
        lock_data["depsManualPath"]["LibA"] = liba_path  # type: ignore[index]
        lock_data["depsManualPath"]["LibB"] = libb_path  # type: ignore[index]
        self._write_lock_data(lock_data)

        problems = self.workflow.validate_dependency_roots(
            self.workflow.load_dependency_roots(repo_root=self.repo_root)
        )

        self.assertEqual(len(problems), 1)
        self.assertIn("packaged source root metadata mismatch", problems[0])

    def test_materialize_command_passes_offline_flag(self) -> None:
        resolved = mock.Mock()
        resolved.as_environment_map.return_value = {}
        with mock.patch.object(
            self.workflow,
            "materialize_dependency_roots",
            return_value=resolved,
        ) as materialize:
            result = self.workflow.cmd_materialize(mock.Mock())

        self.assertEqual(result, 0)
        materialize.assert_called_once_with(allow_network=False)

    def test_cli_does_not_expose_networked_seed_prepare_command(self) -> None:
        parser = self.workflow.build_parser()
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            parser.parse_args(["prepare-seed-closure"])

        self.assertIn("invalid choice", stderr.getvalue())

    def test_mutating_dependency_operations_use_workspace_lock(self) -> None:
        remotes, _ = self._bootstrap()
        seed_lock_calls: list[Path] = []
        materializer_lock_calls: list[Path] = []

        @contextmanager
        def seed_lock(repo_root: Path):
            seed_lock_calls.append(Path(repo_root).resolve())
            yield

        @contextmanager
        def materializer_lock(repo_root: Path):
            materializer_lock_calls.append(Path(repo_root).resolve())
            yield

        with mock.patch("freecm.seed_store.workspace_mutation_lock", side_effect=seed_lock):
            self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        with mock.patch(
            "freecm.materializer.workspace_mutation_lock", side_effect=materializer_lock
        ):
            self.workflow.materialize_dependency_roots(
                repo_root=self.repo_root, allow_network=False
            )
            self.workflow.pin_dependency_ref(
                "LibA",
                self.git(remotes["LibA"], "rev-parse", "HEAD"),
                repo_root=self.repo_root,
            )

        self.assertEqual(seed_lock_calls, [self.repo_root.resolve()])
        self.assertEqual(
            materializer_lock_calls,
            [self.repo_root.resolve(), self.repo_root.resolve()],
        )

    def test_pin_defaults_to_local_seed_without_fetching(self) -> None:
        remotes, commits = self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        default_branch = self.git(remotes["LibA"], "symbolic-ref", "--short", "HEAD")
        self.git(remotes["LibA"], "checkout", "-b", "offline-pin")
        (remotes["LibA"] / "CMakeLists.txt").write_text("LibA:offline-pin\n", encoding="utf-8")
        advanced_head = self._commit_repo(remotes["LibA"], "advance offline pin")
        self.git(remotes["LibA"], "checkout", default_branch)

        with self.assertRaisesRegex(RuntimeError, "Unable to resolve ref"):
            self.workflow.pin_dependency_ref("LibA", "offline-pin", repo_root=self.repo_root)

        lock_data = self.workflow.load_lock_file(self.repo_root)
        self.assertEqual(lock_data["dependencies"]["LibA"]["commit"], commits["LibA"])
        self.assertEqual(
            advanced_head,
            self.workflow.pin_dependency_ref(
                "LibA",
                "offline-pin",
                repo_root=self.repo_root,
                allow_fetch=True,
            ),
        )

    def test_pin_dependency_ref_default_path_does_not_fetch(self) -> None:
        self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        seed_head = self._head(self._seed_root("LibA"))

        with mock.patch.object(self.workflow, "_fetch_remote_refs") as fetch_refs:
            commit = self.workflow.pin_dependency_ref(
                "LibA",
                seed_head,
                repo_root=self.repo_root,
            )

        self.assertEqual(commit, seed_head)
        fetch_refs.assert_not_called()
        self.assertFalse(workspace_lock_path(self.repo_root).exists())

    def test_init_syncs_managed_seed_even_when_active_lock_points_to_it_as_manual(self) -> None:
        remotes, commits = self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        seed_root = self._seed_root("LibA")
        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "manual"
        lock_data["depsManualPath"]["LibA"] = str(seed_root)  # type: ignore[index]
        self._write_lock_data(lock_data)

        (remotes["LibA"] / "CMakeLists.txt").write_text("LibA:manual-seed\n", encoding="utf-8")
        advanced_head = self._commit_repo(remotes["LibA"], "advance manual seed remote")

        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        self.assertEqual(self._head(seed_root), advanced_head)

    def test_init_rejects_dirty_managed_seed_even_when_active_lock_points_to_it_as_manual(
        self,
    ) -> None:
        remotes, commits = self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        seed_root = self._seed_root("LibA")
        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "manual"
        lock_data["depsManualPath"]["LibA"] = str(seed_root)  # type: ignore[index]
        self._write_lock_data(lock_data)
        (seed_root / "local-notes.txt").write_text("manual edit\n", encoding="utf-8")

        self._assert_init_fails_without_changing_seed("LibA", "worktree is dirty")
        self.assertTrue((seed_root / "local-notes.txt").is_file())

    def test_init_syncs_existing_seed_back_to_default_branch(self) -> None:
        remotes, _ = self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        seed_root = self._seed_root("LibA")
        default_branch = self.git(remotes["LibA"], "rev-parse", "--abbrev-ref", "HEAD")
        self.git(seed_root, "checkout", "-b", "manual-test")

        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        self.assertEqual(self.git(seed_root, "rev-parse", "--abbrev-ref", "HEAD"), default_branch)

    def test_init_fails_when_existing_seed_worktree_is_dirty(self) -> None:
        self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        seed_root = self._seed_root("LibA")
        (seed_root / "local-notes.txt").write_text("manual edit\n", encoding="utf-8")

        self._assert_init_fails_without_changing_seed("LibA", "worktree is dirty")
        self.assertTrue((seed_root / "local-notes.txt").is_file())

    def test_init_discards_dirty_seed_submodule_pointer(self) -> None:
        remotes, commits = self._bootstrap()
        tooling_remote, tooling_first = self._create_remote_repo("FreeCM", ("tool.txt",))
        self.git(
            remotes["LibA"],
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(tooling_remote),
            "FreeCM",
        )
        commits["LibA"] = self._commit_repo(remotes["LibA"], "add tooling submodule")
        self._write_lock_file(remotes, commits)

        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        seed_root = self._seed_root("LibA")
        self.git(
            seed_root,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "update",
            "--init",
            "--checkout",
        )

        (tooling_remote / "tool.txt").write_text("tooling update\n", encoding="utf-8")
        tooling_second = self._commit_repo(tooling_remote, "advance tooling")
        self.git(seed_root / "FreeCM", "fetch", "origin")
        self.git(seed_root / "FreeCM", "checkout", tooling_second)
        self.assertIn("FreeCM", self.git(seed_root, "status", "--porcelain"))

        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        self.assertEqual(self._head(seed_root / "FreeCM"), tooling_first)
        self.assertEqual(self.git(seed_root, "status", "--porcelain", "--untracked-files=all"), "")

    def test_init_reclones_existing_seed_when_origin_mismatches(self) -> None:
        remotes, commits = self._bootstrap()
        wrong_remote, _ = self._create_remote_repo("WrongLibA", ("CMakeLists.txt",))
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        seed_root = self._seed_root("LibA")
        self.git(seed_root, "remote", "set-url", "origin", str(wrong_remote))

        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        self.assertEqual(commits["LibA"], self._head(seed_root))
        self.assertTrue(remotes["LibA"].is_dir())
        self.assertEqual(self.git(seed_root, "remote", "get-url", "origin"), str(remotes["LibA"]))

    def test_init_fails_when_existing_seed_is_plain_directory_before_cloning_missing(self) -> None:
        self._bootstrap()
        liba_seed = self._seed_root("LibA")
        liba_seed.mkdir(parents=True)
        (liba_seed / "README.txt").write_text("not a checkout\n", encoding="utf-8")
        libb_seed = self._seed_root("LibB")

        with self.assertRaisesRegex(RuntimeError, "path is not a git worktree"):
            self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        self.assertTrue((liba_seed / "README.txt").is_file())
        self.assertFalse(libb_seed.exists())

    def test_init_clones_missing_seed_after_existing_seeds_pass_preflight(self) -> None:
        self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        existing_seed = self._seed_root("LibA")
        existing_head = self._head(existing_seed)
        missing_seed = self._seed_root("LibB")
        remove_path(missing_seed)

        closure = self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        self.assertEqual(closure.topo_order, ("LibA", "LibB"))
        self.assertTrue(git_is_work_tree(missing_seed))
        self.assertEqual(self._head(existing_seed), existing_head)

    def test_init_does_not_fetch_immediately_after_cloning_missing_seed(self) -> None:
        self._bootstrap()

        with mock.patch("freecm.dependency_roots.fetch_remote_refs") as fetch_remote_refs:
            closure = self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        self.assertEqual(closure.topo_order, ("LibA", "LibB"))
        self.assertTrue(git_is_work_tree(self._seed_root("LibA")))
        self.assertTrue(git_is_work_tree(self._seed_root("LibB")))
        fetch_remote_refs.assert_not_called()

    def test_seed_clone_defaults_to_visible_git_output_unless_quiet(self) -> None:
        remotes, _ = self._bootstrap()
        dependency = tuple(
            self.workflow._root_dependency_specs_from_lock(
                self.workflow.load_lock_file(self.repo_root),
            )
        )[0]
        visible_seed_root = self._seed_root("LibA")
        quiet_seed_root = self._seed_root("LibB")

        with (
            mock.patch.object(self.workflow, "_remote_default_branch", return_value="master"),
            mock.patch("freecm.seed_store.run") as run_mock,
            mock.patch("freecm.seed_store.git"),
        ):
            self.workflow._clone_missing_seed_repo_to_default_branch(
                visible_seed_root,
                dependency,
            )
            self.workflow._clone_missing_seed_repo_to_default_branch(
                quiet_seed_root,
                dependency,
                quiet=True,
            )

        self.assertEqual(
            run_mock.call_args_list[0],
            mock.call(
                ["git", "clone", str(remotes["LibA"]), str(visible_seed_root)],
                quiet=False,
            ),
        )
        self.assertEqual(
            run_mock.call_args_list[1],
            mock.call(
                ["git", "clone", str(remotes["LibA"]), str(quiet_seed_root)],
                quiet=True,
            ),
        )

    def test_init_reports_seed_progress_during_clone_and_sync(self) -> None:
        self._bootstrap()
        events: list[tuple[str, str, str]] = []

        closure = self.workflow.prepare_seed_repository_closure(
            repo_root=self.repo_root,
            progress=lambda action, message, level: events.append(
                (action, message, level),
            ),
        )

        self.assertEqual(closure.topo_order, ("LibA", "LibB"))
        self.assertTrue(
            any(action == "seed" and "LibA: cloning" in message for action, message, _ in events)
        )
        self.assertTrue(
            any(action == "seed" and "LibA: cloned" in message for action, message, _ in events)
        )
        self.assertTrue(
            any(action == "seed" and "LibA: syncing" in message for action, message, _ in events)
        )
        self.assertTrue(
            any(
                action == "seed" and "LibA: ready" in message and level == "ok"
                for action, message, level in events
            )
        )

    def test_init_skips_invalid_nested_seed_template(self) -> None:
        remotes, _ = self._bootstrap()
        (remotes["LibA"] / "source_roots.lock.jsonc.in").write_text(
            json.dumps({"schemaVersion": 2, "dependencies": {}}) + "\n",
            encoding="utf-8",
        )
        self._commit_repo(remotes["LibA"], "add legacy nested template")

        closure = self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        self.assertEqual(closure.topo_order, ("LibA", "LibB"))
        self.assertTrue(git_is_work_tree(self._seed_root("LibA")))

    def test_init_accepts_jsonc_nested_seed_template(self) -> None:
        remotes, _ = self._bootstrap()
        libc_remote, libc_commit = self._create_remote_repo("LibC", ("CMakeLists.txt",))
        (remotes["LibA"] / "source_roots.lock.jsonc.in").write_text(
            f"""{{
  // Nested templates may also use JSONC.
  "schemaVersion": 5,
  "depsMode": "pinned",
  "cmakeEnvironment": {{}},
  "cmakeCacheVariables": {{}},
  "depsManualPath": {{
    "LibC": "",
  }},
  "dependencies": {{
    "LibC": {{
      "remote": {json.dumps(str(libc_remote))},
      "commit": "{libc_commit}",
    }},
  }},
}}
""",
            encoding="utf-8",
        )
        self._commit_repo(remotes["LibA"], "add jsonc nested template")

        closure = self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        self.assertEqual(closure.topo_order, ("LibC", "LibA", "LibB"))
        self.assertTrue(git_is_work_tree(self.workflow._seed_repo_root(self.repo_root, "LibC")))

    def test_init_syncs_seed_that_appears_after_parent_seed_update(self) -> None:
        remotes, _ = self._bootstrap()
        libc_remote, libc_initial = self._create_remote_repo("LibC", ("CMakeLists.txt",))
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        stale_libc_seed = self.workflow._seed_repo_root(self.repo_root, "LibC")
        run_git_fixture(stale_libc_seed.parent, "clone", str(libc_remote), str(stale_libc_seed))

        (libc_remote / "CMakeLists.txt").write_text("LibC:updated\n", encoding="utf-8")
        libc_updated = self._commit_repo(libc_remote, "advance LibC")
        self.assertEqual(self._head(stale_libc_seed), libc_initial)

        self._write_nested_template(
            remotes["LibA"],
            dependencies={
                "LibC": {
                    "remote": str(libc_remote),
                    "commit": libc_updated,
                }
            },
        )

        closure = self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        self.assertIn("LibC", closure.topo_order)
        self.assertEqual(self._head(stale_libc_seed), libc_updated)

    def test_lock_validation_rejects_missing_dependency(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        del lock_data["dependencies"]["LibB"]  # type: ignore[index]
        del lock_data["depsManualPath"]["LibB"]  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "missing dependencies: LibB"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_rejects_extra_dependency(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibC"] = copy.deepcopy(lock_data["dependencies"]["LibA"])  # type: ignore[index]
        lock_data["depsManualPath"]["LibC"] = ""  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "unexpected dependencies: LibC"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_rejects_path_unsafe_dependency_names(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["../LibC"] = copy.deepcopy(lock_data["dependencies"]["LibA"])  # type: ignore[index]
        lock_data["depsManualPath"]["../LibC"] = ""  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "path-safe segment"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_rejects_path_unsafe_repo_name(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["repoName"] = "../RepoA"  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "repository name"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_repo_alias_uses_repo_name_for_seed_materialize_pin_and_resolve(self) -> None:
        workflow = self._alias_workflow()
        remote, initial_commit = self._create_remote_repo(
            "RepoAlias",
            ("CMakeLists.txt", "include/RepoAlias"),
        )
        lock_data: dict[str, object] = {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "cmakeEnvironment": {},
            "cmakeCacheVariables": {},
            "depsManualPath": {"LibAlias": ""},
            "dependencies": {
                "LibAlias": {
                    "remote": str(remote),
                    "commit": initial_commit,
                }
            },
        }
        self._write_lock_data(lock_data)

        closure = workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        alias_seed = self.repo_root / "build" / "dependency_seed_repos" / "RepoAlias"
        wrong_seed = self.repo_root / "build" / "dependency_seed_repos" / "LibAlias"

        self.assertEqual(closure.dependency_pins_by_name["LibAlias"].repo_name, "RepoAlias")
        self.assertTrue(git_is_work_tree(alias_seed))
        self.assertFalse(wrong_seed.exists())

        dependency_roots = workflow.materialize_dependency_roots(
            repo_root=self.repo_root,
            allow_network=False,
        )
        alias_root = self.repo_root / "build" / "dependency_source_roots" / "RepoAlias"
        wrong_root = self.repo_root / "build" / "dependency_source_roots" / "LibAlias"

        self.assertEqual(dependency_roots.seed_repository_for("LibAlias"), alias_seed.resolve())
        self.assertEqual(dependency_roots.dependency_root_for("LibAlias"), alias_root.resolve())
        self.assertTrue(git_is_work_tree(alias_root))
        self.assertFalse(wrong_root.exists())
        self.assertEqual(
            dependency_roots.as_json_dict()["dependencies"]["LibAlias"]["repoName"],
            "RepoAlias",
        )

        self.git(alias_seed, "tag", "alias-pin", initial_commit)
        self.assertEqual(
            workflow.pin_dependency_ref("LibAlias", "alias-pin", repo_root=self.repo_root),
            initial_commit,
        )

    def test_nested_dependency_repo_name_is_preserved_from_lock_entry(self) -> None:
        remotes, commits = self._bootstrap()
        nested_remote, nested_commit = self._create_remote_repo(
            "RepoNested",
            ("CMakeLists.txt",),
        )
        liba_commit = self._write_nested_template(
            remotes["LibA"],
            dependencies={
                "LibNested": {
                    "repoName": "RepoNested",
                    "remote": str(nested_remote),
                    "commit": nested_commit,
                }
            },
        )
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["commit"] = liba_commit  # type: ignore[index]
        self._write_lock_data(lock_data)

        closure = self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        nested_seed = self.repo_root / "build" / "dependency_seed_repos" / "RepoNested"
        wrong_seed = self.repo_root / "build" / "dependency_seed_repos" / "LibNested"

        self.assertIn("LibNested", closure.topo_order)
        self.assertEqual(closure.dependency_pins_by_name["LibNested"].repo_name, "RepoNested")
        self.assertTrue(git_is_work_tree(nested_seed))
        self.assertFalse(wrong_seed.exists())

    def test_nested_manual_dependency_lock_helper_writes_core_manual_lock(self) -> None:
        dependency_root = self.repo_root / "build" / "dependency_source_roots" / "LibA"
        dependency_root.mkdir(parents=True)
        child_root = self.repo_root / "build" / "dependency_source_roots" / "LibB"
        child_root.mkdir(parents=True)
        (dependency_root / "source_roots.lock.jsonc.in").write_text(
            json.dumps(
                {
                    "schemaVersion": 5,
                    "depsMode": "pinned",
                    "cmakeEnvironment": {},
                    "cmakeCacheVariables": {},
                    "depsManualPath": {"LibB": ""},
                    "dependencies": {
                        "LibB": {
                            "remote": "file:///LibB",
                            "commit": "b" * 40,
                        },
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        def dependency_root_for(dependency_name: str) -> Path:
            self.assertEqual(dependency_name, "LibB")
            return child_root

        write_nested_manual_dependency_lock(dependency_root, dependency_root_for)

        lock_path = dependency_root / "source_roots.lock.jsonc"
        lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(lock_data["depsMode"], "manual")
        self.assertEqual(lock_data["depsManualPath"]["LibB"], str(child_root))
        assert_atomic_write_sidecars(self, lock_path)

    def test_lock_validation_rejects_invalid_mode(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "floating"
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "Invalid depsMode"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_rejects_removed_mode_and_manual_path_fields(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["defaultMode"] = lock_data.pop("depsMode")
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "defaultMode is no longer supported"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

        lock_data = self._lock_data(remotes, commits)
        lock_data["manualRoots"] = lock_data.pop("depsManualPath")
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "manualRoots is no longer supported"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_rejects_invalid_dependency_field(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["commit"] = ""  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "Invalid field 'commit'"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_defaults_missing_cmake_maps(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        del lock_data["cmakeEnvironment"]
        del lock_data["cmakeCacheVariables"]
        self._write_lock_data(lock_data)

        loaded = self.workflow.load_lock_file(repo_root=self.repo_root)

        self.assertEqual(loaded["cmakeEnvironment"], {})
        self.assertEqual(loaded["cmakeCacheVariables"], {})

    def test_lock_validation_rejects_old_cmake_settings(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["cmakeSettings"] = {"CMAKE_EXECUTABLE": "cmake"}
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "cmakeSettings is no longer supported"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_rejects_invalid_cmake_environment_value(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["cmakeEnvironment"] = {"CC": 7}
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "Invalid cmakeEnvironment.CC"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_rejects_invalid_cmake_cache_variables_map(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["cmakeCacheVariables"] = ["CMAKE_EXPORT_COMPILE_COMMANDS"]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "Invalid cmakeCacheVariables map"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_accepts_platform_cmake_cache_variables(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["cmakeCacheVariables"] = {
            "DEV_MODE": "true",
            "mac": {
                "DEV_MODE": "false",
                "APPLE_ONLY": "true",
            },
            "linux": {
                "LINUX_ONLY": "true",
            },
            "win": {},
        }
        self._write_lock_data(lock_data)

        loaded = self.workflow.load_lock_file(repo_root=self.repo_root)

        self.assertEqual(loaded["cmakeCacheVariables"]["DEV_MODE"], "true")
        self.assertEqual(
            loaded["cmakeCacheVariables"]["mac"],
            {
                "DEV_MODE": "false",
                "APPLE_ONLY": "true",
            },
        )

    def test_lock_validation_rejects_unknown_cmake_cache_variable_group(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["cmakeCacheVariables"] = {
            "ios": {
                "IOS_ONLY": "true",
            },
        }
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "platform keys"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_accepts_terminal_path(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["terminalPath"] = {
            "common": ["tools/bin"],
            "mac": ["/opt/homebrew/bin"],
            "linux": ["/usr/local/bin"],
            "win": ["tools/win/bin"],
        }
        self._write_lock_data(lock_data)

        loaded = self.workflow.load_lock_file(repo_root=self.repo_root)

        self.assertEqual(
            loaded["terminalPath"],
            {
                "common": ["tools/bin"],
                "mac": ["/opt/homebrew/bin"],
                "linux": ["/usr/local/bin"],
                "win": ["tools/win/bin"],
            },
        )

    def test_lock_validation_rejects_invalid_terminal_path(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["terminalPath"] = {
            "ios": ["tools/ios/bin"],
        }
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "Invalid terminalPath.ios"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

        lock_data["terminalPath"] = {
            "common": "tools/bin",
        }
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "Invalid terminalPath.common"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_accepts_jsonc_comments_and_trailing_commas(self) -> None:
        remotes, commits = self._bootstrap()
        (self.repo_root / "source_roots.lock.jsonc").write_text(
            f"""{{
  // JSONC comments are allowed in local lock files.
  "schemaVersion": 5,
  "depsMode": "pinned", // inline comment
  "cmakeEnvironment": {{}},
  "cmakeCacheVariables": {{
    "DOC_URL": "https://example.com/not-a-comment",
  }},
  "depsManualPath": {{
    "LibA": "",
    "LibB": "",
  }},
  "dependencies": {{
    "LibA": {{
      "remote": {json.dumps(str(remotes["LibA"]))},
      "commit": "{commits["LibA"]}",
      "abiGroup": "abi-main",
    }},
    "LibB": {{
      "remote": {json.dumps(str(remotes["LibB"]))},
      "commit": "{commits["LibB"]}",
      "abiGroup": "abi-main",
    }},
  }},
}}
""",
            encoding="utf-8",
        )

        lock_data = self.workflow.load_lock_file(repo_root=self.repo_root)

        self.assertEqual(
            lock_data["cmakeCacheVariables"]["DOC_URL"], "https://example.com/not-a-comment"
        )
        self.assertEqual(lock_data["dependencies"]["LibA"]["commit"], commits["LibA"])
        self.assertNotIn("abiGroup", lock_data["dependencies"]["LibA"])

    def test_loads_jsonc_rejects_invalid_jsonc_with_path_label(self) -> None:
        with self.assertRaisesRegex(LockfileValidationError, "Invalid JSON/JSONC in lock"):
            loads_jsonc('{"schemaVersion": }', path_label="lock")

    def test_lock_validation_rejects_old_schema_version(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["schemaVersion"] = 4
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(LockfileValidationError, "schemaVersion 4") as raised:
            self.workflow.load_lock_file(repo_root=self.repo_root)
        self.assertIsInstance(raised.exception, FreeCMError)

    def test_lock_validation_rejects_unknown_dependency_field(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["legacy"] = "nope"  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "unexpected fields: legacy"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_ignores_legacy_abi_group(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["abiGroup"] = ""  # type: ignore[index]
        self._write_lock_data(lock_data)

        loaded = self.workflow.load_lock_file(repo_root=self.repo_root)

        self.assertNotIn("abiGroup", loaded["dependencies"]["LibA"])

    def test_lock_validation_rejects_removed_v3_metadata_fields(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["groups"] = ["core"]  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "unexpected fields: groups"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_accepts_optional_repo_name_field(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["repoName"] = "RepoAlias"  # type: ignore[index]
        self._write_lock_data(lock_data)

        loaded = self.workflow.load_lock_file(repo_root=self.repo_root)

        self.assertEqual(loaded["dependencies"]["LibA"]["repoName"], "RepoAlias")

    def test_offline_materialize_fails_when_locked_commit_is_missing_locally(self) -> None:
        remotes, commits = self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["commit"] = "f" * 40  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(MaterializationError, "Missing locked commit"):
            self.workflow.materialize_dependency_roots(
                repo_root=self.repo_root,
                allow_network=False,
            )

    def test_resolve_json_includes_direct_transitive_manual_and_declaration_data(self) -> None:
        remotes, commits = self._bootstrap()
        libc_remote, libc_commit = self._create_remote_repo("LibC", ("CMakeLists.txt",))
        liba_commit = self._write_nested_template(
            remotes["LibA"],
            dependencies={
                "LibC": {
                    "remote": str(libc_remote),
                    "commit": libc_commit,
                    "abiGroup": "abi-libc",
                }
            },
        )
        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "manual"
        lock_data["depsManualPath"]["LibA"] = str(remotes["LibA"])  # type: ignore[index]
        lock_data["dependencies"]["LibA"]["commit"] = liba_commit  # type: ignore[index]
        self._write_lock_data(lock_data)
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        dependency_roots = self.workflow.load_dependency_roots(repo_root=self.repo_root)
        data = dependency_roots.as_json_dict()

        self.assertEqual(data["directDependencyNames"], ["LibA", "LibB"])
        self.assertEqual(data["dependencyNamesByParent"]["LibA"], ["LibC"])
        self.assertIn("LibC", data["closureOrder"])
        self.assertEqual(data["dependencies"]["LibA"]["mode"], "manual")
        self.assertEqual(
            data["dependencies"]["LibA"]["manualOverride"], str(remotes["LibA"].resolve())
        )
        self.assertEqual(data["dependencies"]["LibC"]["parents"], ["LibA"])
        self.assertNotIn("abiGroup", data["dependencies"]["LibC"])
        self.assertEqual(data["dependencies"]["LibA"]["repoName"], "LibA")
        self.assertEqual(data["dependencies"]["LibC"]["repoName"], "LibC")
        self.assertNotIn("repoName", data["dependencies"]["LibC"]["declaredBy"][0])
        self.assertNotIn("groups", data["dependencies"]["LibC"])
        self.assertIn(
            "source_roots.lock.jsonc.in", data["dependencies"]["LibC"]["declaredBy"][0]["source"]
        )

    def test_dependency_conflict_reports_sources(self) -> None:
        remotes, commits = self._bootstrap()
        libc_remote, libc_commit = self._create_remote_repo("LibC", ("CMakeLists.txt",))
        other_remote, _ = self._create_remote_repo("OtherLibC", ("CMakeLists.txt",))
        self._write_nested_template(
            remotes["LibA"],
            dependencies={
                "LibC": {
                    "remote": str(libc_remote),
                    "commit": libc_commit,
                }
            },
        )
        self._write_nested_template(
            remotes["LibB"],
            dependencies={
                "LibC": {
                    "remote": str(other_remote),
                    "commit": libc_commit,
                }
            },
        )
        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "manual"
        lock_data["depsManualPath"]["LibA"] = str(remotes["LibA"])  # type: ignore[index]
        lock_data["depsManualPath"]["LibB"] = str(remotes["LibB"])  # type: ignore[index]
        self._write_lock_data(lock_data)
        parser = self.workflow.build_parser()

        audit_stdout = io.StringIO()
        with redirect_stdout(audit_stdout):
            audit_code = self.workflow.cmd_audit(parser.parse_args(["audit", "--format", "json"]))
        audit_data = json.loads(audit_stdout.getvalue())

        self.assertEqual(audit_code, 1)
        self.assertEqual(audit_data["policyViolations"], [])
        self.assertEqual(audit_data["conflicts"][0]["dependencyName"], "LibC")
        self.assertEqual(audit_data["conflicts"][0]["fieldName"], "remote")
        self.assertIn("LibA", audit_data["conflicts"][0]["existing"]["source"])
        self.assertIn("LibB", audit_data["conflicts"][0]["candidate"]["source"])
        self.assertTrue(audit_data["conflicts"][0]["suggestedActions"])

        explain_stdout = io.StringIO()
        with redirect_stdout(explain_stdout):
            explain_code = self.workflow.cmd_explain_conflict(
                parser.parse_args(["explain-conflict", "LibC", "--format", "json"]),
            )
        explain_data = json.loads(explain_stdout.getvalue())

        self.assertEqual(explain_code, 0)
        self.assertTrue(explain_data["found"])
        self.assertEqual(explain_data["conflicts"][0]["dependencyName"], "LibC")

        with self.assertRaisesRegex(
            DependencyConflictError, r"(?s)existing: .*LibA.*candidate: .*LibB"
        ):
            self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

    def test_cli_resolve_outputs_full_closure(self) -> None:
        self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        parser = self.workflow.build_parser()
        resolve_args = parser.parse_args(["resolve", "--format", "json"])

        resolve_stdout = io.StringIO()
        with redirect_stdout(resolve_stdout):
            resolve_code = self.workflow.cmd_resolve(resolve_args)
        self.assertEqual(resolve_code, 0)
        resolve_data = json.loads(resolve_stdout.getvalue())
        self.assertEqual(resolve_data["closureOrder"], ["LibA", "LibB"])
        self.assertNotIn("abiGroup", resolve_data["dependencies"]["LibA"])
        self.assertEqual(resolve_data["dependencies"]["LibA"]["repoName"], "LibA")

    def test_large_dependency_chain_resolves_graph_report(self) -> None:
        repo_infos: dict[str, tuple[Path, str]] = {}
        for index in reversed(range(12)):
            dependency_name = f"Lib{index}"
            remote, commit = self._create_remote_repo(dependency_name, ("CMakeLists.txt",))
            if index < 11:
                child_name = f"Lib{index + 1}"
                child_remote, child_commit = repo_infos[child_name]
                commit = self._write_nested_template(
                    remote,
                    dependencies={
                        child_name: {
                            "remote": str(child_remote),
                            "commit": child_commit,
                        }
                    },
                )
            repo_infos[dependency_name] = (remote, commit)
        spec = DependencyRootSpec(
            dependency_name="Lib0",
            repo_name="Lib0",
            env_key="LIB0_SOURCE_ROOT",
            required_relative_paths=("CMakeLists.txt",),
        )
        workflow = DependencyRootManager(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=(spec,),
                repo_display_name="HostRepo",
            )
        )
        lib0_remote, lib0_commit = repo_infos["Lib0"]
        self._write_lock_data(
            {
                "schemaVersion": 5,
                "depsMode": "pinned",
                "cmakeEnvironment": {},
                "cmakeCacheVariables": {},
                "depsManualPath": {"Lib0": ""},
                "dependencies": {
                    "Lib0": {
                        "remote": str(lib0_remote),
                        "commit": lib0_commit,
                    }
                },
            }
        )

        workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        report = workflow.dependency_graph_report(repo_root=self.repo_root)

        self.assertEqual(len(report["dependencies"]), 12)
        self.assertEqual(len(report["edges"]), 11)
        self.assertEqual(
            [dependency["dependencyName"] for dependency in report["dependencies"]],
            [f"Lib{index}" for index in reversed(range(12))],
        )

    def test_synthetic_deep_dependency_chain_uses_iterative_traversal(self) -> None:
        dependency_count = 1500
        adjacency = {f"Lib{index}": (f"Lib{index + 1}",) for index in range(dependency_count - 1)}

        closure = self._synthetic_dependency_closure(adjacency)

        self.assertEqual(len(closure.topo_order), dependency_count)
        self.assertEqual(
            closure.topo_order,
            tuple(f"Lib{index}" for index in reversed(range(dependency_count))),
        )
        self.assertTrue(
            all(
                len(declarations) == 1
                for declarations in closure.dependency_declarations_by_name.values()
            )
        )

    def test_synthetic_wide_dependency_graph_preserves_child_order(self) -> None:
        child_names = tuple(f"Lib{index}" for index in range(1, 1501))

        closure = self._synthetic_dependency_closure({"Lib0": child_names})

        self.assertEqual(closure.topo_order, (*child_names, "Lib0"))
        self.assertEqual(closure.dependency_names_by_parent["Lib0"], child_names)
        self.assertTrue(
            all(
                closure.dependency_parent_names_by_name[child_name] == ("Lib0",)
                for child_name in child_names
            )
        )

    def test_synthetic_dependency_cycle_reports_exact_cycle(self) -> None:
        adjacency = {
            "LibA": ("LibB",),
            "LibB": ("LibC",),
            "LibC": ("LibA",),
        }

        with self.assertRaisesRegex(
            ValueError,
            "Source-root dependency cycle detected: LibA -> LibB -> LibC -> LibA",
        ):
            self._synthetic_dependency_closure(
                adjacency,
                direct_dependency_names=("LibA",),
            )

    def test_synthetic_shared_dependency_tracks_each_edge_once(self) -> None:
        operation_counts: dict[tuple[str, str], int] = {}
        adjacency = {
            "LibA": ("LibB", "LibC"),
            "LibB": ("LibD",),
            "LibC": ("LibD",),
        }

        closure = self._synthetic_dependency_closure(
            adjacency,
            direct_dependency_names=("LibA",),
            operation_counts=operation_counts,
        )

        self.assertEqual(closure.topo_order, ("LibD", "LibB", "LibC", "LibA"))
        self.assertEqual(
            closure.dependency_parent_names_by_name["LibD"],
            ("LibB", "LibC"),
        )
        self.assertEqual(
            [
                declaration.parent_dependency_name
                for declaration in closure.dependency_declarations_by_name["LibD"]
            ],
            ["LibB", "LibC"],
        )
        for dependency_name in ("LibA", "LibB", "LibC", "LibD"):
            self.assertEqual(operation_counts[("prepare", dependency_name)], 1)
            self.assertEqual(operation_counts[("load", dependency_name)], 1)

    def test_synthetic_high_fan_in_graph_indexes_parents_once(self) -> None:
        parent_names = tuple(f"Lib{index}" for index in range(1500))
        adjacency = {parent_name: ("LibShared",) for parent_name in parent_names}
        operation_counts: dict[tuple[str, str], int] = {}

        closure = self._synthetic_dependency_closure(
            adjacency,
            direct_dependency_names=parent_names,
            operation_counts=operation_counts,
        )

        self.assertEqual(
            closure.dependency_parent_names_by_name["LibShared"],
            parent_names,
        )
        self.assertEqual(
            len(closure.dependency_declarations_by_name["LibShared"]),
            len(parent_names),
        )
        self.assertEqual(operation_counts[("prepare", "LibShared")], 1)
        self.assertEqual(operation_counts[("load", "LibShared")], 1)

    def test_synthetic_root_override_is_registered_before_transitive_pin(self) -> None:
        closure = self._synthetic_dependency_closure(
            {"LibA": ("LibC",)},
            direct_dependency_names=("LibA", "LibC"),
            edge_commits={("LibA", "LibC"): "transitive-LibC"},
        )

        self.assertEqual(
            closure.dependency_pins_by_name["LibC"].commit,
            "commit-LibC",
        )
        self.assertEqual(
            [declaration.commit for declaration in closure.dependency_declarations_by_name["LibC"]],
            ["commit-LibC", "transitive-LibC"],
        )
        self.assertEqual(closure.topo_order, ("LibC", "LibA"))

    def test_policy_check_reports_direct_lock_violations_without_seed_repos(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "manual"
        lock_data["depsManualPath"]["LibA"] = str(remotes["LibA"])  # type: ignore[index]
        self._write_lock_data(lock_data)
        self._write_policy_data(
            {
                "schemaVersion": 1,
                "allowedRemotes": [str(remotes["LibB"])],
                "dependencyPolicies": {
                    "LibA": {
                        "pinRequired": True,
                        "manualAllowed": False,
                    },
                    "LibB": {
                        "pinRequired": True,
                    },
                },
            }
        )
        parser = self.workflow.build_parser()
        policy_args = parser.parse_args(["policy-check", "--format", "json"])

        policy_stdout = io.StringIO()
        with redirect_stdout(policy_stdout):
            policy_code = self.workflow.cmd_policy_check(policy_args)
        policy_data = json.loads(policy_stdout.getvalue())
        violation_codes = {violation["code"] for violation in policy_data["policyViolations"]}

        self.assertEqual(policy_code, 1)
        self.assertFalse((self.repo_root / "build" / "dependency_seed_repos").exists())
        self.assertEqual(policy_data["dependencies"][0]["dependencyName"], "LibA")
        self.assertIn("repoName", policy_data["dependencies"][0])
        self.assertIn("remote-not-allowed", violation_codes)
        self.assertIn("pin-required", violation_codes)
        self.assertIn("manual-not-allowed", violation_codes)
        self.assertTrue(
            all(violation["severity"] == "error" for violation in policy_data["policyViolations"])
        )

    def test_policy_check_can_downgrade_selected_violation_severity(self) -> None:
        remotes, commits = self._bootstrap()
        self._write_lock_file(remotes, commits)
        self._write_policy_data(
            {
                "schemaVersion": 1,
                "allowedRemotes": [str(remotes["LibA"])],
                "violationSeverities": {
                    "remote-not-allowed": "warning",
                },
            }
        )
        parser = self.workflow.build_parser()

        policy_stdout = io.StringIO()
        with redirect_stdout(policy_stdout):
            policy_code = self.workflow.cmd_policy_check(
                parser.parse_args(["policy-check", "--format", "json"]),
            )
        policy_data = json.loads(policy_stdout.getvalue())

        self.assertEqual(policy_code, 0)
        self.assertEqual(policy_data["policyViolations"][0]["code"], "remote-not-allowed")
        self.assertEqual(policy_data["policyViolations"][0]["severity"], "warning")

    def test_audit_reports_manual_mode_warning_without_failing(self) -> None:
        remotes, commits = self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        lock_data = self._lock_data(remotes, commits)
        lock_data["depsMode"] = "manual"
        lock_data["depsManualPath"]["LibA"] = str(remotes["LibA"])  # type: ignore[index]
        self._write_lock_data(lock_data)
        parser = self.workflow.build_parser()

        audit_stdout = io.StringIO()
        with redirect_stdout(audit_stdout):
            audit_code = self.workflow.cmd_audit(parser.parse_args(["audit", "--format", "json"]))
        audit_data = json.loads(audit_stdout.getvalue())

        self.assertEqual(audit_code, 0)
        self.assertEqual(audit_data["modeWarnings"][0]["code"], "manual-mode-active")

        plain_stdout = io.StringIO()
        plain_stderr = io.StringIO()
        with redirect_stdout(plain_stdout), redirect_stderr(plain_stderr):
            plain_code = self.workflow.cmd_audit(parser.parse_args(["audit"]))

        self.assertEqual(plain_code, 0)
        self.assertIn("audit ok", plain_stdout.getvalue())
        self.assertIn("uses manual mode", plain_stderr.getvalue())

    def test_load_dependency_policy_rejects_invalid_policy_with_structured_error(self) -> None:
        self._write_policy_data(
            {
                "schemaVersion": 1,
                "dependencyPolicies": {
                    "LibA": {
                        "pinRequired": "yes",
                    },
                },
            }
        )

        with self.assertRaises(LockfileValidationError) as context:
            self.workflow.load_dependency_policy(repo_root=self.repo_root)

        self.assertIsInstance(context.exception, FreeCMError)
        self.assertIsInstance(context.exception, ValueError)
        self.assertIn("dependencyPolicies.LibA.pinRequired", str(context.exception))

    def test_load_dependency_policy_rejects_invalid_violation_severity(self) -> None:
        self._write_policy_data(
            {
                "schemaVersion": 1,
                "violationSeverities": {
                    "remote-not-allowed": "advisory",
                },
            }
        )

        with self.assertRaisesRegex(
            LockfileValidationError, "violationSeverities.remote-not-allowed"
        ):
            self.workflow.load_dependency_policy(repo_root=self.repo_root)

    def test_policy_check_ignores_legacy_abi_group_and_reports_catalog_violations(self) -> None:
        remotes, commits = self._bootstrap()
        self._write_lock_data(self._lock_data(remotes, commits))
        self._write_policy_data(
            {
                "schemaVersion": 1,
                "allowedRemotes": [str(remotes["LibA"]), str(remotes["LibB"])],
                "dependencyCatalog": {
                    "LibA": {
                        "owner": "Core Platform",
                        "tier": "production",
                        "license": "GPL-3.0-only",
                        "approvalRequired": True,
                    }
                },
                "dependencyPolicies": {
                    "LibA": {
                        "abiGroup": "abi-v2",
                        "licenseAllowlist": ["MIT", "Apache-2.0"],
                    }
                },
            }
        )
        parser = self.workflow.build_parser()
        policy_stdout = io.StringIO()

        with redirect_stdout(policy_stdout):
            policy_code = self.workflow.cmd_policy_check(
                parser.parse_args(["policy-check", "--format", "json"]),
            )
        policy_data = json.loads(policy_stdout.getvalue())
        violation_codes = {violation["code"] for violation in policy_data["policyViolations"]}

        self.assertEqual(policy_code, 1)
        self.assertEqual(
            policy_data["dependencyCatalog"]["LibA"]["owner"],
            "Core Platform",
        )
        self.assertNotIn("abiGroup", policy_data["dependencies"][0])
        self.assertNotIn("abi-group-mismatch", violation_codes)
        self.assertIn("license-not-allowed", violation_codes)

    def test_policy_check_normalizes_remote_urls_and_reports_extension_points(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["remote"] = "git@github.com:my-org/LibA.git"  # type: ignore[index]
        self._write_lock_data(lock_data)
        self._write_policy_data(
            {
                "schemaVersion": 1,
                "allowedRemotes": [
                    "https://github.com/my-org/*",
                    str(remotes["LibB"]),
                ],
                "signaturePolicy": {
                    "provider": "external-ci",
                    "requiredFor": ["production"],
                },
                "refPolicy": {
                    "allowedRefs": ["refs/heads/main", "refs/tags/v*"],
                },
                "sbomPolicy": {
                    "reportPath": "build/reports/sbom.json",
                },
                "licensePolicy": {
                    "reportPath": "build/reports/licenses.json",
                },
                "ownerApprovalPolicy": {
                    "system": "internal-approval",
                },
                "vulnerabilityPolicy": {
                    "reportPath": "build/reports/vulnerabilities.json",
                },
            }
        )
        parser = self.workflow.build_parser()
        policy_stdout = io.StringIO()

        with redirect_stdout(policy_stdout):
            policy_code = self.workflow.cmd_policy_check(
                parser.parse_args(["policy-check", "--format", "json"]),
            )
        policy_data = json.loads(policy_stdout.getvalue())

        self.assertEqual(policy_code, 0)
        self.assertEqual(
            policy_data["dependencies"][0]["normalizedRemote"],
            "github.com/my-org/LibA",
        )
        self.assertEqual(
            policy_data["policyExtensions"]["signaturePolicy"]["provider"],
            "external-ci",
        )
        self.assertEqual(
            policy_data["policyExtensions"]["refPolicy"]["allowedRefs"],
            ["refs/heads/main", "refs/tags/v*"],
        )
        self.assertEqual(
            policy_data["policyExtensions"]["vulnerabilityPolicy"]["reportPath"],
            "build/reports/vulnerabilities.json",
        )
        self.assertEqual(policy_data["policyViolations"], [])

    def test_graph_and_audit_json_include_closure_edges_and_policy(self) -> None:
        remotes, commits = self._bootstrap()
        libc_remote, libc_commit = self._create_remote_repo("RepoC", ("CMakeLists.txt",))
        liba_commit = self._write_nested_template(
            remotes["LibA"],
            dependencies={
                "LibC": {
                    "repoName": "RepoC",
                    "remote": str(libc_remote),
                    "commit": libc_commit,
                }
            },
        )
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["commit"] = liba_commit  # type: ignore[index]
        self._write_lock_data(lock_data)
        self._write_policy_data(
            {
                "schemaVersion": 1,
                "allowedRemotes": [str(remotes["LibA"]), str(remotes["LibB"])],
                "dependencyPolicies": {
                    "LibC": {
                        "pinRequired": True,
                    }
                },
            }
        )
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        parser = self.workflow.build_parser()

        graph_stdout = io.StringIO()
        with redirect_stdout(graph_stdout):
            self.assertEqual(
                self.workflow.cmd_graph(parser.parse_args(["graph", "--format", "json"])),
                0,
            )
        graph_data = json.loads(graph_stdout.getvalue())
        self.assertIn({"from": "LibA", "to": "LibC"}, graph_data["edges"])
        for dependency in graph_data["dependencies"]:
            self.assertNotIn("abiGroup", dependency)
        self.assertEqual(
            [
                dependency["repoName"]
                for dependency in graph_data["dependencies"]
                if dependency["dependencyName"] == "LibC"
            ],
            ["RepoC"],
        )

        dot_stdout = io.StringIO()
        with redirect_stdout(dot_stdout):
            self.assertEqual(
                self.workflow.cmd_graph(parser.parse_args(["graph", "--format", "dot"])),
                0,
            )
        self.assertIn('"LibA" -> "LibC"', dot_stdout.getvalue())

        audit_stdout = io.StringIO()
        with redirect_stdout(audit_stdout):
            audit_code = self.workflow.cmd_audit(parser.parse_args(["audit", "--format", "json"]))
        audit_data = json.loads(audit_stdout.getvalue())

        self.assertEqual(audit_code, 1)
        self.assertEqual(audit_data["conflicts"], [])
        for dependency in audit_data["dependencies"]:
            self.assertNotIn("abiGroup", dependency)
        self.assertEqual(
            audit_data["policyViolations"][0]["dependencyName"],
            "LibC",
        )
        self.assertEqual(
            audit_data["policyViolations"][0]["code"],
            "remote-not-allowed",
        )

    def test_root_lock_override_transitive_pin_mismatch_is_reported(self) -> None:
        specs = (
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
            DependencyRootSpec(
                dependency_name="LibD",
                repo_name="LibD",
                env_key="LIBD_SOURCE_ROOT",
                required_relative_paths=("CMakeLists.txt", "include/LibD"),
            ),
        )
        workflow = DependencyRootManager(
            DependencyRootConfig(
                repo_root=self.repo_root,
                dependency_root_specs=specs,
                repo_display_name="HostRepo",
            )
        )
        remotes: dict[str, Path] = {}
        commits: dict[str, str] = {}
        for spec in specs:
            remote, commit = self._create_remote_repo(spec.repo_name, spec.required_relative_paths)
            remotes[spec.dependency_name] = remote
            commits[spec.dependency_name] = commit

        libd_transitive_commit = commits["LibD"]
        (remotes["LibD"] / "CMakeLists.txt").write_text("LibD:root-override\n", encoding="utf-8")
        libd_root_commit = self._commit_repo(remotes["LibD"], "advance LibD ABI")
        commits["LibD"] = libd_root_commit
        commits["LibA"] = self._write_nested_template(
            remotes["LibA"],
            dependencies={
                "LibD": {
                    "remote": str(remotes["LibD"]),
                    "commit": libd_transitive_commit,
                }
            },
        )
        commits["LibB"] = self._write_nested_template(
            remotes["LibB"],
            dependencies={
                "LibD": {
                    "remote": str(remotes["LibD"]),
                    "commit": libd_transitive_commit,
                }
            },
        )
        lock_data = {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "cmakeEnvironment": {},
            "cmakeCacheVariables": {},
            "depsManualPath": {spec.dependency_name: "" for spec in specs},
            "dependencies": {
                spec.dependency_name: {
                    "remote": str(remotes[spec.dependency_name]),
                    "commit": commits[spec.dependency_name],
                }
                for spec in specs
            },
        }
        self._write_lock_data(lock_data)
        workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        parser = workflow.build_parser()

        resolve_stdout = io.StringIO()
        with redirect_stdout(resolve_stdout):
            self.assertEqual(
                workflow.cmd_resolve(parser.parse_args(["resolve", "--format", "json"])),
                0,
            )
        resolve_data = json.loads(resolve_stdout.getvalue())
        resolve_mismatches = resolve_data["rootOverrideTransitivePinMismatches"]

        self.assertEqual(len(resolve_mismatches), 2)
        self.assertEqual(
            {mismatch["parentDependencyName"] for mismatch in resolve_mismatches},
            {"LibA", "LibB"},
        )
        for mismatch in resolve_mismatches:
            self.assertEqual(mismatch["code"], "root-override-transitive-pin-mismatch")
            self.assertEqual(mismatch["dependencyName"], "LibD")
            self.assertEqual(mismatch["rootCommit"], libd_root_commit)
            self.assertEqual(mismatch["transitiveCommit"], libd_transitive_commit)
            self.assertEqual(mismatch["rootSource"], "root lock")
            self.assertIn("source_roots.lock.jsonc.in", mismatch["transitiveSource"])

        audit_stdout = io.StringIO()
        with redirect_stdout(audit_stdout):
            audit_code = workflow.cmd_audit(parser.parse_args(["audit", "--format", "json"]))
        audit_data = json.loads(audit_stdout.getvalue())

        self.assertEqual(audit_code, 0)
        self.assertEqual(audit_data["conflicts"], [])
        self.assertEqual(audit_data["policyViolations"], [])
        self.assertEqual(audit_data["rootOverrideTransitivePinMismatches"], resolve_mismatches)

        plain_stdout = io.StringIO()
        plain_stderr = io.StringIO()
        with redirect_stdout(plain_stdout), redirect_stderr(plain_stderr):
            plain_code = workflow.cmd_audit(parser.parse_args(["audit"]))

        self.assertEqual(plain_code, 0)
        self.assertIn("audit ok", plain_stdout.getvalue())
        self.assertIn("root override transitive pin mismatches", plain_stderr.getvalue())
        self.assertIn("LibD", plain_stderr.getvalue())

        colored_lines = format_root_override_transitive_pin_mismatch_lines(
            resolve_mismatches[:1],
            use_color=True,
        )
        self.assertIn(ANSI_GREEN, colored_lines[1])
        self.assertIn(ANSI_RED, colored_lines[1])

    def test_pin_command_uses_local_seed_refs_only(self) -> None:
        self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        seed_root = self._seed_root("LibA")
        seed_head = self._head(seed_root)
        self.git(seed_root, "tag", "test-pin", seed_head)
        parser = self.workflow.build_parser()
        pin_args = parser.parse_args(["pin", "--dep", "LibA", "--ref", "test-pin"])

        pin_stdout = io.StringIO()
        with redirect_stdout(pin_stdout):
            self.assertEqual(self.workflow.cmd_pin(pin_args), 0)

        self.assertIn(f"LibA={seed_head}", pin_stdout.getvalue())
        self.assertEqual(
            self.workflow.load_lock_file(self.repo_root)["dependencies"]["LibA"]["commit"],
            seed_head,
        )
        lock_path = self.repo_root / "source_roots.lock.jsonc"
        assert_atomic_write_sidecars(self, lock_path)
        self.assertEqual(
            json.loads(lock_path.read_text(encoding="utf-8"))["dependencies"]["LibA"]["commit"],
            seed_head,
        )


if __name__ == "__main__":
    unittest.main()
