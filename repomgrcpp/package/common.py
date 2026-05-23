from __future__ import annotations

import json
import os
import shutil
import subprocess
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
        value = self.required_string(dotted_key) if required else self.optional_string(dotted_key, default)
        if not required and not value:
            return Path("")
        return resolve_path(value, self.base_dir)

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


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


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
    if target != root and not is_relative_to(target, root):
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
        "qt.binDir",
        "qt.qmlDir",
    ):
        config.required_string(key)
    resource_section = config.section("resources")
    for field in ("copyTrees", "copyFiles"):
        value = resource_section.get(field, [])
        if value is None:
            continue
        if not isinstance(value, list):
            raise PackageError(f"Invalid resources.{field}; expected array")
        for index, entry in enumerate(value):
            if not isinstance(entry, dict):
                raise PackageError(f"Invalid resources.{field}[{index}]; expected object")
            destination_key = "destination" if field == "copyTrees" else "destinationDir"
            destination_value = str(entry.get(destination_key, ""))
            validate_relative_path_fragment(
                destination_value,
                label=f"resources.{field}[{index}].{destination_key}",
            )
    for index, value in enumerate(resource_section.get("remove", []) or []):
        if not isinstance(value, str) or not value:
            raise PackageError(f"Invalid resources.remove[{index}]; expected non-empty string")
        validate_relative_path_fragment(value, label=f"resources.remove[{index}]")


def validate_platform_config(config: PackageConfig, platform: str) -> None:
    if platform == "win":
        config.required_string("windows.windeployqt")
    elif platform == "mac":
        config.required_string("mac.bundlePath")
        config.required_string("mac.entitlementsFile")
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
    check: bool = False,
    capture: bool = False,
    cwd: Path | None = None,
    prefix: str = "package",
) -> subprocess.CompletedProcess[str]:
    log("run: " + " ".join(command), prefix=prefix)
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=capture,
        text=True,
        check=False,
    )
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
    shutil.copy2(src, dst_dir)
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
        shutil.rmtree(path)
    ensure_dir(path)


def clean_dist_dir(config: PackageConfig, dist_dir: Path) -> None:
    binary_dir = config.path("paths.binaryDir").resolve()
    dist_dir = dist_dir.resolve()
    if dist_dir == binary_dir or not is_relative_to(dist_dir, binary_dir):
        raise PackageError(
            "Invalid paths.distDir: deployment cleanup must target a child directory under paths.binaryDir"
        )
    clean_dir(dist_dir)


def copy_configured_resources(config: PackageConfig, destination_root: Path, *, prefix: str) -> None:
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
        if source.exists():
            ensure_dir(target)
            for qm in source.glob("*.qm"):
                copy_file(qm, target, prefix=prefix)
        else:
            warn(f"Translation directory not found: {source}", prefix=prefix)

    fonts_dir = resources.get("fontsDir")
    if isinstance(fonts_dir, str) and fonts_dir:
        copy_tree(
            resolve_path(fonts_dir, config.base_dir),
            destination_root / "Fonts",
            required=False,
            prefix=prefix,
        )

    for entry in resources.get("copyTrees", []) or []:
        source = resolve_path(str(entry.get("source", "")), config.base_dir)
        destination = contained_child(
            destination_root,
            str(entry.get("destination", "")),
            label="resources.copyTrees.destination",
        )
        copy_tree(source, destination, required=bool(entry.get("required", True)), prefix=prefix)

    for entry in resources.get("copyFiles", []) or []:
        source = resolve_path(str(entry.get("source", "")), config.base_dir)
        destination = contained_child(
            destination_root,
            str(entry.get("destinationDir", "")),
            label="resources.copyFiles.destinationDir",
        )
        copy_file(source, destination, required=bool(entry.get("required", True)), prefix=prefix)
