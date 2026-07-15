# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m freecm.asset_seeds --repo-root <repo> verify
#   Library: from freecm.asset_seeds import prepare_asset_seeds, require_asset_seeds

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .jsonc import loads_jsonc
from .lock_schema import (
    ACTIVE_LOCK_FILE_NAME,
    REMOVED_TOP_LEVEL_FIELDS,
    TEMPLATE_LOCK_FILE_NAME,
)

ASSETS_FIELD = "assets"
LEGACY_ASSET_FIELDS = tuple(
    field for field, replacement in REMOVED_TOP_LEVEL_FIELDS.items() if replacement == ASSETS_FIELD
)
ASSET_TYPES = ("file", "archive")
ASSET_LIMIT_FIELDS = {
    "maxDownloadBytes",
    "maxArchiveMembers",
    "maxArchiveMemberBytes",
    "maxArchiveTotalBytes",
    "maxCompressionRatio",
}
DEFAULT_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_MEMBERS = 10_000
DEFAULT_MAX_ARCHIVE_MEMBER_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_TOTAL_BYTES = 1024 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 200.0
STREAM_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class _AssetSeedLimits:
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES
    max_archive_members: int = DEFAULT_MAX_ARCHIVE_MEMBERS
    max_archive_member_bytes: int = DEFAULT_MAX_ARCHIVE_MEMBER_BYTES
    max_archive_total_bytes: int = DEFAULT_MAX_ARCHIVE_TOTAL_BYTES
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO


@dataclass(frozen=True)
class AssetSeedFile:
    asset_name: str
    item_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    source_url: str | None


@dataclass(frozen=True)
class AssetSeedSummary:
    asset_name: str
    seed_root: Path
    files: tuple[AssetSeedFile, ...]


@dataclass(frozen=True)
class _PreparedAssetOutput:
    temp_path: Path
    destination: Path


def load_lock_assets(repo_root: Path, *, active: bool = True) -> dict[str, Any]:
    lock_name = ACTIVE_LOCK_FILE_NAME if active else TEMPLATE_LOCK_FILE_NAME
    path = repo_root / lock_name
    if not path.is_file():
        raise FileNotFoundError(f"Missing source-roots lock file: {path}")
    data = loads_jsonc(path.read_text(encoding="utf-8"), path_label=str(path))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid source-roots lock file: {path}")
    return validate_assets_lock_data(data, repo_root=repo_root, path_label=str(path))


def validate_assets_lock_data(
    data: dict[str, Any],
    *,
    repo_root: Path,
    path_label: str,
) -> dict[str, Any]:
    for legacy_field in LEGACY_ASSET_FIELDS:
        if legacy_field in data:
            raise ValueError(f"{legacy_field} is no longer supported in {path_label}; use assets")

    assets = data.get(ASSETS_FIELD, {})
    if assets is None:
        assets = {}
    if not isinstance(assets, dict):
        raise ValueError(f"Invalid assets map in {path_label}")

    seen_destinations: set[Path] = set()
    for asset_name, asset_data in assets.items():
        _validate_asset_name(asset_name, path_label=path_label)
        if not isinstance(asset_data, dict):
            raise ValueError(f"Invalid assets.{asset_name} entry in {path_label}")
        limits = _asset_limits(
            asset_data,
            path_label=f"assets.{asset_name} in {path_label}",
        )
        seed_root = _safe_repo_relative_path(
            repo_root,
            _required_string(
                asset_data, "seedPath", path_label=f"assets.{asset_name} in {path_label}"
            ),
            label=f"assets.{asset_name}.seedPath",
        )
        files = asset_data.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError(
                f"Invalid assets.{asset_name}.files in {path_label}; expected non-empty list"
            )

        asset_destinations: set[Path] = set()
        for index, item in enumerate(files):
            item_label = f"assets.{asset_name}.files[{index}] in {path_label}"
            for destination in _validate_asset_item(
                repo_root,
                seed_root,
                item,
                item_label=item_label,
                limits=limits,
            ):
                if destination in asset_destinations or destination in seen_destinations:
                    raise ValueError(f"Duplicate asset output path in {path_label}: {destination}")
                asset_destinations.add(destination)
                seen_destinations.add(destination)
    return data


