from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from freecm.dependency_roots import (  # noqa: E402
    DEFAULT_REQUIRED_RELATIVE_PATHS,
    DependencyRootConfig,
    DependencyRootManager,
    DependencyRootSpec,
    loads_jsonc,
)
from freecm.git_repositories import git_is_work_tree, remove_path  # noqa: E402
from freecm.path_maps import (  # noqa: E402
    dedupe_dependency_specs,
    dependency_root_path_map,
    environment_map,
    print_environment_map,
)


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
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def _create_remote_repo(self, name: str, required_relative_paths: tuple[str, ...]) -> tuple[Path, str]:
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
        self._write_lock_file(remotes, commits)
        return remotes, commits

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
                    "abiGroup": "abi-main",
                }
                for spec in self.specs
            },
        }

    def _write_lock_data(self, lock_data: dict[str, object]) -> None:
        (self.repo_root / "source_roots.lock.jsonc").write_text(
            json.dumps(lock_data, indent=2) + "\n",
            encoding="utf-8",
        )

    def _seed_root(self, dependency_name: str) -> Path:
        spec = self.workflow.spec_by_dependency_name[dependency_name]
        return self.workflow._seed_repo_root(self.repo_root, spec.repo_name)

    def _head(self, repo_root: Path) -> str:
        return self.git(repo_root, "rev-parse", "HEAD")

    def test_path_map_helpers_build_env_maps_and_dedupe_specs(self) -> None:
        duplicate_specs = (
            *self.specs,
            DependencyRootSpec(
                dependency_name="LibA",
                repo_name="LibAOther",
                env_key="LIBA_OTHER_SOURCE_ROOT",
                required_relative_paths=("ignored",),
            ),
        )
        deduped = dedupe_dependency_specs(duplicate_specs)
        roots = {
            "LibA": Path("/tmp/LibA"),
            "LibB": Path("/tmp/LibB"),
        }

        path_map = dependency_root_path_map(deduped, roots.__getitem__)
        env_map = environment_map(path_map)
        plain_stdout = io.StringIO()
        shell_stdout = io.StringIO()

        with redirect_stdout(plain_stdout):
            print_environment_map(env_map, "plain")
        with redirect_stdout(shell_stdout):
            print_environment_map(env_map, "shell")

        self.assertEqual([spec.env_key for spec in deduped], ["LIBA_SOURCE_ROOT", "LIBB_SOURCE_ROOT"])
        self.assertEqual(path_map["LIBA_SOURCE_ROOT"], Path("/tmp/LibA"))
        self.assertEqual(env_map["LIBB_SOURCE_ROOT"], str(Path("/tmp/LibB")))
        self.assertIn(f"LIBA_SOURCE_ROOT={Path('/tmp/LibA')}", plain_stdout.getvalue())
        self.assertIn(f'export LIBB_SOURCE_ROOT="{Path("/tmp/LibB")}"', shell_stdout.getvalue())

        with self.assertRaisesRegex(ValueError, "Unsupported environment map output format"):
            print_environment_map(env_map, "json")

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

        with self.assertRaisesRegex(ValueError, "assetSeeds is no longer supported"):
            self.workflow.load_lock_file(self.repo_root)

    def _assert_init_fails_without_changing_seed(
        self,
        dependency_name: str,
        expected_reason: str,
    ) -> RuntimeError:
        seed_root = self._seed_root(dependency_name)
        original_head = self._head(seed_root) if git_is_work_tree(seed_root) else None
        with self.assertRaisesRegex(RuntimeError, expected_reason) as raised:
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

    def test_materialize_defaults_to_offline_mode(self) -> None:
        self._bootstrap()

        with self.assertRaisesRegex(FileNotFoundError, "Missing dependency seed repo path"):
            self.workflow.materialize_dependency_roots(repo_root=self.repo_root)

    def test_materialize_command_passes_offline_flag(self) -> None:
        resolved = mock.Mock()
        with mock.patch.object(
            self.workflow,
            "materialize_dependency_roots",
            return_value=resolved,
        ) as materialize, mock.patch.object(self.workflow, "_print_env_map"):
            result = self.workflow.cmd_materialize(mock.Mock())

        self.assertEqual(result, 0)
        materialize.assert_called_once_with(allow_network=False)

    def test_cli_does_not_expose_networked_seed_prepare_command(self) -> None:
        parser = self.workflow.build_parser()
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            parser.parse_args(["prepare-seed-closure"])

        self.assertIn("invalid choice", stderr.getvalue())

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

    def test_init_rejects_dirty_managed_seed_even_when_active_lock_points_to_it_as_manual(self) -> None:
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
        self.assertTrue(
            git_is_work_tree(self.workflow._seed_repo_root(self.repo_root, "LibC"))
        )

    def test_init_syncs_seed_that_appears_after_parent_seed_update(self) -> None:
        remotes, _ = self._bootstrap()
        libc_remote, libc_initial = self._create_remote_repo("LibC", ("CMakeLists.txt",))
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)

        stale_libc_seed = self.workflow._seed_repo_root(self.repo_root, "LibC")
        subprocess.run(["git", "clone", str(libc_remote), str(stale_libc_seed)], check=True)

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

        self.assertEqual(lock_data["cmakeCacheVariables"]["DOC_URL"], "https://example.com/not-a-comment")
        self.assertEqual(lock_data["dependencies"]["LibA"]["commit"], commits["LibA"])

    def test_loads_jsonc_rejects_invalid_jsonc_with_path_label(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid JSON/JSONC in lock"):
            loads_jsonc('{"schemaVersion": }', path_label="lock")

    def test_lock_validation_rejects_old_schema_version(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["schemaVersion"] = 4
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "schemaVersion 4"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_rejects_unknown_dependency_field(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["legacy"] = "nope"  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "unexpected fields: legacy"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_allows_missing_abi_group(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        del lock_data["dependencies"]["LibA"]["abiGroup"]  # type: ignore[index]
        self._write_lock_data(lock_data)

        loaded = self.workflow.load_lock_file(repo_root=self.repo_root)

        self.assertIsNone(loaded["dependencies"]["LibA"]["abiGroup"])

    def test_lock_validation_rejects_invalid_abi_group(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["abiGroup"] = ""  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "abiGroup"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_rejects_removed_v3_metadata_fields(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["groups"] = ["core"]  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "unexpected fields: groups"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_lock_validation_rejects_removed_repo_name_field(self) -> None:
        remotes, commits = self._bootstrap()
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["repoName"] = "LibA"  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(ValueError, "unexpected fields: repoName"):
            self.workflow.load_lock_file(repo_root=self.repo_root)

    def test_offline_materialize_fails_when_locked_commit_is_missing_locally(self) -> None:
        remotes, commits = self._bootstrap()
        self.workflow.prepare_seed_repository_closure(repo_root=self.repo_root)
        lock_data = self._lock_data(remotes, commits)
        lock_data["dependencies"]["LibA"]["commit"] = "f" * 40  # type: ignore[index]
        self._write_lock_data(lock_data)

        with self.assertRaisesRegex(RuntimeError, "Missing locked commit"):
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
        self.assertEqual(data["dependencies"]["LibA"]["manualOverride"], str(remotes["LibA"].resolve()))
        self.assertEqual(data["dependencies"]["LibC"]["parents"], ["LibA"])
        self.assertEqual(data["dependencies"]["LibC"]["abiGroup"], "abi-libc")
        self.assertNotIn("repoName", data["dependencies"]["LibC"])
        self.assertNotIn("repoName", data["dependencies"]["LibC"]["declaredBy"][0])
        self.assertNotIn("groups", data["dependencies"]["LibC"])
        self.assertIn("source_roots.lock.jsonc.in", data["dependencies"]["LibC"]["declaredBy"][0]["source"])

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

        with self.assertRaisesRegex(ValueError, r"(?s)existing: .*LibA.*candidate: .*LibB"):
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
        self.assertEqual(resolve_data["dependencies"]["LibA"]["abiGroup"], "abi-main")

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



if __name__ == "__main__":
    unittest.main()
