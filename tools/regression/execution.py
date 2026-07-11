# Internal: regression case process execution and artifact lifecycle.

from __future__ import annotations

import shutil
import subprocess  # nosec B404
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import (
    CaseInvocation,
    CaseProcessResult,
    PreparedCase,
    RegressionAppConfig,
)

LOG_TAIL_BYTES = 64 * 1024


@dataclass(frozen=True)
class CaseExecutionServices:
    run_process: Callable[..., subprocess.CompletedProcess[Any]] = field(
        default_factory=lambda: subprocess.run
    )
    monotonic: Callable[[], float] = field(default_factory=lambda: time.monotonic)


def _as_text(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def _write_captured_tail_if_empty(path: Path, data: Any) -> None:
    if data is None or path.stat().st_size != 0:
        return
    encoded = _as_text(data).encode("utf-8", errors="replace")
    path.write_bytes(encoded[-LOG_TAIL_BYTES:])


def read_log_tail(path: Path, *, max_bytes: int = LOG_TAIL_BYTES) -> str:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - max_bytes))
        return stream.read(max_bytes).decode("utf-8", errors="replace")


def _format_command_tokens(
    tokens: Sequence[str],
    *,
    invocation: CaseInvocation,
    target_path: Path | None,
    report_path: Path,
) -> list[str]:
    result: list[str] = []
    backend_flag = f"--backend={invocation.backend}" if invocation.backend else ""
    strict_flag = "--strict" if invocation.strict else ""
    target_value = str(target_path) if target_path is not None else invocation.target
    values = {
        "target": target_value,
        "report": str(report_path),
        "backend": invocation.backend,
        "backend_flag": backend_flag,
        "strict_flag": strict_flag,
    }
    for token in tokens:
        value = token.format(**values)
        if value:
            result.append(value)
    return result


def execute_case_process(
    app: Path,
    prepared: PreparedCase,
    app_config: RegressionAppConfig,
    *,
    services: CaseExecutionServices | None = None,
) -> CaseProcessResult:
    services = services or CaseExecutionServices()
    if prepared.case_out_dir.exists():
        shutil.rmtree(prepared.case_out_dir)
    prepared.case_out_dir.mkdir(parents=True, exist_ok=True)

    command_tail = _format_command_tokens(
        app_config.mode_commands[prepared.invocation.mode],
        invocation=prepared.invocation,
        target_path=prepared.target_path,
        report_path=prepared.report_path,
    )
    command = (str(app), *command_tail)
    cwd = prepared.case_dir if prepared.target_path is not None else app.parent

    captured_stdout: Any = None
    captured_stderr: Any = None
    with (
        prepared.stdout_path.open("w", encoding="utf-8") as stdout_log,
        prepared.stderr_path.open("w", encoding="utf-8") as stderr_log,
    ):
        try:
            start = services.monotonic()
            process = services.run_process(
                list(command),
                cwd=cwd,
                text=True,
                stdout=stdout_log,
                stderr=stderr_log,
                timeout=prepared.timeout_sec,
                check=False,
            )
            duration_sec = services.monotonic() - start
            return_code = process.returncode
            captured_stdout = process.stdout
            captured_stderr = process.stderr
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            duration_sec = prepared.timeout_sec
            return_code = None
            captured_stdout = exc.stdout
            captured_stderr = exc.stderr
            timed_out = True

    # Test doubles and custom process services may return captured output instead
    # of writing to the supplied streams. Preserve only a bounded tail in that
    # compatibility path; the production subprocess path streams the full logs.
    _write_captured_tail_if_empty(prepared.stdout_path, captured_stdout)
    _write_captured_tail_if_empty(prepared.stderr_path, captured_stderr)
    return CaseProcessResult(
        command=command,
        cwd=cwd,
        timed_out=timed_out,
        return_code=return_code,
        duration_sec=duration_sec,
        stdout_path=prepared.stdout_path,
        stderr_path=prepared.stderr_path,
        stdout_tail=read_log_tail(prepared.stdout_path),
        stderr_tail=read_log_tail(prepared.stderr_path),
    )
