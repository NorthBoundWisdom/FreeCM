from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PackageError(RuntimeError):
    pass


@dataclass(frozen=True)
class PackageConfig:
    data: dict[str, Any]
    base_dir: Path

    def section(self, name: str) -> dict[str, Any]:
        value = self.data.get(name, {})
        if not isinstance(value, dict):
            raise PackageError(f"Invalid config section '{name}'; expected object")
        return value

    def required_string(self, dotted_key: str) -> str:
        value = nested_get(self.data, dotted_key)
        if not isinstance(value, str) or not value:
            raise PackageError(f"Missing required string config: {dotted_key}")
        return value

    def optional_string(self, dotted_key: str, default: str = "") -> str:
        value = nested_get(self.data, dotted_key, default)
        if value is None:
            return default
        if not isinstance(value, str):
            raise PackageError(f"Invalid string config: {dotted_key}")
        return value

    def optional_bool(self, dotted_key: str, default: bool = False) -> bool:
        value = nested_get(self.data, dotted_key, default)
        if isinstance(value, bool):
            return value
        raise PackageError(f"Invalid boolean config: {dotted_key}")

    def path(self, dotted_key: str, *, required: bool = True, default: str = "") -> Path:
        value = (
            self.required_string(dotted_key)
            if required
            else self.optional_string(dotted_key, default)
        )
        if not required and not value:
            return Path("")
        return resolve_path(value, self.base_dir)

    def optional_path(self, dotted_key: str) -> Path | None:
        value = self.optional_string(dotted_key, "")
        return resolve_path(value, self.base_dir) if value else None

    def optional_path_list(self, dotted_key: str) -> list[Path]:
        values = nested_get(self.data, dotted_key, [])
        if values is None:
            return []
        if not isinstance(values, list):
            raise PackageError(f"Invalid path list config: {dotted_key}")
        result: list[Path] = []
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value:
                raise PackageError(f"Invalid path string at {dotted_key}[{index}]")
            result.append(resolve_path(value, self.base_dir))
        return result

    def optional_string_list(self, dotted_key: str) -> list[str]:
        values = nested_get(self.data, dotted_key, [])
        if values is None:
            return []
        if not isinstance(values, list):
            raise PackageError(f"Invalid string list config: {dotted_key}")
        result: list[str] = []
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value:
                raise PackageError(f"Invalid string at {dotted_key}[{index}]")
            result.append(value)
        return result


