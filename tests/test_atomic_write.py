from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


from freecm.atomic_write import atomic_write_json, atomic_write_text


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_text_replaces_content_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "nested" / "source_roots.lock.jsonc"

            atomic_write_text(target, "first\n")
            atomic_write_text(target, "second\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "second\n")
            self.assertEqual(list(target.parent.glob(".source_roots.lock.jsonc.*.tmp")), [])
            self.assertTrue((target.parent / ".source_roots.lock.jsonc.lock").is_file())

    def test_atomic_write_json_keeps_existing_content_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "source_roots.lock.jsonc"
            target.write_text("original\n", encoding="utf-8")

            with mock.patch("freecm.atomic_write.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    atomic_write_json(target, {"depsMode": "manual"})

            self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(list(target.parent.glob(".source_roots.lock.jsonc.*.tmp")), [])

    def test_atomic_write_json_formats_with_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "source_roots.lock.jsonc"

            atomic_write_json(target, {"depsMode": "manual"})

            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"depsMode": "manual"},
            )
            self.assertTrue(target.read_text(encoding="utf-8").endswith("\n"))


if __name__ == "__main__":
    unittest.main()