def prepare_asset_seeds(repo_root: Path) -> tuple[AssetSeedSummary, ...]:
    path = repo_root / ACTIVE_LOCK_FILE_NAME
    if not path.exists():
        return ()
    data = load_lock_assets(repo_root, active=True)
    return tuple(
        _prepare_asset(repo_root, name, spec) for name, spec in data.get(ASSETS_FIELD, {}).items()
    )


def require_asset_seeds(repo_root: Path) -> tuple[AssetSeedSummary, ...]:
    path = repo_root / ACTIVE_LOCK_FILE_NAME
    if not path.exists():
        return ()
    data = load_lock_assets(repo_root, active=True)
    summaries: list[AssetSeedSummary] = []
    problems: list[str] = []
    for asset_name, asset_data in data.get(ASSETS_FIELD, {}).items():
        summary, asset_problems = _verify_asset(repo_root, asset_name, asset_data)
        summaries.append(summary)
        problems.extend(asset_problems)
    if problems:
        details = "\n".join(f"- {problem}" for problem in problems)
        raise FileNotFoundError(
            "Asset seeds are missing or invalid:\n"
            f"{details}\n"
            "Run `python3 configs/source_root_workflow.py --init` first."
        )
    return tuple(summaries)


def asset_seed_root(repo_root: Path, asset_name: str) -> Path:
    data = load_lock_assets(repo_root, active=True)
    asset_data = data.get(ASSETS_FIELD, {}).get(asset_name)
    if not isinstance(asset_data, dict):
        raise KeyError(f"Asset seed {asset_name!r} is not defined in source_roots.lock.jsonc")
    return _safe_repo_relative_path(
        repo_root,
        _required_string(asset_data, "seedPath", path_label=f"assets.{asset_name}"),
        label=f"assets.{asset_name}.seedPath",
    )


def asset_seed_file_names(repo_root: Path, asset_name: str) -> tuple[str, ...]:
    data = load_lock_assets(repo_root, active=True)
    asset_data = data.get(ASSETS_FIELD, {}).get(asset_name)
    if not isinstance(asset_data, dict):
        raise KeyError(f"Asset seed {asset_name!r} is not defined in source_roots.lock.jsonc")
    names: list[str] = []
    for item in asset_data["files"]:
        item_type = item["type"]
        if item_type == "file":
            names.append(_required_file_name(item, "fileName", path_label=f"assets.{asset_name}"))
        elif item_type == "archive":
            for entry in item["extract"]:
                names.append(
                    str(_safe_relative_path(entry["to"], label=f"assets.{asset_name}.extract.to"))
                )
    return tuple(names)


def _prepare_asset(
    repo_root: Path, asset_name: str, asset_data: dict[str, Any]
) -> AssetSeedSummary:
    seed_root = _safe_repo_relative_path(
        repo_root,
        _required_string(asset_data, "seedPath", path_label=f"assets.{asset_name}"),
        label=f"assets.{asset_name}.seedPath",
    )
    limits = _asset_limits(asset_data, path_label=f"assets.{asset_name}")
    seed_root.mkdir(parents=True, exist_ok=True)
    files: list[AssetSeedFile] = []
    for item in asset_data["files"]:
        item_type = item["type"]
        if item_type == "file":
            files.append(_prepare_file_item(seed_root, asset_name, item, limits))
        elif item_type == "archive":
            files.extend(_prepare_archive_item(seed_root, asset_name, item, limits))
        else:  # pragma: no cover - validation catches this before dispatch.
            raise ValueError(f"Unsupported asset type {item_type!r}")
    _write_manifest(seed_root, asset_name, files)
    return AssetSeedSummary(asset_name=asset_name, seed_root=seed_root, files=tuple(files))


