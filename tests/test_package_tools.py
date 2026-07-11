from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repomgrcpp.package import mac_deploy as mac_deploy_module  # noqa: E402
from repomgrcpp.package.common import (  # noqa: E402
    PackageError,
    clean_dist_dir,
    contained_child,
    copy_configured_resources,
    load_package_config,
    run_command,
)
from repomgrcpp.package.linux_deploy import (
    deploy_linux,
    generate_apprun,
    should_skip_system_library,
)  # noqa: E402
from repomgrcpp.package.mac_deploy import (  # noqa: E402
    build_library_search_index,
    build_sign_command,
    collect_bundle_binaries,
    deploy_mac,
    find_library,
    inspect_otool_outputs,
    normalize_bundle_rpaths,
    parse_otool_deps,
    parse_otool_rpaths,
)
from repomgrcpp.package.win_deploy import (  # noqa: E402
    deploy_windows,
    find_in_search_patterns,
    is_api_set,
    is_system_dll,
    parse_dumpbin_deps,
)
from repomgrcpp.package.wix import generate_wix_fragment, stable_id  # noqa: E402


def python_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(REPO_ROOT) if not pythonpath else os.pathsep.join([str(REPO_ROOT), pythonpath])
    )
    return env


def minimal_config(tempdir: Path) -> dict[str, object]:
    return {
        "app": {
            "name": "DemoApp",
            "displayName": "Demo App",
            "version": "1.2.3",
        },
        "paths": {
            "sourceDir": str(tempdir / "src"),
            "binaryDir": str(tempdir / "build"),
            "targetPath": str(tempdir / "build" / "DemoApp"),
            "distDir": str(tempdir / "build" / "dist"),
        },
        "qt": {
            "binDir": str(tempdir / "qt" / "bin"),
            "qmlDir": str(tempdir / "src" / "qml"),
        },
        "resources": {},
        "windows": {
            "windeployqt": str(tempdir / "qt" / "bin" / "windeployqt"),
        },
        "mac": {
            "bundlePath": str(tempdir / "build" / "DemoApp.app"),
            "entitlementsFile": str(tempdir / "src" / "entitlements.plist"),
        },
        "linux": {
            "packageName": "DemoApp-1.2.3-x86_64",
        },
    }


class WixFragmentTests(unittest.TestCase):
    def test_wix_fragment_contains_stable_dirs_files_and_component_group(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "Cases" / "A").mkdir(parents=True)
            (root / "Cases" / "A" / "case.txt").write_text("case", encoding="utf-8")
            (root / "readme.txt").write_text("readme", encoding="utf-8")

            fragment = generate_wix_fragment(
                root,
                root_id="INSTALL_ROOT",
                prefix="Demo",
                component_group_id="DemoFiles",
            )

        self.assertIn('<DirectoryRef Id="INSTALL_ROOT">', fragment)
        self.assertIn(f'Directory Id="DemoDir_{stable_id("Cases")}" Name="Cases"', fragment)
        self.assertIn(f'Directory Id="DemoDir_{stable_id("Cases/A")}" Name="A"', fragment)
        self.assertIn(f'File Id="DemoFile_{stable_id("Cases/A/case.txt")}"', fragment)
        self.assertIn('<ComponentGroup Id="DemoFiles">', fragment)

    def test_wix_fragment_rejects_missing_source(self) -> None:
        with self.assertRaisesRegex(PackageError, "Source directory not found"):
            generate_wix_fragment(Path("/missing/source"), root_id="ROOT", prefix="Demo")


