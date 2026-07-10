from __future__ import annotations

import os
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

SYSTEM_LIBRARY_PREFIXES = (
    "libc.so",
    "libm.so",
    "libpthread.so",
    "libdl.so",
    "librt.so",
    "libgcc_s.so",
    "libstdc++.so",
    "ld-linux",
    "libgomp.so",
    "libatomic.so",
    "libresolv.so",
    "libutil.so",
)


def should_skip_system_library(name: str) -> bool:
    return name.startswith(SYSTEM_LIBRARY_PREFIXES)


def generate_apprun(
    *,
    app_name: str,
    debug_build: bool = False,
    enable_fallback_default: str = "0",
) -> str:
    debug_literal = "true" if debug_build else "false"
    return f"""#!/bin/bash
HERE="$(dirname "$(readlink -f "${{0}}")")"
export PREFIX=${{HERE}}/usr
export LD_LIBRARY_PATH=${{PREFIX}}/lib:${{LD_LIBRARY_PATH}}
export PATH=${{PREFIX}}/bin:${{PATH}}
export QT_PLUGIN_PATH=${{PREFIX}}/plugins
export QT_QPA_PLATFORM_PLUGIN_PATH=${{PREFIX}}/plugins/platforms
export QML_IMPORT_PATH=${{PREFIX}}/qml
export QT_QML_IMPORT_PATH=${{PREFIX}}/qml
export APP_ENABLE_FALLBACK="${{APP_ENABLE_FALLBACK:-{enable_fallback_default}}}"
APP_PLATFORM="${{APP_PLATFORM:-auto}}"
DEBUG_BUILD="{debug_literal}"

debug_log() {{
    echo "[AppRun Debug]: $1" >&2
    if [ "$DEBUG_BUILD" = true ] && [ -w "${{PREFIX}}/logs" ]; then
        echo "$(date): [AppRun Debug]: $1" >> "${{PREFIX}}/logs/apprun.log"
    fi
}}

if [ -z "$QT_QPA_PLATFORM" ]; then
    XCB_PLUGIN="${{PREFIX}}/plugins/platforms/libqxcb.so"
    WAYLAND_PLUGIN="${{PREFIX}}/plugins/platforms/libqwayland-egl.so"
    case "${{APP_PLATFORM}}" in
        wayland)
            [ -f "$WAYLAND_PLUGIN" ] && export QT_QPA_PLATFORM=wayland
            ;;
        xcb)
            [ -f "$XCB_PLUGIN" ] && export QT_QPA_PLATFORM=xcb
            [ -z "$DISPLAY" ] && export DISPLAY=:0
            ;;
    esac
    if [ -z "$QT_QPA_PLATFORM" ]; then
        if [ "$XDG_SESSION_TYPE" = "wayland" ] && [ -n "$WAYLAND_DISPLAY" ] && [ -f "$WAYLAND_PLUGIN" ]; then
            export QT_QPA_PLATFORM=wayland
        elif [ -f "$XCB_PLUGIN" ]; then
            export QT_QPA_PLATFORM=xcb
            [ -z "$DISPLAY" ] && export DISPLAY=:0
        fi
    fi
fi

run_with_fallback() {{
    "${{PREFIX}}/bin/{app_name}" "$@"
    local exit_code=$?
    if [ $exit_code -ne 0 ] && [ "$QT_QPA_PLATFORM" = "wayland" ] && [ "${{APP_ENABLE_FALLBACK}}" = "1" ] && [ -f "${{PREFIX}}/plugins/platforms/libqxcb.so" ]; then
        debug_log "Wayland failed, trying XCB fallback..."
        export QT_QPA_PLATFORM=xcb
        export DISPLAY=${{DISPLAY:-:0}}
        "${{PREFIX}}/bin/{app_name}" "$@"
        exit_code=$?
    fi
    return $exit_code
}}

run_with_fallback "$@"
"""


