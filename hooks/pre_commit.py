#!/usr/bin/env python3
# Internal:
#   Python implementation for the shared pre-commit hook.
#   Normally invoked by hooks/pre-commit from the host repository.

from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from freecm.git_repositories import git_toplevel

MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024

CLANG_FORMAT_CONFIG_KEY = "freecm.clangFormatPath"
QMLFORMAT_CONFIG_KEY = "freecm.qmlformatPath"
SOURCE_ROOTS_CONFIG_KEY = "freecm.hooks.sourceRoots"
EXCLUDED_DIRS_CONFIG_KEY = "freecm.hooks.excludeDirs"

DEFAULT_SOURCE_ROOTS = (Path("SourceCode"),)
DEFAULT_EXCLUDED_DIRS = (Path("SourceCode/thirdparty"),)

CPP_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx"}
QML_EXTENSIONS = {".qml", ".js", ".mjs"}
REGULAR_BLOB_MODES = frozenset({"100644", "100755"})


def _print_console(message: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    safe_message = message.encode(encoding, errors="backslashreplace").decode(encoding)
    print(safe_message)


@dataclass(frozen=True)
class StagedEntry:
    path: Path
    mode: str
    object_id: str
    size_bytes: int
    raw_path: bytes | None = None

    @property
    def is_regular_blob(self) -> bool:
        return self.mode in REGULAR_BLOB_MODES

    @property
    def git_path(self) -> bytes:
        return self.raw_path if self.raw_path is not None else os.fsencode(str(self.path))


@dataclass(frozen=True)
class PreparedBlob:
    entry: StagedEntry
    transformed: bytes | None
    size_bytes: int
    normalized: bool

    @property
    def changed(self) -> bool:
        return self.transformed is not None


@dataclass(frozen=True)
class IndexUpdate:
    entry: StagedEntry
    object_id: str


@dataclass(frozen=True)
class LargeFile:
    path: Path
    size_bytes: int

    @property
    def size_mb(self) -> str:
        return f"{self.size_bytes / (1024 * 1024):.2f}"


def run_git(
    repo_root: Path, args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=check,
    )


def run_git_bytes(
    repo_root: Path,
    args: list[str],
    *,
    input_data: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # nosec B603 B607
        ["git", *args],
        cwd=repo_root,
        input=input_data,
        capture_output=True,
        check=check,
    )


def get_git_config(repo_root: Path, key: str) -> str | None:
    result = run_git(repo_root, ["config", "--get", key], check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def parse_path_list(value: str | None, default: tuple[Path, ...]) -> tuple[Path, ...]:
    if value is None:
        return default
    paths = tuple(Path(part.strip()) for part in value.split(";") if part.strip())
    return paths or default


def resolve_tool_cmd(repo_root: Path, config_key: str, label: str) -> str | None:
    configured = get_git_config(repo_root, config_key)
    if not configured:
        _print_console(f"Error: {label} path is not configured.")
        _print_console("Run: python hooks/install.py")
        return None

    configured_path = Path(configured).expanduser()
    if not configured_path.is_file():
        _print_console(f"Error: configured {label} not found: {configured_path}")
        return None
    if not os.access(configured_path, os.X_OK):
        _print_console(f"Error: configured {label} is not executable: {configured_path}")
        return None
    return str(configured_path)


def resolve_optional_tool_cmd(repo_root: Path, config_key: str, label: str) -> str | None:
    configured = get_git_config(repo_root, config_key)
    if not configured:
        return None

    configured_path = Path(configured).expanduser()
    if not configured_path.is_file():
        _print_console(
            f"Warning: configured {label} not found; skipping optional formatter: {configured_path}"
        )
        return None
    if not os.access(configured_path, os.X_OK):
        _print_console(
            f"Warning: configured {label} is not executable; skipping optional formatter: {configured_path}"
        )
        return None
    return str(configured_path)


def is_under_configured_roots(
    path: Path,
    *,
    source_roots: tuple[Path, ...],
    excluded_dirs: tuple[Path, ...],
) -> bool:
    if not any(path.is_relative_to(source_root) for source_root in source_roots):
        return False
    return not any(path.is_relative_to(excluded) for excluded in excluded_dirs)


def is_cpp_formattable(
    path: Path,
    *,
    source_roots: tuple[Path, ...],
    excluded_dirs: tuple[Path, ...],
) -> bool:
    return path.suffix.lower() in CPP_EXTENSIONS and is_under_configured_roots(
        path,
        source_roots=source_roots,
        excluded_dirs=excluded_dirs,
    )


def is_qml_formattable(
    path: Path,
    *,
    source_roots: tuple[Path, ...],
    excluded_dirs: tuple[Path, ...],
) -> bool:
    return path.suffix.lower() in QML_EXTENSIONS and is_under_configured_roots(
        path,
        source_roots=source_roots,
        excluded_dirs=excluded_dirs,
    )


def _decode_git_path(raw_path: bytes) -> Path:
    return Path(os.fsdecode(raw_path))


def _parse_stage_zero_entries(
    raw: bytes,
    candidates: set[bytes] | None = None,
) -> dict[bytes, tuple[str, str]]:
    entries: dict[bytes, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split(b" ")
        if not separator or len(fields) != 3:
            raise ValueError("Unexpected git ls-files --stage record")
        mode, object_id, stage = fields
        if candidates is not None and raw_path not in candidates:
            continue
        if stage != b"0":
            continue
        entries[raw_path] = (mode.decode("ascii"), object_id.decode("ascii"))
    return entries


def _read_stage_zero_entries(
    repo_root: Path,
    candidates: set[bytes] | None = None,
) -> dict[bytes, tuple[str, str]]:
    result = run_git_bytes(repo_root, ["ls-files", "--stage", "-z"])
    return _parse_stage_zero_entries(result.stdout, candidates)


def _read_blob_sizes(repo_root: Path, object_ids: list[str]) -> dict[str, int]:
    unique_object_ids = list(dict.fromkeys(object_ids))
    if not unique_object_ids:
        return {}
    result = run_git_bytes(
        repo_root,
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input_data="".join(f"{object_id}\n" for object_id in unique_object_ids).encode("ascii"),
    )
    sizes: dict[str, int] = {}
    for line in result.stdout.splitlines():
        parts = line.split(b" ")
        if len(parts) != 3 or parts[1] != b"blob":
            raise ValueError(f"Unexpected git cat-file metadata: {line!r}")
        sizes[parts[0].decode("ascii")] = int(parts[2])
    if len(sizes) != len(unique_object_ids):
        raise ValueError("Git did not return metadata for every staged blob")
    return sizes


def get_staged_entries(repo_root: Path) -> list[StagedEntry]:
    changed_result = run_git_bytes(
        repo_root,
        [
            "diff",
            "--cached",
            "--name-only",
            "--no-renames",
            "--diff-filter=ACM",
            "-z",
        ],
    )
    candidates = {path for path in changed_result.stdout.split(b"\0") if path}
    if not candidates:
        return []
    stage_zero_entries = _read_stage_zero_entries(repo_root, candidates)
    metadata = [
        (raw_path, *stage_zero_entries[raw_path])
        for raw_path in changed_result.stdout.split(b"\0")
        if raw_path in stage_zero_entries
    ]
    regular_object_ids = [
        object_id for _raw_path, mode, object_id in metadata if mode in REGULAR_BLOB_MODES
    ]
    sizes = _read_blob_sizes(repo_root, regular_object_ids)
    return [
        StagedEntry(
            path=_decode_git_path(raw_path),
            mode=mode,
            object_id=object_id,
            size_bytes=sizes.get(object_id, 0),
            raw_path=raw_path,
        )
        for raw_path, mode, object_id in metadata
    ]


def get_staged_paths(repo_root: Path) -> list[Path]:
    return [entry.path for entry in get_staged_entries(repo_root)]


def _parse_batch_blob_output(raw: bytes, object_ids: list[str]) -> dict[str, bytes]:
    contents: dict[str, bytes] = {}
    offset = 0
    for expected_object_id in object_ids:
        header_end = raw.find(b"\n", offset)
        if header_end < 0:
            raise ValueError("Truncated git cat-file blob header")
        header = raw[offset:header_end].split(b" ")
        if len(header) != 3 or header[1] != b"blob":
            raise ValueError(f"Unexpected git cat-file blob header: {raw[offset:header_end]!r}")
        object_id = header[0].decode("ascii")
        if object_id != expected_object_id:
            raise ValueError("Git returned staged blobs out of order")
        size = int(header[2])
        content_start = header_end + 1
        content_end = content_start + size
        if content_end >= len(raw) or raw[content_end : content_end + 1] != b"\n":
            raise ValueError("Truncated git cat-file blob content")
        contents[object_id] = raw[content_start:content_end]
        offset = content_end + 1
    if offset != len(raw):
        raise ValueError("Unexpected trailing data from git cat-file")
    return contents


def read_staged_blobs(repo_root: Path, entries: list[StagedEntry]) -> dict[str, bytes]:
    object_ids = list(dict.fromkeys(entry.object_id for entry in entries if entry.is_regular_blob))
    if not object_ids:
        return {}
    result = run_git_bytes(
        repo_root,
        ["cat-file", "--batch"],
        input_data="".join(f"{object_id}\n" for object_id in object_ids).encode("ascii"),
    )
    return _parse_batch_blob_output(result.stdout, object_ids)


def read_staged_binary_overrides(
    repo_root: Path,
    entries: list[StagedEntry],
) -> dict[bytes, bool | None]:
    regular_entries = [entry for entry in entries if entry.is_regular_blob]
    if not regular_entries:
        return {}
    result = run_git_bytes(
        repo_root,
        ["check-attr", "--cached", "-z", "--stdin", "diff", "text"],
        input_data=b"".join(entry.git_path + b"\0" for entry in regular_entries),
    )
    fields = result.stdout.split(b"\0")
    if fields and not fields[-1]:
        fields.pop()
    if len(fields) % 3 != 0:
        raise ValueError("Unexpected git check-attr output")
    attributes: dict[bytes, dict[bytes, bytes]] = {}
    for offset in range(0, len(fields), 3):
        raw_path, name, value = fields[offset : offset + 3]
        attributes.setdefault(raw_path, {})[name] = value

    overrides: dict[bytes, bool | None] = {}
    for entry in regular_entries:
        values = attributes.get(entry.git_path, {})
        diff_value = values.get(b"diff", b"unspecified")
        text_value = values.get(b"text", b"unspecified")
        if b"unset" in {diff_value, text_value}:
            overrides[entry.git_path] = True
        elif b"set" in {diff_value, text_value}:
            overrides[entry.git_path] = False
        else:
            overrides[entry.git_path] = None
    return overrides


def is_binary_blob(content: bytes, override: bool | None = None) -> bool:
    if override is not None:
        return override
    return b"\0" in content[:8000]


def normalize_text_bytes(original: bytes) -> bytes:
    data = original.replace(b"\r\n", b"\n")
    data = re.sub(rb"[ \t]+(?=\n)", b"", data)
    return re.sub(rb"[ \t]+$", b"", data)


def normalize_text_file(path: Path) -> bool:
    original = path.read_bytes()
    data = normalize_text_bytes(original)
    if data == original:
        return False
    path.write_bytes(data)
    return True


def find_large_files(entries: list[StagedEntry]) -> list[LargeFile]:
    return [
        LargeFile(path=entry.path, size_bytes=entry.size_bytes)
        for entry in entries
        if entry.is_regular_blob and entry.size_bytes > MAX_FILE_SIZE_BYTES
    ]


def _formatter_error(result: subprocess.CompletedProcess[bytes]) -> str:
    output = result.stderr or result.stdout or b""
    return output.decode("utf-8", errors="replace").rstrip()


def _copy_qmlformat_configs(repo_root: Path, file_path: Path, temp_root: Path) -> None:
    source_parent = (repo_root / file_path).parent
    while source_parent.is_relative_to(repo_root):
        source_config = source_parent / ".qmlformat.ini"
        if source_config.is_file():
            relative_parent = source_parent.relative_to(repo_root)
            target_config = temp_root / relative_parent / source_config.name
            target_config.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_config, target_config)
        if source_parent == repo_root:
            break
        source_parent = source_parent.parent


def format_blob(
    repo_root: Path,
    file_path: Path,
    content: bytes,
    formatter_cmd: str,
    *,
    qml: bool,
) -> bytes | None:
    if not qml:
        try:
            result = subprocess.run(  # nosec B603
                [
                    formatter_cmd,
                    "-style=file",
                    f"-assume-filename={repo_root / file_path}",
                ],
                cwd=repo_root,
                input=content,
                capture_output=True,
            )
        except OSError as exc:
            _print_console(f"Error formatting {file_path}: {exc}")
            return None
        if result.returncode != 0:
            _print_console(f"Error formatting {file_path}: {_formatter_error(result)}")
            return None
        return result.stdout

    try:
        git_dir = Path(run_git(repo_root, ["rev-parse", "--absolute-git-dir"]).stdout.strip())
        with tempfile.TemporaryDirectory(prefix="freecm-qmlformat-", dir=git_dir) as tempdir:
            temp_root = Path(tempdir)
            temp_path = temp_root / file_path
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            _copy_qmlformat_configs(repo_root, file_path, temp_root)
            temp_path.write_bytes(content)
            result = subprocess.run(  # nosec B603
                [formatter_cmd, "-i", str(temp_path)],
                cwd=repo_root,
                capture_output=True,
            )
            if result.returncode != 0:
                _print_console(f"Error formatting {file_path}: {_formatter_error(result)}")
                return None
            return temp_path.read_bytes()
    except OSError as exc:
        _print_console(f"Error formatting {file_path}: {exc}")
        return None


def prepare_staged_blobs(
    repo_root: Path,
    entries: list[StagedEntry],
    blobs: dict[str, bytes],
) -> list[PreparedBlob] | None:
    source_roots = parse_path_list(
        get_git_config(repo_root, SOURCE_ROOTS_CONFIG_KEY),
        DEFAULT_SOURCE_ROOTS,
    )
    excluded_dirs = parse_path_list(
        get_git_config(repo_root, EXCLUDED_DIRS_CONFIG_KEY),
        DEFAULT_EXCLUDED_DIRS,
    )
    binary_overrides = read_staged_binary_overrides(repo_root, entries)
    text_entries = [
        entry
        for entry in entries
        if entry.is_regular_blob
        and not is_binary_blob(
            blobs[entry.object_id],
            binary_overrides.get(entry.git_path),
        )
    ]
    cpp_entries = [
        entry
        for entry in text_entries
        if is_cpp_formattable(
            entry.path,
            source_roots=source_roots,
            excluded_dirs=excluded_dirs,
        )
    ]
    qml_entries = [
        entry
        for entry in text_entries
        if is_qml_formattable(
            entry.path,
            source_roots=source_roots,
            excluded_dirs=excluded_dirs,
        )
    ]

    clang_format: str | None = None
    if cpp_entries:
        clang_format = resolve_tool_cmd(repo_root, CLANG_FORMAT_CONFIG_KEY, "clang-format")
        if clang_format is None:
            return None
    else:
        _print_console("No C/C++ files to format.")

    qmlformat: str | None = None
    if qml_entries:
        qmlformat = resolve_optional_tool_cmd(repo_root, QMLFORMAT_CONFIG_KEY, "qmlformat")
        if qmlformat is None:
            _print_console(
                "Skipping QML/JS formatting: optional qmlformat is not configured "
                f"({len(qml_entries)} file(s))."
            )
    else:
        _print_console("No QML/JS files to format.")

    prepared: list[PreparedBlob] = []
    cpp_paths = {entry.path for entry in cpp_entries}
    qml_paths = {entry.path for entry in qml_entries}
    for entry in text_entries:
        original = blobs[entry.object_id]
        transformed = normalize_text_bytes(original)
        normalized = transformed != original
        if entry.path in cpp_paths:
            if clang_format is None:
                raise RuntimeError("clang-format was not resolved for a staged C/C++ blob")
            _print_console(f"Formatting C/C++: {entry.path}")
            formatted = format_blob(
                repo_root,
                entry.path,
                transformed,
                clang_format,
                qml=False,
            )
            if formatted is None:
                return None
            transformed = normalize_text_bytes(formatted)
            normalized = normalized or transformed != formatted
        elif entry.path in qml_paths and qmlformat is not None:
            _print_console(f"Formatting QML/JS: {entry.path}")
            formatted = format_blob(
                repo_root,
                entry.path,
                transformed,
                qmlformat,
                qml=True,
            )
            if formatted is None:
                return None
            transformed = normalize_text_bytes(formatted)
            normalized = normalized or transformed != formatted
        prepared.append(
            PreparedBlob(
                entry=entry,
                transformed=transformed if transformed != original else None,
                size_bytes=len(transformed),
                normalized=normalized,
            )
        )
    return prepared


def hash_prepared_blobs(repo_root: Path, prepared: list[PreparedBlob]) -> list[IndexUpdate]:
    updates: list[IndexUpdate] = []
    object_ids_by_content: dict[bytes, str] = {}
    for item in prepared:
        if not item.changed:
            continue
        if item.transformed is None:
            raise RuntimeError("Changed staged blob is missing transformed content")
        object_id = object_ids_by_content.get(item.transformed)
        if object_id is None:
            result = run_git_bytes(
                repo_root,
                ["hash-object", "-w", "--stdin"],
                input_data=item.transformed,
            )
            object_id = result.stdout.decode("ascii").strip()
            if not re.fullmatch(r"[0-9a-f]+", object_id):
                raise ValueError(f"Unexpected git hash-object output: {object_id!r}")
            object_ids_by_content[item.transformed] = object_id
        updates.append(IndexUpdate(entry=item.entry, object_id=object_id))
    return updates


def _index_update_payload(updates: list[IndexUpdate]) -> bytes:
    return b"".join(
        update.entry.mode.encode("ascii")
        + b" "
        + update.object_id.encode("ascii")
        + b"\t"
        + update.entry.git_path
        + b"\0"
        for update in updates
    )


def _write_index_updates(repo_root: Path, updates: list[IndexUpdate]) -> None:
    run_git_bytes(
        repo_root,
        ["update-index", "-z", "--index-info"],
        input_data=_index_update_payload(updates),
    )


def apply_index_updates(repo_root: Path, updates: list[IndexUpdate]) -> None:
    if not updates:
        return
    expected = {
        update.entry.git_path: (update.entry.mode, update.entry.object_id) for update in updates
    }
    current = _read_stage_zero_entries(repo_root, set(expected))
    if current != expected:
        raise RuntimeError("Git index changed while staged files were being prepared")

    try:
        _write_index_updates(repo_root, updates)
    except subprocess.CalledProcessError as update_error:
        try:
            after_failure = _read_stage_zero_entries(repo_root, set(expected))
        except (subprocess.CalledProcessError, ValueError) as inspect_error:
            raise RuntimeError(
                "Git index update failed and the resulting index state could not be inspected: "
                f"update={update_error}; inspect={inspect_error}"
            ) from inspect_error
        restore_updates = [
            IndexUpdate(entry=update.entry, object_id=update.entry.object_id)
            for update in updates
            if after_failure.get(update.entry.git_path) == (update.entry.mode, update.object_id)
        ]
        if restore_updates:
            try:
                _write_index_updates(repo_root, restore_updates)
            except subprocess.CalledProcessError as restore_error:
                raise RuntimeError(
                    "Git index update failed and the original staged entries could not be restored: "
                    f"update={update_error}; restore={restore_error}"
                ) from restore_error
        raise


def print_large_file_error(large_files: list[LargeFile]) -> None:
    _print_console("Commit rejected: files larger than 15MB detected")
    for item in large_files:
        _print_console(f"  - {item.path} ({item.size_mb} MB)")
    _print_console("Please remove these files from staging or use Git LFS for large files.")


def run_pre_commit(repo_root: Path) -> int:
    entries = get_staged_entries(repo_root)
    if not entries:
        return 0

    large_files = find_large_files(entries)
    if large_files:
        print_large_file_error(large_files)
        return 1

    blobs = read_staged_blobs(repo_root, entries)
    prepared = prepare_staged_blobs(repo_root, entries, blobs)
    blobs.clear()
    if prepared is None:
        return 1

    transformed_large_files = [
        LargeFile(path=item.entry.path, size_bytes=item.size_bytes)
        for item in prepared
        if item.size_bytes > MAX_FILE_SIZE_BYTES
    ]
    if transformed_large_files:
        print_large_file_error(transformed_large_files)
        return 1

    updates = hash_prepared_blobs(repo_root, prepared)
    apply_index_updates(repo_root, updates)
    for item in prepared:
        if item.changed and item.normalized:
            _print_console(f"Normalized whitespace/EOL: {item.entry.path}")
    return 0


def main() -> int:
    try:
        return run_pre_commit(git_toplevel(Path.cwd()))
    except (subprocess.CalledProcessError, RuntimeError, ValueError) as exc:
        _print_console(f"Error updating staged files: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
