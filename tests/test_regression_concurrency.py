from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tools.regression import runner
from tools.regression.cases import DEFAULT_APP_CONFIG
from tools.regression.models import CaseResult
from tools.regression.reporting import RegressionConsoleReporter


class RegressionConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.suite_root = self.root / "suite"
        self.out_root = self.root / "out"
        for name in ("a", "b"):
            case_dir = self.suite_root / name
            case_dir.mkdir(parents=True)
            (case_dir / "case.json").write_text(
                json.dumps(
                    {
                        "name": name.upper(),
                        "invoke": {"mode": "scenario", "target": name},
                    }
                ),
                encoding="utf-8",
            )

    def _result(self, case_file: Path, *, passed: bool = True, reason: str = "ok") -> CaseResult:
        return CaseResult(
            case_file.parent.name.upper(),
            case_file.parent,
            passed,
            reason,
            0 if passed else None,
            0.1,
            self.out_root / case_file.parent.name / "report.json",
        )

    def _run(self, *, jobs: int) -> int:
        return runner.run_regression_suite(
            app=self.root / "app",
            suite_root=self.suite_root,
            out_root=self.out_root,
            control_path=self.suite_root / "case_control.json",
            app_config=DEFAULT_APP_CONFIG,
            default_timeout=30.0,
            jobs=jobs,
        )

    def test_parallel_completion_order_reaches_console_summary_and_junit(self) -> None:
        release_a = threading.Event()

        def run_case(
            _app: Path,
            case_file: Path,
            *_args: object,
        ) -> CaseResult:
            if case_file.parent.name == "a":
                if not release_a.wait(timeout=5):
                    raise RuntimeError("timed out waiting to release A")
            else:
                timer = threading.Timer(0.1, release_a.set)
                timer.start()
            return self._result(case_file)

        stdout = io.StringIO()
        with (
            mock.patch.object(runner, "run_case", side_effect=run_case),
            mock.patch.dict(os.environ, {"NO_COLOR": "1"}),
            redirect_stdout(stdout),
        ):
            exit_code = self._run(jobs=2)

        self.assertEqual(exit_code, 0)
        case_lines = [line for line in stdout.getvalue().splitlines() if line.startswith("[PASS")]
        self.assertEqual(
            [line.split("] ", 1)[1].split(" ::", 1)[0] for line in case_lines], ["B", "A"]
        )
        summary = json.loads((self.out_root / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual([result["name"] for result in summary["results"]], ["B", "A"])
        suite = ET.parse(self.out_root / "junit.xml").getroot().find("testsuite")
        assert suite is not None
        self.assertEqual(
            [case.attrib["name"] for case in suite.findall("testcase")],
            ["B", "A"],
        )

    def test_parallel_peak_is_bounded_by_jobs(self) -> None:
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        active = 0
        peak = 0

        def run_case(
            _app: Path,
            case_file: Path,
            *_args: object,
        ) -> CaseResult:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait(timeout=5)
            with lock:
                active -= 1
            return self._result(case_file)

        with (
            mock.patch.object(runner, "run_case", side_effect=run_case),
            mock.patch.dict(os.environ, {"NO_COLOR": "1"}),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(self._run(jobs=2), 0)
        self.assertEqual(peak, 2)

    def test_parallel_exception_becomes_result_but_sequential_propagates(self) -> None:
        def fail(
            _app: Path,
            case_file: Path,
            *_args: object,
        ) -> CaseResult:
            if case_file.parent.name == "a":
                raise RuntimeError("worker failed")
            return self._result(case_file)

        with (
            mock.patch.object(runner, "run_case", side_effect=fail),
            mock.patch.dict(os.environ, {"NO_COLOR": "1"}),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(self._run(jobs=2), 1)
        summary = json.loads((self.out_root / "summary.json").read_text(encoding="utf-8"))
        failed = next(result for result in summary["results"] if not result["passed"])
        self.assertEqual(failed["reason"], "runner exception: worker failed")
        self.assertIsNone(failed["exit_code"])
        self.assertEqual(failed["duration_sec"], 0.0)

        sequential_out = self.root / "sequential-out"
        self.out_root = sequential_out
        with (
            mock.patch.object(runner, "run_case", side_effect=RuntimeError("worker failed")),
            mock.patch.dict(os.environ, {"NO_COLOR": "1"}),
            redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(RuntimeError, "worker failed"),
        ):
            self._run(jobs=1)
        self.assertFalse((sequential_out / "summary.json").exists())
        self.assertFalse((sequential_out / "junit.xml").exists())

    def test_parallel_base_exceptions_propagate(self) -> None:
        for error in (KeyboardInterrupt(), SystemExit(4)):
            with self.subTest(error=type(error).__name__):
                out_root = self.root / type(error).__name__
                self.out_root = out_root
                with (
                    mock.patch.object(runner, "run_case", side_effect=error),
                    mock.patch.dict(os.environ, {"NO_COLOR": "1"}),
                    redirect_stdout(io.StringIO()),
                    self.assertRaises(type(error)),
                ):
                    self._run(jobs=2)
                self.assertFalse((out_root / "summary.json").exists())

    def test_reporter_calls_stay_on_suite_thread(self) -> None:
        suite_thread = threading.get_ident()
        call_threads: list[int] = []

        class RecordingReporter(RegressionConsoleReporter):
            def report_case_result(self, result: CaseResult) -> None:
                call_threads.append(threading.get_ident())
                super().report_case_result(result)

        with (
            mock.patch.object(runner, "RegressionConsoleReporter", RecordingReporter),
            mock.patch.object(
                runner,
                "run_case",
                side_effect=lambda _app, case_file, *_: self._result(case_file),
            ),
            mock.patch.dict(os.environ, {"NO_COLOR": "1"}),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(self._run(jobs=2), 0)
        self.assertEqual(call_threads, [suite_thread, suite_thread])


if __name__ == "__main__":
    unittest.main()
