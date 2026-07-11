from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path, PurePosixPath
from typing import cast
from unittest import mock

from tools.regression.models import CaseResult
from tools.regression.reporting import (
    RegressionConsoleReporter,
    RegressionReportingServices,
    build_summary,
    write_junit,
    write_summary,
)


def posix_path(value: str) -> Path:
    return cast(Path, PurePosixPath(value))


class RegressionReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.lines: list[str] = []
        self.reporter = RegressionConsoleReporter(
            RegressionReportingServices(
                write_line=self.lines.append,
                color_enabled=lambda: False,
            )
        )
        self.passed = CaseResult(
            "通过案例",
            posix_path("cases/group/pass"),
            True,
            "ok",
            0,
            0.125,
            posix_path("artifacts/pass/report.json"),
        )
        self.failed = CaseResult(
            "Fail & <case>",
            posix_path("cases/group/fail"),
            False,
            "错误 & <reason>",
            7,
            1.25,
            posix_path("artifacts/fail/report.json"),
        )

    def test_console_output_golden_without_color(self) -> None:
        self.reporter.warn_no_cases()
        self.reporter.report_validation_errors(["a: invalid", "b: missing"])
        self.reporter.report_suite_start(
            app=posix_path("/app"),
            suite_root=posix_path("/cases"),
            selected_count=2,
            skipped_count=3,
            out_root=posix_path("/out"),
            control_path=posix_path("/cases/control.json"),
            jobs=0,
        )
        self.reporter.report_case_result(self.passed)
        self.reporter.report_case_result(self.failed)
        self.reporter.report_artifacts(
            posix_path("/out/summary.json"),
            posix_path("/out/junit.xml"),
        )
        self.reporter.report_final(build_summary([self.passed, self.failed]))
        self.reporter.report_final(build_summary([self.passed]))

        self.assertEqual(
            self.lines,
            [
                "[WARN] no cases found",
                "[ERROR] invalid case schema",
                "  - a: invalid",
                "  - b: missing",
                "App: /app",
                "Suite root: /cases",
                "Cases: 2",
                "Skipped: 3",
                "Artifacts: /out",
                "Control: /cases/control.json",
                "Jobs: 1",
                "[PASS: 0.1s] 通过案例 :: ok",
                "[FAIL: 1.2s] Fail & <case> :: 错误 & <reason>",
                "Summary: /out/summary.json",
                "JUnit: /out/junit.xml",
                "Cases failed (1/2)",
                "All cases passed (1/1)",
            ],
        )

    def test_console_color_and_default_no_color_behavior(self) -> None:
        colored: list[str] = []
        reporter = RegressionConsoleReporter(
            RegressionReportingServices(colored.append, lambda: True)
        )
        reporter.report_case_result(self.passed)
        reporter.report_case_result(self.failed)
        self.assertEqual(colored[0], "[\x1b[32m\x1b[1mPASS\x1b[0m: 0.1s] 通过案例 :: ok")
        self.assertEqual(
            colored[1],
            "[\x1b[31m\x1b[1mFAIL\x1b[0m: 1.2s] Fail & <case> :: 错误 & <reason>",
        )

        with (
            mock.patch.dict(os.environ, {"NO_COLOR": "1"}),
            mock.patch("sys.stdout.isatty", return_value=True),
        ):
            services = RegressionReportingServices()
            self.assertFalse(services.color_enabled())

    def test_default_reporter_resolves_stdout_and_color_helper_at_call_time(self) -> None:
        reporter = RegressionConsoleReporter()
        stdout = io.StringIO()
        with (
            mock.patch(
                "tools.regression.reporting._default_color_enabled",
                return_value=True,
            ),
            redirect_stdout(stdout),
        ):
            reporter.warn_no_cases()

        self.assertEqual(
            stdout.getvalue(),
            "\x1b[33m\x1b[1m[WARN] no cases found\x1b[0m\n",
        )

    def test_summary_json_complete_byte_golden(self) -> None:
        summary = build_summary([self.passed, self.failed])
        path = self.root / "summary.json"
        write_summary(summary, path)

        expected = (
            "{\n"
            '  "total": 2,\n'
            '  "passed": 1,\n'
            '  "failed": 1,\n'
            '  "results": [\n'
            "    {\n"
            '      "name": "通过案例",\n'
            '      "case_dir": "cases/group/pass",\n'
            '      "passed": true,\n'
            '      "reason": "ok",\n'
            '      "exit_code": 0,\n'
            '      "duration_sec": 0.125,\n'
            '      "report": "artifacts/pass/report.json"\n'
            "    },\n"
            "    {\n"
            '      "name": "Fail & <case>",\n'
            '      "case_dir": "cases/group/fail",\n'
            '      "passed": false,\n'
            '      "reason": "错误 & <reason>",\n'
            '      "exit_code": 7,\n'
            '      "duration_sec": 1.25,\n'
            '      "report": "artifacts/fail/report.json"\n'
            "    }\n"
            "  ]\n"
            "}\n"
        )
        self.assertEqual(path.read_text(encoding="utf-8"), expected)
        self.assertEqual(json.loads(expected), summary)

    def test_junit_xml_structure_order_escaping_and_byte_golden(self) -> None:
        path = self.root / "nested" / "junit.xml"
        write_junit([self.passed, self.failed], path)
        payload = path.read_bytes()
        expected = (
            b"<?xml version='1.0' encoding='utf-8'?>\n"
            b'<testsuites><testsuite name="cppkit_regression" tests="2" failures="1" '
            b'errors="0" skipped="0" time="1.375">'
            b'<testcase classname="group" name="\xe9\x80\x9a\xe8\xbf\x87\xe6\xa1\x88\xe4\xbe\x8b" time="0.125" />'
            b'<testcase classname="group" name="Fail &amp; &lt;case&gt;" time="1.250">'
            b'<failure message="\xe9\x94\x99\xe8\xaf\xaf &amp; &lt;reason&gt;">'
            b"exit_code=7, report=artifacts/fail/report.json"
            b"</failure></testcase></testsuite></testsuites>"
        )
        self.assertEqual(payload, expected)

        root = ET.fromstring(payload)
        suite = root.find("testsuite")
        assert suite is not None
        self.assertEqual(
            suite.attrib,
            {
                "name": "cppkit_regression",
                "tests": "2",
                "failures": "1",
                "errors": "0",
                "skipped": "0",
                "time": "1.375",
            },
        )
        cases = suite.findall("testcase")
        self.assertEqual([case.attrib["name"] for case in cases], ["通过案例", "Fail & <case>"])
        failure = cases[1].find("failure")
        assert failure is not None
        self.assertEqual(failure.attrib["message"], "错误 & <reason>")
        self.assertEqual(
            failure.text,
            "exit_code=7, report=artifacts/fail/report.json",
        )

    def test_junit_custom_suite_name(self) -> None:
        path = self.root / "custom.xml"
        write_junit([self.passed], path, suite_name="custom-suite")
        suite = ET.parse(path).getroot().find("testsuite")
        assert suite is not None
        self.assertEqual(suite.attrib["name"], "custom-suite")


if __name__ == "__main__":
    unittest.main()
