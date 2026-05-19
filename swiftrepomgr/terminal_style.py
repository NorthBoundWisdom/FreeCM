from __future__ import annotations

from typing import Any, Iterable

from depsfixture.terminal_style import (
    ANSI_BLUE,
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_GREEN,
    ANSI_RED,
    ANSI_RESET,
    ANSI_YELLOW,
    MODE_COLORS,
    MODE_LABELS,
    _stderr_supports_color,
    _stdout_supports_color,
    format_dependency_commit_change_lines as _format_dependency_commit_change_lines,
    format_dependency_resolution_lines as _format_dependency_resolution_lines,
    format_status_line,
)


def stderr_supports_color() -> bool:
    return _stderr_supports_color()


def stdout_supports_color() -> bool:
    return _stdout_supports_color()


def format_dependency_resolution_lines(
    resolutions: Iterable[Any],
    *,
    use_color: bool,
) -> list[str]:
    return _format_dependency_resolution_lines(tuple(resolutions), use_color=use_color)


def format_dependency_commit_change_lines(
    changes: Iterable[Any],
    *,
    use_color: bool,
) -> list[str]:
    return _format_dependency_commit_change_lines(tuple(changes), use_color=use_color)


__all__ = (
    "ANSI_BLUE",
    "ANSI_BOLD",
    "ANSI_CYAN",
    "ANSI_DIM",
    "ANSI_GREEN",
    "ANSI_RED",
    "ANSI_RESET",
    "ANSI_YELLOW",
    "MODE_COLORS",
    "MODE_LABELS",
    "format_dependency_commit_change_lines",
    "format_dependency_resolution_lines",
    "format_status_line",
    "stderr_supports_color",
    "stdout_supports_color",
)
