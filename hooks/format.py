#!/usr/bin/env python3
# Internal:
#   Legacy staged C/C++ formatting hook helper.
#   Normally invoked through FreeCM hook wiring, not as a user CLI.

from __future__ import annotations

try:
    from .pre_commit import (
        CLANG_FORMAT_CONFIG_KEY,
        DEFAULT_EXCLUDED_DIRS,
        DEFAULT_SOURCE_ROOTS,
        EXCLUDED_DIRS_CONFIG_KEY,
        SOURCE_ROOTS_CONFIG_KEY,
        format_file,
        get_git_config,
        get_repo_root,
        get_staged_paths,
        is_cpp_formattable as is_formattable,
        parse_path_list,
        resolve_tool_cmd as _resolve_tool_cmd,
    )
except ImportError:
    from pre_commit import (  # type: ignore[no-redef]
        CLANG_FORMAT_CONFIG_KEY,
        DEFAULT_EXCLUDED_DIRS,
        DEFAULT_SOURCE_ROOTS,
        EXCLUDED_DIRS_CONFIG_KEY,
        SOURCE_ROOTS_CONFIG_KEY,
        format_file,
        get_git_config,
        get_repo_root,
        get_staged_paths,
        is_cpp_formattable as is_formattable,
        parse_path_list,
        resolve_tool_cmd as _resolve_tool_cmd,
    )


def resolve_clang_format_cmd(repo_root):
    return _resolve_tool_cmd(repo_root, CLANG_FORMAT_CONFIG_KEY, "clang-format")


def get_staged_files(repo_root):
    source_roots = parse_path_list(
        get_git_config(repo_root, SOURCE_ROOTS_CONFIG_KEY),
        DEFAULT_SOURCE_ROOTS,
    )
    excluded_dirs = parse_path_list(
        get_git_config(repo_root, EXCLUDED_DIRS_CONFIG_KEY),
        DEFAULT_EXCLUDED_DIRS,
    )
    return [
        path
        for path in get_staged_paths(repo_root)
        if is_formattable(
            path,
            source_roots=source_roots,
            excluded_dirs=excluded_dirs,
        )
    ]


def main() -> int:
    repo_root = get_repo_root()
    clang_format = resolve_clang_format_cmd(repo_root)
    if clang_format is None:
        return 1
    success = True
    for path in get_staged_files(repo_root):
        print(f"Formatting C/C++: {path}")
        success = format_file(repo_root, path, clang_format, qml=False) and success
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
