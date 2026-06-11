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

from repomgrcpp.package.common import (  # noqa: E402
    PackageError,
    clean_dist_dir,
    contained_child,
    load_package_config,
)
from repomgrcpp.package.linux_deploy import (
    generate_apprun,
    should_skip_system_library,
)  # noqa: E402
from repomgrcpp.package.mac_deploy import (  # noqa: E402
    build_sign_command,
    find_library,
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
