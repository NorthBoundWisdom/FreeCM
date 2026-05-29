"""Reusable repository maintenance tools."""

from .cleanup import collect_empty_dirs, remove_empty_dirs
from .file_lists import list_filenames, normalize_suffixes
from .git_summary import ChurnStat, collect_daily_stats, collect_monthly_stats
from .json_codegen import (
    DeduplicationResult,
    collect_json_keys,
    collect_json_keys_from_files,
    deduplicate_json_array,
)

__all__ = [
    "ChurnStat",
    "DeduplicationResult",
    "collect_daily_stats",
    "collect_empty_dirs",
    "collect_json_keys",
    "collect_json_keys_from_files",
    "collect_monthly_stats",
    "deduplicate_json_array",
    "list_filenames",
    "normalize_suffixes",
    "remove_empty_dirs",
]
