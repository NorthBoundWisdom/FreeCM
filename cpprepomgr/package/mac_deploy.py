from __future__ import annotations

import shutil
from pathlib import Path

from .common import (
    PackageConfig,
    PackageError,
    clean_dist_dir,
    copy_configured_resources,
    copy_file,
    copy_tree,
    ensure_dir,
    log,
    run_command,
    warn,
)


def parse_otool_deps(output: str) -> list[str]:
    deps: list[str] = []
    for line in output.splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        deps.append(stripped.split(" (compatibility version")[0].strip())
    return deps


def find_library(name: str, search_paths: list[Path]) -> Path | None:
    for base in search_paths:
        if not base.exists():
            continue
        candidate = base / name
        if candidate.exists():
            return candidate
        for found in base.rglob(name):
            if found.exists():
                return found
    return None


def build_sign_command(path: Path, *, identity: str = "-", entitlements: Path | None = None, runtime: bool = False) -> list[str]:
    command = ["codesign", "--force", "--sign", identity]
    if entitlements is not None:
        command.extend(["--entitlements", str(entitlements)])
    if runtime:
        command.extend(["--options", "runtime"])
    command.extend(["--timestamp", str(path)])
    return command


def _bundle_binaries(bundle: Path) -> list[Path]:
    binaries: list[Path] = []
    contents = bundle / "Contents"
    for path in contents.rglob("*"):
        if path.is_file() and path.suffix in {".dylib", ".so", ".bundle"}:
            binaries.append(path)
    macos_dir = contents / "MacOS"
    if macos_dir.exists():
        binaries.extend(path for path in macos_dir.rglob("*") if path.is_file())
    return sorted(set(binaries))


def deploy_mac(config: PackageConfig) -> Path:
    prefix = "deploy_mac"
    app_name = config.required_string("app.name")
    display_name = config.required_string("app.displayName")
    source_bundle = config.path("mac.bundlePath")
    dist_dir = config.path("paths.distDir")
    qt_bin_dir = config.path("qt.binDir")
    qml_dir = config.path("qt.qmlDir")
    entitlements = config.path("mac.entitlementsFile")
    sign_identity = config.optional_string("mac.signIdentity", "-") or "-"

    if not source_bundle.exists():
        raise PackageError(f"App bundle not found: {source_bundle}")
    if not entitlements.is_file():
        raise PackageError(f"Entitlements file not found: {entitlements}")

    clean_dist_dir(config, dist_dir)
    deployed_app = dist_dir / f"{display_name}.app"
    shutil.copytree(source_bundle, deployed_app, symlinks=True)

    macdeployqt = qt_bin_dir / "macdeployqt"
    run_command(
        [
            str(macdeployqt),
            str(deployed_app),
            "-verbose=1",
            f"-qmldir={qml_dir}",
            "-always-overwrite",
            "-appstore-compliant",
        ],
        prefix=prefix,
    )

    resources_dir = deployed_app / "Contents" / "Resources"
    frameworks_dir = deployed_app / "Contents" / "Frameworks"
    ensure_dir(resources_dir)
    ensure_dir(frameworks_dir)
    copy_configured_resources(config, resources_dir, prefix=prefix)

    for library in config.optional_path_list("mac.extraLibraries"):
        copy_file(library, frameworks_dir, required=False, prefix=prefix)

    search_paths = config.optional_path_list("mac.librarySearchPaths")
    if search_paths:
        for binary in _bundle_binaries(deployed_app):
            completed = run_command(["otool", "-L", str(binary)], capture=True, prefix=prefix)
            if completed.returncode != 0:
                continue
            for dep in parse_otool_deps(completed.stdout or ""):
                dep_name = Path(dep).name
                if dep.startswith("@") or not dep_name.endswith(".dylib"):
                    continue
                found = find_library(dep_name, search_paths)
                if found:
                    copy_file(found, frameworks_dir, required=False, prefix=prefix)
                    run_command(
                        ["install_name_tool", "-change", dep, f"@rpath/{dep_name}", str(binary)],
                        prefix=prefix,
                    )

    for binary in _bundle_binaries(deployed_app):
        if binary.suffix == ".dylib":
            run_command(["install_name_tool", "-id", f"@rpath/{binary.name}", str(binary)], prefix=prefix)
        run_command(build_sign_command(binary, identity=sign_identity), prefix=prefix)

    run_command(
        build_sign_command(deployed_app, identity=sign_identity, entitlements=entitlements, runtime=True),
        check=False,
        prefix=prefix,
    )
    log(f"Deployment completed for {app_name}: {deployed_app}", prefix=prefix)
    return deployed_app
