# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.tools.repo_tool update-header-guards <root> [--dry-run]
#   Library: from repomgrcpp.tools.header_guards import update_header_guards

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx")


@dataclass(frozen=True)
class HeaderGuardUpdate:
    path: Path
    macro: str
    old_macro: str | None
    changed: bool


def camel_to_upper_snake(value: str) -> str:
    value = re.sub(r"\.[^.]+$", "", value)
    value = re.sub(r"([a-z])([A-Z])", r"\1_\2", value)
    value = re.sub(r"([A-Z])([A-Z][a-z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value.upper()


def header_guard_macro_for_path(path: Path, *, root: Path | None = None) -> str:
    if root is not None:
        path = path.resolve().relative_to(root.resolve())
    path_text = path.as_posix().lstrip("./")
    parent = Path(path_text).parent.as_posix()
    filename = Path(path_text).name
    file_macro = camel_to_upper_snake(filename)
    if parent in ("", "."):
        return f"{file_macro}_H"
    parent_macro = re.sub(r"[^A-Za-z0-9]+", "_", parent).strip("_").upper()
    return f"{parent_macro}_{file_macro}_H"


def _rewrite_header_guard(content: str, new_macro: str) -> tuple[str, str | None, bool]:
    lines = content.splitlines(keepends=True)
    ifndef_pattern = re.compile(r"^(\s*)#ifndef\s+([A-Z0-9_]+)\s*$")
    define_pattern = re.compile(r"^(\s*)#define\s+([A-Z0-9_]+)\s*$")
    endif_pattern = re.compile(r"^(\s*#endif)\s*(.*)$")

    old_macro: str | None = None
    found_ifndef = False
    found_define = False
    last_endif_index = -1

    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        newline = line[len(stripped) :]
        if not found_ifndef:
            match = ifndef_pattern.match(stripped)
            if match:
                old_macro = match.group(2)
                found_ifndef = True
                lines[index] = f"{match.group(1)}#ifndef {new_macro}{newline or chr(10)}"
                continue
        elif not found_define:
            match = define_pattern.match(stripped)
            if match:
                found_define = True
                lines[index] = f"{match.group(1)}#define {new_macro}{newline or chr(10)}"
                continue
        if stripped.lstrip().startswith("#endif"):
            last_endif_index = index

    if not found_ifndef:
        body = content.rstrip("\n")
        rewritten = f"#ifndef {new_macro}\n#define {new_macro}\n\n{body}\n#endif // {new_macro}\n"
        return rewritten, None, True

    if last_endif_index >= 0:
        stripped = lines[last_endif_index].rstrip("\r\n")
        newline = lines[last_endif_index][len(stripped) :]
        match = endif_pattern.match(stripped)
        if match:
            comment = match.group(2).strip()
            if old_macro and old_macro in comment:
                comment = comment.replace(old_macro, new_macro)
            elif not comment:
                comment = f"// {new_macro}"
            lines[last_endif_index] = f"{match.group(1)} {comment}{newline or chr(10)}"

    rewritten = "".join(line if line.endswith(("\n", "\r")) else line + "\n" for line in lines)
    return rewritten, old_macro, rewritten != content


def update_header_guard_file(
    path: Path,
    *,
    root: Path | None = None,
    dry_run: bool = False,
) -> HeaderGuardUpdate:
    macro = header_guard_macro_for_path(path, root=root)
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="latin-1")
    rewritten, old_macro, changed = _rewrite_header_guard(content, macro)
    if changed and not dry_run:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=path.suffix) as temp_file:
            temp_file.write(rewritten)
            temp_path = Path(temp_file.name)
        shutil.move(str(temp_path), path)
    return HeaderGuardUpdate(path=path, macro=macro, old_macro=old_macro, changed=changed)


def update_header_guards(
    root: Path,
    *,
    dry_run: bool = False,
    suffixes: Iterable[str] = DEFAULT_HEADER_SUFFIXES,
) -> list[HeaderGuardUpdate]:
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    suffix_filter = {suffix.lower() for suffix in suffixes}
    updates: list[HeaderGuardUpdate] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in suffix_filter:
            update = update_header_guard_file(path, root=root, dry_run=dry_run)
            if update.changed:
                updates.append(update)
    return updates
