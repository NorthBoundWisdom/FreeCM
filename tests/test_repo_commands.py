from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from freecm.errors import RepoCommandManifestError
from freecm.repo_commands import (
    validate_repo_command_manifest,
    validate_repo_command_manifest_text,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_TOOL = REPO_ROOT / "tools" / "validate_repo_commands.py"


def valid_manifest() -> dict[str, object]:
    return {
        "version": 2,
        "commands": {
            "config": [
                {
                    "id": "linux-debug",
                    "label": "Linux Debug",
                    "command": "cmake",
                    "args": ["--preset", "linux_debug"],
                    "platforms": ["linux"],
                    "default": True,
                    "defaults": {"build": "linux-debug"},
                    "readiness": {
                        "inputs": ["CMakePresets.json"],
                        "outputs": ["build/linux_debug/CMakeCache.txt"],
                    },
                }
            ],
            "build": [
                {
                    "id": "linux-debug",
                    "label": "Linux Debug",
                    "command": "cmake",
                    "args": ["--build", "--preset", "linux_debug"],
                    "configurations": ["linux-debug"],
                }
            ],
        },
    }


class RepoCommandManifestTests(unittest.TestCase):
    def test_validates_manifest_from_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            manifest_path = repo_root / "configs" / "freecm.commands.jsonc"
            manifest_path.parent.mkdir()
            manifest_path.write_text(json.dumps(valid_manifest()), encoding="utf-8")

            summary = validate_repo_command_manifest(repo_root)

        self.assertEqual(summary.manifest_path, manifest_path.resolve())
        self.assertEqual(summary.configuration_count, 1)
        self.assertEqual(summary.variant_count, 2)

    def test_accepts_jsonc_comments_and_trailing_commas(self) -> None:
        manifest = valid_manifest()
        text = json.dumps(manifest, indent=2).replace(
            '"version": 2,',
            '// Config Context contract\n  "version": 2,',
        )
        text = text.replace("\n}", ",\n}")

        summary = validate_repo_command_manifest_text(text, manifest_path="commands.jsonc")

        self.assertEqual(summary.configuration_count, 1)

    def test_rejects_non_array_command_arguments(self) -> None:
        manifest = valid_manifest()
        manifest["commands"]["build"][0]["args"] = "--build"  # type: ignore[index]

        with self.assertRaisesRegex(
            RepoCommandManifestError,
            r"commands\.build\[0\]\.args must be a string array",
        ):
            validate_repo_command_manifest_text(json.dumps(manifest))

    def test_rejects_explicit_null_action_and_readiness_arrays(self) -> None:
        manifest = valid_manifest()
        manifest["commands"]["test"] = None  # type: ignore[index]
        with self.assertRaisesRegex(RepoCommandManifestError, "commands.test must be an array"):
            validate_repo_command_manifest_text(json.dumps(manifest))

        manifest = valid_manifest()
        manifest["commands"]["config"][0]["readiness"]["inputs"] = None  # type: ignore[index]
        with self.assertRaisesRegex(
            RepoCommandManifestError,
            r"readiness\.inputs must be a string array",
        ):
            validate_repo_command_manifest_text(json.dumps(manifest))

    def test_rejects_unknown_config_reference(self) -> None:
        manifest = valid_manifest()
        manifest["commands"]["build"][0]["configurations"] = ["missing"]  # type: ignore[index]

        with self.assertRaisesRegex(RepoCommandManifestError, "references unknown Config"):
            validate_repo_command_manifest_text(json.dumps(manifest))

    def test_rejects_ambiguous_platform_default(self) -> None:
        manifest = valid_manifest()
        second_config = dict(manifest["commands"]["config"][0])  # type: ignore[index]
        second_config["id"] = "linux-release"
        second_config["defaults"] = {}
        manifest["commands"]["config"].append(second_config)  # type: ignore[index]

        with self.assertRaisesRegex(
            RepoCommandManifestError,
            "platform linux must have exactly one default Config; found 2",
        ):
            validate_repo_command_manifest_text(json.dumps(manifest))

    def test_rejects_readiness_path_outside_repository(self) -> None:
        manifest = valid_manifest()
        manifest["commands"]["config"][0]["readiness"]["inputs"] = [  # type: ignore[index]
            "../outside"
        ]

        with self.assertRaisesRegex(RepoCommandManifestError, "must stay within the repository"):
            validate_repo_command_manifest_text(json.dumps(manifest))

    def test_zero_install_cli_uses_only_python(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            manifest_path = repo_root / "configs" / "freecm.commands.jsonc"
            manifest_path.parent.mkdir()
            manifest_path.write_text(json.dumps(valid_manifest()), encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(VALIDATOR_TOOL), str(repo_root)],
                cwd=repo_root,
                env={"PATH": ""},
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Validated", result.stdout)
        self.assertIn("1 Configs, 2 variants", result.stdout)


if __name__ == "__main__":
    unittest.main()
