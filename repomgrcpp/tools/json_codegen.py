# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.tools.repo_tool generate-json-keys --input <json> --output <header> --namespace <ns> --header-guard <guard>
#   Library: from repomgrcpp.tools.json_codegen import generate_cpp_string_key_header

from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence


DEFAULT_CPP_STRING_INCLUDE = "#include <string>"


def token_to_constant_part(token: str) -> str:
    if not token:
        return ""
    if token[0].isdigit():
        match = re.match(r"^(\d+)([A-Za-z]+)$", token)
        if match is not None:
            return match.group(1) + match.group(2).upper()
        return token
    return token[0].upper() + token[1:].lower()


def key_to_constant_name(key: str, *, special_names: Mapping[str, str] | None = None) -> str:
    if special_names and key in special_names:
        return special_names[key]
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", key) if part]
    if not parts:
        return "k"
    return "k" + "".join(token_to_constant_part(part) for part in parts)


def namespace_blocks(namespace: str) -> tuple[list[str], list[str]]:
    names = [part.strip() for part in namespace.split("::") if part.strip()]
    openings = [f"namespace {name}" + "\n{" for name in names]
    closings = [f"}} // namespace {name}" for name in reversed(names)]
    return openings, closings


def generate_cpp_string_key_header(
    keys: Iterable[str],
    *,
    namespace: str,
    header_guard: str,
    special_names: Mapping[str, str] | None = None,
    include_line: str = DEFAULT_CPP_STRING_INCLUDE,
) -> str:
    normalized_keys = sorted({key for key in keys if str(key).strip()})
    openings, closings = namespace_blocks(namespace)
    lines = [
        f"#ifndef {header_guard}",
        f"#define {header_guard}",
        "",
        include_line,
        "",
        *openings,
        "",
    ]
    for key in normalized_keys:
        constant_name = key_to_constant_name(key, special_names=special_names)
        escaped_key = key.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'const std::string {constant_name} = "{escaped_key}";')
    lines.extend(["", *closings, f"#endif // {header_guard}", ""])
    return "\n".join(lines)


def parse_special_name_entries(entries: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"Invalid special-name entry {entry!r}; expected key=ConstantName")
        key, value = entry.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(f"Invalid special-name entry {entry!r}; expected key=ConstantName")
        result[key] = value
    return result
