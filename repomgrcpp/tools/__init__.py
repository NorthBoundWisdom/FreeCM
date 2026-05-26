"""C++ repository maintenance tools."""

from tools.cleanup import DEFAULT_EXCLUDED_DIR_NAMES, collect_empty_dirs, remove_empty_dirs
from tools.file_lists import list_filenames, normalize_suffixes
from tools.git_summary import ChurnStat, collect_daily_stats, collect_monthly_stats
from tools.json_codegen import (
    DeduplicationResult,
    collect_json_keys,
    collect_json_keys_from_files,
    deduplicate_json_array,
)
from tools.remove_old_build import OldBuildCleanupResult, remove_old_build

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
    "ChurnStat",
    "DEFAULT_EXCLUDED_DIR_NAMES",
    "DeduplicationResult",
    "MarkdownCatalogEntry",
    "OldBuildCleanupResult",
    "collect_daily_stats",
    "collect_empty_dirs",
    "collect_json_keys",
    "collect_json_keys_from_files",
    "collect_markdown_catalog_docs",
    "collect_monthly_stats",
    "deduplicate_json_array",
    "format_source_tree",
    "generate_cpp_catalog_entries",
    "generate_cpp_string_key_header",
    "generate_qrc_entries",
    "header_guard_macro_for_path",
    "list_filenames",
    "normalize_suffixes",
    "remove_empty_dirs",
    "remove_old_build",
    "run_cmake_targets",
    "selected_ci_targets",
    "simplify_brief_comments",
    "simplify_brief_comments_in_file",
    "update_header_guards",
]
