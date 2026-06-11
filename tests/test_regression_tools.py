from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.regression.runner import (  # noqa: E402
    CaseInvocation,
    classify_case_outcome,
    load_app_config,
    parse_case_invocation,
    resolve_app_executable,
    resolve_report_path,
)


class RegressionToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def test_resolve_report_path_supports_current_document_and_indices(self) -> None:
        report = {
            "documents": [
                {"name": "old", "is_current": False},
                {"name": "active", "is_current": True, "items": [{"value": 7}]},
            ]
        }

        self.assertEqual(resolve_report_path(report, "documents_count"), 2)
        self.assertEqual(resolve_report_path(report, "current_document.name"), "active")
        self.assertEqual(resolve_report_path(report, "current_document.items[0].value"), 7)

    def test_parse_case_invocation_validates_mode_target_and_paths(self) -> None:
        case_dir = self.root / "case"
        case_dir.mkdir()
        target = case_dir / "script.txt"
        target.write_text("", encoding="utf-8")
        case_file = case_dir / "case.json"

        invocation = parse_case_invocation(
            {"invoke": {"mode": "script", "target": "script.txt", "strict": True}},
            case_file,
            validate_paths=True,
        )

        self.assertEqual(invocation.mode, "script")
        self.assertTrue(invocation.strict)

        with self.assertRaisesRegex(Exception, "target not found"):
            parse_case_invocation(
                {"invoke": {"mode": "script", "target": "missing.txt"}},
                case_file,
                validate_paths=True,
            )

    def test_classify_case_outcome_uses_report_semantics(self) -> None:
        self.assertEqual(
            classify_case_outcome(
                CaseInvocation("script", "x", True, ""),
                False,
                0,
                {"script_result": {"has_error": True}},
            ),
            "assert_fail",
        )
        self.assertEqual(
            classify_case_outcome(
                CaseInvocation("scenario", "x", False, ""),
                False,
                0,
                {"scenario_result": {"ok": False}},
            ),
            "scenario_fail",
        )
        self.assertEqual(
            classify_case_outcome(CaseInvocation("script", "x", False, ""), True, None, None),
            "timeout",
        )

    def test_app_config_resolves_executable_candidates(self) -> None:
        app = self.root / "bin" / "demo-cli"
        app.parent.mkdir()
        app.write_text("#!/bin/sh\n", encoding="utf-8")
        app.chmod(app.stat().st_mode | 0o111)
        config_path = self.root / "runner.json"
        config_path.write_text(
            json.dumps(
                {
                    "executableCandidates": ["{app}/bin/demo-cli"],
                    "modeCommands": {"script": ["script", "{target}", "{strict_flag}"]},
                    "preferSubstrings": ["demo-cli"],
                }
            ),
            encoding="utf-8",
        )

        app_config = load_app_config(config_path)

        self.assertEqual(resolve_app_executable(str(self.root), app_config), app)

    def test_app_config_allows_partial_override_of_executable_candidates(self) -> None:
        config_path = self.root / "runner.json"
        config_path.write_text(
            json.dumps({"executableCandidates": ["{app}/bin/demo-cli"]}),
            encoding="utf-8",
        )

        app_config = load_app_config(config_path)

        self.assertIn("script", app_config.mode_commands)
        self.assertEqual(app_config.executable_candidates, ("{app}/bin/demo-cli",))

    def test_regression_tool_help(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "tools.regression.cli", "--help"],
            cwd=REPO_ROOT,
            env={"PYTHONPATH": str(REPO_ROOT), **os.environ},
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertIn("--suite-root", completed.stdout)


if __name__ == "__main__":
    unittest.main()
