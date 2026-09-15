from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from freecm.dependency_roots import (
    DependencyRootConfig,
    DependencyRootManager,
    DependencyRootSpec,
    bind_dependency_root_workflow,
)
from repomgrcpp.cmake_workflow import bind_cmake_workflow_script
from tests.git_test_helpers import create_git_fixture_repo, run_git_fixture


class DisabledDependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "SampleApp"
        self.root.mkdir()
        self.remote, self.commit = create_git_fixture_repo(
            Path(temporary.name) / "remotes", "LibA", ("CMakeLists.txt",)
        )
        self.spec = DependencyRootSpec("LibA", "LibA", "LIBA_ROOT", ("CMakeLists.txt",))
        self.lock = {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "depsManualPath": {"LibA": "", "LibB": "/missing/checkout"},
            "dependencies": {
                "LibA": {"remote": str(self.remote), "commit": self.commit},
                "LibB": {"remote": "", "commit": "", "disabled": True},
            },
            "AppConfigs": {"EnableExtra": False},
        }
        self.namespace = {}
        self.manager = bind_dependency_root_workflow(
            self.namespace,
            DependencyRootConfig(
                self.root,
                (self.spec, DependencyRootSpec("LibB", "LibB", "LIBB_ROOT", ("CMakeLists.txt",))),
                "SampleApp",
            ),
        )
        self.materialize = mock.Mock(wraps=self.manager._materialize_dependency_roots_unlocked)
        self.namespace["_materialize_dependency_roots_unlocked"] = self.materialize
        self.script = bind_cmake_workflow_script(
            self.namespace,
            repo_root=self.root,
            repo_display_name="SampleApp",
            dependency_build_order=(),
        )

    def write_lock(self, *, template: bool = False) -> Path:
        path = self.root / ("source_roots.lock.jsonc.in" if template else "source_roots.lock.jsonc")
        path.write_text(json.dumps(self.lock), encoding="utf-8")
        return path

    def exercise_workflow(self) -> None:
        self.write_lock(template=True)
        with redirect_stdout(io.StringIO()):
            with mock.patch.object(
                self.manager,
                "_clone_missing_seed_repo_to_default_branch",
                wraps=self.manager._clone_missing_seed_repo_to_default_branch,
            ) as prepare:
                self.assertEqual(0, self.script.cmd_init(quiet=True))
                for call in prepare.call_args_list:
                    self.assertEqual("LibA", call.args[1].dependency_name)
                if self.lock["depsMode"] != "manual":
                    self.assertGreater(prepare.call_count, 0)
            with (
                mock.patch.object(
                    self.manager,
                    "_clone_missing_seed_repo_to_default_branch",
                    side_effect=AssertionError("clone"),
                ),
                mock.patch.object(
                    self.manager, "_ensure_seed_repo", side_effect=AssertionError("clone")
                ),
                mock.patch.object(
                    self.manager, "_fetch_remote_refs", side_effect=AssertionError("fetch")
                ),
            ):
                self.assertEqual(0, self.script.cmd_update())
                self.assertFalse(self.materialize.call_args.kwargs["allow_network"])
                roots = self.manager.require_dependency_roots()
        self.assertEqual(("LibA",), roots.closure_order)
        generated = (self.root / "CMakePresets.json").read_text()
        self.assertIn("LibA", generated)
        for preset in json.loads(generated)["configurePresets"]:
            cache = preset["cacheVariables"]
            self.assertTrue(cache["CMAKE_DISABLE_FIND_PACKAGE_LibB"])
            self.assertNotIn("LibB", cache.get("CMAKE_PREFIX_PATH", ""))
        self.assertFalse((self.root / "build/dependency_seed_repos/LibB").exists())
        persisted = json.loads((self.root / "source_roots.lock.jsonc").read_text())
        self.assertEqual(
            self.lock["dependencies"].get("LibB"), persisted["dependencies"].get("LibB")
        )

    def test_init_update_without_optional_declaration(self) -> None:
        self.lock["dependencies"]["LibB"] = {"disabled": True}
        del self.lock["depsManualPath"]["LibB"]
        self.exercise_workflow()

    def test_init_update_with_empty_optional_remote(self) -> None:
        self.exercise_workflow()

    def test_init_update_with_unavailable_optional_remote(self) -> None:
        self.lock["dependencies"]["LibB"] = {
            "remote": str(self.root / "inaccessible.git"),
            "commit": "1" * 40,
            "disabled": True,
        }
        self.exercise_workflow()

    def test_manual_init_update_ignores_missing_optional_checkout(self) -> None:
        self.lock["depsMode"] = "manual"
        self.lock["depsManualPath"]["LibA"] = str(self.remote)
        run_git_fixture(self.remote, "remote", "add", "origin", str(self.remote))
        self.exercise_workflow()

    def test_latest_init_update_preserves_inactive_declaration(self) -> None:
        self.lock["depsMode"] = "latest"
        self.exercise_workflow()

    def test_pin_and_refresh_preserve_inactive_entries(self) -> None:
        path = self.write_lock()
        self.write_lock(template=True)
        self.manager.prepare_seed_repository_closure()
        self.manager.pin_dependency_ref("LibA", self.commit)
        # A changed template forces refreshpin to write rather than return a no-op.
        self.lock["dependencies"]["LibA"]["commit"] = "2" * 40
        self.write_lock(template=True)
        self.manager.refresh_pinned_lock()
        persisted = json.loads(path.read_text())
        self.assertEqual("2" * 40, persisted["dependencies"]["LibA"]["commit"])
        self.assertEqual(self.lock["dependencies"]["LibB"], persisted["dependencies"]["LibB"])
        self.assertEqual(self.lock["depsManualPath"], persisted["depsManualPath"])
        self.manager.set_latest_mode()
        self.assertEqual(
            self.lock["dependencies"]["LibB"], json.loads(path.read_text())["dependencies"]["LibB"]
        )

    def test_enabling_dependency_requires_valid_declaration(self) -> None:
        self.lock["dependencies"]["LibB"].pop("disabled")
        self.write_lock()
        enabled = DependencyRootManager(
            DependencyRootConfig(
                self.root,
                (self.spec, DependencyRootSpec("LibB", "LibB", "LIBB_ROOT", ("CMakeLists.txt",))),
                "SampleApp",
            )
        )
        with self.assertRaisesRegex(ValueError, "remote.*LibB"):
            enabled.load_lock_file()
        del self.lock["dependencies"]["LibB"]
        self.write_lock()
        with self.assertRaisesRegex(ValueError, "missing dependencies: LibB"):
            enabled.load_lock_file()

    def test_nested_disabled_declaration_is_skipped(self) -> None:
        nested = self.root / "nested"
        nested.mkdir()
        path = nested / "source_roots.lock.jsonc.in"
        path.write_text(json.dumps(self.lock))
        specs = self.manager._load_nested_dependency_specs(nested, parent_dependency_name="LibA")
        self.assertNotIn("LibB", [spec.dependency_name for spec in specs])

    def test_root_disabled_overrides_transitive_requirement(self) -> None:
        remote, commit = create_git_fixture_repo(self.remote.parent, "LibB", ("CMakeLists.txt",))
        nested = {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "depsManualPath": {"LibB": ""},
            "dependencies": {"LibB": {"remote": str(remote), "commit": commit}},
        }
        (self.remote / "source_roots.lock.jsonc.in").write_text(json.dumps(nested))
        run_git_fixture(self.remote, "add", ".")
        run_git_fixture(self.remote, "commit", "-m", "nested requirement")
        self.lock["dependencies"]["LibA"]["commit"] = run_git_fixture(
            self.remote, "rev-parse", "HEAD"
        )
        self.write_lock()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, self.script.cmd_init(quiet=True))
            self.assertEqual(0, self.script.cmd_update())
        roots = self.manager.require_dependency_roots()
        self.assertEqual(("LibA",), roots.closure_order)
        nested_path = roots.dependency_root_for("LibA") / "source_roots.lock.jsonc"
        self.assertTrue(json.loads(nested_path.read_text())["dependencies"]["LibB"]["disabled"])
        self.assertEqual(("LibA",), roots.direct_dependency_names)

    def test_root_can_disable_transitive_name_without_a_direct_spec(self) -> None:
        nested = {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "depsManualPath": {"LibC": ""},
            "dependencies": {"LibC": {"remote": "unavailable", "commit": "missing"}},
        }
        (self.remote / "source_roots.lock.jsonc.in").write_text(json.dumps(nested))
        run_git_fixture(self.remote, "add", ".")
        run_git_fixture(self.remote, "commit", "-m", "nested requirement")
        self.lock["dependencies"]["LibA"]["commit"] = run_git_fixture(
            self.remote, "rev-parse", "HEAD"
        )
        self.lock["dependencies"]["LibC"] = {"disabled": True}
        self.write_lock()
        with redirect_stdout(io.StringIO()):
            self.script.cmd_init(quiet=True)
            self.script.cmd_update()
        self.assertEqual(("LibA",), self.manager.require_dependency_roots().closure_order)

    def test_enabling_unavailable_dependency_propagates_failure(self) -> None:
        self.lock["dependencies"]["LibA"]["remote"] = str(self.root / "unavailable.git")
        path = self.write_lock()
        with redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(subprocess.CalledProcessError):
                self.script.cmd_init(quiet=True)
        self.assertIn("LibA: cloning", output.getvalue())
        self.assertEqual(self.lock, json.loads(path.read_text()))
        self.assertFalse((self.root / "CMakePresets.json").exists())

    def test_root_selection_does_not_leak_when_reusing_manager(self) -> None:
        self.write_lock()
        self.manager.load_lock_file()
        other = self.root / "other"
        other.mkdir()
        self.lock["dependencies"]["LibB"] = {"remote": "local", "commit": "abc"}
        self.lock["depsManualPath"]["LibB"] = ""
        (other / "source_roots.lock.jsonc").write_text(json.dumps(self.lock))
        loaded = self.manager.load_lock_file(other)
        self.assertFalse(loaded["dependencies"]["LibB"].get("disabled", False))
        self.assertFalse(self.manager.disabled_dependency_names)

    def test_disabled_requires_boolean(self) -> None:
        for value in (None, 0, 1, "true", "false", [], {}):
            with self.subTest(value=value):
                self.lock["dependencies"]["LibB"]["disabled"] = value
                self.write_lock()
                with self.assertRaisesRegex(ValueError, "disabled.*boolean"):
                    self.manager.load_lock_file()

    def test_disabled_cannot_hide_removed_repo_name(self) -> None:
        self.lock["dependencies"]["LibB"]["repoName"] = "Alias"
        self.write_lock()
        with self.assertRaisesRegex(ValueError, "repoName"):
            self.manager.load_lock_file()

    def test_unknown_declarations_still_fail(self) -> None:
        self.lock["dependencies"]["Typo"] = {}
        self.write_lock()
        with self.assertRaisesRegex(ValueError, "unexpected dependencies: Typo"):
            self.manager.load_lock_file()

    def test_disabled_dependency_cannot_be_pinned(self) -> None:
        self.write_lock()
        with self.assertRaisesRegex(ValueError, "Cannot pin disabled dependency LibB"):
            self.manager.pin_dependency_ref("LibB", "HEAD")
