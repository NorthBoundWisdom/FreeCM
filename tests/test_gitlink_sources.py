from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from freecm.git_repositories import ensure_worktree_at_commit
from freecm.gitlink_sources import (
    GitlinkPolicy,
    gitlink_sources,
    materialize_gitlinks,
    preflight_gitlinks,
    remove_obsolete_gitlinks,
    verify_gitlinks,
)
from tests.git_test_helpers import commit_git_fixture_repo, create_git_fixture_repo, run_git_fixture


class GitlinkSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module, self.first = create_git_fixture_repo(self.root, "Module", ("file.txt",))
        self.parent, _ = create_git_fixture_repo(self.root, "Parent", ("main.txt",))
        run_git_fixture(
            self.parent,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(self.module),
            "libs/module",
        )
        self.old = commit_git_fixture_repo(self.parent, "add module")
        self.cache = self.root / "cache"
        self.target = self.root / "target"

    def prepare(self, commit: str):
        return gitlink_sources(
            self.parent, commit, str(self.parent), self.cache, allow_network=True
        )

    def test_upgrade_materializes_exact_pin_offline(self) -> None:
        old_sources = self.prepare(self.old)
        ensure_worktree_at_commit(self.parent, self.target, self.old)
        materialize_gitlinks(self.target, old_sources)
        (self.module / "file.txt").write_text("new version")
        second = commit_git_fixture_repo(self.module, "advance module")
        run_git_fixture(self.parent / "libs/module", "fetch", "origin")
        run_git_fixture(self.parent / "libs/module", "checkout", second)
        new = commit_git_fixture_repo(self.parent, "update pin")
        self.prepare(new)
        # Break the upstream URL: all offline operations must use cached objects.
        self.module.rename(self.root / "HiddenModule")
        sources = gitlink_sources(self.parent, new, str(self.parent), self.cache)
        preflight_gitlinks(self.target)
        ensure_worktree_at_commit(self.parent, self.target, new)
        materialize_gitlinks(self.target, sources)
        self.assertEqual(run_git_fixture(self.target / "libs/module", "rev-parse", "HEAD"), second)
        self.assertEqual(run_git_fixture(self.target, "status", "--porcelain"), "")
        # A pinned downgrade must also remain offline.
        ensure_worktree_at_commit(self.parent, self.target, self.old)
        materialize_gitlinks(self.target, old_sources)
        self.assertEqual(
            run_git_fixture(self.target / "libs/module", "rev-parse", "HEAD"), self.first
        )

    def test_missing_cache_fails_without_network_or_target_changes(self) -> None:
        with mock.patch("freecm.gitlink_sources.git") as command:
            # Metadata reads use git_output; the mutating git helper must not run.
            with self.assertRaisesRegex(FileNotFoundError, "run --initrepo"):
                gitlink_sources(self.parent, self.old, str(self.parent), self.cache)
            command.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_module_edits_are_preserved(self) -> None:
        sources = self.prepare(self.old)
        ensure_worktree_at_commit(self.parent, self.target, self.old)
        materialize_gitlinks(self.target, sources)
        modified = self.target / "libs/module/file.txt"
        modified.write_text("user edit")
        with self.assertRaisesRegex(ValueError, "modified gitlink"):
            preflight_gitlinks(self.target)
        self.assertEqual(modified.read_text(), "user edit")

    def test_symlink_module_is_rejected(self) -> None:
        sources = self.prepare(self.old)
        self.target.mkdir()
        (self.target / "libs").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink|escapes"):
            materialize_gitlinks(self.target, sources)

    def test_recursive_modules_are_pinned(self) -> None:
        leaf, leaf_sha = create_git_fixture_repo(self.root, "Leaf", ("leaf.txt",))
        run_git_fixture(
            self.module, "-c", "protocol.file.allow=always", "submodule", "add", str(leaf), "nested"
        )
        module_sha = commit_git_fixture_repo(self.module, "add nested module")
        run_git_fixture(self.parent / "libs/module", "fetch", "origin")
        run_git_fixture(self.parent / "libs/module", "checkout", module_sha)
        parent_sha = commit_git_fixture_repo(self.parent, "advance module")
        sources = self.prepare(parent_sha)
        ensure_worktree_at_commit(self.parent, self.target, parent_sha)
        materialize_gitlinks(self.target, sources)
        self.assertEqual(
            run_git_fixture(self.target / "libs/module/nested", "rev-parse", "HEAD"), leaf_sha
        )
        (self.target / "libs/module/nested/untracked.txt").write_text("keep me")
        with self.assertRaisesRegex(ValueError, "modified gitlink"):
            preflight_gitlinks(self.target)

    def test_cache_symlink_is_rejected(self) -> None:
        self.cache.symlink_to(self.module, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.prepare(self.old)

    def test_removed_module_is_cleaned_and_edits_are_protected(self) -> None:
        sources = self.prepare(self.old)
        ensure_worktree_at_commit(self.parent, self.target, self.old)
        materialize_gitlinks(self.target, sources)
        self.assertEqual(verify_gitlinks(self.target, self.old), [])
        modified = self.target / "libs/module/file.txt"
        modified.write_text("keep")
        with self.assertRaisesRegex(ValueError, "modified gitlink"):
            remove_obsolete_gitlinks(self.target, ())
        run_git_fixture(modified.parent, "restore", "file.txt")
        remove_obsolete_gitlinks(self.target, ())
        self.assertFalse(modified.parent.exists())
        self.assertIn("Uninitialized gitlink", verify_gitlinks(self.target, self.old)[0])

    def test_transport_rewrite_does_not_change_cache_identity(self) -> None:
        sources = self.prepare(self.old)
        cache = sources[0].cache
        run_git_fixture(cache, "config", "url.ssh://transport.invalid/.insteadOf", str(self.module))
        self.assertEqual(
            run_git_fixture(cache, "remote", "get-url", "origin"), "ssh://transport.invalid/"
        )
        resolved = gitlink_sources(self.parent, self.old, str(self.parent), self.cache)
        self.assertEqual(resolved[0].remote, str(self.module))

    def test_policy_prepares_only_selected_transitive_modules(self) -> None:
        needed, needed_sha = create_git_fixture_repo(self.root, "Needed", ("needed.txt",))
        unused, _ = create_git_fixture_repo(self.root, "Unused", ("unused.txt",))
        for name, module in (("needed", needed), ("unused", unused)):
            run_git_fixture(
                self.parent,
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(module),
                name,
            )
        commit = commit_git_fixture_repo(self.parent, "add optional modules")
        # A selected module declares its sibling dependency; the unused remote
        # disappears before init, so accidentally fetching everything fails.
        unused.rename(self.root / "UnavailableUnused")
        policy = GitlinkPolicy(
            ("libs/module",), lambda name, repo: ("needed",) if name == "libs/module" else ()
        )
        sources = gitlink_sources(
            self.parent, commit, str(self.parent), self.cache, allow_network=True, policy=policy
        )
        self.assertEqual({source.path for source in sources}, {"libs/module", "needed"})
        needed.rename(self.root / "UnavailableNeeded")
        sources = gitlink_sources(self.parent, commit, str(self.parent), self.cache, policy=policy)
        ensure_worktree_at_commit(self.parent, self.target, commit)
        materialize_gitlinks(self.target, sources)
        self.assertEqual(run_git_fixture(self.target / "needed", "rev-parse", "HEAD"), needed_sha)
        self.assertFalse((self.target / "unused/.git").exists())
        self.assertEqual(
            verify_gitlinks(
                self.target, commit, selected_paths=frozenset(source.path for source in sources)
            ),
            [],
        )
        with self.assertRaisesRegex(ValueError, "Unknown selected gitlink"):
            gitlink_sources(
                self.parent,
                commit,
                str(self.parent),
                self.cache,
                policy=GitlinkPolicy(("missing",), lambda *_: ()),
            )

    def test_init_reuses_existing_module_objects_without_remote_access(self) -> None:
        sources = self.prepare(self.old)
        ensure_worktree_at_commit(self.parent, self.target, self.old)
        materialize_gitlinks(self.target, sources)
        self.module.rename(self.root / "UnavailableModule")
        new_cache = self.root / "new_cache"
        prepared = gitlink_sources(
            self.parent,
            self.old,
            str(self.parent),
            new_cache,
            allow_network=True,
            local_root=self.target,
        )
        self.assertEqual(prepared[0].commit, self.first)
        self.assertEqual(run_git_fixture(prepared[0].cache, "rev-parse", "HEAD"), self.first)