def _verify_asset(
    repo_root: Path,
    asset_name: str,
    asset_data: dict[str, Any],
) -> tuple[AssetSeedSummary, list[str]]:
    seed_root = _safe_repo_relative_path(
        repo_root,
        _required_string(asset_data, "seedPath", path_label=f"assets.{asset_name}"),
        label=f"assets.{asset_name}.seedPath",
    )
    files: list[AssetSeedFile] = []
    problems: list[str] = []
    for item in asset_data["files"]:
        item_type = item["type"]
        if item_type == "file":
            file_name = _required_file_name(item, "fileName", path_label=f"assets.{asset_name}")
            path = seed_root / file_name
            record = _asset_file_record(asset_name, item, file_name, item.get("url"))
            files.append(record)
            if not _file_matches(path, record):
                problems.append(
                    f"{asset_name}/{file_name} missing or does not match lock at {path}"
                )
        elif item_type == "archive":
            for entry in item["extract"]:
                relative_path = str(
                    _safe_relative_path(entry["to"], label=f"assets.{asset_name}.extract.to")
                )
                path = seed_root / relative_path
                record = _asset_file_record(asset_name, entry, relative_path, item.get("url"))
                files.append(record)
                if not _file_matches(path, record):
                    problems.append(
                        f"{asset_name}/{relative_path} missing or does not match lock at {path}"
                    )
        else:  # pragma: no cover - validation catches this before dispatch.
            raise ValueError(f"Unsupported asset type {item_type!r}")
    return (
        AssetSeedSummary(asset_name=asset_name, seed_root=seed_root, files=tuple(files)),
        problems,
    )


def _prepare_file_item(
    seed_root: Path,
    asset_name: str,
    item: dict[str, Any],
    limits: _AssetSeedLimits,
) -> AssetSeedFile:
    file_name = _required_file_name(item, "fileName", path_label=f"assets.{asset_name}")
    destination = seed_root / file_name
    record = _asset_file_record(asset_name, item, file_name, item.get("url"))
    if _file_matches(destination, record):
        _normalize_permissions(destination)
        return record
    path_label = f"assets.{asset_name}.{file_name}"
    _download_to_file(
        _required_string(item, "url", path_label=path_label),
        destination,
        record,
        max_bytes=limits.max_download_bytes,
        http_accept=_optional_http_accept(item, path_label=path_label),
    )
    return record


def _prepare_archive_item(
    seed_root: Path,
    asset_name: str,
    item: dict[str, Any],
    limits: _AssetSeedLimits,
) -> tuple[AssetSeedFile, ...]:
    archive_name = _required_file_name(item, "fileName", path_label=f"assets.{asset_name}")
    archive_path = seed_root / archive_name
    archive_record = _asset_file_record(asset_name, item, archive_name, item.get("url"))
    if not _file_matches(archive_path, archive_record):
        path_label = f"assets.{asset_name}.{archive_name}"
        _download_to_file(
            _required_string(item, "url", path_label=path_label),
            archive_path,
            archive_record,
            max_bytes=limits.max_download_bytes,
            http_accept=_optional_http_accept(item, path_label=path_label),
        )

    records: list[AssetSeedFile] = []
    prepared_outputs: list[_PreparedAssetOutput] = []
    with zipfile.ZipFile(archive_path, "r") as archive:
        members = _validate_archive_limits(archive, archive_path, limits)
        try:
            for entry in item["extract"]:
                source_name = _safe_archive_member(
                    entry["from"], label=f"assets.{asset_name}.extract.from"
                )
                member = members.get(source_name)
                if member is None:
                    raise FileNotFoundError(
                        f"Archive {archive_path} is missing required entry: {source_name}"
                    )
                relative_path = str(
                    _safe_relative_path(entry["to"], label=f"assets.{asset_name}.extract.to")
                )
                destination = seed_root / relative_path
                record = _asset_file_record(asset_name, entry, relative_path, item.get("url"))
                if member.is_dir():
                    raise ValueError(
                        f"Archive {archive_path} entry is a directory, not a file: {source_name}"
                    )
                if member.file_size != record.size_bytes:
                    raise RuntimeError(
                        f"Archive entry size did not match lock: {asset_name}/{relative_path} "
                        f"archiveSize={member.file_size} lockSize={record.size_bytes}"
                    )
                if not _file_matches(destination, record):
                    prepared_outputs.append(
                        _prepare_archive_member(archive, member, destination, record, limits)
                    )
                records.append(record)
        except Exception:
            _cleanup_prepared_outputs(prepared_outputs)
            raise
    _publish_prepared_outputs(prepared_outputs)
    return tuple(records)


