# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.tools.repo_tool --help
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.tools.repo_tool list-files <dir> --suffix cpp,h
#   repo-tool --help

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.cleanup import DEFAULT_EXCLUDED_DIR_NAMES, collect_empty_dirs, remove_empty_dirs
from tools.file_lists import list_filenames, normalize_suffixes
from tools.git_summary import (
    collect_daily_stats,
    collect_monthly_stats,
    detect_current_author,
    format_table,
    table_rows,
)
from tools.json_codegen import (
    collect_json_keys_from_files,
    deduplicate_json_array,
    load_json_file,
    write_json_file,
)
from tools.lock_compat import main as lock_compat_main
from tools.performance_baseline import main as performance_baseline_main

from .ci_targets import run_cmake_targets, selected_ci_targets
from .comments import simplify_brief_comments
from .file_lists import CPP_EXTENSIONS, generate_qrc_entries
from .format_code import format_source_tree
from .header_guards import update_header_guards
from .json_codegen import (
    generate_cpp_string_key_header,
    parse_special_name_entries,
)
from .markdown_catalog import (
    collect_markdown_catalog_docs,
    generate_cpp_catalog_entries,
    order_catalog_entries,
    read_order_from_text,
)


def _write_lines(path: Path | None, lines: list[str]) -> None:
    text = "\n".join(lines) + ("\n" if lines else "")
    if path is None:
        sys.stdout.write(text)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}")


def cmd_list_files(args: argparse.Namespace) -> int:
    if args.all:
        suffixes = None
    elif args.cpptype:
        suffixes = CPP_EXTENSIONS
    else:
        suffixes = normalize_suffixes(args.suffix.split(","))
        if not suffixes:
            print("no suffixes provided", file=sys.stderr)
            return 1
    lines = list_filenames(
        Path(args.directory),
        suffixes=suffixes,
        recursive=args.recursive,
        prefix=args.prefix,
    )
    _write_lines(Path(args.output) if args.output else None, lines)
    print(f"total files: {len(lines)}", file=sys.stderr if args.output is None else sys.stdout)
    return 0


def cmd_qrc_entries(args: argparse.Namespace) -> int:
    suffixes = list(args.suffixes)
    base_path = args.base_path
    entries = generate_qrc_entries(
        Path(args.search_path),
        suffixes,
        base_path=Path(base_path) if base_path else None,
    )
    _write_lines(Path(args.output) if args.output else None, entries)
    total_entries = len([entry for entry in entries if entry.strip()])
    print(f"total entries: {total_entries}", file=sys.stderr if args.output is None else sys.stdout)
    return 0


def cmd_remove_empty_dirs(args: argparse.Namespace) -> int:
    excluded = set(DEFAULT_EXCLUDED_DIR_NAMES)
    excluded.update(args.exclude_dir_name or [])
    candidates = collect_empty_dirs(Path(args.root), excluded_dir_names=excluded)
    for directory in candidates:
        print(directory)
    if args.dry_run:
        print(f"dry-run candidates: {len(candidates)}")
        return 0
    removed = remove_empty_dirs(Path(args.root), excluded_dir_names=excluded)
    print(f"removed: {len(removed)}/{len(candidates)}")
    return 0


def cmd_simplify_briefs(args: argparse.Namespace) -> int:
    modified = simplify_brief_comments(Path(args.root), dry_run=args.dry_run)
    for path in modified:
        print(path)
    print(("would modify" if args.dry_run else "modified") + f": {len(modified)}")
    return 0


def cmd_update_header_guards(args: argparse.Namespace) -> int:
    updates = update_header_guards(Path(args.root), dry_run=args.dry_run)
    for update in updates:
        old = update.old_macro or "(none)"
        print(f"{update.path}: {old} -> {update.macro}")
    print(("would update" if args.dry_run else "updated") + f": {len(updates)}")
    return 0


def cmd_format_code(args: argparse.Namespace) -> int:
    result = format_source_tree(
        Path(args.root),
        clang_format=args.clang_format,
        qml_format=args.qml_format,
        include_qml=not args.no_qml,
        dry_run=args.dry_run,
    )
    print(f"clang-format: {result.clang_format or '<not found>'}")
    print(f"qmlformat: {result.qml_format or '<not found>'}")
    print(f"cpp files: {result.cpp_files}, failed: {result.cpp_failed}")
    print(f"qml files: {result.qml_files}, failed: {result.qml_failed}")
    return 1 if result.failed else 0


