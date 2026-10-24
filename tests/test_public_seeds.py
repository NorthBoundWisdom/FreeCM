from __future__ import annotations

import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from freecm.dependency_lock import load_dependency_lock_data
from freecm.dependency_roots import DependencyRootConfig, DependencyRootManager, DependencyRootSpec
from freecm.git_repositories import git_common_dir
from tests.git_test_helpers import (
    commit_git_fixture_repo,
    create_git_fixture_repo,
    run_git_fixture,
)


class PublicSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.host = self.root / "HostA"
        self.host.mkdir()
        self.public = self.root / "public" / "LibA"
        self.specs = tuple(
            DependencyRootSpec(name, name, f"{name.upper()}_ROOT", ("source.txt",))
            for name in ("LibA", "LibB")
        )
        entries = {}
        self.remotes = {}
        for spec in self.specs:
            remote, commit = create_git_fixture_repo(
                self.root / "remotes", spec.dependency_name, spec.required_relative_paths
            )
            entries[spec.dependency_name] = {"remote": str(remote), "commit": commit}
            self.remotes[spec.dependency_name] = remote
        entries["LibA"]["publicSeedPath"] = "../public/LibA"
        self.data = {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "depsManualPath": {"LibA": "", "LibB": ""},
            "dependencies": entries,
        }
        self.write_lock(self.host, self.data)
        self.manager = self.manager_for(self.host)

    def manager_for(self, host: Path) -> DependencyRootManager:
        return DependencyRootManager(DependencyRootConfig(host, self.specs, "Host"))

    def write_lock(self, host: Path, data: dict) -> Path:
        file = host / "source_roots.lock.jsonc"
        file.write_text(json.dumps(data), encoding="utf-8")
        return file

    def test_public_and_default_seeds_materialize_offline(self) -> None:
        self.manager.prepare_seed_repository_closure(quiet=True)
        local = self.host / "build" / "dependency_seed_repos"
        self.assertFalse((local / "LibA").exists())
        self.assertTrue((local / "LibB" / ".git").is_dir())
        with (
            mock.patch.object(
                self.manager, "_ensure_seed_repo", side_effect=AssertionError("clone")
            ),
            mock.patch.object(
                self.manager, "_fetch_remote_refs", side_effect=AssertionError("fetch")
            ),
        ):
            roots = self.manager.materialize_dependency_roots(allow_network=False, quiet=True)
            self.assertEqual(self.manager.validate_dependency_roots(roots), [])
            self.assertEqual(
                self.manager.load_dependency_roots().seed_repository_for("LibA"), self.public
            )
        self.assertEqual(roots.seed_repository_for("LibA"), self.public)
        self.assertEqual(
            git_common_dir(roots.dependency_root_for("LibA")), git_common_dir(self.public)
        )

    def test_two_hosts_share_objects_and_keep_independent_pins(self) -> None:
        original_commit = self.data["dependencies"]["LibA"]["commit"]
        remote = self.remotes["LibA"]
        (remote / "source.txt").write_text("new version\n", encoding="utf-8")
        newest = commit_git_fixture_repo(remote, "new version")
        self.manager.prepare_seed_repository_closure(quiet=True)
        host_b = self.root / "HostB"
        host_b.mkdir()
        data_b = copy.deepcopy(self.data)
        data_b["dependencies"]["LibA"]["commit"] = newest
        self.write_lock(host_b, data_b)
        manager_b = self.manager_for(host_b)
        manager_b.prepare_seed_repository_closure(quiet=True)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(manager.materialize_dependency_roots, quiet=True)
                for manager in (self.manager, manager_b)
            ]
            roots_a, roots_b = [future.result() for future in futures]
        self.assertEqual(
            run_git_fixture(roots_a.dependency_root_for("LibA"), "rev-parse", "HEAD"),
            original_commit,
        )
        self.assertEqual(
            run_git_fixture(roots_b.dependency_root_for("LibA"), "rev-parse", "HEAD"), newest
        )
        self.assertEqual(run_git_fixture(self.public, "rev-parse", "HEAD"), newest)
        self.assertEqual(run_git_fixture(self.public, "status", "--porcelain"), "")
        self.assertNotEqual(
            roots_a.dependency_root_for("LibA"), roots_b.dependency_root_for("LibA")
        )
        self.assertEqual(
            git_common_dir(roots_a.dependency_root_for("LibA")),
            git_common_dir(roots_b.dependency_root_for("LibA")),
        )

    def test_latest_reads_public_seed_without_fetch(self) -> None:
        self.manager.prepare_seed_repository_closure(quiet=True)
        remote = self.remotes["LibA"]
        (remote / "source.txt").write_text("new version\n", encoding="utf-8")
        newest = commit_git_fixture_repo(remote, "new version")
        self.manager.prepare_seed_repository_closure(quiet=True)
        self.data["depsMode"] = "latest"
        self.write_lock(self.host, self.data)
        with mock.patch.object(
            self.manager, "_fetch_remote_refs", side_effect=AssertionError("fetch")
        ):
            roots = self.manager.materialize_dependency_roots(allow_network=False, quiet=True)
        self.assertEqual(roots.resolved_commits["LibA"], newest)
        self.assertEqual(
            self.manager.load_lock_file()["dependencies"]["LibA"]["publicSeedPath"],
            "../public/LibA",
        )

    def test_missing_public_seed_does_not_fall_back_or_clone(self) -> None:
        local = self.host / "build" / "dependency_seed_repos" / "LibA"
        local.parent.mkdir(parents=True)
        run_git_fixture(self.host, "clone", str(self.remotes["LibA"]), str(local))
        with (
            mock.patch.object(
                self.manager, "_ensure_seed_repo", side_effect=AssertionError("clone")
            ),
            mock.patch.object(
                self.manager, "_fetch_remote_refs", side_effect=AssertionError("fetch")
            ),
            self.assertRaisesRegex(FileNotFoundError, "Missing dependency seed repo path"),
        ):
            self.manager.materialize_dependency_roots(allow_network=False)
        self.assertFalse(self.public.exists())

    def test_manual_rejects_only_paths_declared_by_current_host(self) -> None:
        self.data["depsMode"] = "manual"
        for manual in ("../public/LibA", "../public/LibA/include"):
            with self.subTest(manual=manual):
                self.data["depsManualPath"]["LibA"] = manual
                file = self.write_lock(self.host, self.data)
                with self.assertRaisesRegex(ValueError, "declared publicSeedPath"):
                    load_dependency_lock_data(file)
        self.data["depsManualPath"]["LibA"] = "../public/LibA-other"
        load_dependency_lock_data(self.write_lock(self.host, self.data))
        # A different host without this declaration may manually use the same path.
        host_c = self.root / "HostC"
        host_c.mkdir()
        data_c = copy.deepcopy(self.data)
        data_c["dependencies"]["LibA"].pop("publicSeedPath")
        data_c["depsManualPath"]["LibA"] = str(self.public)
        load_dependency_lock_data(self.write_lock(host_c, data_c))

    def test_manual_symlink_cannot_bypass_declared_public_path(self) -> None:
        self.public.mkdir(parents=True)
        alias = self.host / "alias"
        try:
            alias.symlink_to(self.public, target_is_directory=True)
        except OSError as error:
            self.skipTest(str(error))
        self.data["depsMode"] = "manual"
        self.data["depsManualPath"]["LibA"] = "alias"
        self.write_lock(self.host, self.data)
        with self.assertRaisesRegex(ValueError, "declared publicSeedPath"):
            self.manager.load_lock_file()

    def test_local_manual_seed_can_coexist_with_public_seed_setting(self) -> None:
        self.data["depsMode"] = "manual"
        self.data["depsManualPath"]["LibA"] = "build/dependency_seed_repos/LibA"
        self.write_lock(self.host, self.data)
        self.manager.prepare_seed_repository_closure(quiet=True)
        self.assertTrue(self.public.exists())
        local = self.host / "build" / "dependency_seed_repos" / "LibA"
        (local / "source.txt").write_text("working change\n", encoding="utf-8")
        local_commit = commit_git_fixture_repo(local, "unpublished manual work")
        (local / "source.txt").write_text("working change\nmore editing\n", encoding="utf-8")
        self.manager.prepare_seed_repository_closure(quiet=True)
        self.assertEqual(run_git_fixture(local, "rev-parse", "HEAD"), local_commit)
        roots = self.manager.materialize_dependency_roots(allow_network=False, quiet=True)
        self.assertEqual(roots.dependency_root_for("LibA"), local)
        self.assertEqual((local / "source.txt").read_text(), "working change\nmore editing\n")

    def test_init_refreshes_public_seed_even_with_external_manual_checkout(self) -> None:
        self.manager.prepare_seed_repository_closure(quiet=True)
        editable = self.root / "editable"
        run_git_fixture(self.root, "clone", str(self.public), str(editable))
        (editable / "source.txt").write_text("local edits\n", encoding="utf-8")
        self.data["depsMode"] = "manual"
        self.data["depsManualPath"]["LibA"] = str(editable)
        self.write_lock(self.host, self.data)
        (self.remotes["LibA"] / "source.txt").write_text("new upstream\n", encoding="utf-8")
        newest = commit_git_fixture_repo(self.remotes["LibA"], "advance public seed")
        self.manager.prepare_seed_repository_closure(quiet=True)
        self.assertEqual(run_git_fixture(self.public, "rev-parse", "HEAD"), newest)
        self.assertEqual((editable / "source.txt").read_text(), "local edits\n")

    def test_nested_template_does_not_select_another_hosts_public_seed(self) -> None:
        remote_c, commit_c = create_git_fixture_repo(self.root / "remotes", "LibC", ("source.txt",))
        nested = {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "depsManualPath": {"LibC": ""},
            "dependencies": {
                "LibC": {
                    "remote": str(remote_c),
                    "commit": commit_c,
                    "publicSeedPath": "/another-machine/LibC",
                }
            },
        }
        (self.remotes["LibA"] / "source_roots.lock.jsonc.in").write_text(json.dumps(nested))
        commit_a = commit_git_fixture_repo(self.remotes["LibA"], "nested dependency")
        self.data["dependencies"]["LibA"]["commit"] = commit_a
        self.write_lock(self.host, self.data)
        self.manager.prepare_seed_repository_closure(quiet=True)
        roots = self.manager.materialize_dependency_roots(allow_network=False, quiet=True)
        self.assertEqual(
            roots.seed_repository_for("LibC"), self.host / "build/dependency_seed_repos/LibC"
        )
