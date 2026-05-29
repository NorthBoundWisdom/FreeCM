"""C++ repository maintenance tools."""

from .ci_targets import CMakeTargetRun, run_cmake_targets, selected_ci_targets
from .comments import simplify_brief_comments, simplify_brief_comments_in_file
from .file_lists import CPP_EXTENSIONS, generate_qrc_entries
from .format_code import format_source_tree
from .header_guards import header_guard_macro_for_path, update_header_guards
from .json_codegen import generate_cpp_string_key_header
from .markdown_catalog import (
    MarkdownCatalogEntry,
    collect_markdown_catalog_docs,
    generate_cpp_catalog_entries,
)

__all__ = [
    "CPP_EXTENSIONS",
    "CMakeTargetRun",
    "MarkdownCatalogEntry",
    "collect_markdown_catalog_docs",
    "format_source_tree",
    "generate_cpp_catalog_entries",
    "generate_cpp_string_key_header",
    "generate_qrc_entries",
    "header_guard_macro_for_path",
    "run_cmake_targets",
    "selected_ci_targets",
    "simplify_brief_comments",
    "simplify_brief_comments_in_file",
    "update_header_guards",
]
