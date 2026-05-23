# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.tools.repo_tool simplify-briefs <root> [--dry-run]
#   Library: from repomgrcpp.tools.comments import simplify_brief_comments

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

DEFAULT_COMMENT_SUFFIXES = (".h", ".hh", ".hpp", ".hxx", ".cpp", ".cc", ".cxx")


def simplify_brief_comments_in_file(file_path: Path, *, dry_run: bool = False) -> bool:
    content = file_path.read_text(encoding="utf-8")
    lines = content.split("\n")
    new_lines: list[str] = []
    modified = False
    index = 0

    while index < len(lines):
        line = lines[index]
        if re.match(r"^\s*/\*\*\s*$", line):
            match = None
            if index + 1 < len(lines):
                match = re.match(r"^\s*\*\s+@brief\s+(.+)$", lines[index + 1])
            if match and index + 2 < len(lines) and re.match(r"^\s*\*/\s*$", lines[index + 2]):
                indent_match = re.match(r"^(\s*)/\*\*", line)
                indent = indent_match.group(1) if indent_match else ""
                brief_text = match.group(1).strip()
                new_lines.append(f"{indent}/** @brief {brief_text} */")
                index += 3
                modified = True
                continue
        new_lines.append(line)
        index += 1

    if modified and not dry_run:
        file_path.write_text("\n".join(new_lines), encoding="utf-8")
    return modified


def simplify_brief_comments(
    root: Path,
    *,
    dry_run: bool = False,
    suffixes: Iterable[str] = DEFAULT_COMMENT_SUFFIXES,
) -> list[Path]:
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    suffix_filter = {suffix.lower() for suffix in suffixes}
    modified: list[Path] = []
    for file_path in sorted(root.rglob("*")):
        if file_path.is_file() and file_path.suffix.lower() in suffix_filter:
            if simplify_brief_comments_in_file(file_path, dry_run=dry_run):
                modified.append(file_path)
    return modified
