from __future__ import annotations

import shutil
from pathlib import Path

from .common import (
    PackageConfig,
    PackageError,
    clean_dist_dir,
    copy_configured_resources,
    copy_file,
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


def _macho_magic(path: Path) -> bytes:
    try:
        return path.read_bytes()[:4]
    except OSError:
        return b""


def is_macho_file(path: Path) -> bool:
    return _macho_magic(path) in {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xd0\x0d",
        b"\x0d\xd0\xfe\xca",
    }


def parse_otool_rpaths(output: str) -> list[str]:
    rpaths: list[str] = []
    in_rpath = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped == "cmd LC_RPATH":
            in_rpath = True
            continue
        if in_rpath and stripped.startswith("path "):
            rpaths.append(stripped.split(" (offset", 1)[0].removeprefix("path ").strip())
            in_rpath = False
    return list(dict.fromkeys(rpaths))


def build_sign_command(
    path: Path, *, identity: str = "-", entitlements: Path | None = None, runtime: bool = False
) -> list[str]:
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
        if path.is_file() and not path.is_symlink() and path.suffix in {".dylib", ".so", ".bundle"}:
            binaries.append(path)
    macos_dir = contents / "MacOS"
    if macos_dir.exists():
        binaries.extend(
            path for path in macos_dir.rglob("*") if path.is_file() and not path.is_symlink()
        )
    for path in contents.rglob("*"):
        if not path.is_file() or path.is_symlink() or not is_macho_file(path):
            continue
        framework_part = next((part for part in path.parts if part.endswith(".framework")), None)
        if framework_part:
            framework_name = Path(framework_part).stem
            if path.name == framework_name:
                binaries.append(path)
    return sorted(set(binaries))


def _safe_install_name_tool(args: list[str], *, prefix: str) -> None:
    completed = run_command(["install_name_tool", *args], capture=True, prefix=prefix)
    if completed.returncode != 0:
        warn(f"install_name_tool failed ({completed.returncode}): {' '.join(args)}", prefix=prefix)


def normalize_bundle_rpaths(bundle: Path, *, prefix: str) -> None:
    bundle_framework_rpath = "@executable_path/../Frameworks"
    absolute_prefixes = ("/opt/homebrew/", "/usr/local/")
    for binary in _bundle_binaries(bundle):
        completed = run_command(["otool", "-l", str(binary)], capture=True, prefix=prefix)
        if completed.returncode != 0:
            continue
        rpaths = parse_otool_rpaths(completed.stdout or "")
        if not rpaths:
            continue
        for rpath in rpaths:
            _safe_install_name_tool(["-delete_rpath", rpath, str(binary)], prefix=prefix)
        ordered = [bundle_framework_rpath]
        ordered.extend(
            rpath
            for rpath in rpaths
            if rpath != bundle_framework_rpath and not rpath.startswith(absolute_prefixes)
        )
        for rpath in dict.fromkeys(ordered):
            _safe_install_name_tool(["-add_rpath", rpath, str(binary)], prefix=prefix)


def verify_no_homebrew_qt_resolution(bundle: Path, *, app_name: str) -> None:
    frameworks_dir = bundle / "Contents" / "Frameworks"
    executable = bundle / "Contents" / "MacOS" / app_name
    for framework in ("QtCore.framework", "QtGui.framework"):
        if not (frameworks_dir / framework).exists():
            raise PackageError(f"Missing bundled Qt framework: {framework}")
    completed = run_command(["otool", "-l", str(executable)], capture=True, prefix="deploy_mac")
    if completed.returncode != 0:
        return
    rpaths = parse_otool_rpaths(completed.stdout or "")
    if not rpaths or rpaths[0] != "@executable_path/../Frameworks":
        raise PackageError(
            "Bundle framework rpath is not first; Qt may resolve to Homebrew instead"
        )
    for rpath in rpaths:
        if "qtbase" in rpath.lower() or rpath == "/opt/homebrew/lib":
            raise PackageError(f"Unsafe Qt-resolving rpath remains in app executable: {rpath}")


def _copy_libraries_by_name(config: PackageConfig, deployed_app: Path, *, prefix: str) -> None:
    frameworks_dir = deployed_app / "Contents" / "Frameworks"
    ensure_dir(frameworks_dir)
    search_paths = config.optional_path_list("mac.librarySearchPaths")
    for library_name in config.optional_string_list("mac.copyLibraryNames"):
        found = find_library(library_name, search_paths)
        if found:
            copy_file(found, frameworks_dir, required=False, prefix=prefix)
        else:
            warn(f"Library not found, skipped: {library_name}", prefix=prefix)


def _copy_globbed_libraries(config: PackageConfig, deployed_app: Path, *, prefix: str) -> None:
    frameworks_dir = deployed_app / "Contents" / "Frameworks"
    ensure_dir(frameworks_dir)
    for pattern in config.optional_string_list("mac.libraryGlobs"):
        matched = False
        for search_path in config.optional_path_list("mac.librarySearchPaths"):
            if not search_path.exists():
                continue
            for library in sorted(search_path.glob(pattern)):
                if library.is_file():
                    matched = True
                    copy_file(library, frameworks_dir, required=False, prefix=prefix)
        if not matched:
            log(f"No library matched pattern: {pattern}", prefix=prefix)


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

    background = config.path("mac.dmgBackground", required=False)
    if str(background):
        background_dir = dist_dir / ".background"
        copy_file(background, background_dir, required=False, prefix=prefix)
        copied = background_dir / background.name
        if copied.exists() and copied.name != "background.png":
            shutil.copy2(copied, background_dir / "background.png")

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
    _copy_libraries_by_name(config, deployed_app, prefix=prefix)
    _copy_globbed_libraries(config, deployed_app, prefix=prefix)

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

    if config.optional_bool("mac.normalizeRpaths", False):
        normalize_bundle_rpaths(deployed_app, prefix=prefix)
    if config.optional_bool("mac.verifyBundledQt", False):
        verify_no_homebrew_qt_resolution(deployed_app, app_name=app_name)

    for binary in _bundle_binaries(deployed_app):
        if binary.suffix == ".dylib":
            run_command(
                ["install_name_tool", "-id", f"@rpath/{binary.name}", str(binary)], prefix=prefix
            )
        run_command(build_sign_command(binary, identity=sign_identity), prefix=prefix)

    run_command(
        build_sign_command(
            deployed_app, identity=sign_identity, entitlements=entitlements, runtime=True
        ),
        check=False,
        prefix=prefix,
    )
    log(f"Deployment completed for {app_name}: {deployed_app}", prefix=prefix)
    return deployed_app