class PackageConfigTests(unittest.TestCase):
    def test_config_validation_accepts_defaults_and_resolves_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            config_path = root / "package.json"
            config_path.write_text(json.dumps(minimal_config(root)), encoding="utf-8")

            config = load_package_config(config_path, platform="win")

        self.assertEqual(config.required_string("app.name"), "DemoApp")
        self.assertEqual(config.optional_path_list("windows.dllSearchPaths"), [])
        self.assertEqual(config.path("linux.iconFile", required=False), Path(""))
        self.assertIsNone(config.optional_path("linux.iconFile"))

    def test_config_validation_rejects_missing_required_field(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data = minimal_config(root)
            del data["qt"]  # type: ignore[index]
            config_path = root / "package.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaisesRegex(PackageError, "qt.binDir"):
                load_package_config(config_path, platform="win")

    def test_config_validation_rejects_resource_destination_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            for destination in ("../escape", str(root / "escape")):
                with self.subTest(destination=destination):
                    data = minimal_config(root)
                    data["resources"] = {  # type: ignore[index]
                        "copyTrees": [
                            {
                                "source": "assets",
                                "destination": destination,
                            }
                        ]
                    }
                    config_path = root / "package.json"
                    config_path.write_text(json.dumps(data), encoding="utf-8")

                    with self.assertRaisesRegex(PackageError, "Invalid resources.copyTrees"):
                        load_package_config(config_path, platform="win")

    def test_config_validation_rejects_resource_remove_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data = minimal_config(root)
            data["resources"] = {"remove": ["../escape"]}  # type: ignore[index]
            config_path = root / "package.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaisesRegex(PackageError, "Invalid resources.remove"):
                load_package_config(config_path, platform="mac")

    def test_config_validation_rejects_malformed_resource_entries(self) -> None:
        invalid_resources = (
            ({"remove": "stale.txt"}, "resources.remove"),
            ({"translationsDir": None}, "resources.translationsDir"),
            ({"fontsDir": ""}, "resources.fontsDir"),
            (
                {"copyTrees": [{"destination": "Assets"}]},
                r"resources.copyTrees\[0\].source",
            ),
            (
                {"copyFiles": [{"source": "icon.png"}]},
                r"resources.copyFiles\[0\].destinationDir",
            ),
            (
                {
                    "copyFiles": [
                        {
                            "source": "icon.png",
                            "destinationDir": "Assets",
                            "required": "false",
                        }
                    ]
                },
                r"resources.copyFiles\[0\].required",
            ),
            (
                {
                    "copyTrees": [
                        {
                            "source": "assets",
                            "destination": "Assets",
                            "optional": True,
                        }
                    ]
                },
                "unknown fields: optional",
            ),
        )
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            config_path = root / "package.json"
            for resources, message in invalid_resources:
                with self.subTest(resources=resources):
                    data = minimal_config(root)
                    data["resources"] = resources
                    config_path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaisesRegex(PackageError, message):
                        load_package_config(config_path, platform="win")

    def test_run_command_fails_closed_and_wraps_launch_errors(self) -> None:
        failed = subprocess.CompletedProcess(
            args=["package-step"],
            returncode=7,
            stdout="",
            stderr="failed",
        )
        with mock.patch("repomgrcpp.package.common.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(PackageError, r"command failed \(7\): package-step"):
                run_command(["package-step"])
            self.assertEqual(run_command(["optional-probe"], check=False).returncode, 7)

        with mock.patch(
            "repomgrcpp.package.common.subprocess.run",
            side_effect=FileNotFoundError("missing tool"),
        ):
            with self.assertRaisesRegex(PackageError, "unable to run command: missing-tool"):
                run_command(["missing-tool"])

    def test_configured_resource_inputs_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            for resources, expected in (
                ({"translationsDir": "missing-i18n"}, "translation directory"),
                ({"fontsDir": "missing-fonts"}, "Required directory not found"),
            ):
                with self.subTest(resources=resources):
                    data = minimal_config(root)
                    data["resources"] = resources
                    config_path = root / "package.json"
                    config_path.write_text(json.dumps(data), encoding="utf-8")
                    config = load_package_config(config_path, platform="linux")
                    with self.assertRaisesRegex(PackageError, expected):
                        copy_configured_resources(config, root / "dist", prefix="test")

    def test_package_paths_stay_inside_expected_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            config_path = root / "package.json"
            data = minimal_config(root)
            config_path.write_text(json.dumps(data), encoding="utf-8")
            config = load_package_config(config_path, platform="win")

            self.assertEqual(
                contained_child(root / "dist", "assets/icons", label="resource"),
                (root / "dist" / "assets" / "icons").resolve(),
            )
            with self.assertRaisesRegex(PackageError, "parent traversal"):
                contained_child(root / "dist", "../outside", label="resource")
            with self.assertRaisesRegex(PackageError, "deployment cleanup"):
                clean_dist_dir(config, root)
            with self.assertRaisesRegex(PackageError, "deployment cleanup"):
                clean_dist_dir(config, root / "build")

    def test_clean_dist_dir_removes_readonly_payload_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data = minimal_config(root)
            config_path = root / "package.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")
            config = load_package_config(config_path, platform="win")
            dist_dir = root / "build" / "dist"
            dist_dir.mkdir(parents=True)
            readonly_file = dist_dir / "icuuc.dll"
            readonly_file.write_text("dll", encoding="utf-8")
            readonly_file.chmod(stat.S_IREAD)

            clean_dist_dir(config, dist_dir)

            self.assertTrue(dist_dir.is_dir())
            self.assertEqual(list(dist_dir.iterdir()), [])


class PlatformHelperTests(unittest.TestCase):
    def test_windows_dumpbin_and_dll_filters(self) -> None:
        output = """
Image has the following dependencies:

    KERNEL32.dll
    Qt6Core.dll
    api-ms-win-core.dll

Summary
"""
        self.assertEqual(
            parse_dumpbin_deps(output), ["KERNEL32.dll", "Qt6Core.dll", "api-ms-win-core.dll"]
        )
        self.assertTrue(is_system_dll("kernel32.dll"))
        self.assertTrue(is_api_set("api-ms-win-core-file-l1-1-0.dll"))

    def test_windows_pattern_search(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "opencv_world4120.dll").write_text("", encoding="utf-8")

            found = find_in_search_patterns([root], ["opencv_world*.dll"])

        self.assertEqual(found.name if found else "", "opencv_world4120.dll")

    def test_windows_deploy_reuses_pattern_match_already_in_dist(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data = minimal_config(root)
            data["windows"] = {  # type: ignore[index]
                "windeployqt": str(root / "qt" / "bin" / "windeployqt"),
                "requiredDlls": ["boost_iostreams-vc145-mt-x64-1_89.dll"],
                "optionalDllPatterns": {
                    "boost_iostreams-vc145-mt-x64-1_89.dll": ["boost_iostreams-vc145-mt-x64-*.dll"]
                },
            }
            target_exe = root / "build" / "DemoApp.exe"
            target_exe.parent.mkdir(parents=True)
            target_exe.write_text("exe", encoding="utf-8")
            data["paths"]["targetPath"] = str(target_exe)  # type: ignore[index]
            (root / "qt" / "bin").mkdir(parents=True)
            (root / "src" / "qml").mkdir(parents=True)
            config_path = root / "package.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")
            config = load_package_config(config_path, platform="win")

            def fake_run_command(
                *args: object, **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                deployed_dll = root / "build" / "dist" / "boost_iostreams-vc145-mt-x64-1_90.dll"
                deployed_dll.write_text("dll", encoding="utf-8")
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

            with mock.patch("repomgrcpp.package.win_deploy.find_dumpbin", return_value=None):
                with mock.patch(
                    "repomgrcpp.package.win_deploy.run_command", side_effect=fake_run_command
                ):
                    dist_dir = deploy_windows(config)

            self.assertTrue((dist_dir / "boost_iostreams-vc145-mt-x64-1_90.dll").is_file())

    def test_windows_deploy_fails_when_windeployqt_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data = minimal_config(root)
            target_exe = root / "build" / "DemoApp.exe"
            target_exe.parent.mkdir(parents=True)
            target_exe.write_text("exe", encoding="utf-8")
            data["paths"]["targetPath"] = str(target_exe)  # type: ignore[index]
            config_path = root / "package.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")
            config = load_package_config(config_path, platform="win")
            failed = subprocess.CompletedProcess([], 9, "", "windeployqt failed")

            with mock.patch("repomgrcpp.package.common.subprocess.run", return_value=failed):
                with self.assertRaisesRegex(PackageError, r"command failed \(9\).+windeployqt"):
                    deploy_windows(config)

    def test_windows_deploy_rejects_missing_required_dll(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data = minimal_config(root)
            data["windows"]["requiredDlls"] = ["LibA.dll"]  # type: ignore[index]
            target_exe = root / "build" / "DemoApp.exe"
            target_exe.parent.mkdir(parents=True)
            target_exe.write_text("exe", encoding="utf-8")
            data["paths"]["targetPath"] = str(target_exe)  # type: ignore[index]
            config_path = root / "package.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")
            config = load_package_config(config_path, platform="win")
            succeeded = subprocess.CompletedProcess([], 0, "", "")

            with mock.patch("repomgrcpp.package.common.subprocess.run", return_value=succeeded):
                with mock.patch("repomgrcpp.package.win_deploy.find_dumpbin", return_value=None):
                    with self.assertRaisesRegex(PackageError, "Required DLL not found.+LibA.dll"):
                        deploy_windows(config)

    def test_windows_deploy_rejects_missing_dumpbin_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data = minimal_config(root)
            target_exe = root / "build" / "DemoApp.exe"
            target_exe.parent.mkdir(parents=True)
            target_exe.write_text("exe", encoding="utf-8")
            data["paths"]["targetPath"] = str(target_exe)  # type: ignore[index]
            config_path = root / "package.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")
            config = load_package_config(config_path, platform="win")

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                stdout = (
                    "Image has the following dependencies:\n\n    LibA.dll\n\nSummary\n"
                    if Path(command[0]).name == "dumpbin"
                    else ""
                )
                return subprocess.CompletedProcess(command, 0, stdout, "")

            with mock.patch("repomgrcpp.package.common.subprocess.run", side_effect=fake_run):
                with mock.patch(
                    "repomgrcpp.package.win_deploy.find_dumpbin", return_value="dumpbin"
                ):
                    with self.assertRaisesRegex(PackageError, "dumpbin not found.+LibA.dll"):
                        deploy_windows(config)

    def test_mac_deploy_propagates_fixup_and_signing_failures(self) -> None:
        for failing_step, include_library in (("install_name_tool", True), ("codesign", False)):
            with self.subTest(failing_step=failing_step):
                with tempfile.TemporaryDirectory() as tempdir:
                    root = Path(tempdir)
                    data = minimal_config(root)
                    source_bundle = root / "build" / "DemoApp.app"
                    executable = source_bundle / "Contents" / "MacOS" / "DemoApp"
                    executable.parent.mkdir(parents=True)
                    executable.write_text("app", encoding="utf-8")
                    if include_library:
                        library = source_bundle / "Contents" / "Frameworks" / "libdemo.dylib"
                        library.parent.mkdir(parents=True)
                        library.write_text("library", encoding="utf-8")
                    entitlements = root / "src" / "entitlements.plist"
                    entitlements.parent.mkdir(parents=True)
                    entitlements.write_text("plist", encoding="utf-8")
                    config_path = root / "package.json"
                    config_path.write_text(json.dumps(data), encoding="utf-8")
                    config = load_package_config(config_path, platform="mac")
                    command_names: list[str] = []

                    def fake_run(
                        command: list[str],
                        *,
                        _failing_step: str = failing_step,
                        _command_names: list[str] = command_names,
                        **_: object,
                    ) -> subprocess.CompletedProcess[str]:
                        command_name = Path(command[0]).name
                        _command_names.append(command_name)
                        return subprocess.CompletedProcess(
                            command,
                            8 if command_name == _failing_step else 0,
                            "",
                            (f"{_failing_step} failed" if command_name == _failing_step else ""),
                        )

                    with mock.patch(
                        "repomgrcpp.package.common.subprocess.run", side_effect=fake_run
                    ):
                        with self.assertRaisesRegex(PackageError, failing_step):
                            deploy_mac(config)
                    self.assertIn(failing_step, command_names)

    def test_mac_deploy_requires_configured_libraries(self) -> None:
        for mac_overrides, expected in (
            ({"extraLibraries": ["missing.dylib"]}, "Required file not found"),
            ({"copyLibraryNames": ["LibA.dylib"]}, "macOS library not found"),
            ({"libraryGlobs": ["LibA*.dylib"]}, "matched configured pattern"),
        ):
            with self.subTest(mac_overrides=mac_overrides):
                with tempfile.TemporaryDirectory() as tempdir:
                    root = Path(tempdir)
                    data = minimal_config(root)
                    data["mac"].update(mac_overrides)  # type: ignore[attr-defined]
                    source_bundle = root / "build" / "DemoApp.app"
                    executable = source_bundle / "Contents" / "MacOS" / "DemoApp"
                    executable.parent.mkdir(parents=True)
                    executable.write_text("app", encoding="utf-8")
                    entitlements = root / "src" / "entitlements.plist"
                    entitlements.parent.mkdir(parents=True)
                    entitlements.write_text("plist", encoding="utf-8")
                    config_path = root / "package.json"
                    config_path.write_text(json.dumps(data), encoding="utf-8")
                    config = load_package_config(config_path, platform="mac")
                    succeeded = subprocess.CompletedProcess([], 0, "", "")

                    with mock.patch(
                        "repomgrcpp.package.common.subprocess.run", return_value=succeeded
                    ):
                        with self.assertRaisesRegex(PackageError, expected):
                            deploy_mac(config)

    def test_mac_deploy_rejects_missing_discovered_library(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data = minimal_config(root)
            search_path = root / "libraries"
            search_path.mkdir()
            data["mac"]["librarySearchPaths"] = [str(search_path)]  # type: ignore[index]
            source_bundle = root / "build" / "DemoApp.app"
            executable = source_bundle / "Contents" / "MacOS" / "DemoApp"
            executable.parent.mkdir(parents=True)
            executable.write_text("app", encoding="utf-8")
            entitlements = root / "src" / "entitlements.plist"
            entitlements.parent.mkdir(parents=True)
            entitlements.write_text("plist", encoding="utf-8")
            config_path = root / "package.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")
            config = load_package_config(config_path, platform="mac")

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                stdout = (
                    f"{command[-1]}:\n"
                    "    /opt/local/lib/LibA.dylib (compatibility version 1.0.0)\n"
                    if command[:2] == ["otool", "-L"]
                    else ""
                )
                return subprocess.CompletedProcess(command, 0, stdout, "")

            with mock.patch("repomgrcpp.package.common.subprocess.run", side_effect=fake_run):
                with self.assertRaisesRegex(PackageError, "Mach-O dependency not found.+LibA"):
                    deploy_mac(config)

    def test_linux_deploy_propagates_appimage_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data = minimal_config(root)
            target = root / "build" / "DemoApp"
            target.parent.mkdir(parents=True)
            target.write_text("app", encoding="utf-8")
            data["paths"]["targetPath"] = str(target)  # type: ignore[index]
            data["linux"]["appImageTool"] = "appimagetool"  # type: ignore[index]
            config_path = root / "package.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")
            config = load_package_config(config_path, platform="linux")
            failed = subprocess.CompletedProcess([], 6, "", "appimage failed")

            with mock.patch("repomgrcpp.package.common.subprocess.run", return_value=failed):
                with self.assertRaisesRegex(PackageError, r"command failed \(6\): appimagetool"):
                    deploy_linux(config)

    def test_linux_deploy_requires_configured_library_and_appimage_output(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            for linux_overrides, expected in (
                ({"extraLibraries": ["missing.so"]}, "Linux library not found"),
                ({"appImageTool": "appimagetool"}, "did not create expected output"),
            ):
                with self.subTest(linux_overrides=linux_overrides):
                    data = minimal_config(root)
                    data["linux"].update(linux_overrides)  # type: ignore[attr-defined]
                    target = root / "build" / "DemoApp"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("app", encoding="utf-8")
                    data["paths"]["targetPath"] = str(target)  # type: ignore[index]
                    config_path = root / "package.json"
                    config_path.write_text(json.dumps(data), encoding="utf-8")
                    config = load_package_config(config_path, platform="linux")
                    succeeded = subprocess.CompletedProcess([], 0, "", "")

                    with mock.patch(
                        "repomgrcpp.package.common.subprocess.run", return_value=succeeded
                    ):
                        with self.assertRaisesRegex(PackageError, expected):
                            deploy_linux(config)

    def test_mac_otool_library_helpers(self) -> None:
        output = """/tmp/App
    @rpath/QtCore.framework/Versions/A/QtCore (compatibility version 6.0.0, current version 6.7.0)
    /opt/homebrew/lib/libdemo.dylib (compatibility version 1.0.0, current version 1.0.0)
"""
        self.assertEqual(parse_otool_deps(output)[1], "/opt/homebrew/lib/libdemo.dylib")
        rpath_output = """
Load command 1
          cmd LC_RPATH
      cmdsize 48
         path @executable_path/../Frameworks (offset 12)
Load command 2
          cmd LC_RPATH
      cmdsize 32
         path /opt/homebrew/lib (offset 12)
"""
        self.assertEqual(
            parse_otool_rpaths(rpath_output),
            ["@executable_path/../Frameworks", "/opt/homebrew/lib"],
        )
        command = build_sign_command(Path("/tmp/App.app"), identity="Developer ID", runtime=True)
        self.assertIn("--options", command)
        self.assertIn("runtime", command)

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "libdemo.dylib").write_text("", encoding="utf-8")
            self.assertEqual(find_library("libdemo.dylib", [root]), root / "libdemo.dylib")

    def test_mac_large_search_and_bundle_fixtures_walk_each_root_once(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            search_root = root / "libraries"
            bundle = root / "SampleApp.app"
            (search_root / "libdemo.dylib").parent.mkdir(parents=True)
            (search_root / "libdemo.dylib").write_bytes(b"library")
            for index in range(256):
                library = search_root / f"group{index:03d}" / f"lib{index:03d}.dylib"
                library.parent.mkdir()
                library.write_bytes(b"library")
            for index in range(64):
                library = bundle / "Contents" / "Frameworks" / f"lib{index:03d}.dylib"
                library.parent.mkdir(parents=True, exist_ok=True)
                library.write_bytes(b"library")
            for index in range(4):
                executable = bundle / "Contents" / "MacOS" / f"helper{index}"
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_bytes(b"executable")
            for index in range(256):
                resource = bundle / "Contents" / "Resources" / f"asset{index:03d}.dat"
                resource.parent.mkdir(parents=True, exist_ok=True)
                resource.write_bytes(b"asset")

            with mock.patch.object(
                mac_deploy_module,
                "_iter_tree_files",
                wraps=mac_deploy_module._iter_tree_files,
            ) as walk:
                search_index = build_library_search_index([search_root])
                binaries = collect_bundle_binaries(bundle)
                self.assertEqual(
                    find_library("lib200.dylib", [search_root], index=search_index),
                    search_root / "group200" / "lib200.dylib",
                )
                self.assertEqual(search_index.matching("*.dylib"), (search_root / "libdemo.dylib",))
                self.assertEqual(len(search_index.matching("group*/*.dylib")), 256)
                self.assertEqual(len(search_index.matching("**/*.dylib")), 257)

            self.assertEqual(walk.call_count, 2)
            self.assertEqual(
                [call.args[0] for call in walk.call_args_list],
                [search_root, bundle / "Contents"],
            )
            self.assertEqual(len(binaries), 68)

    def test_mac_otool_and_install_name_tool_processes_are_batched(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            bundle = root / "SampleApp.app"
            binaries = [
                bundle / "Contents" / "MacOS" / "SampleApp",
                bundle / "Contents" / "Frameworks" / "libsample.dylib",
            ]
            commands: list[list[str]] = []

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                if command[:2] == ["otool", "-l"]:
                    chunks = []
                    for binary in binaries:
                        chunks.append(
                            f"{binary}:\n"
                            "Load command 1\n"
                            "          cmd LC_RPATH\n"
                            "         path /opt/homebrew/lib (offset 12)\n"
                            "Load command 2\n"
                            "          cmd LC_RPATH\n"
                            "         path @loader_path/../Frameworks (offset 12)"
                        )
                    return subprocess.CompletedProcess(command, 0, "\n".join(chunks), "")
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch("repomgrcpp.package.common.subprocess.run", side_effect=fake_run):
                normalize_bundle_rpaths(bundle, prefix="test", binaries=binaries)

            self.assertEqual(commands[0], ["otool", "-l", *map(str, binaries)])
            install_commands = [
                command for command in commands if command[0] == "install_name_tool"
            ]
            self.assertEqual(len(install_commands), 4)
            for binary in binaries:
                binary_commands = [
                    command for command in install_commands if command[-1] == str(binary)
                ]
                self.assertEqual(len(binary_commands), 2)
                self.assertEqual(binary_commands[0].count("-delete_rpath"), 2)
                self.assertNotIn("-add_rpath", binary_commands[0])
                self.assertEqual(binary_commands[1].count("-add_rpath"), 2)
                self.assertNotIn("-delete_rpath", binary_commands[1])

    def test_mac_otool_batch_failure_falls_back_per_binary(self) -> None:
        binaries = [Path("/tmp/a"), Path("/tmp/b")]
        responses = (
            subprocess.CompletedProcess([], 1, "", "batch failed"),
            subprocess.CompletedProcess([], 0, "a:\n", ""),
            subprocess.CompletedProcess([], 1, "", "b failed"),
        )
        with mock.patch("repomgrcpp.package.common.subprocess.run", side_effect=responses) as run:
            outputs = inspect_otool_outputs(
                binaries,
                "-L",
                prefix="test",
                allow_failures=True,
            )

        self.assertEqual(outputs, {binaries[0]: "a:\n", binaries[1]: None})
        self.assertEqual(run.call_count, 3)

    @unittest.skipUnless(sys.platform == "darwin", "native macOS packaging smoke")
    def test_native_macos_deploy_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data = minimal_config(root)
            source_bundle = root / "build" / "DemoApp.app"
            executable = source_bundle / "Contents" / "MacOS" / "DemoApp"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            entitlements = root / "src" / "entitlements.plist"
            entitlements.parent.mkdir(parents=True)
            entitlements.write_text('<plist version="1.0"><dict/></plist>\n', encoding="utf-8")
            tool_dir = root / "tools"
            qt_bin = root / "qt" / "bin"
            tool_dir.mkdir()
            qt_bin.mkdir(parents=True)
            for tool in (tool_dir / "codesign", qt_bin / "macdeployqt"):
                tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                tool.chmod(0o755)
            config_path = root / "package.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")
            config = load_package_config(config_path, platform="mac")

            with mock.patch.dict(
                os.environ,
                {"PATH": os.pathsep.join([str(tool_dir), os.environ.get("PATH", "")])},
            ):
                deployed = deploy_mac(config)

            self.assertTrue((deployed / "Contents" / "MacOS" / "DemoApp").is_file())

    @unittest.skipUnless(sys.platform == "darwin", "native macOS rpath smoke")
    def test_native_macos_rpath_normalize_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "main.c"
            binary = root / "SampleApp"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            compiled = subprocess.run(
                [
                    "cc",
                    str(source),
                    "-Wl,-rpath,/opt/homebrew/lib",
                    "-Wl,-rpath,@loader_path/../Frameworks",
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)

            normalize_bundle_rpaths(root / "SampleApp.app", prefix="native", binaries=[binary])

            inspected = subprocess.run(
                ["otool", "-l", str(binary)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(
                parse_otool_rpaths(inspected.stdout),
                [
                    "@executable_path/../Frameworks",
                    "@loader_path/../Frameworks",
                ],
            )

    @unittest.skipUnless(sys.platform.startswith("linux"), "native Linux packaging smoke")
    def test_native_linux_deploy_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data = minimal_config(root)
            target = root / "build" / "DemoApp"
            target.parent.mkdir(parents=True)
            target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            target.chmod(0o755)
            config_path = root / "package.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")
            config = load_package_config(config_path, platform="linux")

            deployed = deploy_linux(config)

            self.assertTrue((deployed / "AppRun").is_file())
            syntax = subprocess.run(
                ["bash", "-n", str(deployed / "AppRun")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(syntax.returncode, 0, syntax.stderr)

    @unittest.skipUnless(sys.platform == "win32", "native Windows packaging smoke")
    def test_native_windows_deploy_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            data = minimal_config(root)
            target = root / "build" / "DemoApp.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"MZ")
            data["paths"]["targetPath"] = str(target)  # type: ignore[index]
            windeployqt = root / "qt" / "bin" / "windeployqt.cmd"
            windeployqt.parent.mkdir(parents=True)
            windeployqt.write_text("@exit /b 0\r\n", encoding="utf-8")
            data["windows"]["windeployqt"] = str(windeployqt)  # type: ignore[index]
            config_path = root / "package.json"
            config_path.write_text(json.dumps(data), encoding="utf-8")
            config = load_package_config(config_path, platform="win")

            with mock.patch("repomgrcpp.package.win_deploy.find_dumpbin", return_value=None):
                deployed = deploy_windows(config)

            self.assertTrue((deployed / "DemoApp.exe").is_file())

    def test_linux_apprun_and_system_library_filter(self) -> None:
        script = generate_apprun(app_name="DemoApp", debug_build=True)

        self.assertIn("QT_QPA_PLATFORM_PLUGIN_PATH", script)
        self.assertIn("APP_ENABLE_FALLBACK", script)
        self.assertIn("Wayland failed, trying XCB fallback", script)
        self.assertTrue(should_skip_system_library("libstdc++.so.6"))
        self.assertFalse(should_skip_system_library("libQt6Core.so.6"))


class PackageCliTests(unittest.TestCase):
    def test_package_tool_help_and_validate_config(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            config_path = root / "package.json"
            config_path.write_text(json.dumps(minimal_config(root)), encoding="utf-8")
            help_result = subprocess.run(
                [sys.executable, "-m", "repomgrcpp.package.cli", "--help"],
                cwd=REPO_ROOT,
                env=python_subprocess_env(),
                capture_output=True,
                text=True,
                check=False,
            )
            validate_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomgrcpp.package.cli",
                    "validate-config",
                    "--config",
                    str(config_path),
                    "--platform",
                    "win",
                ],
                cwd=REPO_ROOT,
                env=python_subprocess_env(),
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("deploy-win", help_result.stdout)
        self.assertEqual(validate_result.returncode, 0, validate_result.stderr)

    def test_package_tool_subcommand_help(self) -> None:
        for command in (
            "wix-fragment",
            "deploy-win",
            "deploy-mac",
            "deploy-linux",
            "validate-config",
        ):
            with self.subTest(command=command):
                completed = subprocess.run(
                    [sys.executable, "-m", "repomgrcpp.package.cli", command, "--help"],
                    cwd=REPO_ROOT,
                    env=python_subprocess_env(),
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
