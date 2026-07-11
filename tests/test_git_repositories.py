from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from freecm.git_repositories import (
    GitRepositoryState,
    ensure_worktree_at_commit,
    git_repository_state,
    remove_path,
)
from freecm.io_metrics import capture_io_operations
from tests.git_test_helpers import (
    commit_git_fixture_repo,
    create_git_fixture_repo,
    run_git_fixture,
)


class GitRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.repositories_root = self.root / "repositories"
        self.repositories_root.mkdir()
        self.seed_root, self.first_commit = create_git_fixture_repo(
            self.repositories_root,
            "LibA",
            ("fixture.txt",),
        )

    def test_repository_state_combines_common_dir_and_head_for_linked_worktrees(
        self,
    ) -> None:
        linked_root = self.root / "linked"
        run_git_fixture(
            self.seed_root,
            "worktree",
            "add",
            "--detach",
            str(linked_root),
            self.first_commit,
        )

        seed_state = git_repository_state(self.seed_root)
        linked_state = git_repository_state(linked_root)

        self.assertIsNotNone(seed_state)
        self.assertIsNotNone(linked_state)
        assert seed_state is not None
        assert linked_state is not None
        self.assertEqual(seed_state.head, self.first_commit)
        self.assertEqual(linked_state.head, self.first_commit)
        self.assertEqual(seed_state.common_dir, linked_state.common_dir)
        self.assertEqual(seed_state.work_tree, self.seed_root)
        self.assertEqual(linked_state.work_tree, linked_root)

        plain_directory = self.root / "plain"
        plain_directory.mkdir()
        self.assertIsNone(git_repository_state(plain_directory))
        self.assertIsNone(git_repository_state(self.root / "missing"))

    def test_ensure_worktree_uses_repository_state_without_weakening_repairs(self) -> None:
        target_root = self.root / "target"
        seed_state = git_repository_state(self.seed_root)
        self.assertIsNotNone(seed_state)
        assert seed_state is not None

        with capture_io_operations() as missing_recorder:
            ensure_worktree_at_commit(
                self.seed_root,
                target_root,
                self.first_commit,
                seed_repository_state=seed_state,
                quiet=True,
            )
        self.assertEqual(
            missing_recorder.git_summary()["byCategory"],
            {"worktree_add": 1, "worktree_prune": 1},
        )
        first_target_state = git_repository_state(target_root)
        self.assertIsNotNone(first_target_state)
        assert first_target_state is not None
        self.assertEqual(first_target_state.head, self.first_commit)

        with capture_io_operations() as recorder:
            ensure_worktree_at_commit(
                self.seed_root,
                target_root,
                self.first_commit,
                seed_repository_state=seed_state,
                quiet=True,
            )
        self.assertEqual(
            recorder.git_summary()["byCategory"],
            {
                "rev_parse_repository_state": 1,
                "status": 1,
                "worktree_prune": 1,
            },
        )

        (target_root / "local.txt").write_text("dirty\n", encoding="utf-8")
        ensure_worktree_at_commit(
            self.seed_root,
            target_root,
            self.first_commit,
            seed_repository_state=seed_state,
            quiet=True,
        )
        self.assertFalse((target_root / "local.txt").exists())

        (self.seed_root / "fixture.txt").write_text("advanced\n", encoding="utf-8")
        second_commit = commit_git_fixture_repo(self.seed_root, "advance fixture")
        advanced_seed_state = git_repository_state(self.seed_root)
        self.assertIsNotNone(advanced_seed_state)
        assert advanced_seed_state is not None
        ensure_worktree_at_commit(
            self.seed_root,
            target_root,
            second_commit,
            seed_repository_state=advanced_seed_state,
            quiet=True,
        )
        advanced_target_state = git_repository_state(target_root)
        self.assertIsNotNone(advanced_target_state)
        assert advanced_target_state is not None
        self.assertEqual(advanced_target_state.head, second_commit)

        remove_path(target_root)
        unrelated_root, _ = create_git_fixture_repo(
            self.root,
            "target",
            ("unrelated.txt",),
        )
        self.assertEqual(unrelated_root, target_root)
        ensure_worktree_at_commit(
            self.seed_root,
            target_root,
            second_commit,
            seed_repository_state=advanced_seed_state,
            quiet=True,
        )
        replacement_state = git_repository_state(target_root)
        self.assertIsNotNone(replacement_state)
        assert replacement_state is not None
        self.assertEqual(replacement_state.common_dir, advanced_seed_state.common_dir)
        self.assertEqual(replacement_state.head, second_commit)

    def test_ensure_worktree_rejects_mismatched_seed_state(self) -> None:
        wrong_state = GitRepositoryState(
            work_tree=self.root / "OtherRoot",
            common_dir=self.seed_root / ".git",
            head=self.first_commit,
        )

        with self.assertRaisesRegex(ValueError, "state does not match"):
            ensure_worktree_at_commit(
                self.seed_root,
                self.root / "target",
                self.first_commit,
                seed_repository_state=wrong_state,
            )


if __name__ == "__main__":
    unittest.main()
