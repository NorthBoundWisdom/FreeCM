# Usage:
#   python3 scripts/smoke_installed_wheel.py --dist-dir dist
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.resources
import os
import shutil
import subprocess  # nosec B404
import sys
import sysconfig
import tempfile
import venv
from importlib.resources.abc import Traversable
from pathlib import Path

EXPECTED_CONSOLE_SCRIPTS = {
    "freecm-deps",
    "package-tool",
    "regression-tool",
    "repo-tool",
    "repomgrcpp",
}
EXPECTED_CMAKE_RESOURCES = {
    "cmake/CppKitAddExecutable.cmake",
    "cmake/CppKitBundleResources.cmake",
    "cmake/CppKitCompilerFlags.cmake",
    "cmake/CppKitCoverage.cmake",
    "cmake/CppKitDeployQt.cmake",
    "cmake/CppKitDoxygen.cmake",
    "cmake/CppKitHeaderExport.cmake",
    "cmake/CppKitMemcheck.cmake",
    "cmake/CppKitPackage.cmake",
    "cmake/CppKitRunMemcheck.cmake",
    "cmake/CppKitRust.cmake",
    "cmake/CppKitThirdPartyChecks.cmake",
    "cmake/DependencyBootstrap.cmake",
    "cmake/DependencyBuildContext.json.in",
    "cmake/debug_pkg_config.cmake",
    "cmake_presets/CMakePresets.json.linux.in",
    "cmake_presets/CMakePresets.json.mac.in",
    "cmake_presets/CMakePresets.json.win.in",
}
INCLUDABLE_CMAKE_RESOURCES = {
    name
    for name in EXPECTED_CMAKE_RESOURCES
    if name.endswith(".cmake")
    and name not in {"cmake/CppKitRunMemcheck.cmake", "cmake/debug_pkg_config.cmake"}
}
REPO_ROOT = Path(__file__).resolve().parents[1]


def validate_console_script_names(names: set[str]) -> None:
    missing = sorted(EXPECTED_CONSOLE_SCRIPTS - names)
    if missing:
        raise RuntimeError(f"wheel is missing console scripts: {', '.join(missing)}")


def validate_cmake_resource_names(names: set[str]) -> None:
    missing = sorted(EXPECTED_CMAKE_RESOURCES - names)
    if missing:
        raise RuntimeError(f"wheel is missing CMake resources: {', '.join(missing)}")


def select_wheel(dist_dir: Path, expected_version: str) -> Path:
    candidates = sorted(dist_dir.resolve().glob(f"freecm-{expected_version}-*.whl"))
    if len(candidates) != 1:
        formatted = ", ".join(str(path) for path in candidates) or "none"
        raise RuntimeError(
            f"expected exactly one FreeCM {expected_version} wheel in {dist_dir}: {formatted}"
        )
    return candidates[0]


def _clean_environment() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return env


def _venv_executable(venv_root: Path, name: str) -> Path:
    scripts_dir = venv_root / ("Scripts" if os.name == "nt" else "bin")
    executable = scripts_dir / name
    if os.name == "nt" and executable.suffix == "":
        executable = executable.with_suffix(".exe")
    return executable


def create_and_smoke_wheel(wheel_path: Path, expected_version: str) -> None:
    wheel_path = wheel_path.resolve()
    if not wheel_path.is_file():
        raise RuntimeError(f"wheel does not exist: {wheel_path}")
    with tempfile.TemporaryDirectory(prefix="freecm-wheel-install-") as temp_dir:
        temp_root = Path(temp_dir)
        venv_root = temp_root / "venv"
        empty_cwd = temp_root / "empty-cwd"
        empty_cwd.mkdir()
        venv.EnvBuilder(with_pip=True, clear=True).create(venv_root)
        python = _venv_executable(venv_root, "python")
        env = _clean_environment()
        subprocess.run(  # nosec B603
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--disable-pip-version-check",
                str(wheel_path),
            ],
            cwd=empty_cwd,
            env=env,
            check=True,
            timeout=120,
        )
        subprocess.run(  # nosec B603
            [
                str(python),
                str(Path(__file__).resolve()),
                "--installed-check",
                "--expected-version",
                expected_version,
            ],
            cwd=empty_cwd,
            env=env,
            check=True,
            timeout=120,
        )


