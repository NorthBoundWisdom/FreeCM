# Usage:
#   python3 scripts/check-version-consistency.py
#   GITHUB_REF=refs/tags/v0.1.57 python3 scripts/check-version-consistency.py

from __future__ import annotations

import json
import os
import re
import sys
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10.
    tomllib = None


REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[A-Za-z0-9_.+-]+)?$")


def _read_version_file() -> str:
    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"VERSION contains invalid version {version!r}")
    return version


def _read_pyproject_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if tomllib is None:
        match = re.search(r'(?m)^version = "([^"]+)"$', text)
        if match is None:
            raise ValueError("Unable to find pyproject.toml project version")
        return match.group(1)
    data = tomllib.loads(text)
    return str(data["project"]["version"])


def _read_json_version(path: Path) -> str:
    return str(json.loads(path.read_text(encoding="utf-8"))["version"])


def _read_package_lock_versions(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    versions = {"root": str(data["version"])}
    packages = data.get("packages", {})
    if isinstance(packages, dict) and isinstance(packages.get(""), dict):
        versions["packages.root"] = str(packages[""].get("version"))
    return versions


def _tag_version_from_environment() -> str | None:
    github_ref = os.environ.get("GITHUB_REF", "")
    if github_ref.startswith("refs/tags/v"):
        return github_ref.removeprefix("refs/tags/v")
    github_ref_name = os.environ.get("GITHUB_REF_NAME", "")
    github_ref_type = os.environ.get("GITHUB_REF_TYPE", "")
    if github_ref_type == "tag" and github_ref_name.startswith("v"):
        return github_ref_name.removeprefix("v")
    return None


def _artifact_versions(expected: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for wheel_path in sorted((REPO_ROOT / "dist").glob("freecm-*.whl")):
        match = re.match(r"^freecm-(.+?)-", wheel_path.name)
        if match is None or match.group(1) != expected:
            continue
        versions[f"{wheel_path}:filename"] = match.group(1)
        with zipfile.ZipFile(wheel_path) as wheel:
            metadata_names = [
                name
                for name in wheel.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if metadata_names:
                metadata = wheel.read(metadata_names[0]).decode("utf-8", errors="replace")
                for line in metadata.splitlines():
                    if line.startswith("Version: "):
                        versions[f"{wheel_path}:metadata"] = line.removeprefix("Version: ")
                        break

    for vsix_path in sorted((REPO_ROOT / "plugin").glob("FreeCM_*_v*.vsix")):
        match = re.match(r"^FreeCM_.+_v(.+)\.vsix$", vsix_path.name)
        if match is None or match.group(1) != expected:
            continue
        versions[f"{vsix_path}:filename"] = match.group(1)
        with zipfile.ZipFile(vsix_path) as vsix:
            try:
                package_json = json.loads(vsix.read("extension/package.json").decode("utf-8"))
            except KeyError:
                continue
            versions[f"{vsix_path}:package.json"] = str(package_json.get("version"))
    return versions


def main() -> int:
    expected = _read_version_file()
    checks = {
        "pyproject.toml": _read_pyproject_version(),
        "vscode-extension/package.json": _read_json_version(
            REPO_ROOT / "vscode-extension" / "package.json"
        ),
    }
    checks.update(
        {
            f"vscode-extension/package-lock.json:{key}": value
            for key, value in _read_package_lock_versions(
                REPO_ROOT / "vscode-extension" / "package-lock.json"
            ).items()
        }
    )
    tag_version = _tag_version_from_environment()
    if tag_version is not None:
        checks["git tag"] = tag_version
    checks.update(_artifact_versions(expected))

    mismatches = {
        source: actual
        for source, actual in checks.items()
        if actual != expected
    }
    if mismatches:
        print(f"VERSION={expected}", file=sys.stderr)
        for source, actual in sorted(mismatches.items()):
            print(f"{source}={actual}", file=sys.stderr)
        return 1

    print(f"FreeCM version metadata is consistent: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
