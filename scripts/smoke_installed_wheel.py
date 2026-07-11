# Usage:
#   python3 scripts/smoke_installed_wheel.py --dist-dir dist
from __future__ import annotations

import argparse
import importlib
import importlib.abc
import importlib.metadata
import importlib.resources
import json
import os
import pickle  # nosec B403
import shutil
import subprocess  # nosec B404
import sys
import sysconfig
import tempfile
import venv
import xml.etree.ElementTree as ET  # nosec B405
from pathlib import Path
from typing import Any

try:
    from importlib.resources.abc import Traversable
except ImportError:  # Python 3.10 keeps the protocol in importlib.abc.
    from importlib.abc import Traversable

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
    "cmake/CppKitEnsureRustArtifact.cmake",
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
    and name
    not in {
        "cmake/CppKitEnsureRustArtifact.cmake",
        "cmake/CppKitRunMemcheck.cmake",
        "cmake/debug_pkg_config.cmake",
    }
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


class _BlockedCppAdapterFinder(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: Any = None,
    ) -> None:
        del path, target
        if fullname == "repomgrcpp" or fullname.startswith("repomgrcpp."):
            raise ImportError("repomgrcpp import blocked by Swift wheel smoke")
        return None


def smoke_installed_swift_adapter() -> None:
    import inspect

    importlib.import_module("freecm.dependency_workflow")

    blocker = _BlockedCppAdapterFinder()
    sys.meta_path.insert(0, blocker)
    try:
        import repomgrswift
        from repomgrswift.source_roots import (
            DependencyResolution,
            DependencyRootSpec,
            DependencyRootWorkflow,
            DependencyRootWorkflowConfig,
            ExtraDependencyPathSpec,
            ResolvedSwiftDependencyRoots,
        )

        expected_exports = {
            "DependencyResolution": DependencyResolution,
            "DependencyRootSpec": DependencyRootSpec,
            "DependencyRootWorkflow": DependencyRootWorkflow,
            "DependencyRootWorkflowConfig": DependencyRootWorkflowConfig,
            "ExtraDependencyPathSpec": ExtraDependencyPathSpec,
            "ResolvedSwiftDependencyRoots": ResolvedSwiftDependencyRoots,
        }
        if any(
            getattr(repomgrswift, name, None) is not value
            for name, value in expected_exports.items()
        ):
            raise RuntimeError("installed Swift adapter is missing compatibility exports")
        spec = DependencyRootSpec("LibA", "LibA", "LIBA_ROOT", ())
        workflow = DependencyRootWorkflow(
            DependencyRootWorkflowConfig(
                repo_root=Path.cwd(),
                dependency_root_specs=(spec,),
                repo_display_name="SampleApp",
            )
        )
        if tuple(workflow.spec_by_dependency_name) != ("LibA",):
            raise RuntimeError("installed Swift workflow failed minimal construction")
        if tuple(inspect.signature(DependencyRootWorkflow).parameters) != ("config",):
            raise RuntimeError("installed Swift workflow constructor signature changed")
    finally:
        sys.meta_path.remove(blocker)

    completed = subprocess.run(  # nosec B603
        [sys.executable, "-m", "repomgrswift.source_roots", "--help"],
        cwd=Path.cwd(),
        env=_clean_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0 or "usage:" not in completed.stdout.lower():
        raise RuntimeError("installed repomgrswift module help failed")
    print("[wheel-smoke] Swift adapter imports and constructs without repomgrcpp")


def smoke_installed_regression_modules() -> None:
    import inspect

    assertions = importlib.import_module("tools.regression.assertions")
    cases = importlib.import_module("tools.regression.cases")
    importlib.import_module("tools.regression.execution")
    models = importlib.import_module("tools.regression.models")
    reporting = importlib.import_module("tools.regression.reporting")
    runner = importlib.import_module("tools.regression.runner")
    expected_identities = {
        "CaseResult": models.CaseResult,
        "CaseInvocation": models.CaseInvocation,
        "CaseMeta": models.CaseMeta,
        "ControlConfig": models.ControlConfig,
        "RegressionAppConfig": models.RegressionAppConfig,
        "CaseConfigError": cases.CaseConfigError,
        "load_app_config": cases.load_app_config,
        "parse_case_invocation": cases.parse_case_invocation,
        "resolve_report_path": assertions.resolve_report_path,
        "classify_case_outcome": assertions.classify_case_outcome,
        "write_junit": reporting.write_junit,
    }
    if any(getattr(runner, name, None) is not value for name, value in expected_identities.items()):
        raise RuntimeError("installed regression runner compatibility exports changed")
    result = runner.CaseResult(
        "Sample",
        Path.cwd(),
        True,
        "ok",
        0,
        0.1,
        Path.cwd() / "report.json",
    )
    restored_result = pickle.loads(pickle.dumps(result))  # nosec B301
    restored_error = pickle.loads(  # nosec B301
        pickle.dumps(runner.CaseConfigError("invalid case"))
    )
    if type(restored_result) is not runner.CaseResult or restored_result != result:
        raise RuntimeError("installed regression CaseResult pickle identity changed")
    if type(restored_error) is not runner.CaseConfigError:
        raise RuntimeError("installed regression CaseConfigError pickle identity changed")
    if tuple(inspect.signature(runner.run_case).parameters) != (
        "app",
        "case_file",
        "case_id",
        "out_root",
        "default_timeout",
        "app_config",
    ):
        raise RuntimeError("installed regression run_case signature changed")

    with tempfile.TemporaryDirectory(prefix="freecm-regression-smoke-") as temp_dir:
        artifact_root = Path(temp_dir)
        summary = reporting.build_summary([result])
        summary_path = artifact_root / "summary.json"
        junit_path = artifact_root / "junit.xml"
        reporting.write_summary(summary, summary_path)
        reporting.write_junit([result], junit_path)
        if json.loads(summary_path.read_text(encoding="utf-8"))["total"] != 1:
            raise RuntimeError("installed regression summary smoke failed")
        if ET.parse(junit_path).getroot().tag != "testsuites":  # nosec B314
            raise RuntimeError("installed regression JUnit smoke failed")

    completed = subprocess.run(  # nosec B603
        [sys.executable, "-m", "tools.regression.cli", "--help"],
        cwd=Path.cwd(),
        env=_clean_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0 or "--suite-root" not in completed.stdout:
        raise RuntimeError("installed regression module help failed")
    print("[wheel-smoke] regression runner compatibility modules")


def smoke_installed_performance_baselines() -> None:
    importlib.import_module("freecm.io_metrics")
    baseline = importlib.import_module("tools.performance_baseline")
    importlib.import_module("tools.performance_fixtures")
    memory_report = baseline.run_benchmarks(dependency_count=1, iterations=1)
    if len(memory_report["benchmarks"]) != 4:
        raise RuntimeError("installed in-memory performance baseline changed")
    io_report = baseline.run_io_benchmarks(dependency_count=1, iterations=1)
    names = [benchmark["name"] for benchmark in io_report["benchmarks"]]
    if names != [
        "seed_preflight_init",
        "offline_closure_discovery",
        "offline_materialize_cold",
        "offline_materialize_warm",
        "dependency_root_verify",
    ]:
        raise RuntimeError("installed I/O performance baseline scenarios changed")
    for benchmark in io_report["benchmarks"][1:]:
        if benchmark["gitNetworkCommands"]["total"] != 0:
            raise RuntimeError("installed offline I/O baseline used a network Git command")
    print("[wheel-smoke] I/O performance baseline scenarios")


def smoke_installed_wheel(expected_version: str) -> None:
    distribution = importlib.metadata.distribution("freecm")
    if distribution.version != expected_version:
        raise RuntimeError(
            f"installed freecm version is {distribution.version}, expected {expected_version}"
        )
    distribution_root = Path(str(distribution.locate_file(""))).resolve()
    if not distribution_root.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError(f"freecm distribution is outside isolated venv: {distribution_root}")
    smoke_installed_swift_adapter()
    smoke_installed_regression_modules()
    smoke_installed_performance_baselines()
    for module_name in (
        "freecm",
        "freecm.dependency_workflow",
        "freecm.io_metrics",
        "repomgrcpp",
        "repomgrandroid",
        "repomgrdotnet",
        "repomgrswift",
        "tools.regression.cases",
        "tools.regression.execution",
        "tools.regression.assertions",
        "tools.regression.models",
        "tools.regression.reporting",
        "tools.regression.runner",
        "tools.performance_baseline",
        "tools.performance_fixtures",
    ):
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
