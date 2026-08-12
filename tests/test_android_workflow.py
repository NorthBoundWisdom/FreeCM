from __future__ import annotations

import json
import ntpath
import posixpath
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from freecm.errors import RepoCommandManifestError  # noqa: E402
from freecm.repo_commands import validate_repo_command_manifest  # noqa: E402
from repomgrandroid.workflow import (  # noqa: E402
    AndroidWorkflowConfig,
    android_environment,
    gradlew_command,
    run_test_level,
)


class AndroidWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.repo_root = self.root / "HostAndroid"
        self.repo_root.mkdir()
        self.repo_root = self.repo_root.resolve()

        self.commands: list[tuple[str, tuple[str, ...], Path, dict[str, str]]] = []

        patcher = mock.patch("repomgrandroid.workflow.run_logged_command")
        self.mock_run = patcher.start()
        self.addCleanup(patcher.stop)

        def side_effect(command, cwd=None, env=None, prefix="", check=True):
            label = prefix.strip(" \n[]")
            self.commands.append((label, tuple(command), cwd, env))
            return mock.Mock(returncode=0)

        self.mock_run.side_effect = side_effect

    def android_config(self, **overrides: object) -> AndroidWorkflowConfig:
        values: dict[str, object] = {
            "repo_root": self.repo_root,
            "shell_check_scripts": ("configs/run_android_app.sh",),
            "python_check_files": (
                "configs/android_screenshot_nonblank.py",
                "configs/android_workflow.py",
            ),
            "l0_gradle_tasks": (":core:nativebridge:testDebugUnitTest", ":app:testDebugUnitTest"),
            "l1_gradle_tasks": (
                ":core:nativebridge:externalNativeBuildDebug",
                ":app:assembleDebug",
            ),
            "l2_scripts": ("configs/smoke_packet_schema.sh", "configs/smoke_native_handles.sh"),
            "l3_scripts": ("configs/smoke_android_viewer.sh",),
            "l4_scripts": ("configs/smoke_activity_lifecycle.sh",),
            "host_platform": "darwin",
        }
        values.update(overrides)
        return AndroidWorkflowConfig(**values)

    def write_command_manifest(self, manifest: object | None = None) -> Path:
        if manifest is None:
            manifest = {
                "version": 2,
                "commands": {
                    "config": [
                        {
                            "id": "android-debug",
                            "label": "Android Debug",
                            "command": "python3",
                            "args": ["configs/android_workflow.py", "config"],
                            "platforms": ["darwin"],
                            "default": True,
                            "defaults": {},
                        }
                    ]
                },
            }
        manifest_path = self.repo_root / "configs" / "freecm.commands.jsonc"
        manifest_path.parent.mkdir()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path

    def test_android_environment_prefers_android_sdk_root_and_existing_java_home(self) -> None:
        env = android_environment(
            {
                "ANDROID_SDK_ROOT": "/sdk/root",
                "ANDROID_HOME": "/sdk/home",
                "JAVA_HOME": "/jdk",
                "PATH": "/usr/bin",
            },
            home=self.root,
            homebrew_jdk_path=self.root / "missing-jdk",
            platform="linux",
        )

        self.assertEqual(env["ANDROID_SDK_ROOT"], "/sdk/root")
        self.assertEqual(env["ANDROID_HOME"], "/sdk/root")
        path_entries = env["PATH"].split(":")
        self.assertEqual(
            path_entries[:4],
            [
                "/jdk/bin",
                "/sdk/root/platform-tools",
                "/sdk/root/emulator",
                "/sdk/root/cmdline-tools/latest/bin",
            ],
        )
        self.assertEqual(path_entries[4], "/usr/bin")

    def test_android_environment_uses_android_home_then_default_sdk_and_homebrew_jdk(self) -> None:
        homebrew_jdk = self.root / "openjdk@17"
        homebrew_jdk.mkdir()
        android_home_env = android_environment(
            {"ANDROID_HOME": "/sdk/home", "PATH": "/bin"},
            home=self.root,
            homebrew_jdk_path=homebrew_jdk,
            platform="darwin",
        )
        default_env = android_environment(
            {"PATH": "/bin"},
            home=self.root,
            homebrew_jdk_path=self.root / "missing-jdk",
            platform="darwin",
        )

        self.assertEqual(android_home_env["ANDROID_SDK_ROOT"], "/sdk/home")
        self.assertEqual(android_home_env["ANDROID_HOME"], "/sdk/home")
        self.assertEqual(android_home_env["JAVA_HOME"], str(homebrew_jdk))
        self.assertEqual(
            default_env["ANDROID_SDK_ROOT"],
            posixpath.join(str(self.root), "Library", "Android", "sdk"),
        )
        self.assertNotIn("JAVA_HOME", default_env)

    def test_gradlew_command_uses_repo_local_wrapper(self) -> None:
        command = gradlew_command(self.repo_root, [":app:assembleDebug"], platform="linux")

        self.assertEqual(
            command,
            [posixpath.join(str(self.repo_root), "gradlew"), ":app:assembleDebug"],
        )

    def test_android_defaults_and_path_assembly_are_platform_aware(self) -> None:
        fake_homebrew_jdk = self.root / "openjdk@17"
        fake_homebrew_jdk.mkdir()
        mac = android_environment({}, home=self.root, platform="darwin")
        linux = android_environment(
            {},
            home=self.root,
            platform="linux",
            homebrew_jdk_path=fake_homebrew_jdk,
        )
        windows = android_environment(
            {
                "LOCALAPPDATA": r"C:\Users\Dev\AppData\Local",
                "JAVA_HOME": r"C:\Jdk",
                "Path": r"C:\Windows",
            },
            home=Path("C:/Users/Dev"),
            platform="win32",
        )

        self.assertEqual(
            mac["ANDROID_SDK_ROOT"],
            posixpath.join(str(self.root), "Library", "Android", "sdk"),
        )
        self.assertEqual(
            linux["ANDROID_SDK_ROOT"],
            posixpath.join(str(self.root), "Android", "Sdk"),
        )
        self.assertNotIn("JAVA_HOME", linux)
        self.assertEqual(
            windows["ANDROID_SDK_ROOT"],
            r"C:\Users\Dev\AppData\Local\Android\Sdk",
        )
        self.assertEqual(
            windows["Path"].split(";"),
            [
                r"C:\Jdk\bin",
                r"C:\Users\Dev\AppData\Local\Android\Sdk\platform-tools",
                r"C:\Users\Dev\AppData\Local\Android\Sdk\emulator",
                r"C:\Users\Dev\AppData\Local\Android\Sdk\cmdline-tools\latest\bin",
                r"C:\Windows",
            ],
        )
        self.assertNotIn("PATH", windows)

        windows_without_local_app_data = android_environment(
            {},
            home=Path("C:/Users/Dev"),
            platform="win32",
        )
        self.assertEqual(
            windows_without_local_app_data["ANDROID_SDK_ROOT"],
            r"C:\Users\Dev\AppData\Local\Android\Sdk",
        )

        windows_with_forward_slash_sdk = android_environment(
            {"ANDROID_SDK_ROOT": "C:/Android/Sdk", "Path": r"C:\Windows"},
            home=Path("C:/Users/Dev"),
            platform="win32",
        )
        self.assertEqual(
            windows_with_forward_slash_sdk["ANDROID_SDK_ROOT"],
            "C:/Android/Sdk",
        )
        self.assertEqual(
            windows_with_forward_slash_sdk["Path"].split(";")[:3],
            [
                r"C:\Android\Sdk\platform-tools",
                r"C:\Android\Sdk\emulator",
                r"C:\Android\Sdk\cmdline-tools\latest\bin",
            ],
        )

    def test_gradlew_command_uses_platform_default_and_explicit_override(self) -> None:
        windows_repo_root = Path("C:/Work/SampleApp")
        self.assertEqual(
            gradlew_command(windows_repo_root, ["tasks"], platform="win32"),
            [r"C:\Work\SampleApp\gradlew.bat", "tasks"],
        )
        self.assertEqual(
            gradlew_command(
                windows_repo_root,
                ["tasks"],
                platform="win32",
                gradle_wrapper="tools/custom-wrapper",
            ),
            [r"C:\Work\SampleApp\tools\custom-wrapper", "tasks"],
        )
        self.assertEqual(
            gradlew_command(
                windows_repo_root,
                ["tasks"],
                platform="win32",
                gradle_wrapper=r"D:\BuildTools\gradlew.bat",
            ),
            [r"D:\BuildTools\gradlew.bat", "tasks"],
        )

    def test_run_l0_generates_checks_and_gradle_tasks(self) -> None:
        config = self.android_config()

        run_test_level(config, "l0", env={"PATH": "/usr/bin"})

        commands = [command for _, command, _, _ in self.commands]
        self.assertEqual(
            commands,
            [
                ("bash", "-n", str(self.repo_root / "configs/run_android_app.sh")),
                (
                    "python3",
                    "-m",
                    "py_compile",
                    str(self.repo_root / "configs/android_screenshot_nonblank.py"),
                    str(self.repo_root / "configs/android_workflow.py"),
                ),
                ("git", "-C", str(self.repo_root), "diff", "--check"),
                (
                    posixpath.join(str(self.repo_root), "gradlew"),
                    ":core:nativebridge:testDebugUnitTest",
                    ":app:testDebugUnitTest",
                ),
            ],
        )
        self.assertTrue(all(cwd == self.repo_root for _, _, cwd, _ in self.commands))

    def test_run_l0_uses_windows_gradle_wrapper_default(self) -> None:
        config = self.android_config(
            host_platform="win32",
            shell_check_scripts=(),
            python_check_files=(),
        )

        run_test_level(config, "l0", env={"Path": r"C:\Windows"})

        self.assertEqual(
            self.commands[-1][1][0],
            ntpath.normpath(ntpath.join(str(self.repo_root), "gradlew.bat")),
        )

    def test_run_l1_validates_manifest_in_process_without_toolchain(self) -> None:
        self.write_command_manifest()
        config = self.android_config()

        with mock.patch(
            "repomgrandroid.workflow.validate_repo_command_manifest",
            wraps=validate_repo_command_manifest,
        ) as validate:
            run_test_level(config, "l1", env={"PATH": "/usr/bin"})

        commands = [command for _, command, _, _ in self.commands]
        self.assertEqual(
            commands,
            [
                (
                    posixpath.join(str(self.repo_root), "gradlew"),
                    ":core:nativebridge:externalNativeBuildDebug",
                    ":app:assembleDebug",
                ),
            ],
        )
        validate.assert_called_once_with(self.repo_root)

    def test_run_l1_reports_invalid_manifest(self) -> None:
        self.write_command_manifest({"version": 2, "commands": {"config": "invalid"}})

        with self.assertRaisesRegex(RepoCommandManifestError, "commands.config must be an array"):
            run_test_level(self.android_config(), "l1", env={"PATH": "/usr/bin"})

    def test_run_l1_skips_optional_missing_manifest(self) -> None:
        config = self.android_config()

        with mock.patch("repomgrandroid.workflow.validate_repo_command_manifest") as validate:
            run_test_level(config, "l1", env={"PATH": "/usr/bin"})

        validate.assert_not_called()
        self.assertEqual(
            [command for _, command, _, _ in self.commands],
            [
                (
                    posixpath.join(str(self.repo_root), "gradlew"),
                    ":core:nativebridge:externalNativeBuildDebug",
                    ":app:assembleDebug",
                ),
            ],
        )

    def test_precommit_and_all_expand_test_levels(self) -> None:
        config = self.android_config()

        run_test_level(config, "precommit", env={"PATH": "/usr/bin"})
        precommit_labels = [label for label, _, _, _ in self.commands]
        self.commands.clear()
        run_test_level(config, "all", env={"PATH": "/usr/bin"})
        all_labels = [label for label, _, _, _ in self.commands]

        self.assertEqual(
            precommit_labels,
            ["l0", "l0", "l0", "l0", "l1", "l2", "l2"],
        )
        self.assertEqual(
            all_labels,
            ["l0", "l0", "l0", "l0", "l1", "l2", "l2", "l3", "l4"],
        )

    def test_unknown_test_level_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported test level"):
            run_test_level(
                self.android_config(),
                "nightly",
                env={"PATH": "/usr/bin"},
            )


if __name__ == "__main__":
    unittest.main()
