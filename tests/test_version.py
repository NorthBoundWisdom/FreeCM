from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import freecm  # noqa: E402


class VersionMetadataTests(unittest.TestCase):
    def test_source_import_reports_version_file_value(self) -> None:
        expected = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()

        self.assertEqual(freecm.__version__, expected)

    def test_source_import_exposes_structured_errors(self) -> None:
        self.assertTrue(issubclass(freecm.FreeCMError, Exception))
        self.assertTrue(issubclass(freecm.LockfileValidationError, ValueError))
        self.assertTrue(issubclass(freecm.LockfileValidationError, freecm.FreeCMError))
        self.assertTrue(issubclass(freecm.SeedRepositoryError, RuntimeError))
        self.assertTrue(issubclass(freecm.MaterializationError, RuntimeError))

    def test_version_consistency_script_passes(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/check-version-consistency.py"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("FreeCM version metadata is consistent", completed.stdout)


if __name__ == "__main__":
    unittest.main()
