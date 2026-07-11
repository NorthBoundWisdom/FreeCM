from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from tools.regression import assertions, execution, runner
from tools.regression.cases import DEFAULT_APP_CONFIG, prepare_case
from tools.regression.execution import (
    LOG_TAIL_BYTES,
    CaseExecutionServices,
    execute_case_process,
    read_log_tail,
)
from tools.regression.models import (
    CaseInvocation,
    CaseProcessResult,
    CaseResult,
    PreparedCase,
    RegressionAppConfig,
)


class RegressionExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.case_dir = self.root / "suite" / "group" / "case"
        self.case_dir.mkdir(parents=True)
        self.case_file = self.case_dir / "case.json"
        self.out_root = self.root / "out"

    def _write_case(self, data: Any) -> None:
        self.case_file.write_text(json.dumps(data), encoding="utf-8")

    def _prepared(
        self,
        *,
        invocation: CaseInvocation | None = None,
        assert_config: dict[str, Any] | None = None,
        expected_outcome: str = "pass",
        timeout_sec: float = 3.0,
        target_path: Path | None = None,
    ) -> PreparedCase:
        case_out_dir = self.out_root / "group__case"
        return PreparedCase(
            name="Sample",
            case_file=self.case_file,
            case_dir=self.case_dir,
            case_id="group/case",
            invocation=invocation or CaseInvocation("scenario", "sample", False, ""),
            target_path=target_path,
            timeout_sec=timeout_sec,
            assert_config=dict(assert_config or {}),
            expected_outcome=expected_outcome,
            case_out_dir=case_out_dir,
            report_path=case_out_dir / "report.json",
            stdout_path=case_out_dir / "stdout.log",
            stderr_path=case_out_dir / "stderr.log",
        )

    def _process(
        self,
        prepared: PreparedCase,
        *,
        timed_out: bool = False,
        return_code: int | None = 0,
        duration_sec: float = 0.5,
    ) -> CaseProcessResult:
        return CaseProcessResult(
            command=("app",),
            cwd=prepared.case_dir,
            timed_out=timed_out,
            return_code=return_code,
            duration_sec=duration_sec,
            stdout_path=prepared.stdout_path,
            stderr_path=prepared.stderr_path,
        )

    def test_prepare_case_preserves_preflight_failure_paths_and_order(self) -> None:
        cases = (
            (
                {"invoke": {"mode": "unknown", "target": "x"}},
                "unsupported invoke.mode: unknown",
                self.out_root / "unknown_report.json",
            ),
            (
                {
                    "invoke": {"mode": "scenario", "target": "x"},
                    "assert": [],
                },
                "assert must be an object",
                self.out_root / "unknown_report.json",
            ),
            (
                {
                    "invoke": {"mode": "scenario", "target": "x"},
                    "assert": {"outcome": "invalid"},
                },
                "invalid assert.outcome: invalid",
                self.out_root / "group__case" / "report.json",
            ),
        )
        for data, reason, report_path in cases:
            with self.subTest(reason=reason):
                self._write_case(data)
                result = prepare_case(
                    self.case_file,
                    "group/case",
                    self.out_root,
                    30.0,
                    DEFAULT_APP_CONFIG,
                )
                self.assertIsInstance(result, CaseResult)
                assert isinstance(result, CaseResult)
                self.assertEqual(result.reason, reason)
                self.assertEqual(result.report_path, report_path)
                self.assertIsNone(result.exit_code)
                self.assertEqual(result.duration_sec, 0.0)
                self.assertFalse((self.out_root / "group__case").exists())

        self._write_case(
            {
                "invoke": {"mode": "scenario", "target": "x"},
                "timeout_sec": "invalid",
                "assert": [],
            }
        )
        with self.assertRaises(ValueError):
            prepare_case(
                self.case_file,
                "group/case",
                self.out_root,
                30.0,
                DEFAULT_APP_CONFIG,
            )

    def test_prepare_case_resolves_target_and_all_artifact_fields(self) -> None:
        target = self.case_dir / "fixture.txt"
        target.write_text("fixture", encoding="utf-8")
        self._write_case(
            {
                "name": "Prepared",
                "invoke": {
                    "mode": "script",
                    "target": "fixture.txt",
                    "strict": True,
                },
                "timeout_sec": 7,
                "assert": {"outcome": "assert_fail"},
            }
        )

        prepared = prepare_case(
            self.case_file,
            "group/case",
            self.out_root,
            30.0,
            DEFAULT_APP_CONFIG,
        )

        self.assertIsInstance(prepared, PreparedCase)
        assert isinstance(prepared, PreparedCase)
        self.assertEqual(prepared.name, "Prepared")
        self.assertEqual(prepared.target_path, target.resolve())
        self.assertEqual(prepared.timeout_sec, 7.0)
        self.assertEqual(prepared.expected_outcome, "assert_fail")
        self.assertEqual(prepared.case_out_dir, self.out_root / "group__case")
        self.assertEqual(prepared.stdout_path.name, "stdout.log")
        self.assertEqual(prepared.stderr_path.name, "stderr.log")

    def test_execute_case_replaces_stale_artifacts_and_writes_logs(self) -> None:
        target = self.case_dir / "fixture.txt"
        target.write_text("fixture", encoding="utf-8")
        prepared = self._prepared(
            invocation=CaseInvocation("script", "fixture.txt", True, ""),
            target_path=target.resolve(),
        )
        prepared.case_out_dir.mkdir(parents=True)
        stale = prepared.case_out_dir / "stale.txt"
        stale.write_text("stale", encoding="utf-8")
        process_runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [],
                4,
                stdout="standard output\n",
                stderr="standard error\n",
            )
        )
        clock = mock.Mock(side_effect=(10.0, 10.25))

        result = execute_case_process(
            self.root / "bin" / "app",
            prepared,
            DEFAULT_APP_CONFIG,
            services=CaseExecutionServices(process_runner, clock),
        )

        self.assertFalse(stale.exists())
        self.assertEqual(result.return_code, 4)
        self.assertEqual(result.duration_sec, 0.25)
        self.assertEqual(result.cwd, self.case_dir)
        self.assertTrue(any(str(target.resolve()) in token for token in result.command))
        self.assertIn("--strict", result.command)
        self.assertEqual(prepared.stdout_path.read_text(), "standard output\n")
        self.assertEqual(prepared.stderr_path.read_text(), "standard error\n")
        process_runner.assert_called_once()
        call = process_runner.call_args
        self.assertEqual(call.args, (list(result.command),))
        self.assertEqual(call.kwargs["cwd"], self.case_dir)
        self.assertTrue(call.kwargs["text"])
        self.assertEqual(call.kwargs["timeout"], 3.0)
        self.assertFalse(call.kwargs["check"])
        self.assertEqual(Path(call.kwargs["stdout"].name), prepared.stdout_path)
        self.assertEqual(Path(call.kwargs["stderr"].name), prepared.stderr_path)
        self.assertNotIn("capture_output", call.kwargs)

    def test_execution_services_capture_helpers_per_construction(self) -> None:
        first_runner = mock.Mock()
        second_runner = mock.Mock()
        with mock.patch.object(execution.subprocess, "run", first_runner):
            first = CaseExecutionServices()
        with mock.patch.object(execution.subprocess, "run", second_runner):
            second = CaseExecutionServices()

        self.assertIs(first.run_process, first_runner)
        self.assertIs(second.run_process, second_runner)
        self.assertIsNot(first.run_process, second.run_process)

    def test_execute_viewer_uses_case_cwd_and_backend_flag(self) -> None:
        target = self.case_dir / "viewer.json"
        target.write_text("{}", encoding="utf-8")
        prepared = self._prepared(
            invocation=CaseInvocation("viewer2d", "viewer.json", False, "metal"),
            target_path=target.resolve(),
        )
        process_runner = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")
        )

        result = execute_case_process(
            self.root / "app",
            prepared,
            DEFAULT_APP_CONFIG,
            services=CaseExecutionServices(
                process_runner,
                mock.Mock(side_effect=(1.0, 1.1)),
            ),
        )

        self.assertEqual(result.cwd, self.case_dir)
        self.assertIn("--backend=metal", result.command)

    def test_execute_scenario_uses_app_parent_and_drops_empty_flags(self) -> None:
        prepared = self._prepared()
        app = self.root / "bin" / "app"
        runner_config = RegressionAppConfig(
            ("{app}",),
            {"scenario": ("run", "{strict_flag}", "{backend_flag}", "{target}")},
            (),
        )
        process_runner = mock.Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")
        )

        result = execute_case_process(
            app,
            prepared,
            runner_config,
            services=CaseExecutionServices(
                process_runner,
                mock.Mock(side_effect=(1.0, 2.0)),
            ),
        )

        self.assertEqual(result.command, (str(app), "run", "sample"))
        self.assertEqual(result.cwd, app.parent)
        self.assertTrue(prepared.stdout_path.is_file())
        self.assertTrue(prepared.stderr_path.is_file())

    def test_execute_timeout_preserves_bytes_and_string_partial_output(self) -> None:
        prepared = self._prepared(timeout_sec=1.5)
        timeout = subprocess.TimeoutExpired(
            ["app"],
            1.5,
            output=b"partial \xff",
            stderr="timed out\n",
        )
        process_runner = mock.Mock(side_effect=timeout)

        result = execute_case_process(
            self.root / "app",
            prepared,
            DEFAULT_APP_CONFIG,
            services=CaseExecutionServices(process_runner, mock.Mock(return_value=5.0)),
        )

        self.assertTrue(result.timed_out)
        self.assertIsNone(result.return_code)
        self.assertEqual(result.duration_sec, 1.5)
        self.assertEqual(prepared.stdout_path.read_text(), "partial �")
        self.assertEqual(prepared.stderr_path.read_text(), "timed out\n")

    def test_execution_errors_propagate_and_formatting_happens_after_mkdir(self) -> None:
        prepared = self._prepared()
        invalid_config = RegressionAppConfig(
            ("{app}",),
            {"scenario": ("{missing}",)},
            (),
        )
        with self.assertRaises(KeyError):
            execute_case_process(self.root / "app", prepared, invalid_config)
        self.assertTrue(prepared.case_out_dir.is_dir())

        with self.assertRaisesRegex(OSError, "cannot start"):
            execute_case_process(
                self.root / "app",
                prepared,
                DEFAULT_APP_CONFIG,
                services=CaseExecutionServices(
                    mock.Mock(side_effect=OSError("cannot start")),
                    mock.Mock(return_value=0.0),
                ),
            )

    def test_execute_real_python_process_passes_report_path_without_shell(self) -> None:
        prepared = self._prepared()
        code = (
            "import json, pathlib, sys; "
            "print('stdout-text'); "
            "print('stderr-text', file=sys.stderr); "
            "pathlib.Path(sys.argv[1]).write_text("
            "json.dumps(dict(scenario_result=dict(ok=True))))"
        )
        config = RegressionAppConfig(
            ("{app}",),
            {"scenario": ("-c", code, "{report}")},
            (),
        )

        result = execute_case_process(Path(sys.executable), prepared, config)

        self.assertEqual(result.return_code, 0)
        self.assertEqual(prepared.stdout_path.read_text(), "stdout-text\n")
        self.assertEqual(prepared.stderr_path.read_text(), "stderr-text\n")
        self.assertEqual(
            json.loads(prepared.report_path.read_text()),
            {"scenario_result": {"ok": True}},
        )

    def test_execute_real_process_streams_full_logs_and_keeps_bounded_tails(self) -> None:
        prepared = self._prepared()
        stdout_text = "a" * (LOG_TAIL_BYTES + 257)
        stderr_text = "b" * (LOG_TAIL_BYTES + 129)
        code = (
            "import sys; "
            f"sys.stdout.write({stdout_text!r}); "
            f"sys.stderr.write({stderr_text!r})"
        )
        config = RegressionAppConfig(
            ("{app}",),
            {"scenario": ("-c", code)},
            (),
        )

        result = execute_case_process(Path(sys.executable), prepared, config)

        self.assertEqual(prepared.stdout_path.stat().st_size, len(stdout_text))
        self.assertEqual(prepared.stderr_path.stat().st_size, len(stderr_text))
        self.assertEqual(result.stdout_tail, "a" * LOG_TAIL_BYTES)
        self.assertEqual(result.stderr_tail, "b" * LOG_TAIL_BYTES)
        self.assertEqual(read_log_tail(prepared.stdout_path, max_bytes=257), "a" * 257)
        with self.assertRaisesRegex(ValueError, "max_bytes"):
            read_log_tail(prepared.stdout_path, max_bytes=0)

    def test_assertion_outcomes_and_failure_reasons(self) -> None:
        outcome_cases = (
            ("pass", False, 0, None),
            ("assert_fail", False, 0, {"script_result": {"has_error": True}}),
            ("timeout", True, None, None),
            ("scenario_fail", False, 0, {"scenario_result": {"ok": False}}),
            ("process_crash", False, 9, None),
        )
        for expected, timed_out, code, report in outcome_cases:
            with self.subTest(expected=expected):
                mode = "scenario" if expected == "scenario_fail" else "script"
                prepared = self._prepared(
                    invocation=CaseInvocation(mode, "x", False, ""),
                    expected_outcome=expected,
                )
                result = assertions.evaluate_case_result(
                    prepared,
                    self._process(prepared, timed_out=timed_out, return_code=code),
                    report,
                )
                self.assertTrue(result.passed)
                self.assertEqual(result.reason, "ok")

        prepared = self._prepared(expected_outcome="timeout")
        mismatch = assertions.evaluate_case_result(
            prepared,
            self._process(prepared),
            None,
        )
        self.assertEqual(
            mismatch.reason,
            "outcome mismatch: expected timeout, got pass",
        )

    def test_assertions_preserve_duration_report_exit_path_and_relation_order(self) -> None:
        prepared = self._prepared(assert_config={"max_duration_sec": 0.25})
        result = assertions.evaluate_case_result(
            prepared,
            self._process(prepared, duration_sec=0.5),
            {"value": 1},
        )
        self.assertEqual(result.reason, "duration exceeded: 0.500s > 0.250s")

        prepared = self._prepared(assert_config={"report_paths": {"value": 1}})
        missing = assertions.evaluate_case_result(
            prepared,
            self._process(prepared),
            None,
        )
        self.assertEqual(missing.reason, "regression report not generated")

        prepared = self._prepared(assert_config={"exit_code": 2})
        exit_mismatch = assertions.evaluate_case_result(
            prepared,
            self._process(prepared),
            {"value": 1},
        )
        self.assertEqual(exit_mismatch.reason, "exit_code mismatch: expected 2, got 0")

        path_cases: tuple[tuple[dict[str, Any], str], ...] = (
            ({"report_paths": {"value": 2}}, "assert failed at value: expected 2, got 1"),
            (
                {"report_paths": {"missing": 1}},
                "resolve path failed (missing): 'missing'",
            ),
            (
                {"report_relations": [{"left": "left", "right": "right", "op": "bad"}]},
                "unsupported relation op: bad",
            ),
            (
                {"report_relations": [{"left": "left", "right": "right", "op": "eq"}]},
                "relation failed: left eq right (1 vs 2)",
            ),
            (
                {"report_relations": [{"left": "left", "right": ""}]},
                "invalid report_relations entry",
            ),
        )
        for config, reason in path_cases:
            with self.subTest(reason=reason):
                prepared = self._prepared(assert_config=config)
                result = assertions.evaluate_case_result(
                    prepared,
                    self._process(prepared),
                    {"value": 1, "left": 1, "right": 2},
                )
                self.assertEqual(result.reason, reason)

        prepared = self._prepared(
            assert_config={
                "report_paths": {"value": 1},
                "report_relations": [{"left": "left", "right": "right", "op": "ne"}],
            }
        )
        success = assertions.evaluate_case_result(
            prepared,
            self._process(prepared),
            {"value": 1, "left": 1, "right": 2},
        )
        self.assertTrue(success.passed)

    def test_load_case_report_propagates_invalid_json(self) -> None:
        report_path = self.root / "report.json"
        self.assertIsNone(assertions.load_case_report(report_path))
        report_path.write_text("not json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            assertions.load_case_report(report_path)

    def test_run_case_is_thin_ordered_orchestration(self) -> None:
        prepared = self._prepared()
        process = self._process(prepared)
        evaluated = CaseResult(
            "Sample",
            self.case_dir,
            True,
            "ok",
            0,
            0.5,
            prepared.report_path,
        )
        events: list[str] = []

        def prepare(*_: Any) -> PreparedCase:
            events.append("prepare")
            return prepared

        def execute(*_: Any) -> CaseProcessResult:
            events.append("execute")
            return process

        def load(*_: Any) -> dict[str, int]:
            events.append("load")
            return {"value": 1}

        def evaluate(*_: Any) -> CaseResult:
            events.append("evaluate")
            return evaluated

        with (
            mock.patch.object(
                runner,
                "prepare_case",
                side_effect=prepare,
            ),
            mock.patch.object(
                runner,
                "execute_case_process",
                side_effect=execute,
            ),
            mock.patch.object(
                runner,
                "load_case_report",
                side_effect=load,
            ),
            mock.patch.object(
                runner,
                "evaluate_case_result",
                side_effect=evaluate,
            ),
        ):
            result = runner.run_case(
                self.root / "app",
                self.case_file,
                "group/case",
                self.out_root,
                30.0,
            )
        self.assertIs(result, evaluated)
        self.assertEqual(events, ["prepare", "execute", "load", "evaluate"])

        preflight = CaseResult(
            "Sample",
            self.case_dir,
            False,
            "preflight",
            None,
            0.0,
            self.out_root / "unknown_report.json",
        )
        with (
            mock.patch.object(runner, "prepare_case", return_value=preflight),
            mock.patch.object(runner, "execute_case_process") as execute,
            mock.patch.object(runner, "load_case_report") as load,
            mock.patch.object(runner, "evaluate_case_result") as evaluate,
        ):
            result = runner.run_case(
                self.root / "app",
                self.case_file,
                "group/case",
                self.out_root,
                30.0,
            )
        self.assertIs(result, preflight)
        execute.assert_not_called()
        load.assert_not_called()
        evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
