# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.tools.repo_tool generate-json-keys --input <json> --output <header> --namespace <ns> --header-guard <guard>
#   Library: from repomgrcpp.tools.json_codegen import generate_cpp_string_key_header

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

DEFAULT_CPP_STRING_INCLUDE = "#include <string>"
_CPP_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CPP_KEYWORDS = frozenset(
    {
        "alignas",
        "alignof",
        "and",
        "and_eq",
        "asm",
        "atomic_cancel",
        "atomic_commit",
        "atomic_noexcept",
        "auto",
        "bitand",
        "bitor",
        "bool",
        "break",
        "case",
        "catch",
        "char",
        "char8_t",
        "char16_t",
        "char32_t",
        "class",
        "compl",
        "concept",
        "const",
        "consteval",
        "constexpr",
        "constinit",
        "const_cast",
        "continue",
        "co_await",
        "co_return",
        "co_yield",
        "decltype",
        "default",
        "delete",
        "do",
        "double",
        "dynamic_cast",
        "else",
        "enum",
        "explicit",
        "export",
        "extern",
        "false",
        "float",
        "for",
        "friend",
        "goto",
        "if",
        "inline",
        "int",
        "long",
        "mutable",
        "namespace",
        "new",
        "noexcept",
        "not",
        "not_eq",
        "nullptr",
        "operator",
        "or",
        "or_eq",
        "private",
        "protected",
        "public",
        "reflexpr",
        "register",
        "reinterpret_cast",
        "requires",
        "return",
        "short",
        "signed",
        "sizeof",
        "static",
        "static_assert",
        "static_cast",
        "struct",
        "switch",
        "synchronized",
        "template",
        "this",
        "thread_local",
        "throw",
        "true",
        "try",
        "typedef",
        "typeid",
        "typename",
        "union",
        "unsigned",
        "using",
        "virtual",
        "void",
        "volatile",
        "wchar_t",
        "while",
        "xor",
        "xor_eq",
    }
)


def _validate_identifier(value: str, *, description: str, reject_keywords: bool) -> None:
    if _CPP_IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"Invalid {description} {value!r}; expected a C++ identifier")
    if reject_keywords and value in _CPP_KEYWORDS:
        raise ValueError(f"Invalid {description} {value!r}; C++ keywords are not allowed")


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
    names = [part.strip() for part in namespace.split("::")]
    if not names or any(not name for name in names):
        raise ValueError(
            f"Invalid namespace {namespace!r}; expected non-empty C++ identifiers separated by ::"
        )
    for name in names:
        _validate_identifier(name, description="namespace component", reject_keywords=True)
    openings = [f"namespace {name}" + "\n{" for name in names]
    closings = [f"}} // namespace {name}" for name in reversed(names)]
    return openings, closings


def _constant_names_by_key(
    keys: Sequence[str], *, special_names: Mapping[str, str] | None
) -> dict[str, str]:
    for key, name in sorted((special_names or {}).items()):
        _validate_identifier(
            name,
            description=f"special name for JSON key {key!r}",
            reject_keywords=True,
        )

    names_by_key = {key: key_to_constant_name(key, special_names=special_names) for key in keys}
    keys_by_name: dict[str, list[str]] = {}
    for key, name in names_by_key.items():
        _validate_identifier(
            name,
            description=f"generated constant name for JSON key {key!r}",
            reject_keywords=True,
        )
        keys_by_name.setdefault(name, []).append(key)

    collisions = {
        name: sorted(source_keys)
        for name, source_keys in keys_by_name.items()
        if len(source_keys) > 1
    }
    if collisions:
        details = "; ".join(
            f"{name}: {', '.join(repr(key) for key in source_keys)}"
            for name, source_keys in sorted(collisions.items())
        )
        raise ValueError(f"Generated C++ constant name collisions: {details}")
    return names_by_key


def generate_cpp_string_key_header(
    keys: Iterable[str],
    *,
    namespace: str,
    header_guard: str,
    special_names: Mapping[str, str] | None = None,
    include_line: str = DEFAULT_CPP_STRING_INCLUDE,
) -> str:
    normalized_keys = sorted({key for key in keys if str(key).strip()})
    _validate_identifier(header_guard, description="header guard", reject_keywords=False)
    openings, closings = namespace_blocks(namespace)
    names_by_key = _constant_names_by_key(normalized_keys, special_names=special_names)
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
        constant_name = names_by_key[key]
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
