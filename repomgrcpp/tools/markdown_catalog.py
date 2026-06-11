# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.tools.repo_tool markdown-catalog --root <docs> --output <entries.inc>
#   Library: from repomgrcpp.tools.markdown_catalog import collect_markdown_catalog_docs, generate_cpp_catalog_entries

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MarkdownCatalogEntry:
    entry_id: str
    description: str
    content: str
    source_path: Path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def expected_id_from_filename(path: Path, *, prefix: str, suffix: str) -> str:
    name = path.name
    if not name.startswith(prefix) or not name.endswith(suffix):
        raise ValueError(f"File name does not match {prefix}*{suffix}: {path}")
    return name[len(prefix) : len(name) - len(suffix)]


def parse_markdown_catalog_doc(
    path: Path,
    *,
    expected_id: str,
    body_id_label: str = "CmdId",
) -> MarkdownCatalogEntry:
    content = read_text(path)
    header_match = re.search(r"^##\s+(.+)$", content, re.MULTILINE)
    if not header_match:
        raise ValueError(f"Invalid doc header (expected '## ...'): {path}")

    header = header_match.group(1).strip()
    entry_id = expected_id
    description = header

    if re.match(r"^[A-Z][A-Za-z0-9_]*\s*-\s*.+$", header):
        raise ValueError(f"Doc header uses removed id-description form: {path}")

    modern_header = re.match(
        rf"^[A-Za-z]*{re.escape(expected_id)}(?:（(.+)）|\((.+)\))?$",
        header,
    )
    if modern_header:
        description = (modern_header.group(1) or modern_header.group(2) or header).strip()

    body_id_match = re.search(
        rf"^\s*-\s*{re.escape(body_id_label)}:\s*`([^`]+)`",
        content,
        re.MULTILINE,
    )
    if body_id_match:
        entry_id = body_id_match.group(1).strip()
        if entry_id != expected_id:
            raise ValueError(f"Doc id mismatch: file={path.name} body={entry_id}")

    body = content[header_match.end() :].strip()
    return MarkdownCatalogEntry(
        entry_id=entry_id,
        description=description,
        content=body,
        source_path=path,
    )


def collect_markdown_catalog_docs(
    root: Path,
    *,
    file_prefix: str = "Cmd",
    file_suffix: str = "Doc.md",
    body_id_label: str = "CmdId",
) -> dict[str, MarkdownCatalogEntry]:
    docs: dict[str, MarkdownCatalogEntry] = {}
    errors: list[str] = []
    for path in sorted(root.rglob(f"{file_prefix}*{file_suffix}")):
        try:
            expected_id = expected_id_from_filename(path, prefix=file_prefix, suffix=file_suffix)
            doc = parse_markdown_catalog_doc(
                path,
                expected_id=expected_id,
                body_id_label=body_id_label,
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if doc.entry_id in docs:
            errors.append(
                f"Duplicate doc id {doc.entry_id}: {docs[doc.entry_id].source_path} and {path}"
            )
            continue
        docs[doc.entry_id] = doc

    if errors:
        raise ValueError("\n".join(errors))
    return docs


def read_order_from_text(text: str, pattern: str) -> list[str]:
    regex = re.compile(pattern, re.MULTILINE)
    return [match.group(1) for match in regex.finditer(text)]


def order_catalog_entries(
    docs: dict[str, MarkdownCatalogEntry],
    order: Iterable[str],
) -> list[MarkdownCatalogEntry]:
    remaining = dict(docs)
    ordered: list[MarkdownCatalogEntry] = []
    for entry_id in order:
        if entry_id in remaining:
            ordered.append(remaining.pop(entry_id))
    for entry_id in sorted(remaining):
        ordered.append(remaining[entry_id])
    return ordered


def make_raw_cpp_string(text: str) -> str:
    base_tag = "__DOC__"
    tag = base_tag
    counter = 1
    while f'){tag}"' in text:
        tag = f"{base_tag}{counter}"
        counter += 1
    return f'R"{tag}({text}){tag}"'


def make_cpp_string_expr(text: str, *, chunk_size: int = 4096) -> str:
    if len(text) <= chunk_size:
        return make_raw_cpp_string(text)
    chunks = [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]
    return "\n".join(make_raw_cpp_string(chunk) for chunk in chunks)


def generate_cpp_catalog_entries(entries: Iterable[MarkdownCatalogEntry]) -> str:
    lines = ["/* This file is generated; do not edit it manually. */"]
    for entry in entries:
        entry_id = entry.entry_id.replace("\\", "\\\\").replace('"', '\\"')
        raw_description = make_cpp_string_expr(entry.description)
        raw_content = make_cpp_string_expr(entry.content)
        lines.append(f'{{"{entry_id}", {raw_description}, {raw_content}}},')
    return "\n".join(lines) + "\n"
