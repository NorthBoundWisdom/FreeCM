from __future__ import annotations

import shutil
from pathlib import Path

from .common import (
    PackageConfig,
    PackageError,
    clean_dist_dir,
    copy_configured_resources,
    copy_file,
    log,
    run_command,
    warn,
)

SYSTEM_DLLS = {
    "advapi32.dll",
    "authz.dll",
    "bcryptprimitives.dll",
    "combase.dll",
    "comctl32.dll",
    "comdlg32.dll",
    "d3d11.dll",
    "d3d12.dll",
    "dbghelp.dll",
    "dwrite.dll",
    "dnsapi.dll",
    "dwmapi.dll",
    "dxgi.dll",
    "gdi32.dll",
    "glu32.dll",
    "iphlpapi.dll",
    "kernel32.dll",
    "kernelbase.dll",
    "mf.dll",
    "mfplat.dll",
    "mfreadwrite.dll",
    "mpr.dll",
    "msvcp_win.dll",
    "msvcrt.dll",
    "ntdll.dll",
    "ole32.dll",
    "oleaut32.dll",
    "opengl32.dll",
    "psapi.dll",
    "rpcrt4.dll",
    "sechost.dll",
    "secur32.dll",
    "shell32.dll",
    "shlwapi.dll",
    "user32.dll",
    "userenv.dll",
    "uxtheme.dll",
    "version.dll",
    "win32u.dll",
    "winhttp.dll",
    "winmm.dll",
    "windows.storage.dll",
    "ws2_32.dll",
    "wsock32.dll",
}

DEFAULT_REDIST_ALLOWLIST = {
    "concrt140.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "vcomp140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
}


def parse_dumpbin_deps(output: str) -> list[str]:
    deps: list[str] = []
    in_section = False
    for line in output.splitlines():
        if "Image has the following dependencies" in line:
            in_section = True
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped:
            if deps:
                break
            continue
        if stripped.lower().endswith(".dll"):
            deps.append(stripped)
    return deps


def is_api_set(dll_name: str) -> bool:
    name = dll_name.lower()
    return name.startswith("api-ms-win-") or name.startswith("ext-ms-win-")


def is_system_dll(dll_name: str) -> bool:
    return dll_name.lower() in SYSTEM_DLLS


def find_dumpbin() -> str | None:
    for name in ("dumpbin.exe", "dumpbin"):
        found = shutil.which(name)
        if found:
            return found
    return None


def find_in_search_patterns(search_paths: list[Path], patterns: list[str]) -> Path | None:
    for search_dir in search_paths:
        if not search_dir.is_dir():
            continue
        for pattern in patterns:
            if any(ch in pattern for ch in "*?["):
                for candidate in sorted(search_dir.glob(pattern)):
                    if candidate.is_file():
                        return candidate
                continue
            candidate = search_dir / pattern
            if candidate.is_file():
                return candidate
    return None


def _string_list(value: object, *, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PackageError(f"Invalid {label}; expected array")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise PackageError(f"Invalid {label}[{index}]; expected non-empty string")
        result.append(item)
    return result


def deploy_windows(config: PackageConfig) -> Path:
    prefix = "deploy_win"
    app_name = config.required_string("app.name")
    target_exe = config.path("paths.targetPath")
    dist_dir = config.path("paths.distDir")
    qt_bin_dir = config.path("qt.binDir")
    qml_dir = config.path("qt.qmlDir")
    windeployqt = config.path("windows.windeployqt")

    if not target_exe.is_file():
        raise PackageError(f"Target executable not found: {target_exe}")
    clean_dist_dir(config, dist_dir)
    copy_file(target_exe, dist_dir, prefix=prefix)
    copy_configured_resources(config, dist_dir, prefix=prefix)

    run_command(
        [
            str(windeployqt),
            "--verbose",
            "1",
            "--qmldir",
            str(qml_dir),
            "--plugindir",
            str(dist_dir / "plugins"),
            "--no-translations",
            "--dir",
            str(dist_dir),
            str(dist_dir / f"{app_name}.exe"),
        ],
        prefix=prefix,
    )

    windows = config.section("windows")
    search_paths = [qt_bin_dir, dist_dir, config.path("paths.binaryDir")]
    search_paths.extend(config.optional_path_list("windows.dllSearchPaths"))
    redist_allowlist = DEFAULT_REDIST_ALLOWLIST | {
        name.lower()
        for name in _string_list(windows.get("redistAllowlist"), label="windows.redistAllowlist")
    }

    def ensure_in_dist(dll_name: str, patterns: list[str] | None = None) -> Path | None:
        if is_api_set(dll_name) or is_system_dll(dll_name):
            return None
        dist_candidate = dist_dir / dll_name
        if dist_candidate.exists():
            return dist_candidate
        source = find_in_search_patterns(search_paths, patterns or [dll_name])
        if source is None:
            return None
        if source.name.lower() in SYSTEM_DLLS and source.name.lower() not in redist_allowlist:
            return None
        if source.parent.resolve() == dist_dir.resolve():
            return source
        copy_file(source, dist_dir, prefix=prefix)
        return dist_dir / source.name

    dumpbin = find_dumpbin()
    if dumpbin:
        queue = [dist_dir / f"{app_name}.exe"]
        seen: set[str] = set()
        while queue:
            binary = queue.pop(0)
            key = str(binary).lower()
            if key in seen or not binary.exists():
                continue
            seen.add(key)
            completed = run_command(
                [dumpbin, "/dependents", str(binary)], capture=True, prefix=prefix
            )
            for dep in parse_dumpbin_deps((completed.stdout or "") + (completed.stderr or "")):
                if is_api_set(dep) or is_system_dll(dep):
                    continue
                dep_path = ensure_in_dist(dep)
                if dep_path is None:
                    raise PackageError(
                        f"Dependency reported by dumpbin not found in search paths: {dep} "
                        f"(required by {binary.name})"
                    )
                queue.append(dep_path)
    else:
        warn("dumpbin not found; using configured required DLL list only", prefix=prefix)

    required_dlls = _string_list(windows.get("requiredDlls"), label="windows.requiredDlls")
    optional_patterns = windows.get("optionalDllPatterns", {})
    if optional_patterns is None:
        optional_patterns = {}
    if not isinstance(optional_patterns, dict):
        raise PackageError("Invalid windows.optionalDllPatterns; expected object")

    for dll in required_dlls:
        patterns = optional_patterns.get(dll)
        validated_patterns = (
            _string_list(patterns, label=f"windows.optionalDllPatterns.{dll}")
            if patterns is not None
            else None
        )
        if not ensure_in_dist(dll, validated_patterns):
            raise PackageError(f"Required DLL not found in any search paths: {dll}")

    for dll in _string_list(windows.get("optionalDlls"), label="windows.optionalDlls"):
        patterns = optional_patterns.get(dll)
        validated_patterns = (
            _string_list(patterns, label=f"windows.optionalDllPatterns.{dll}")
            if patterns is not None
            else None
        )
        if ensure_in_dist(dll, validated_patterns):
            continue
        log(f"Optional DLL not found, skipped: {dll}", prefix=prefix)

    log(f"Deployment completed: {dist_dir}", prefix=prefix)
    return dist_dir
