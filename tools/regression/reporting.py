# Internal: regression console, summary JSON, and JUnit rendering.

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET  # nosec B405
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import CaseResult


class _Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"


def _default_color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


@dataclass(frozen=True)
class RegressionReportingServices:
    write_line: Callable[[str], None] = field(default_factory=lambda: lambda line: print(line))
    color_enabled: Callable[[], bool] = field(
        default_factory=lambda: lambda: _default_color_enabled()
    )


class RegressionConsoleReporter:
    def __init__(
        self,
        services: RegressionReportingServices | None = None,
    ) -> None:
        self.services = services or RegressionReportingServices()

    def _paint(self, text: str, *styles: str) -> str:
        if not self.services.color_enabled() or not styles:
            return text
        return "".join(styles) + text + _Color.RESET

    def _info(self, label: str, value: str) -> None:
        self.services.write_line(f"{self._paint(label + ':', _Color.CYAN, _Color.BOLD)} {value}")

    def warn_no_cases(self) -> None:
        self.services.write_line(self._paint("[WARN] no cases found", _Color.YELLOW, _Color.BOLD))

    def report_validation_errors(self, errors: Sequence[str]) -> None:
        self.services.write_line(
            self._paint("[ERROR] invalid case schema", _Color.RED, _Color.BOLD)
        )
        for error in errors:
            self.services.write_line(self._paint(f"  - {error}", _Color.RED))

    def report_suite_start(
        self,
        *,
        app: Path,
        suite_root: Path,
        selected_count: int,
        skipped_count: int,
        out_root: Path,
        control_path: Path,
        jobs: int,
    ) -> None:
        self._info("App", str(app))
        self._info("Suite root", str(suite_root))
        self._info("Cases", str(selected_count))
        self._info("Skipped", str(skipped_count))
        self._info("Artifacts", str(out_root))
        self._info("Control", str(control_path))
        self._info("Jobs", str(max(1, int(jobs))))

    def report_case_result(self, result: CaseResult) -> None:
        status = "PASS" if result.passed else "FAIL"
        color = _Color.GREEN if result.passed else _Color.RED
        self.services.write_line(
            f"[{self._paint(status, color, _Color.BOLD)}: "
            f"{result.duration_sec:.1f}s] {result.name} :: {result.reason}"
        )

    def report_artifacts(self, summary_path: Path, junit_path: Path) -> None:
        self._info("Summary", str(summary_path))
        self._info("JUnit", str(junit_path))

    def report_final(self, summary: Mapping[str, Any]) -> None:
        if summary["failed"] == 0:
            self.services.write_line(
                self._paint(
                    f"All cases passed ({summary['passed']}/{summary['total']})",
                    _Color.GREEN,
                    _Color.BOLD,
                )
            )
            return
        self.services.write_line(
            self._paint(
                f"Cases failed ({summary['failed']}/{summary['total']})",
                _Color.RED,
                _Color.BOLD,
            )
        )


def build_summary(results: Sequence[CaseResult]) -> dict[str, Any]:
    return {
        "total": len(results),
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
        "results": [
            {
                "name": result.name,
                "case_dir": str(result.case_dir),
                "passed": result.passed,
                "reason": result.reason,
                "exit_code": result.exit_code,
                "duration_sec": result.duration_sec,
                "report": str(result.report_path),
            }
            for result in results
        ],
    }


def write_summary(summary: Mapping[str, Any], out_path: Path) -> None:
    out_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_junit(
    results: Sequence[CaseResult],
    out_path: Path,
    *,
    suite_name: str = "cppkit_regression",
) -> None:
    tests = len(results)
    failures = sum(1 for result in results if not result.passed)
    duration = sum(result.duration_sec for result in results)
    suite = ET.Element(
        "testsuite",
        name=suite_name,
        tests=str(tests),
        failures=str(failures),
        errors="0",
        skipped="0",
        time=f"{duration:.3f}",
    )
    for result in results:
        case = ET.SubElement(
            suite,
            "testcase",
            classname=str(result.case_dir.parent.name),
            name=result.name,
            time=f"{result.duration_sec:.3f}",
        )
        if not result.passed:
            failure = ET.SubElement(case, "failure", message=result.reason)
            failure.text = f"exit_code={result.exit_code}, report={result.report_path}"
    suites = ET.Element("testsuites")
    suites.append(suite)
    tree = ET.ElementTree(suites)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
