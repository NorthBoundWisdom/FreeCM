# Internal: shared execution boundary for FreeCM command adapters.

from __future__ import annotations

import subprocess  # nosec B404
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

CLI_DATA_ERRORS = (
    FileNotFoundError,
    RuntimeError,
    ValueError,
)
CLI_PROCESS_ERRORS = (
    *CLI_DATA_ERRORS,
    subprocess.CalledProcessError,
)
CLI_INIT_ERRORS = (
    FileExistsError,
    *CLI_PROCESS_ERRORS,
)


def run_cli_action(
    action: Callable[[], T],
    render: Callable[[T], int],
    *,
    error_types: tuple[type[Exception], ...],
    report_error: Callable[[BaseException], None],
) -> int:
    try:
        result = action()
    except error_types as error:
        report_error(error)
        return 1
    return render(result)


__all__ = (
    "CLI_DATA_ERRORS",
    "CLI_INIT_ERRORS",
    "CLI_PROCESS_ERRORS",
    "run_cli_action",
)
