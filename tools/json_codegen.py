# Usage:
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.tools.repo_tool generate-json-keys --input <json> --output <header> --namespace <ns> --header-guard <guard>
#   PYTHONPATH=/path/to/FreeCM python3 -m repomgrcpp.tools.repo_tool dedup-json-array --input <json> --array-key <array> --dedup-key <key>
#   Library: from tools.json_codegen import collect_json_keys_from_files, deduplicate_json_array

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DeduplicationResult:
    data: dict[str, Any]
    original_count: int
    deduplicated_count: int
    removed_indices: tuple[int, ...]


def collect_json_keys(data: Any, keys: set[str] | None = None) -> set[str]:
    if keys is None:
        keys = set()
    if isinstance(data, dict):
        for key, value in data.items():
            keys.add(str(key))
            if isinstance(value, (dict, list)):
                collect_json_keys(value, keys)
    elif isinstance(data, list):
        for item in data:
            collect_json_keys(item, keys)
    return keys


def collect_json_keys_from_files(paths: Iterable[Path]) -> set[str]:
    keys: set[str] = set()
    for path in paths:
        with path.open("r", encoding="utf-8") as input_file:
            collect_json_keys(json.load(input_file), keys)
    return keys


def _value_at_path(item: Any, path: str) -> Any:
    current = item
    for token in path.split("."):
        if not token:
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(token)
    return current


def canonical_json_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def deduplicate_json_array(
    data: dict[str, Any],
    *,
    array_key: str,
    dedup_key: str,
) -> DeduplicationResult:
    items = data.get(array_key)
    if not isinstance(items, list):
        raise ValueError(f"JSON field {array_key!r} must be an array")

    seen: set[str] = set()
    deduplicated: list[Any] = []
    removed_indices: list[int] = []
    for index, item in enumerate(items):
        key_value = _value_at_path(item, dedup_key)
        key = canonical_json_key(key_value)
        if key in seen:
            removed_indices.append(index)
            continue
        seen.add(key)
        deduplicated.append(item)

    result_data = dict(data)
    result_data[array_key] = deduplicated
    return DeduplicationResult(
        data=result_data,
        original_count=len(items),
        deduplicated_count=len(deduplicated),
        removed_indices=tuple(removed_indices),
    )


def load_json_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as input_file:
        data = json.load(input_file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected top-level JSON object: {path}")
    return data


def write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
