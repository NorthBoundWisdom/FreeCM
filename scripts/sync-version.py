# Usage:
#   python3 scripts/sync-version.py

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _sync_pyproject(version: str) -> None:
    path = REPO_ROOT / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    updated = re.sub(
        r'(?m)^version = "[^"]+"$',
        f'version = "{version}"',
        text,
        count=1,
    )
    if updated == text:
        raise RuntimeError("Unable to find pyproject.toml project version")
    path.write_text(updated, encoding="utf-8")


def _sync_json_version(path: Path, version: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    if path.name == "package-lock.json":
        packages = data.get("packages", {})
        if isinstance(packages, dict) and isinstance(packages.get(""), dict):
            packages[""]["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    version = _version()
    _sync_pyproject(version)
    _sync_json_version(REPO_ROOT / "vscode-extension" / "package.json", version)
    _sync_json_version(REPO_ROOT / "vscode-extension" / "package-lock.json", version)
    print(f"Synchronized FreeCM version metadata to {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
