from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repomgrandroid.workflow import (  # noqa: E402
    AndroidWorkflowConfig,
    android_environment,
    find_freecm_extension_root,
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
            "l1_gradle_tasks": (":core:nativebridge:externalNativeBuildDebug", ":app:assembleDebug"),
            "l2_scripts": ("configs/smoke_packet_schema.sh", "configs/smoke_native_handles.sh"),
            "l3_scripts": ("configs/smoke_android_viewer.sh",),
            "l4_scripts": ("configs/smoke_activity_lifecycle.sh",),
            "validator_platform": "darwin",
        }
        values.update(overrides)
        return AndroidWorkflowConfig(**values)

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
        )

        self.assertEqual(env["ANDROID_SDK_ROOT"], "/sdk/root")
        self.assertEqual(env["ANDROID_HOME"], "/sdk/root")
        self.assertEqual(
            env["PATH"].split(os.pathsep)[:4],
            [
                "/jdk/bin",
                "/sdk/root/platform-tools",
                "/sdk/root/emulator",
                "/sdk/root/cmdline-tools/latest/bin",
            ],
        )
        self.assertEqual(env["PATH"].split(os.pathsep)[4], "/usr/bin")

    def test_android_environment_uses_android_home_then_default_sdk_and_homebrew_jdk(self) -> None:
        homebrew_jdk = self.root / "openjdk@17"
        homebrew_jdk.mkdir()
        android_home_env = android_environment(
            {"ANDROID_HOME": "/sdk/home", "PATH": "/bin"},
            home=self.root,
            homebrew_jdk_path=homebrew_jdk,
        )
        default_env = android_environment(
            {"PATH": "/bin"},
            home=self.root,
            homebrew_jdk_path=self.root / "missing-jdk",
        )

        self.assertEqual(android_home_env["ANDROID_SDK_ROOT"], "/sdk/home")
        self.assertEqual(android_home_env["ANDROID_HOME"], "/sdk/home")
        self.assertEqual(android_home_env["JAVA_HOME"], str(homebrew_jdk))
        self.assertEqual(
            default_env["ANDROID_SDK_ROOT"],
            str(self.root / "Library/Android/sdk"),
        )
        self.assertNotIn("JAVA_HOME", default_env)

    def test_gradlew_command_uses_repo_local_wrapper(self) -> None:
        command = gradlew_command(self.repo_root, [":app:assembleDebug"])

        self.assertEqual(command, [str(self.repo_root / "gradlew"), ":app:assembleDebug"])

    def test_find_freecm_extension_root_uses_env_then_repo_then_sibling(self) -> None:
        env_extension = self.root / "EnvFreeCM" / "vscode-extension"
        repo_extension = self.repo_root / "FreeCM" / "vscode-extension"
        sibling_extension = self.repo_root.parent / "FreeCM" / "vscode-extension"
        for extension_root in (env_extension, repo_extension, sibling_extension):
            extension_root.mkdir(parents=True, exist_ok=True)
            (extension_root / "package.json").write_text("{}\n", encoding="utf-8")

        self.assertEqual(
            find_freecm_extension_root(
                self.repo_root,
                {"FREECM_EXTENSION_ROOT": str(env_extension)},
            ),
            env_extension.resolve(),
        )
        (env_extension / "package.json").unlink()
        self.assertEqual(
            find_freecm_extension_root(
                self.repo_root,
                {"FREECM_EXTENSION_ROOT": str(env_extension)},
            ),
            repo_extension.resolve(),
        )
        (repo_extension / "package.json").unlink()
        self.assertEqual(
            find_freecm_extension_root(self.repo_root, {}),
            sibling_extension.resolve(),
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
                    str(self.repo_root / "gradlew"),
                    ":core:nativebridge:testDebugUnitTest",
                    ":app:testDebugUnitTest",
                ),
            ],
        )
        self.assertTrue(all(cwd == self.repo_root for _, _, cwd, _ in self.commands))

    def test_run_l1_generates_gradle_extension_compile_and_validator(self) -> None:
        extension_root = self.repo_root / "FreeCM" / "vscode-extension"
        extension_root.mkdir(parents=True)
        (extension_root / "package.json").write_text("{}\n", encoding="utf-8")
        config = self.android_config()

        run_test_level(config, "l1", env={"PATH": "/usr/bin"})

        commands = [command for _, command, _, _ in self.commands]
        self.assertEqual(
            commands,
            [
                (
                    str(self.repo_root / "gradlew"),
                    ":core:nativebridge:externalNativeBuildDebug",
                    ":app:assembleDebug",
                ),
                (
                    "npm",
                    "--prefix",
                    str(extension_root.resolve()),
                    "run",
                    "compile",
                    "--",
                    "--pretty",
                    "false",
                ),
                (
                    "node",
                    str(extension_root.resolve() / "out/validateRepoCommands.js"),
                    "--preview",
                    "--platform",
                    "darwin",
                    str(self.repo_root),
                ),
            ],
        )

    def test_run_l1_requires_extension_when_configured(self) -> None:
        config = self.android_config()

        with self.assertRaisesRegex(RuntimeError, "extension root was not found"):
            run_test_level(config, "l1", env={"PATH": "/usr/bin"})

    def test_run_l1_can_skip_validator_when_extension_is_optional(self) -> None:
        config = self.android_config(require_freecm_extension=False)

        run_test_level(config, "l1", env={"PATH": "/usr/bin"})

        self.assertEqual(
            [command for _, command, _, _ in self.commands],
            [
                (
                    str(self.repo_root / "gradlew"),
                    ":core:nativebridge:externalNativeBuildDebug",
                    ":app:assembleDebug",
                ),
            ],
        )

    def test_precommit_and_all_expand_test_levels(self) -> None:
        extension_root = self.repo_root / "FreeCM" / "vscode-extension"
        extension_root.mkdir(parents=True)
        (extension_root / "package.json").write_text("{}\n", encoding="utf-8")
        config = self.android_config()

        run_test_level(config, "precommit", env={"PATH": "/usr/bin"})
        precommit_labels = [label for label, _, _, _ in self.commands]
        self.commands.clear()
        run_test_level(config, "all", env={"PATH": "/usr/bin"})
        all_labels = [label for label, _, _, _ in self.commands]

        self.assertEqual(
            precommit_labels,
            ["l0", "l0", "l0", "l0", "l1", "l1", "l1", "l2", "l2"],
        )
        self.assertEqual(
            all_labels,
            ["l0", "l0", "l0", "l0", "l1", "l1", "l1", "l2", "l2", "l3", "l4"],
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