def copy_lib_with_deps(lib: Path, dest_dir: Path) -> None:
    lib_name = lib.name
    if should_skip_system_library(lib_name):
        return
    if not lib.exists():
        raise PackageError(f"Configured Linux library not found: {lib}")
    if not lib.is_file():
        raise PackageError(f"Configured Linux library is not a file: {lib}")
    ensure_dir(dest_dir)
    target = dest_dir / lib_name
    if target.exists():
        return
    if lib.is_symlink():
        real_file = lib.resolve()
        real_target = dest_dir / real_file.name
        if not real_target.exists():
            shutil.copy2(real_file, real_target)
        target.symlink_to(real_file.name)
    else:
        shutil.copy2(lib, target)


def deploy_linux(config: PackageConfig) -> Path:
    prefix = "deploy_linux"
    app_name = config.required_string("app.name")
    display_name = config.required_string("app.displayName")
    target_path = config.path("paths.targetPath")
    dist_dir = config.path("paths.distDir")
    qt_bin_dir = config.path("qt.binDir")
    package_name = config.required_string("linux.packageName")
    debug_package = config.optional_bool("linux.debugPackage", False)

    if not target_path.is_file():
        raise PackageError(f"Target executable not found: {target_path}")
    clean_dist_dir(config, dist_dir)
    appdir = dist_dir
    bin_dir = appdir / "usr" / "bin"
    lib_dir = appdir / "usr" / "lib"
    plugins_dir = appdir / "usr" / "plugins"
    ensure_dir(bin_dir)
    ensure_dir(lib_dir)
    ensure_dir(plugins_dir)
    if debug_package:
        ensure_dir(appdir / "usr" / "debug")
        ensure_dir(appdir / "usr" / "logs")

    copy_file(target_path, bin_dir, prefix=prefix)
    copied_binary = bin_dir / target_path.name
    app_binary = bin_dir / app_name
    if copied_binary != app_binary:
        copied_binary.rename(app_binary)
    os.chmod(bin_dir / app_name, 0o755)  # nosec B103
    copy_configured_resources(config, bin_dir, prefix=prefix)

    icon_file = config.optional_path("linux.iconFile")
    if icon_file is not None:
        copy_file(icon_file, appdir, prefix=prefix)
        copied_icon = appdir / icon_file.name
        target_icon = appdir / f"{app_name}.png"
        if copied_icon.exists() and copied_icon != target_icon:
            copied_icon.replace(target_icon)

    app_run = appdir / "AppRun"
    app_run.write_text(
        generate_apprun(app_name=app_name, debug_build=debug_package), encoding="utf-8"
    )
    os.chmod(app_run, 0o755)  # nosec B103

    desktop = appdir / f"{app_name}.desktop"
    categories = config.optional_string("linux.desktop.categories", "Utility;")
    desktop.write_text(
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        f"Name={display_name}\n"
        f"Exec={app_name}\n"
        f"Icon={app_name}\n"
        "Terminal=false\n"
        f"Categories={categories}\n",
        encoding="utf-8",
    )

    qt_root = qt_bin_dir.parent
    for subdir, destination in (("plugins", plugins_dir), ("qml", appdir / "usr" / "qml")):
        source = qt_root / subdir
        if source.exists():
            copy_tree(source, destination, required=False, prefix=prefix)
        else:
            warn(f"Qt {subdir} directory not found: {source}", prefix=prefix)

    for library in config.optional_path_list("linux.extraLibraries"):
        copy_lib_with_deps(library, lib_dir)
    for library in config.optional_path_list("linux.optionalExtraLibraries"):
        if library.exists():
            copy_lib_with_deps(library, lib_dir)
        else:
            log(f"Optional Linux library not found, skipped: {library}", prefix=prefix)

    app_image_tool = config.optional_string("linux.appImageTool", "")
    if app_image_tool:
        app_image_path = dist_dir.parent / f"{package_name}.AppImage"
        if app_image_path.exists() or app_image_path.is_symlink():
            if not app_image_path.is_file() and not app_image_path.is_symlink():
                raise PackageError(f"AppImage output path is not a file: {app_image_path}")
            app_image_path.unlink()
        run_command(
            [app_image_tool, str(appdir), str(app_image_path)],
            prefix=prefix,
        )
        if not app_image_path.is_file():
            raise PackageError(f"AppImage tool did not create expected output: {app_image_path}")
    log(f"Deployment completed: {appdir}", prefix=prefix)
    return appdir
