from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import time
import tempfile
import unittest
from pathlib import Path
from unittest import mock


from freecm.atomic_write import atomic_write_json, atomic_write_text
from freecm.workspace_lock import workspace_lock_path, workspace_mutation_lock


def atomic_sidecar_dir(path: Path) -> Path:
    return path.parent / ".freecm" / "atomic"


def assert_atomic_write_sidecars(testcase: unittest.TestCase, path: Path) -> None:
    sidecar_dir = atomic_sidecar_dir(path)
    testcase.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])
    testcase.assertFalse((path.parent / f".{path.name}.lock").exists())
    testcase.assertEqual(list(sidecar_dir.glob(f".{path.name}.*.tmp")), [])
    testcase.assertTrue((sidecar_dir / f".{path.name}.lock").is_file())


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_text_replaces_content_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "nested" / "source_roots.lock.jsonc"

            atomic_write_text(target, "first\n")
            atomic_write_text(target, "second\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "second\n")
            assert_atomic_write_sidecars(self, target)

    def test_atomic_write_json_keeps_existing_content_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "source_roots.lock.jsonc"
            target.write_text("original\n", encoding="utf-8")

            with mock.patch("freecm.atomic_write.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    atomic_write_json(target, {"depsMode": "manual"})

            self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
            assert_atomic_write_sidecars(self, target)

    def test_atomic_write_json_formats_with_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "source_roots.lock.jsonc"

            atomic_write_json(target, {"depsMode": "manual"})

            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"depsMode": "manual"},
            )
            self.assertTrue(target.read_text(encoding="utf-8").endswith("\n"))

    def test_atomic_write_text_serializes_concurrent_writers(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "source_roots.lock.jsonc"
            values = [
                json.dumps({"writer": index, "payload": "x" * 1024}, indent=2) + "\n"
                for index in range(16)
            ]

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lambda value: atomic_write_text(target, value), values))

            final_text = target.read_text(encoding="utf-8")
            self.assertIn(final_text, values)
            self.assertIsInstance(json.loads(final_text), dict)
            assert_atomic_write_sidecars(self, target)

    def test_workspace_mutation_lock_serializes_concurrent_operations(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            order: list[str] = []

            def operation(label: str) -> None:
                with workspace_mutation_lock(repo_root):
                    self.assertTrue(workspace_lock_path(repo_root).is_dir())
                    order.append(f"{label}:start")
                    time.sleep(0.005)
                    order.append(f"{label}:end")

            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(operation, [f"op{index}" for index in range(8)]))

            self.assertFalse(workspace_lock_path(repo_root).exists())
            for index in range(0, len(order), 2):
                self.assertEqual(order[index].split(":")[0], order[index + 1].split(":")[0])

    def test_workspace_mutation_lock_is_reentrant_in_same_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)

            with workspace_mutation_lock(repo_root):
                self.assertTrue(workspace_lock_path(repo_root).is_dir())
                with workspace_mutation_lock(repo_root, timeout_seconds=0.001):
                    self.assertTrue(workspace_lock_path(repo_root).is_dir())
                self.assertTrue(workspace_lock_path(repo_root).is_dir())

            self.assertFalse(workspace_lock_path(repo_root).exists())

    def test_workspace_mutation_lock_times_out_when_existing_lock_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            workspace_lock_path(repo_root).mkdir()

            with self.assertRaisesRegex(TimeoutError, "Unable to acquire workspace lock"):
                with workspace_mutation_lock(repo_root, timeout_seconds=0.001):
                    pass


if __name__ == "__main__":
    unittest.main()