def cmd_git_summary(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    author = args.author
    if args.me:
        author = detect_current_author(repo_root)
    if args.view == "day":
        rows = collect_daily_stats(repo_root, days=args.days, scope_path=args.path, author=author)
    else:
        rows = collect_monthly_stats(
            repo_root, months=args.months, scope_path=args.path, author=author
        )
    headers, body = table_rows(rows, view=args.view)
    print(format_table(headers, body))
    if author:
        print(f"author filter: {author}")
    print(f"view: {args.view}")
    print(f"scope path: {args.path}")
    return 0


def cmd_generate_json_keys(args: argparse.Namespace) -> int:
    keys = collect_json_keys_from_files([Path(path) for path in args.input])
    keys.update(args.extra_key or [])
    special_names = parse_special_name_entries(args.special_name or [])
    content = generate_cpp_string_key_header(
        keys,
        namespace=args.namespace,
        header_guard=args.header_guard,
        special_names=special_names,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"wrote {output}")
    print(f"keys: {len({key for key in keys if key.strip()})}")
    return 0


def cmd_dedup_json_array(args: argparse.Namespace) -> int:
    path = Path(args.input)
    data = load_json_file(path)
    result = deduplicate_json_array(data, array_key=args.array_key, dedup_key=args.dedup_key)
    if args.dry_run:
        print(f"would remove: {len(result.removed_indices)}")
        print(f"removed indices: {list(result.removed_indices)}")
        return 0
    output = Path(args.output) if args.output else path
    write_json_file(output, result.data)
    print(f"wrote {output}")
    print(
        f"items: {result.original_count} -> {result.deduplicated_count}, "
        f"removed: {len(result.removed_indices)}"
    )
    return 0


def cmd_markdown_catalog(args: argparse.Namespace) -> int:
    docs = collect_markdown_catalog_docs(
        Path(args.root),
        file_prefix=args.file_prefix,
        file_suffix=args.file_suffix,
        body_id_label=args.body_id_label,
    )
    order: list[str] = []
    if args.order_file:
        order = read_order_from_text(
            Path(args.order_file).read_text(encoding="utf-8"), args.order_regex
        )
    entries = order_catalog_entries(docs, order)
    content = generate_cpp_catalog_entries(entries)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"wrote {output}")
    print(f"entries: {len(entries)}")
    return 0


def cmd_ci_targets(args: argparse.Namespace) -> int:
    quick_targets = args.quick_target or []
    regular_targets = args.target or []
    targets = selected_ci_targets(
        regular_targets=regular_targets,
        quick_targets=quick_targets,
        pipeline_source=args.pipeline_source,
        quick_sources=args.quick_source or ["merge_request_event"],
    )
    if not targets:
        print("no targets selected", file=sys.stderr)
        return 1
    print(f"selected targets: {', '.join(targets)}")
    if args.dry_run:
        return 0
    results = run_cmake_targets(Path(args.build_dir), targets, parallel=args.parallel)
    for result in results:
        status = "ok" if result.returncode == 0 else f"failed({result.returncode})"
        print(f"{result.target}: {status}")
    return next((result.returncode for result in results if result.returncode != 0), 0)


def cmd_check_lock_compat(args: argparse.Namespace) -> int:
    forwarded_args = []
    if args.repo_root:
        forwarded_args.extend(["--repo-root", args.repo_root])
    forwarded_args.extend(["--format", args.format])
    forwarded_args.extend(args.paths)
    return lock_compat_main(forwarded_args)


