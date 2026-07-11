from __future__ import annotations

import inspect
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tools.regression as regression_package  # noqa: E402
from tools.regression import assertions, cases, models, runner  # noqa: E402
from tools.regression.runner import (  # noqa: E402
    DEFAULT_APP_CONFIG,
    CaseConfigError,
    CaseInvocation,
    CaseMeta,
    CaseResult,
    ControlConfig,
    RegressionAppConfig,
    classify_case_outcome,
    collect_case_meta,
    find_case_files,
    is_case_selected,
    load_app_config,
    load_control,
    parse_case_invocation,
    resolve_app_executable,
    resolve_report_path,
    validate_selected_cases,
)


class RegressionToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def test_runner_preserves_public_api_and_reexport_identity(self) -> None:
        expected_package_exports = [
            "CaseConfigError",
            "CaseInvocation",
            "CaseMeta",
            "CaseResult",
            "ControlConfig",
            "classify_case_outcome",
            "collect_case_meta",
            "find_case_files",
            "load_control",
            "parse_case_invocation",
            "resolve_report_path",
            "run_case",
            "write_junit",
        ]
        self.assertEqual(regression_package.__all__, expected_package_exports)
        identities = {
            "CaseConfigError": cases.CaseConfigError,
            "CaseInvocation": models.CaseInvocation,
            "CaseMeta": models.CaseMeta,
            "CaseResult": models.CaseResult,
            "ControlConfig": models.ControlConfig,
            "DEFAULT_APP_CONFIG": cases.DEFAULT_APP_CONFIG,
            "RegressionAppConfig": models.RegressionAppConfig,
            "collect_case_meta": cases.collect_case_meta,
            "find_case_files": cases.find_case_files,
            "is_case_selected": cases.is_case_selected,
            "load_app_config": cases.load_app_config,
            "load_case": cases.load_case,
            "load_control": cases.load_control,
            "load_json_object": cases.load_json_object,
            "parse_case_invocation": cases.parse_case_invocation,
            "resolve_app_executable": cases.resolve_app_executable,
            "validate_selected_cases": cases.validate_selected_cases,
        }
        for name, value in identities.items():
            with self.subTest(name=name):
                self.assertIs(getattr(runner, name), value)
        self.assertIs(runner.get_current_document, assertions.get_current_document)
        self.assertIs(runner.resolve_report_path, assertions.resolve_report_path)
        self.assertIs(runner.classify_case_outcome, assertions.classify_case_outcome)

        expected_runner_exports = {
            *expected_package_exports,
            "DEFAULT_APP_CONFIG",
            "RegressionAppConfig",
            "get_current_document",
            "is_case_selected",
            "load_app_config",
            "load_case",
            "load_json_object",
            "resolve_app_executable",
            "run_regression_suite",
            "validate_selected_cases",
        }
        self.assertEqual(set(runner.__all__), expected_runner_exports)

    def test_public_dataclass_snapshot_is_stable_and_frozen(self) -> None:
        expected_fields: dict[type[Any], tuple[str, ...]] = {
            CaseResult: (
                "name",
                "case_dir",
                "passed",
                "reason",
                "exit_code",
                "duration_sec",
                "report_path",
            ),
            CaseInvocation: ("mode", "target", "strict", "backend"),
            CaseMeta: (
                "case_file",
                "case_dir",
                "case_id",
                "name",
                "tags",
                "enabled",
            ),
            ControlConfig: ("only_cases", "disabled_cases", "disabled_tags"),
            RegressionAppConfig: (
                "executable_candidates",
                "mode_commands",
                "prefer_substrings",
            ),
        }
        for value, names in expected_fields.items():
            with self.subTest(value=value.__name__):
                self.assertEqual(tuple(field.name for field in fields(value)), names)
                self.assertEqual(tuple(inspect.signature(value).parameters), names)
                self.assertTrue(value.__dataclass_params__.frozen)  # type: ignore[attr-defined]
                self.assertEqual(value.__module__, "tools.regression.runner")

        invocation = CaseInvocation("script", "fixture.txt", False, "")
        with self.assertRaises(FrozenInstanceError):
            invocation.mode = "scenario"  # type: ignore[misc]

    def test_moved_function_signature_snapshot_is_stable(self) -> None:
        expected = {
            "load_json_object": "(path: 'Path') -> 'dict[str, Any]'",
            "load_app_config": "(path: 'Path | None') -> 'RegressionAppConfig'",
            "resolve_app_executable": "(app_arg: 'str', app_config: 'RegressionAppConfig') -> 'Path | None'",
            "load_case": "(case_file: 'Path') -> 'dict[str, Any]'",
            "find_case_files": "(root: 'Path') -> 'list[Path]'",
            "load_control": "(control_path: 'Path') -> 'ControlConfig'",
            "collect_case_meta": "(case_file: 'Path', suite_root: 'Path') -> 'CaseMeta'",
            "is_case_selected": "(meta: 'CaseMeta', control: 'ControlConfig') -> 'bool'",
            "parse_case_invocation": "(case: 'Mapping[str, Any]', case_file: 'Path', validate_paths: 'bool') -> 'CaseInvocation'",
            "validate_selected_cases": "(selected_meta: 'Sequence[CaseMeta]', app_config: 'RegressionAppConfig') -> 'list[str]'",
        }
        for name, signature in expected.items():
            with self.subTest(name=name):
                self.assertEqual(str(inspect.signature(getattr(runner, name))), signature)
        self.assertEqual(
            str(inspect.signature(runner.run_case)),
            "(app: 'Path', case_file: 'Path', case_id: 'str', out_root: 'Path', "
            "default_timeout: 'float', app_config: 'RegressionAppConfig' = "
            "RegressionAppConfig(executable_candidates=('{app}', '{app}/{app_name}', "
            "'{app}/{app_name}.exe', '{app}/bin/{app_name}', "
            "'{app}/bin/{app_name}.exe', '{app}/Release/{app_name}.exe', "
            "'{app}/Debug/{app_name}.exe'), mode_commands={'script': ('script', "
            "'run', '--file={target}', '--report={report}', '{strict_flag}'), "
            "'scenario': ('scenario', 'run', '--name={target}', "
            "'--report={report}'), 'viewer2d': ('viewer2d', 'run', "
            "'--perf-config={target}', '--report={report}', '{backend_flag}')}, "
            "prefer_substrings=())) -> 'CaseResult'",
        )

    def test_case_discovery_metadata_and_selection_precedence(self) -> None:
        enabled_dir = self.root / "nested" / "enabled"
        disabled_dir = self.root / "disabled"
        enabled_dir.mkdir(parents=True)
        disabled_dir.mkdir()
        enabled_file = enabled_dir / "case.json"
        disabled_file = disabled_dir / "case.json"
        enabled_file.write_text(
            json.dumps({"name": "Enabled", "tags": ["fast"]}),
            encoding="utf-8",
        )
        disabled_file.write_text(
            json.dumps({"enabled": False, "tags": ["slow"]}),
            encoding="utf-8",
        )

        self.assertEqual(find_case_files(self.root), [disabled_file, enabled_file])
        enabled = collect_case_meta(enabled_file, self.root)
        disabled = collect_case_meta(disabled_file, self.root)
        self.assertEqual(enabled.case_id, "nested/enabled")
        self.assertNotIn("\\", enabled.case_id)
        self.assertTrue(is_case_selected(enabled, ControlConfig([], [], [])))
        self.assertFalse(is_case_selected(disabled, ControlConfig([], [], [])))
        self.assertFalse(is_case_selected(enabled, ControlConfig(["other"], [], [])))
        self.assertFalse(
            is_case_selected(
                enabled,
                ControlConfig(["nested/enabled"], ["nested/enabled"], []),
            )
        )
        self.assertFalse(is_case_selected(enabled, ControlConfig([], [], ["fast"])))

    def test_control_loading_defaults_and_normalizes_values(self) -> None:
        missing = self.root / "missing.json"
        self.assertEqual(load_control(missing), ControlConfig([], [], []))
        control_path = self.root / "control.json"
        control_path.write_text(
            json.dumps(
                {
                    "only_cases": [1],
                    "disabled_cases": ["case-b"],
                    "disabled_tags": ["slow"],
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            load_control(control_path),
            ControlConfig(["1"], ["case-b"], ["slow"]),
        )

    def test_app_config_rejects_invalid_collection_shapes(self) -> None:
        cases_by_error: tuple[tuple[dict[str, object], str], ...] = (
            ({"executableCandidates": "app"}, "executableCandidates"),
            ({"modeCommands": []}, "modeCommands must be an object"),
            ({"modeCommands": {"script": "run"}}, "modeCommands.script"),
            ({"preferSubstrings": "debug"}, "preferSubstrings"),
        )
        for index, (data, message) in enumerate(cases_by_error):
            with self.subTest(message=message):
                path = self.root / f"invalid-{index}.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaisesRegex(CaseConfigError, message):
                    load_app_config(path)

    def test_invocation_rejects_unknown_keys_and_invalid_types(self) -> None:
        case_file = self.root / "case.json"
        invalid = (
            ({"invoke": {"mode": "script", "target": "x", "extra": 1}}, "unsupported"),
            ({"invoke": {"mode": "script", "target": "x", "strict": 1}}, "boolean"),
            ({"invoke": {"mode": "script", "target": "x", "backend": 1}}, "string"),
            ({"invoke": {"mode": "script", "target": ""}}, "non-empty"),
        )
        for case, message in invalid:
            with self.subTest(message=message):
                with self.assertRaisesRegex(CaseConfigError, message):
                    parse_case_invocation(case, case_file, validate_paths=False)

    def test_validate_selected_cases_reports_unsupported_mode(self) -> None:
        case_dir = self.root / "unsupported"
        case_dir.mkdir()
        case_file = case_dir / "case.json"
        case_file.write_text(
            json.dumps({"invoke": {"mode": "unknown", "target": "fixture"}}),
            encoding="utf-8",
        )
        meta = collect_case_meta(case_file, self.root)

        self.assertEqual(
            validate_selected_cases([meta], DEFAULT_APP_CONFIG),
            ["unsupported: unsupported invoke.mode: 'unknown'"],
        )

    def test_suite_case_filter_selects_matching_case_path(self) -> None:
        suite_root = self.root / "suite"
        out_root = self.root / "out"
        for name in ("alpha", "beta"):
            case_dir = suite_root / name
            case_dir.mkdir(parents=True)
            (case_dir / "case.json").write_text(
                json.dumps(
                    {
                        "name": name.title(),
                        "invoke": {"mode": "scenario", "target": name},
                    }
                ),
                encoding="utf-8",
            )

        def result_for(
            _app: Path,
            case_file: Path,
            _case_id: str,
            _out_root: Path,
            _timeout: float,
            _app_config: RegressionAppConfig,
        ) -> CaseResult:
            return CaseResult(
                case_file.parent.name,
                case_file.parent,
                True,
                "ok",
                0,
                0.1,
                out_root / case_file.parent.name / "report.json",
            )

        with (
            mock.patch.object(runner, "run_case", side_effect=result_for) as run_case_mock,
            mock.patch.dict(os.environ, {"NO_COLOR": "1"}),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = runner.run_regression_suite(
                app=self.root / "app",
                suite_root=suite_root,
                out_root=out_root,
                control_path=suite_root / "case_control.json",
                app_config=DEFAULT_APP_CONFIG,
                default_timeout=30.0,
                case_filter="beta",
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_case_mock.call_count, 1)
        self.assertEqual(run_case_mock.call_args.args[1].parent.name, "beta")
        summary = json.loads((out_root / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["results"][0]["name"], "beta")

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
