from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from freecm.dependency_lock import load_dependency_lock_data, validate_dependency_lock_data
from freecm.errors import FreeCMError, LockfileValidationError


class DependencyLockTests(unittest.TestCase):
    def _minimal_lock_data(self) -> dict[str, object]:
        return {
            "schemaVersion": 5,
            "depsMode": "pinned",
            "depsManualPath": {
                "LibA": "",
            },
            "dependencies": {
                "LibA": {
                    "repoName": "RepoA",
                    "remote": "https://example.invalid/repo-a.git",
                    "commit": "abc123",
                    "latestRef": "main",
                    "abiGroup": "core-v1",
                },
            },
        }

    def test_validate_dependency_lock_data_normalizes_optional_maps_and_repo_name(self) -> None:
        data = self._minimal_lock_data()
        data["AppConfigs"] = {"MARKETING_VERSION": "1.0.0"}

        validated = validate_dependency_lock_data(
            data,
            path_label="source_roots.lock.jsonc",
            expected_dependency_names=("LibA",),
        )

        self.assertEqual(validated["cmakeEnvironment"], {})
        self.assertEqual(validated["cmakeCacheVariables"], {})
        self.assertEqual(validated["terminalPath"], {})
        self.assertEqual(validated["assets"], {})
        self.assertEqual(validated["AppConfigs"], {"MARKETING_VERSION": "1.0.0"})
        self.assertEqual(validated["dependencies"]["LibA"]["repoName"], "RepoA")  # type: ignore[index]

    def test_validate_dependency_lock_data_rejects_legacy_swift_configs(self) -> None:
        data = self._minimal_lock_data()
        data["SwiftConfigs"] = {"MARKETING_VERSION": "1.0.0"}

        with self.assertRaisesRegex(ValueError, "SwiftConfigs is no longer supported"):
            validate_dependency_lock_data(
                data,
                path_label="source_roots.lock.jsonc",
                expected_dependency_names=("LibA",),
            )

    def test_load_dependency_lock_data_wraps_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "source_roots.lock.jsonc"
            data = self._minimal_lock_data()
            data["dependencies"]["LibA"]["repoName"] = "../RepoA"  # type: ignore[index]
            path.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaises(LockfileValidationError) as context:
                load_dependency_lock_data(path, expected_dependency_names=("LibA",))

        self.assertIsInstance(context.exception, FreeCMError)
        self.assertIsInstance(context.exception, ValueError)
        self.assertIn("repository name", str(context.exception))


if __name__ == "__main__":
    unittest.main()