def cmd_performance_baseline(args: argparse.Namespace) -> int:
    return performance_baseline_main(
        [
            "--dependencies",
            str(args.dependencies),
            "--iterations",
            str(args.iterations),
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shared C++ repository maintenance tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_files = subparsers.add_parser("list-files", help="List filenames in a directory.")
    list_files.add_argument("directory")
    mode = list_files.add_mutually_exclusive_group(required=True)
    mode.add_argument("--suffix", help='Suffixes, for example "qml,svg,png"')
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--cpptype", action="store_true")
    list_files.add_argument("--recursive", action="store_true")
    list_files.add_argument("--prefix", default="")
    list_files.add_argument("--output")
    list_files.set_defaults(func=cmd_list_files)

    qrc = subparsers.add_parser("qrc-entries", help="Generate Qt .qrc <file> entries.")
    qrc.add_argument("search_path")
    qrc.add_argument("suffixes", nargs="+")
    qrc.add_argument("--base", "--base-path", dest="base_path")
    qrc.add_argument("--output")
    qrc.set_defaults(func=cmd_qrc_entries)

    empty = subparsers.add_parser("remove-empty-dirs", help="Remove empty directories.")
    empty.add_argument("--root", default=".")
    empty.add_argument("--dry-run", action="store_true")
    empty.add_argument("--exclude-dir-name", action="append", default=[])
    empty.set_defaults(func=cmd_remove_empty_dirs)

    briefs = subparsers.add_parser(
        "simplify-briefs", help="Simplify three-line Doxygen @brief comments."
    )
    briefs.add_argument("root")
    briefs.add_argument("--dry-run", action="store_true")
    briefs.set_defaults(func=cmd_simplify_briefs)

    guards = subparsers.add_parser(
        "update-header-guards", help="Update header guards to path-derived macros."
    )
    guards.add_argument("root")
    guards.add_argument("--dry-run", action="store_true")
    guards.set_defaults(func=cmd_update_header_guards)

    formatter = subparsers.add_parser(
        "format-code", help="Run clang-format and qmlformat over a tree."
    )
    formatter.add_argument("root")
    formatter.add_argument("--clang-format")
    formatter.add_argument("--qml-format")
    formatter.add_argument("--no-qml", action="store_true")
    formatter.add_argument("--dry-run", action="store_true")
    formatter.set_defaults(func=cmd_format_code)

    summary = subparsers.add_parser("git-summary", help="Print code churn stats.")
    summary.add_argument("--repo", default=".")
    summary.add_argument("--view", choices=("day", "month"), default="day")
    summary.add_argument("--days", type=int, default=7)
    summary.add_argument("--months", type=int, default=12)
    summary.add_argument("--path", default=".")
    summary.add_argument("--author")
    summary.add_argument("--me", action="store_true")
    summary.set_defaults(func=cmd_git_summary)

    json_keys = subparsers.add_parser(
        "generate-json-keys",
        help="Generate a C++ string-constant header from JSON keys.",
    )
    json_keys.add_argument("--input", action="append", required=True)
    json_keys.add_argument("--output", required=True)
    json_keys.add_argument("--namespace", required=True, help="C++ namespace, e.g. app::Keys")
    json_keys.add_argument("--header-guard", required=True)
    json_keys.add_argument("--extra-key", action="append", default=[])
    json_keys.add_argument(
        "--special-name",
        action="append",
        default=[],
        help="Map JSON key to constant name: key=kName",
    )
    json_keys.set_defaults(func=cmd_generate_json_keys)

    dedup = subparsers.add_parser(
        "dedup-json-array",
        help="Deduplicate a JSON array by a nested key path.",
    )
    dedup.add_argument("--input", required=True)
    dedup.add_argument("--output")
    dedup.add_argument("--array-key", required=True)
    dedup.add_argument("--dedup-key", required=True)
    dedup.add_argument("--dry-run", action="store_true")
    dedup.set_defaults(func=cmd_dedup_json_array)

    catalog = subparsers.add_parser(
        "markdown-catalog",
        help="Generate C++ catalog entries from Markdown documents.",
    )
    catalog.add_argument("--root", required=True)
    catalog.add_argument("--output", required=True)
    catalog.add_argument("--file-prefix", default="Cmd")
    catalog.add_argument("--file-suffix", default="Doc.md")
    catalog.add_argument("--body-id-label", default="CmdId")
    catalog.add_argument("--order-file")
    catalog.add_argument(
        "--order-regex",
        default=r'^inline const std::string\s+(\w+)\s*=\s*"[^"]*";',
    )
    catalog.set_defaults(func=cmd_markdown_catalog)

    ci = subparsers.add_parser(
        "ci-targets",
        help="Run configured CMake targets, using quick targets for selected CI sources.",
    )
    ci.add_argument("--build-dir", required=True)
    ci.add_argument("--target", action="append", default=[])
    ci.add_argument("--quick-target", action="append", default=[])
    ci.add_argument("--quick-source", action="append", default=[])
    ci.add_argument("--pipeline-source")
    ci.add_argument("--parallel", type=int)
    ci.add_argument("--dry-run", action="store_true")
    ci.set_defaults(func=cmd_ci_targets)

    lock_compat = subparsers.add_parser(
        "check-lock-compat",
        help="Check FreeCM lock files for schema compatibility without modifying them.",
    )
    lock_compat.add_argument("paths", nargs="*")
    lock_compat.add_argument("--repo-root")
    lock_compat.add_argument("--format", choices=("text", "json"), default="text")
    lock_compat.set_defaults(func=cmd_check_lock_compat)

    perf = subparsers.add_parser(
        "performance-baseline",
        help="Run lightweight FreeCM performance baselines.",
    )
    perf.add_argument("--dependencies", type=int, default=50)
    perf.add_argument("--iterations", type=int, default=25)
    perf.set_defaults(func=cmd_performance_baseline)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
