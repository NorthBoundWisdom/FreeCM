# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.tools.repo_tool git-summary --repo <repo> [--view day|month] [--me]
#   Library: from tools.git_summary import collect_daily_stats, collect_monthly_stats

from __future__ import annotations

import datetime as dt
import subprocess  # nosec B404
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CHURN_EXTENSIONS = frozenset(
    {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".qml", ".qmltypes"}
)
GIT_ERROR_TAIL_BYTES = 64 * 1024


@dataclass
class ChurnStat:
    commits: int = 0
    files: int = 0
    added: int = 0
    deleted: int = 0

    @property
    def total(self) -> int:
        return self.added + self.deleted


def run_git(repo_root: Path, args: list[str]) -> str:
    completed = subprocess.run(  # nosec B603 B607
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "unknown git error")
    return completed.stdout


def iter_git_lines(repo_root: Path, args: list[str]) -> Iterator[str]:
    with tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(  # nosec B603 B607
            ["git", *args],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
        )
        if process.stdout is None:
            process.kill()
            process.wait()
            raise RuntimeError("git stdout pipe was not created")
        try:
            yield from process.stdout
        finally:
            process.stdout.close()
            return_code = process.wait()
        if return_code == 0:
            return
        stderr_file.seek(0, 2)
        size = stderr_file.tell()
        stderr_file.seek(max(0, size - GIT_ERROR_TAIL_BYTES))
        detail = stderr_file.read(GIT_ERROR_TAIL_BYTES).decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "unknown git error")


def detect_current_author(repo_root: Path) -> str:
    return run_git(repo_root, ["config", "user.name"]).strip()


def month_start(day: dt.date) -> dt.date:
    return dt.date(day.year, day.month, 1)


def shift_month(month: dt.date, delta: int) -> dt.date:
    total = month.year * 12 + (month.month - 1) + delta
    return dt.date(total // 12, total % 12 + 1, 1)


def add_stat(dst: ChurnStat, src: ChurnStat) -> None:
    dst.commits += src.commits
    dst.files += src.files
    dst.added += src.added
    dst.deleted += src.deleted


def normalize_numstat_path(raw_path: str) -> str:
    path = raw_path.strip()
    if "=>" not in path:
        return path
    brace_start = path.find("{")
    brace_end = path.find("}", brace_start + 1)
    if brace_start != -1 and brace_end != -1:
        inner = path[brace_start + 1 : brace_end]
        if "=>" in inner:
            rhs = inner.split("=>", 1)[1].strip()
            return f"{path[:brace_start]}{rhs}{path[brace_end + 1:]}"
    return path.split("=>", 1)[1].strip()


def _matches_suffix(path: str, suffixes: Iterable[str]) -> bool:
    normalized = normalize_numstat_path(path).lower()
    return any(normalized.endswith(suffix.lower()) for suffix in suffixes)


def collect_stats_raw(
    repo_root: Path,
    *,
    start: dt.date,
    end: dt.date,
    scope_path: str,
    author: str | None = None,
    suffixes: Iterable[str] = DEFAULT_CHURN_EXTENSIONS,
) -> dict[dt.date, ChurnStat]:
    command = [
        "log",
        f"--since={start.isoformat()} 00:00:00",
        f"--until={end.isoformat()} 23:59:59",
        "--date=short",
        "--pretty=format:__COMMIT__%H\t%ad",
        "--numstat",
    ]
    if author:
        command.append(f"--author={author}")
    command.extend(["--", scope_path])

    stats: dict[dt.date, ChurnStat] = {}
    current_date: dt.date | None = None

    for raw_line in iter_git_lines(repo_root, command):
        line = raw_line.rstrip("\r\n")
        if not line:
            continue
        if line.startswith("__COMMIT__"):
            parts = line.split("\t")
            try:
                current_date = dt.date.fromisoformat(parts[1])
            except (IndexError, ValueError):
                current_date = None
                continue
            if start <= current_date <= end:
                stats.setdefault(current_date, ChurnStat()).commits += 1
            continue
        if current_date is None or not (start <= current_date <= end):
            continue
        columns = line.split("\t")
        if len(columns) < 3 or not _matches_suffix(columns[2], suffixes):
            continue
        stat = stats.setdefault(current_date, ChurnStat())
        stat.files += 1
        if columns[0].isdigit():
            stat.added += int(columns[0])
        if columns[1].isdigit():
            stat.deleted += int(columns[1])
    return stats


def collect_daily_stats(
    repo_root: Path,
    *,
    days: int = 7,
    scope_path: str = ".",
    author: str | None = None,
    today: dt.date | None = None,
) -> list[tuple[dt.date, ChurnStat]]:
    if days <= 0:
        raise ValueError("days must be > 0")
    today = today or dt.date.today()
    start = today - dt.timedelta(days=days - 1)
    raw = collect_stats_raw(repo_root, start=start, end=today, scope_path=scope_path, author=author)
    return [
        (day, raw.get(day, ChurnStat()))
        for day in (start + dt.timedelta(days=offset) for offset in range(days))
    ]


def collect_monthly_stats(
    repo_root: Path,
    *,
    months: int = 12,
    scope_path: str = ".",
    author: str | None = None,
    today: dt.date | None = None,
) -> list[tuple[dt.date, ChurnStat]]:
    if months <= 0:
        raise ValueError("months must be > 0")
    today = today or dt.date.today()
    current = month_start(today)
    first = shift_month(current, -(months - 1))
    raw = collect_stats_raw(repo_root, start=first, end=today, scope_path=scope_path, author=author)
    rows: dict[dt.date, ChurnStat] = {
        shift_month(first, offset): ChurnStat() for offset in range(months)
    }
    for day, stat in raw.items():
        key = month_start(day)
        if key in rows:
            add_stat(rows[key], stat)
    return sorted(rows.items(), key=lambda item: item[0])


def table_rows(
    rows: list[tuple[dt.date, ChurnStat]], *, view: str
) -> tuple[list[str], list[list[str]]]:
    if view == "day":
        headers = ["Date", "Wk", "Commits", "Files", "Added", "Deleted", "Total"]
        body = [
            [
                day.isoformat(),
                day.strftime("%a"),
                str(stat.commits),
                str(stat.files),
                str(stat.added),
                str(stat.deleted),
                str(stat.total),
            ]
            for day, stat in rows
        ]
    else:
        headers = ["Month", "Commits", "Files", "Added", "Deleted", "Total"]
        body = [
            [
                day.strftime("%Y-%m"),
                str(stat.commits),
                str(stat.files),
                str(stat.added),
                str(stat.deleted),
                str(stat.total),
            ]
            for day, stat in rows
        ]

    total = ChurnStat()
    for _day, stat in rows:
        add_stat(total, stat)
    total_row = [
        "TOTAL",
        *([] if view == "month" else ["-"]),
        str(total.commits),
        str(total.files),
        str(total.added),
        str(total.deleted),
        str(total.total),
    ]
    body.append(total_row)
    return headers, body


def format_table(headers: list[str], body: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in body:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def format_row(cells: list[str]) -> str:
        return (
            "| "
            + " | ".join(cells[index].rjust(widths[index]) for index in range(len(cells)))
            + " |"
        )

    separator = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    lines = [separator, format_row(headers), separator]
    for index, row in enumerate(body):
        if index == len(body) - 1:
            lines.append(separator)
        lines.append(format_row(row))
    lines.append(separator)
    return "\n".join(lines)
