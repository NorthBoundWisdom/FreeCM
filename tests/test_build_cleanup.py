from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from freecm.build_cleanup import clean_build
from freecm.workspace_lock import workspace_lock_path


class BuildCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo_root = Path(self.tempdir.name) / "SampleApp"
        self.repo_root.mkdir()

    def _create_directory_symlink(self, target: Path, link: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                self.skipTest("creating symlinks requires Windows developer mode")
            raise

    def test_clean_build_removes_only_non_preserved_direct_children(self) -> None:
        build_root = self.repo_root / "build"
        seed_root = build_root / "dependency_seed_repos"
        source_root = build_root / "dependency_source_roots"
        generated_root = build_root / "generated" / "nested"
        seed_root.mkdir(parents=True)
        source_root.mkdir()
        generated_root.mkdir(parents=True)
        (generated_root / "artifact.txt").write_text("generated\n", encoding="utf-8")
        (build_root / "binary.dat").write_text("binary\n", encoding="utf-8")
        (self.repo_root / "DerivedData").mkdir()

        result = clean_build(self.repo_root)

        self.assertEqual(
            result.targets,
            ("build/binary.dat", "build/generated"),
        )
        self.assertEqual(
            result.preserved,
            (
                "build/dependency_seed_repos",
                "build/dependency_source_roots",
            ),
        )
        self.assertFalse(result.dry_run)
        self.assertTrue(seed_root.is_dir())
        self.assertTrue(source_root.is_dir())
        self.assertFalse((build_root / "generated").exists())
        self.assertFalse((build_root / "binary.dat").exists())
        self.assertTrue((self.repo_root / "DerivedData").is_dir())
        self.assertFalse(workspace_lock_path(self.repo_root).exists())

    def test_clean_build_dry_run_reports_without_deleting(self) -> None:
        artifact = self.repo_root / "build" / "output" / "artifact.txt"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("generated\n", encoding="utf-8")

        result = clean_build(self.repo_root, dry_run=True)

        self.assertEqual(result.targets, ("build/output",))
        self.assertTrue(result.dry_run)
        self.assertTrue(artifact.is_file())
        self.assertFalse(workspace_lock_path(self.repo_root).exists())

    def test_clean_build_is_noop_when_build_directory_is_missing(self) -> None:
        result = clean_build(self.repo_root)

        self.assertEqual(result.targets, ())
        self.assertEqual(result.preserved, ())

    def test_clean_build_rejects_symlinked_build_directory(self) -> None:
        external = Path(self.tempdir.name) / "external-build"
        external.mkdir()
        self._create_directory_symlink(external, self.repo_root / "build")

        with self.assertRaisesRegex(RuntimeError, "symlinked build directory"):
            clean_build(self.repo_root)

    def test_clean_build_rejects_non_directory_build_path(self) -> None:
        (self.repo_root / "build").write_text("not a directory\n", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "non-directory build path"):
            clean_build(self.repo_root)

    def test_clean_build_unlinks_child_symlink_without_following_target(self) -> None:
        build_root = self.repo_root / "build"
        external = Path(self.tempdir.name) / "external-output"
        build_root.mkdir()
        external.mkdir()
        external_file = external / "keep.txt"
        external_file.write_text("keep\n", encoding="utf-8")
        linked_output = build_root / "linked-output"
        self._create_directory_symlink(external, linked_output)

        result = clean_build(self.repo_root)

        self.assertEqual(result.targets, ("build/linked-output",))
        self.assertFalse(linked_output.exists())
        self.assertTrue(external_file.is_file())