def nested_get(data: dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    current: Any = data
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def resolve_path(value: str | Path, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def validate_relative_path_fragment(value: str, *, label: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise PackageError(f"Invalid {label}: absolute destinations are not allowed")
    parts = path.parts
    if any(part == ".." for part in parts):
        raise PackageError(f"Invalid {label}: parent traversal is not allowed")
    return path


def contained_child(root: Path, relative_value: str, *, label: str) -> Path:
    relative = validate_relative_path_fragment(relative_value, label=label)
    root = root.resolve()
    target = (root / relative).resolve()
    if target != root and not target.is_relative_to(root):
        raise PackageError(f"Invalid {label}: resolved outside destination root")
    return target


def load_package_config(path: Path, *, platform: str | None = None) -> PackageConfig:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackageError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PackageError(f"Invalid package config in {path}; expected object")
    config = PackageConfig(data=data, base_dir=path.resolve().parent)
    validate_common_config(config)
    if platform:
        validate_platform_config(config, platform)
    return config


def validate_common_config(config: PackageConfig) -> None:
    for key in (
        "app.name",
        "app.displayName",
        "app.version",
        "paths.sourceDir",
        "paths.binaryDir",
        "paths.targetPath",
        "paths.distDir",
    ):
        config.required_string(key)
    resource_section = config.section("resources")
    for field, destination_key in (
        ("copyTrees", "destination"),
        ("copyFiles", "destinationDir"),
    ):
        value = resource_section.get(field, [])
        if not isinstance(value, list):
            raise PackageError(f"Invalid resources.{field}; expected array")
        for index, entry in enumerate(value):
            if not isinstance(entry, dict):
                raise PackageError(f"Invalid resources.{field}[{index}]; expected object")
            allowed_keys = {"source", destination_key, "required"}
            unknown_keys = sorted(set(entry) - allowed_keys)
            if unknown_keys:
                raise PackageError(
                    f"Invalid resources.{field}[{index}]; unknown fields: "
                    + ", ".join(unknown_keys)
                )
            source_value = entry.get("source")
            if not isinstance(source_value, str) or not source_value:
                raise PackageError(
                    f"Invalid resources.{field}[{index}].source; expected non-empty string"
                )
            destination_value = entry.get(destination_key)
            if not isinstance(destination_value, str) or not destination_value:
                raise PackageError(
                    f"Invalid resources.{field}[{index}].{destination_key}; "
                    "expected non-empty string"
                )
            validate_relative_path_fragment(
                destination_value,
                label=f"resources.{field}[{index}].{destination_key}",
            )
            required = entry.get("required", True)
            if not isinstance(required, bool):
                raise PackageError(f"Invalid resources.{field}[{index}].required; expected boolean")
    remove_values = resource_section.get("remove", [])
    if not isinstance(remove_values, list):
        raise PackageError("Invalid resources.remove; expected array")
    for index, value in enumerate(remove_values):
        if not isinstance(value, str) or not value:
            raise PackageError(f"Invalid resources.remove[{index}]; expected non-empty string")
        validate_relative_path_fragment(value, label=f"resources.remove[{index}]")
    for field in ("translationsDir", "fontsDir"):
        if field not in resource_section:
            continue
        value = resource_section[field]
        if not isinstance(value, str) or not value:
            raise PackageError(f"Invalid resources.{field}; expected non-empty string")


def validate_platform_config(config: PackageConfig, platform: str) -> None:
    if platform == "win":
        config.required_string("windows.windeployqt")
        config.required_string("qt.binDir")
        config.required_string("qt.qmlDir")
    elif platform == "mac":
        config.required_string("mac.bundlePath")
        config.required_string("mac.entitlementsFile")
        deployment_tool = config.required_string("mac.deploymentTool")
        if deployment_tool not in {"native", "qt"}:
            raise PackageError("Invalid mac.deploymentTool; expected one of: native, qt")
        if deployment_tool == "qt":
            config.required_string("qt.binDir")
            config.required_string("qt.qmlDir")
        dmg_output = config.optional_string("mac.dmgOutputPath", "")
        dmg_volume = config.optional_string("mac.dmgVolumeName", "")
        if dmg_output:
            if not dmg_output.lower().endswith(".dmg"):
                raise PackageError("Invalid mac.dmgOutputPath; expected a .dmg file")
            if not dmg_volume:
                raise PackageError("Missing required string config: mac.dmgVolumeName")
        elif dmg_volume:
            raise PackageError("mac.dmgVolumeName requires mac.dmgOutputPath")
    elif platform == "linux":
        config.required_string("linux.packageName")
    else:
        raise PackageError(f"Unsupported package platform: {platform}")


def log(message: str, *, prefix: str = "package") -> None:
    print(f"[{prefix}] {message}")


def warn(message: str, *, prefix: str = "package") -> None:
    print(f"[{prefix}][warn] {message}")


def run_command(
    command: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    cwd: Path | None = None,
    prefix: str = "package",
) -> subprocess.CompletedProcess[str]:
    log("run: " + " ".join(command), prefix=prefix)
    try:
        completed = subprocess.run(  # nosec B603
            command,
            cwd=cwd,
            capture_output=capture,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise PackageError(f"unable to run command: {' '.join(command)}: {exc}") from exc
    if capture and completed.stdout:
        log(completed.stdout.strip(), prefix=prefix)
    if capture and completed.stderr:
        warn(completed.stderr.strip(), prefix=prefix)
    if check and completed.returncode != 0:
        raise PackageError(f"command failed ({completed.returncode}): {' '.join(command)}")
    return completed


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def copy_file(src: Path, dst_dir: Path, *, required: bool = True, prefix: str = "package") -> bool:
    if not src.exists():
        if required:
            raise PackageError(f"Required file not found: {src}")
        warn(f"File not found, skipped: {src}", prefix=prefix)
        return False
    ensure_dir(dst_dir)
    try:
        shutil.copy2(src, dst_dir)
    except OSError as exc:
        raise PackageError(f"Failed to copy file: {src} -> {dst_dir}: {exc}") from exc
    return True


def copy_tree(src: Path, dst: Path, *, required: bool = True, prefix: str = "package") -> bool:
    if not src.exists():
        if required:
            raise PackageError(f"Required directory not found: {src}")
        warn(f"Directory not found, skipped: {src}", prefix=prefix)
        return False
    if not src.is_dir():
        raise PackageError(f"Expected directory: {src}")
    for root, dirs, files in os.walk(src):
        root_path = Path(root)
        rel = root_path.relative_to(src)
        target_root = dst / rel if rel != Path(".") else dst
        ensure_dir(target_root)
        for dirname in dirs:
            ensure_dir(target_root / dirname)
        for filename in files:
            shutil.copy2(root_path / filename, target_root / filename)
    return True


def clean_dir(path: Path) -> None:
    if path.exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_make_writable_and_retry)
        else:
            shutil.rmtree(path, onerror=_make_writable_and_retry_legacy)
    ensure_dir(path)


def _make_writable_and_retry(
    function: Callable[[str], object], path: str, excinfo: BaseException
) -> None:
    if not isinstance(excinfo, PermissionError):
        raise excinfo
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _make_writable_and_retry_legacy(
    function: Callable[[str], object],
    path: str,
    excinfo: tuple[type[BaseException], BaseException, object],
) -> None:
    _make_writable_and_retry(function, path, excinfo[1])


def clean_dist_dir(config: PackageConfig, dist_dir: Path) -> None:
    binary_dir = config.path("paths.binaryDir").resolve()
    dist_dir = dist_dir.resolve()
    if dist_dir == binary_dir or not dist_dir.is_relative_to(binary_dir):
        raise PackageError(
            "Invalid paths.distDir: deployment cleanup must target a child directory under paths.binaryDir"
        )
    clean_dir(dist_dir)


def copy_configured_resources(
    config: PackageConfig, destination_root: Path, *, prefix: str
) -> None:
    resources = config.section("resources")
    for relative in config.optional_string_list("resources.remove"):
        target = contained_child(destination_root, relative, label="resources.remove")
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()

    translations_dir = resources.get("translationsDir")
    if isinstance(translations_dir, str) and translations_dir:
        source = resolve_path(translations_dir, config.base_dir)
        target = destination_root / "i18n"
        if not source.is_dir():
            raise PackageError(f"Configured translation directory not found: {source}")
        ensure_dir(target)
        for qm in source.glob("*.qm"):
            copy_file(qm, target, prefix=prefix)

    fonts_dir = resources.get("fontsDir")
    if isinstance(fonts_dir, str) and fonts_dir:
        copy_tree(
            resolve_path(fonts_dir, config.base_dir),
            destination_root / "Fonts",
            required=True,
            prefix=prefix,
        )

    for entry in resources.get("copyTrees", []):
        source = resolve_path(entry["source"], config.base_dir)
        destination = contained_child(
            destination_root,
            entry["destination"],
            label="resources.copyTrees.destination",
        )
        copy_tree(source, destination, required=entry.get("required", True), prefix=prefix)

    for entry in resources.get("copyFiles", []):
        source = resolve_path(entry["source"], config.base_dir)
        destination = contained_child(
            destination_root,
            entry["destinationDir"],
            label="resources.copyFiles.destinationDir",
        )
        copy_file(source, destination, required=entry.get("required", True), prefix=prefix)