def _download_to_file(
    url: str,
    destination: Path,
    expected: AssetSeedFile,
    *,
    max_bytes: int,
    http_accept: str | None,
) -> None:
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in {"file", "http", "https"}:
        raise ValueError(f"Unsupported asset URL scheme: {parsed_url.scheme or '<empty>'}")
    _ensure_safe_existing_destination(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            stream_limit = min(expected.size_bytes, max_bytes)
            label = f"Downloaded asset {expected.asset_name}/{expected.relative_path}"
            if parsed_url.scheme == "file":
                if parsed_url.netloc not in {"", "localhost"}:
                    raise ValueError(f"Unsupported asset file URL host: {parsed_url.netloc}")
                source_path = Path(urllib.request.url2pathname(parsed_url.path))
                _check_stream_size(source_path.stat().st_size, stream_limit, label=label)
                with source_path.open("rb") as source:
                    _copy_stream_limited(source, tmp, stream_limit, label=label)
            else:
                headers = {"User-Agent": "FreeCM-asset-seed/1"}
                if http_accept is not None:
                    headers["Accept"] = http_accept
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310
                    content_length = _response_content_length(response)
                    if content_length is not None:
                        _check_stream_size(content_length, stream_limit, label=label)
                    _copy_stream_limited(response, tmp, stream_limit, label=label)
            tmp.flush()
        if not _file_matches(tmp_path, expected):
            actual_size = tmp_path.stat().st_size
            actual_sha = sha256_file(tmp_path)
            raise RuntimeError(
                f"Downloaded asset did not match lock: {expected.asset_name}/{expected.relative_path} "
                f"size={actual_size} sha256={actual_sha}"
            )
        _publish_prepared_outputs(
            [_PreparedAssetOutput(temp_path=tmp_path, destination=destination)]
        )
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def _prepare_archive_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    destination: Path,
    expected: AssetSeedFile,
    limits: _AssetSeedLimits,
) -> _PreparedAssetOutput:
    _ensure_safe_existing_destination(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            with archive.open(member) as source:
                _copy_stream_limited(
                    source,
                    tmp,
                    min(expected.size_bytes, limits.max_archive_member_bytes),
                    label=f"Extracted asset {expected.asset_name}/{expected.relative_path}",
                )
            tmp.flush()
        if not _file_matches(tmp_path, expected):
            actual_size = tmp_path.stat().st_size
            actual_sha = sha256_file(tmp_path)
            raise RuntimeError(
                f"Extracted asset did not match lock: {expected.asset_name}/{expected.relative_path} "
                f"size={actual_size} sha256={actual_sha}"
            )
        return _PreparedAssetOutput(temp_path=tmp_path, destination=destination)
    except Exception:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()
        raise


def _cleanup_prepared_outputs(outputs: Iterable[_PreparedAssetOutput]) -> None:
    for output in outputs:
        if output.temp_path.exists():
            output.temp_path.unlink()


def _publish_prepared_outputs(outputs: list[_PreparedAssetOutput]) -> None:
    transactions: list[tuple[Path, Path | None]] = []
    try:
        for output in outputs:
            _ensure_safe_existing_destination(output.destination)
            backup: Path | None = None
            if output.destination.exists():
                with tempfile.NamedTemporaryFile(
                    dir=output.destination.parent,
                    delete=False,
                ) as backup_file:
                    backup = Path(backup_file.name)
                try:
                    output.destination.replace(backup)
                except Exception:
                    backup.unlink(missing_ok=True)
                    raise
            transactions.append((output.destination, backup))
            output.temp_path.replace(output.destination)
            _normalize_permissions(output.destination)
    except Exception as publication_error:
        rollback_errors: list[str] = []
        for destination, backup in reversed(transactions):
            try:
                if destination.exists() or destination.is_symlink():
                    destination.unlink()
                if backup is not None:
                    if not backup.exists():
                        raise FileNotFoundError(f"missing backup {backup}")
                    backup.replace(destination)
            except Exception as rollback_error:
                rollback_errors.append(f"{destination}: {rollback_error}")
        if rollback_errors:
            retained_backups = [
                str(backup) for _, backup in transactions if backup is not None and backup.exists()
            ]
            backup_detail = ", ".join(retained_backups) or "none"
            raise RuntimeError(
                "Asset publication failed and rollback was incomplete; "
                f"retained backups: {backup_detail}; rollback errors: " + "; ".join(rollback_errors)
            ) from publication_error
        raise
    else:
        for _, backup in transactions:
            if backup is not None and backup.exists():
                backup.unlink()
    finally:
        _cleanup_prepared_outputs(outputs)


def _validate_archive_limits(
    archive: zipfile.ZipFile,
    archive_path: Path,
    limits: _AssetSeedLimits,
) -> dict[str, zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > limits.max_archive_members:
        raise RuntimeError(
            f"Archive {archive_path} has {len(members)} members; "
            f"limit is {limits.max_archive_members}"
        )

    members_by_name: dict[str, zipfile.ZipInfo] = {}
    total_size = 0
    for member in members:
        try:
            normalized_name = _safe_archive_member(
                member.filename,
                label=f"member path in archive {archive_path}",
            )
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        if normalized_name in members_by_name:
            raise RuntimeError(
                f"Archive {archive_path} has duplicate member path: {normalized_name}"
            )
        members_by_name[normalized_name] = member
        if member.flag_bits & 0x1:
            raise RuntimeError(
                f"Archive {archive_path} has encrypted member, which is unsupported: "
                f"{normalized_name}"
            )
        if member.file_size > limits.max_archive_member_bytes:
            raise RuntimeError(
                f"Archive {archive_path} member {normalized_name} expands to "
                f"{member.file_size} bytes; limit is {limits.max_archive_member_bytes}"
            )
        total_size += member.file_size
        if total_size > limits.max_archive_total_bytes:
            raise RuntimeError(
                f"Archive {archive_path} expands to more than "
                f"{limits.max_archive_total_bytes} total bytes"
            )
        if not member.is_dir() and member.file_size > 0:
            if member.compress_size <= 0:
                raise RuntimeError(
                    f"Archive {archive_path} member {normalized_name} has invalid compressed size"
                )
            compression_ratio = member.file_size / member.compress_size
            if compression_ratio > limits.max_compression_ratio:
                raise RuntimeError(
                    f"Archive {archive_path} member {normalized_name} has compression ratio "
                    f"{compression_ratio:.2f}; limit is {limits.max_compression_ratio:.2f}"
                )
    return members_by_name


def _response_content_length(response: Any) -> int | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw_value = headers.get("Content-Length")
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _check_stream_size(size_bytes: int, max_bytes: int, *, label: str) -> None:
    if size_bytes > max_bytes:
        raise RuntimeError(f"{label} exceeds {max_bytes} bytes (reported {size_bytes})")


def _copy_stream_limited(
    source: Any,
    destination: Any,
    max_bytes: int,
    *,
    label: str,
) -> int:
    total = 0
    while True:
        read_size = min(STREAM_CHUNK_BYTES, max_bytes - total + 1)
        chunk = source.read(read_size)
        if not chunk:
            return total
        if not isinstance(chunk, bytes):
            raise RuntimeError(f"{label} returned non-byte stream content")
        total += len(chunk)
        if total > max_bytes:
            raise RuntimeError(f"{label} exceeds {max_bytes} bytes while streaming")
        destination.write(chunk)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_matches(path: Path, expected: AssetSeedFile) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if path.stat().st_size != expected.size_bytes:
        return False
    return sha256_file(path).lower() == expected.sha256


def _write_manifest(seed_root: Path, asset_name: str, files: Iterable[AssetSeedFile]) -> None:
    manifest = {
        "schemaVersion": 1,
        "asset": asset_name,
        "files": [
            {
                "id": file.item_id,
                "path": file.relative_path,
                "sha256": file.sha256,
                "sizeBytes": file.size_bytes,
                "sourceURL": file.source_url,
            }
            for file in files
        ],
    }
    (seed_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def _asset_limits(asset_data: dict[str, Any], *, path_label: str) -> _AssetSeedLimits:
    limits_data = asset_data.get("limits", {})
    if not isinstance(limits_data, dict):
        raise ValueError(f"Invalid limits in {path_label}; expected object")
    unknown_fields = sorted(set(limits_data) - ASSET_LIMIT_FIELDS)
    if unknown_fields:
        raise ValueError(
            f"Invalid limits in {path_label}; unexpected fields: {', '.join(unknown_fields)}"
        )

    limits = _AssetSeedLimits(
        max_download_bytes=_positive_limit(
            limits_data,
            "maxDownloadBytes",
            DEFAULT_MAX_DOWNLOAD_BYTES,
            path_label=path_label,
        ),
        max_archive_members=_positive_limit(
            limits_data,
            "maxArchiveMembers",
            DEFAULT_MAX_ARCHIVE_MEMBERS,
            path_label=path_label,
        ),
        max_archive_member_bytes=_positive_limit(
            limits_data,
            "maxArchiveMemberBytes",
            DEFAULT_MAX_ARCHIVE_MEMBER_BYTES,
            path_label=path_label,
        ),
        max_archive_total_bytes=_positive_limit(
            limits_data,
            "maxArchiveTotalBytes",
            DEFAULT_MAX_ARCHIVE_TOTAL_BYTES,
            path_label=path_label,
        ),
        max_compression_ratio=_positive_ratio(
            limits_data,
            "maxCompressionRatio",
            DEFAULT_MAX_COMPRESSION_RATIO,
            path_label=path_label,
        ),
    )
    if limits.max_archive_total_bytes < limits.max_archive_member_bytes:
        raise ValueError(
            f"Invalid limits in {path_label}; maxArchiveTotalBytes must be at least "
            "maxArchiveMemberBytes"
        )
    return limits


def _positive_limit(
    data: dict[str, Any],
    field_name: str,
    default: int,
    *,
    path_label: str,
) -> int:
    value = data.get(field_name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Invalid limits.{field_name} in {path_label}; expected positive integer")
    return value


def _positive_ratio(
    data: dict[str, Any],
    field_name: str,
    default: float,
    *,
    path_label: str,
) -> float:
    value = data.get(field_name, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 1
    ):
        raise ValueError(
            f"Invalid limits.{field_name} in {path_label}; expected finite number >= 1"
        )
    return float(value)


def _asset_file_record(
    asset_name: str,
    item: dict[str, Any],
    relative_path: str,
    source_url: object,
) -> AssetSeedFile:
    size_bytes = _required_size(
        item,
        path_label=f"assets.{asset_name}.{relative_path}",
    )
    return AssetSeedFile(
        asset_name=asset_name,
        item_id=str(item.get("id") or relative_path),
        relative_path=relative_path,
        sha256=_required_sha256(item, "sha256", path_label=f"assets.{asset_name}.{relative_path}"),
        size_bytes=size_bytes,
        source_url=str(source_url) if isinstance(source_url, str) and source_url.strip() else None,
    )


def _validate_asset_item(
    repo_root: Path,
    seed_root: Path,
    item: Any,
    *,
    item_label: str,
    limits: _AssetSeedLimits,
) -> tuple[Path, ...]:
    del repo_root
    if not isinstance(item, dict):
        raise ValueError(f"Invalid {item_label}; expected object")
    item_type = _required_string(item, "type", path_label=item_label)
    if item_type not in ASSET_TYPES:
        raise ValueError(f"Invalid {item_label}.type {item_type!r}; expected one of {ASSET_TYPES}")
    _required_string(item, "url", path_label=item_label)
    _optional_http_accept(item, path_label=item_label)
    _required_sha256(item, "sha256", path_label=item_label)
    _required_file_name(item, "fileName", path_label=item_label)
    download_size = _required_size(item, path_label=item_label)
    if download_size > limits.max_download_bytes:
        raise ValueError(
            f"Invalid sizeBytes in {item_label}; {download_size} exceeds "
            f"maxDownloadBytes={limits.max_download_bytes}"
        )

    if item_type == "file":
        file_name = _required_file_name(item, "fileName", path_label=item_label)
        return ((seed_root / file_name).resolve(),)

    extract = item.get("extract")
    if not isinstance(extract, list) or not extract:
        raise ValueError(f"Invalid {item_label}.extract; expected non-empty list")
    if len(extract) > limits.max_archive_members:
        raise ValueError(
            f"Invalid {item_label}.extract; {len(extract)} entries exceed "
            f"maxArchiveMembers={limits.max_archive_members}"
        )
    destinations: list[Path] = []
    total_extract_size = 0
    for index, entry in enumerate(extract):
        entry_label = f"{item_label}.extract[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid {entry_label}; expected object")
        _safe_archive_member(
            _required_string(entry, "from", path_label=entry_label), label=f"{entry_label}.from"
        )
        relative_path = _safe_relative_path(
            _required_string(entry, "to", path_label=entry_label),
            label=f"{entry_label}.to",
        )
        _required_sha256(entry, "sha256", path_label=entry_label)
        size_bytes = _required_size(entry, path_label=entry_label)
        if size_bytes > limits.max_archive_member_bytes:
            raise ValueError(
                f"Invalid sizeBytes in {entry_label}; {size_bytes} exceeds "
                f"maxArchiveMemberBytes={limits.max_archive_member_bytes}"
            )
        total_extract_size += size_bytes
        if total_extract_size > limits.max_archive_total_bytes:
            raise ValueError(
                f"Invalid {item_label}.extract; declared size exceeds "
                f"maxArchiveTotalBytes={limits.max_archive_total_bytes}"
            )
        destinations.append((seed_root / relative_path).resolve())
    return tuple(destinations)


def _optional_http_accept(item: dict[str, Any], *, path_label: str) -> str | None:
    value = item.get("httpAccept")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or "\r" in value or "\n" in value:
        raise ValueError(f"Invalid {path_label}.httpAccept")
    return value.strip()


def _validate_asset_name(name: object, *, path_label: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"Invalid asset name in {path_label}; expected non-empty string")
    _safe_relative_path(name, label=f"asset name in {path_label}")


def _required_string(data: dict[str, Any], field_name: str, *, path_label: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid {field_name} in {path_label}; expected non-empty string")
    return value.strip()


def _required_sha256(data: dict[str, Any], field_name: str, *, path_label: str) -> str:
    value = _required_string(data, field_name, path_label=path_label).lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"Invalid {field_name} in {path_label}; expected SHA-256 hex digest")
    return value


def _required_file_name(data: dict[str, Any], field_name: str, *, path_label: str) -> str:
    value = _required_string(data, field_name, path_label=path_label)
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError(f"Invalid {field_name} in {path_label}; expected single file name")
    return value


def _required_size(data: dict[str, Any], *, path_label: str) -> int:
    size_bytes = data.get("sizeBytes")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
        raise ValueError(f"Invalid sizeBytes in {path_label}; expected positive integer")
    return size_bytes


def _safe_repo_relative_path(repo_root: Path, relative_path: str, *, label: str) -> Path:
    safe_relative = _safe_relative_path(relative_path, label=label)
    root = repo_root.resolve()
    resolved = (root / safe_relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Invalid {label}; resolved outside repository: {relative_path!r}"
        ) from exc
    return resolved


def _safe_relative_path(relative_path: str, *, label: str) -> PurePosixPath:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError(f"Invalid {label}; expected non-empty relative path")
    normalized = relative_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Invalid {label}; expected safe relative path: {relative_path!r}")
    return path


def _safe_archive_member(member: str, *, label: str) -> str:
    path = _safe_relative_path(member, label=label)
    return str(path)


def _ensure_safe_existing_destination(destination: Path) -> None:
    if destination.is_symlink():
        raise RuntimeError(f"Refusing to overwrite symlink asset destination: {destination}")
    if destination.exists() and not destination.is_file():
        raise RuntimeError(f"Refusing to overwrite non-file asset destination: {destination}")


def _normalize_permissions(path: Path) -> None:
    path.chmod(0o644)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify FreeCM locked asset seeds offline. "
            "Networked asset preparation is only allowed through --init."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Configured repository root. Defaults to the current directory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("verify", help="Verify locked assets without network.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    try:
        summaries = require_asset_seeds(repo_root)
    except (FileNotFoundError, RuntimeError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"[freecm] asset: {error}", file=sys.stderr)
        return 1
    for summary in summaries:
        print(
            f"[freecm] asset: {summary.asset_name}: {len(summary.files)} files -> {summary.seed_root}"
        )
    return 0


__all__ = (
    "AssetSeedFile",
    "AssetSeedSummary",
    "asset_seed_file_names",
    "asset_seed_root",
    "build_parser",
    "load_lock_assets",
    "main",
    "prepare_asset_seeds",
    "require_asset_seeds",
    "sha256_file",
    "validate_assets_lock_data",
)


if __name__ == "__main__":
    raise SystemExit(main())
