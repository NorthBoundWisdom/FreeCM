from __future__ import annotations

import os
import sys
from typing import Any, Sequence


ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_RED = "\033[31m"
ANSI_YELLOW = "\033[33m"
ANSI_GREEN = "\033[32m"
ANSI_BLUE = "\033[34m"
ANSI_CYAN = "\033[36m"

MODE_LABELS = {
    "pinned": "pin",
    "latest": "latest",
    "manual": "manual",
}
MODE_COLORS = {
    "pinned": ANSI_YELLOW,
    "latest": ANSI_BLUE,
    "manual": ANSI_GREEN,
}


def _stdout_supports_color() -> bool:
    return _stream_supports_color(sys.stdout)


def _stderr_supports_color() -> bool:
    return _stream_supports_color(sys.stderr)


def stdout_supports_color() -> bool:
    return _stdout_supports_color()


def stderr_supports_color() -> bool:
    return _stderr_supports_color()


def _stream_supports_color(stream: Any) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return bool(stream.isatty())


def _style(text: str, *codes: str, use_color: bool) -> str:
    if not use_color or not codes:
        return text
    return f"{''.join(codes)}{text}{ANSI_RESET}"


STATUS_COLORS = {
    "info": ANSI_CYAN,
    "ok": ANSI_GREEN,
    "warn": ANSI_YELLOW,
    "error": ANSI_RED,
}


def format_status_line(
    action: str,
    message: str,
    *,
    level: str = "info",
    use_color: bool = False,
) -> str:
    color = STATUS_COLORS.get(level, ANSI_CYAN)
    prefix = _style("[freecm]", ANSI_DIM, use_color=use_color)
    action_text = _style(action, ANSI_BOLD, color, use_color=use_color)
    return f"{prefix} {action_text}: {message}"


def print_status(action: str, message: str, *, level: str = "info") -> None:
    print(
        format_status_line(
            action,
            message,
            level=level,
            use_color=stdout_supports_color(),
        )
    )


def print_error(error: BaseException) -> None:
    print(
        format_status_line(
            "error",
            str(error),
            level="error",
            use_color=stderr_supports_color(),
        ),
        file=sys.stderr,
    )


def format_dependency_resolution_lines(
    resolutions: Sequence[Any],
    *,
    use_color: bool = False,
) -> list[str]:
    lines = ["resolved direct dependencies:"]
    for resolution in resolutions:
        dependency_text = _style(
            resolution.dependency_name,
            ANSI_CYAN,
            use_color=use_color,
        )
        mode_text = _style(
            MODE_LABELS[resolution.mode],
            ANSI_BOLD,
            MODE_COLORS[resolution.mode],
            use_color=use_color,
        )
        if resolution.mode == "manual":
            detail_label = "path"
            detail_value = str(resolution.path)
        else:
            detail_label = "sha"
            detail_value = resolution.commit or "<unknown>"
        lines.append(
            f"  {dependency_text}: {mode_text} "
            f"{_style(detail_label, ANSI_DIM, use_color=use_color)}="
            f"{_style(detail_value, MODE_COLORS[resolution.mode], use_color=use_color)}"
        )
    return lines


def _short_commit(commit: str) -> str:
    return commit[:12] if len(commit) > 12 else commit


def format_dependency_commit_change_lines(
    changes: Sequence[Any],
    *,
    use_color: bool = False,
) -> list[str]:
    if not changes:
        unchanged_text = _style("unchanged", ANSI_GREEN, use_color=use_color)
        return [f"dependency lock commits {unchanged_text}"]

    lines = ["updated dependency lock commits:"]
    for change in changes:
        dependency_text = _style(
            change.dependency_name,
            ANSI_CYAN,
            use_color=use_color,
        )
        old_commit = _style(_short_commit(change.old_commit), ANSI_DIM, use_color=use_color)
        new_commit = _style(_short_commit(change.new_commit), ANSI_GREEN, use_color=use_color)
        arrow = _style("->", ANSI_DIM, use_color=use_color)
        lines.append(f"  {dependency_text}: {old_commit} {arrow} {new_commit}")
    return lines
