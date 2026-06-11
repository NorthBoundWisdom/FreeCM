# Internal:
#   Commit message validator used by hooks/commit-msg.

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

VALID_TYPES = (
    "feat",
    "fix",
    "refactor",
    "style",
    "docs",
    "test",
    "chore",
    "perf",
    "ci",
    "build",
    "enhancement",
)


def _supports_color() -> bool:
    return os.environ.get("NO_COLOR") is None


def _style(text: str, color: str) -> str:
    if not _supports_color():
        return text
    colors = {
        "red": "\033[0;31m",
        "green": "\033[0;32m",
        "yellow": "\033[1;33m",
    }
    return f"{colors[color]}{text}\033[0m"


def clean_commit_message(message: str) -> str:
    for line in message.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def validate_commit_message(message: str) -> tuple[bool, list[str]]:
    clean_msg = clean_commit_message(message)
    if re.match(r"^(Merge|Revert) ", clean_msg):
        return True, [_style("Commit message format is correct", "green")]
    if not clean_msg:
        return False, [_style("Commit message cannot be empty", "red")]

    valid_types = "|".join(VALID_TYPES)
    if not re.match(rf"^\[({valid_types})\]: .+", clean_msg):
        return False, [
            _style("Incorrect commit message format", "red"),
            _style("Correct format: [type]: description", "yellow"),
            _style(f"Valid types: {', '.join(VALID_TYPES)}", "yellow"),
            _style("Example: [feat]: add user login feature", "yellow"),
            _style(f"Current message: {clean_msg}", "yellow"),
        ]

    description = re.sub(r"^\[[^]]*\]: ", "", clean_msg)
    messages = [_style("Commit message format is correct", "green")]
    if len(description) > 50:
        messages.insert(
            0,
            _style(
                f"Recommend commit description not exceed 50 characters, current length: {len(description)}",
                "yellow",
            ),
        )
        messages.insert(1, _style(f"Description: {description}", "yellow"))
    return True, messages


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Usage: python -m hooks.commit_msg <commit-msg-file>", file=sys.stderr)
        return 2
    message = Path(args[0]).read_text(encoding="utf-8")
    ok, messages = validate_commit_message(message)
    for line in messages:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