def _assert_imported_from_venv(module_name: str) -> None:
    module = importlib.import_module(module_name)
    module_path = Path(module.__file__ or "").resolve()
    venv_root = Path(sys.prefix).resolve()
    if not module_path.is_relative_to(venv_root):
        raise RuntimeError(f"{module_name} imported outside isolated venv: {module_path}")
    if module_path.is_relative_to(REPO_ROOT):
        raise RuntimeError(f"{module_name} imported from checkout instead of wheel: {module_path}")


def smoke_installed_console_scripts(distribution: importlib.metadata.Distribution) -> None:
    entry_points = sorted(
        (
            entry_point
            for entry_point in distribution.entry_points
            if entry_point.group == "console_scripts"
        ),
        key=lambda entry_point: entry_point.name,
    )
    validate_console_script_names({entry_point.name for entry_point in entry_points})
    scripts_dir = Path(sysconfig.get_path("scripts"))
    with tempfile.TemporaryDirectory(prefix="freecm-wheel-entrypoints-") as temp_dir:
        for entry_point in entry_points:
            executable = scripts_dir / entry_point.name
            if os.name == "nt":
                executable = executable.with_suffix(".exe")
            if not executable.is_file():
                raise RuntimeError(f"installed console script is missing: {executable}")
            completed = subprocess.run(  # nosec B603
                [str(executable), "--help"],
                cwd=temp_dir,
                env=_clean_environment(),
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            output = completed.stdout + completed.stderr
            if completed.returncode != 0 or "usage:" not in output.lower():
                raise RuntimeError(
                    f"console script smoke failed ({completed.returncode}): "
                    f"{entry_point.name}\n{output.strip()}"
                )
            print(f"[wheel-smoke] console script: {entry_point.name}")


def smoke_installed_cmake_resources() -> None:
    repomgrcpp_root = importlib.resources.files("repomgrcpp")
    resources: dict[str, Traversable] = {}
    for directory_name in ("cmake", "cmake_presets"):
        directory = repomgrcpp_root.joinpath(directory_name)
        for resource in directory.iterdir():
            if resource.is_file():
                resources[f"{directory_name}/{resource.name}"] = resource
    validate_cmake_resource_names(set(resources))
    for relative_path in sorted(EXPECTED_CMAKE_RESOURCES):
        if not resources[relative_path].read_bytes():
            raise RuntimeError(f"packaged CMake resource is empty: {relative_path}")

    cmake = shutil.which("cmake")
    if cmake is None:
        raise RuntimeError("cmake is required to smoke installed CMake modules")
    modules = [resources[name] for name in sorted(INCLUDABLE_CMAKE_RESOURCES)]
    with tempfile.TemporaryDirectory(prefix="freecm-cmake-smoke-") as temp_dir:
        script_path = Path(temp_dir) / "include-packaged-modules.cmake"
        include_lines = "\n".join(
            f'include("{Path(str(module)).as_posix()}")' for module in modules
        )
        script_path.write_text(
            "cmake_minimum_required(VERSION 3.20)\n" + include_lines + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(  # nosec B603
            [cmake, "-P", str(script_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            "packaged CMake modules failed to load\n"
            + (completed.stdout + completed.stderr).strip()
        )
    print(f"[wheel-smoke] packaged CMake resources: {len(resources)}")


def smoke_installed_wheel(expected_version: str) -> None:
    distribution = importlib.metadata.distribution("freecm")
    if distribution.version != expected_version:
        raise RuntimeError(
            f"installed freecm version is {distribution.version}, expected {expected_version}"
        )
    distribution_root = Path(str(distribution.locate_file(""))).resolve()
    if not distribution_root.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError(f"freecm distribution is outside isolated venv: {distribution_root}")
    for module_name in ("freecm", "repomgrcpp", "repomgrandroid", "repomgrdotnet", "repomgrswift"):
        _assert_imported_from_venv(module_name)
    smoke_installed_console_scripts(distribution)
    smoke_installed_cmake_resources()
    print(f"[wheel-smoke] FreeCM {distribution.version} passed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke a FreeCM wheel in an isolated venv")
    artifact_group = parser.add_mutually_exclusive_group()
    artifact_group.add_argument("--wheel", type=Path)
    artifact_group.add_argument("--dist-dir", type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument("--installed-check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.installed_check:
        if not args.expected_version:
            parser.error("--expected-version is required with --installed-check")
        smoke_installed_wheel(args.expected_version)
        return 0
    if args.wheel is None and args.dist_dir is None:
        parser.error("--wheel or --dist-dir is required")
    expected_version = (
        args.expected_version or (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    )
    wheel_path = args.wheel or select_wheel(args.dist_dir, expected_version)
    create_and_smoke_wheel(wheel_path, expected_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
