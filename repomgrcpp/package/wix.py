from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .common import PackageError


def stable_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def collect_files(root: Path) -> list[Path]:
    if not root.exists():
        raise PackageError(f"Source directory not found: {root}")
    if not root.is_dir():
        raise PackageError(f"Source path is not a directory: {root}")
    return sorted(
        (path.relative_to(root) for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.as_posix(),
    )


def collect_dirs(files: list[Path]) -> list[str]:
    dirs: set[str] = set()
    for rel in files:
        parts = rel.parts[:-1]
        acc: list[str] = []
        for part in parts:
            acc.append(part)
            dirs.add("/".join(acc))
    return sorted(dirs)


def generate_wix_fragment(
    source_root: Path,
    *,
    root_id: str,
    prefix: str,
    component_group_id: str | None = None,
) -> str:
    root = source_root.resolve()
    files = collect_files(root)
    dirs = collect_dirs(files)
    group_id = component_group_id or f"{prefix}Components"

    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">',
    ]

    for directory in dirs:
        dir_hash = stable_id(directory)
        dir_id = f"{prefix}Dir_{dir_hash}"
        parent = os.path.dirname(directory)
        parent_id = root_id if parent in ("", ".") else f"{prefix}Dir_{stable_id(parent)}"
        name = os.path.basename(directory)
        lines.extend(
            [
                "  <Fragment>",
                f'    <DirectoryRef Id="{parent_id}">',
                f'      <Directory Id="{dir_id}" Name="{name}"/>',
                "    </DirectoryRef>",
                "  </Fragment>",
            ]
        )

    component_refs: list[str] = []
    for rel in files:
        rel_posix = rel.as_posix()
        file_hash = stable_id(rel_posix)
        comp_id = f"{prefix}Comp_{file_hash}"
        file_id = f"{prefix}File_{file_hash}"
        parent = os.path.dirname(rel_posix)
        dir_id = root_id if parent in ("", ".") else f"{prefix}Dir_{stable_id(parent)}"
        source_path = (root / rel).as_posix()
        lines.extend(
            [
                "  <Fragment>",
                f'    <DirectoryRef Id="{dir_id}">',
                f'      <Component Id="{comp_id}" Guid="*" Win64="yes" Permanent="yes">',
                f'        <File Id="{file_id}" Source="{source_path}"/>',
                "      </Component>",
                "    </DirectoryRef>",
                "  </Fragment>",
            ]
        )
        component_refs.append(f'    <ComponentRef Id="{comp_id}"/>')

    lines.append("  <Fragment>")
    lines.append(f'    <ComponentGroup Id="{group_id}">')
    lines.extend(component_refs)
    lines.append("    </ComponentGroup>")
    lines.append("  </Fragment>")
    lines.append("</Wix>")
    return "\n".join(lines) + "\n"


def write_wix_fragment(
    source_root: Path,
    output_path: Path,
    *,
    root_id: str,
    prefix: str,
    component_group_id: str | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        generate_wix_fragment(
            source_root,
            root_id=root_id,
            prefix=prefix,
            component_group_id=component_group_id,
        ),
        encoding="utf-8",
    )
