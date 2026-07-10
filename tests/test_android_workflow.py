from __future__ import annotations

import hashlib
import json
import ntpath
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import repomgrandroid  # noqa: E402
from repomgrandroid.workflow import (  # noqa: E402
    AndroidWorkflowConfig,
    FreeCMValidatorBuildStatus,
    android_environment,
    find_freecm_extension_root,
    freecm_validator_build_status,
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
            "validator_platform": "darwin",
            "host_platform": "darwin",
        }
        values.update(overrides)
        return AndroidWorkflowConfig(**values)

    def write_validator_fixture(self, extension_root: Path) -> dict[str, Path]:
        inputs = (
            "validator-build-contract.json",
            "src/validateRepoCommands.ts",
            "src/repoCommands.ts",
            "tsconfig.json",
            "package.json",
            "package-lock.json",
        )
        outputs = ("out/validateRepoCommands.js", "out/repoCommands.js")
        contract = {
            "schemaVersion": 1,
            "algorithm": "sha256",
            "stampPath": "out/.freecm-validator-inputs.json",
            "inputs": list(inputs),
            "outputs": list(outputs),
        }
        extension_root.mkdir(parents=True, exist_ok=True)
        for relative_path in (*inputs[1:], *outputs):
            path = extension_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture:{relative_path}\n", encoding="utf-8")
        contract_path = extension_root / inputs[0]
        contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")

        def digest(relative_path: str) -> str:
            return hashlib.sha256((extension_root / relative_path).read_bytes()).hexdigest()

        stamp = {
            "schemaVersion": 1,
            "algorithm": "sha256",
            "inputs": {relative_path: digest(relative_path) for relative_path in sorted(inputs)},
            "outputs": {relative_path: digest(relative_path) for relative_path in sorted(outputs)},
        }
        stamp_path = extension_root / contract["stampPath"]
        stamp_path.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
        return {
            "contract": contract_path,
            "source": extension_root / "src/validateRepoCommands.ts",
            "output": extension_root / "out/validateRepoCommands.js",
            "stamp": stamp_path,
        }

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

    def test_validator_status_api_is_exported_from_package(self) -> None:
        self.assertIs(repomgrandroid.FreeCMValidatorBuildStatus, FreeCMValidatorBuildStatus)
        self.assertIs(
            repomgrandroid.freecm_validator_build_status,
            freecm_validator_build_status,
        )

    def test_validator_status_uses_content_hashes_not_mtime(self) -> None:
        extension_root = self.root / "Extension"
        paths = self.write_validator_fixture(extension_root)
        source_stat = paths["source"].stat()

        os.utime(paths["source"], (source_stat.st_atime + 60, source_stat.st_mtime + 60))
        self.assertTrue(freecm_validator_build_status(extension_root).ready)

        paths["source"].write_text("changed without mtime change\n", encoding="utf-8")
        os.utime(paths["source"], (source_stat.st_atime, source_stat.st_mtime))
        status = freecm_validator_build_status(extension_root)
        self.assertFalse(status.ready)
        self.assertIn("input content changed", status.reason or "")

    def test_validator_status_rejects_missing_malformed_and_tampered_outputs(self) -> None:
        extension_root = self.root / "Extension"
        paths = self.write_validator_fixture(extension_root)
        paths["output"].unlink()
        self.assertIn(
            "missing validator output",
            freecm_validator_build_status(extension_root).reason or "",
        )

        paths = self.write_validator_fixture(extension_root)
        paths["stamp"].write_text("{bad", encoding="utf-8")
        self.assertIn(
            "invalid validator stamp",
            freecm_validator_build_status(extension_root).reason or "",
        )

        paths = self.write_validator_fixture(extension_root)
        paths["output"].write_text("tampered\n", encoding="utf-8")
        self.assertIn(
            "output content changed",
            freecm_validator_build_status(extension_root).reason or "",
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for cross-language stamp test")
    def test_node_stamp_writer_matches_python_verifier(self) -> None:
        extension_root = self.root / "Extension"
        contract = {
            "schemaVersion": 1,
            "algorithm": "sha256",
            "stampPath": "out/.freecm-validator-inputs.json",
            "inputs": ["validator-build-contract.json", "src/input.ts"],
            "outputs": ["out/output.js"],
        }
        for relative_path, content in (
            ("src/input.ts", "input\n"),
            ("out/output.js", "output\n"),
        ):
            path = extension_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (extension_root / "validator-build-contract.json").write_text(
            json.dumps(contract, indent=2) + "\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                "node",
                str(REPO_ROOT / "vscode-extension/scripts/write-validator-stamp.mjs"),
                "--extension-root",
                str(extension_root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(freecm_validator_build_status(extension_root).ready)

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

    def test_run_l1_reuses_current_validator_without_compile(self) -> None:
        extension_root = self.repo_root / "FreeCM" / "vscode-extension"
        self.write_validator_fixture(extension_root)
        config = self.android_config()

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

    def test_run_l1_force_rebuild_compiles_once_then_default_reuses(self) -> None:
        extension_root = self.repo_root / "FreeCM" / "vscode-extension"
        self.write_validator_fixture(extension_root)

        run_test_level(
            self.android_config(force_validator_rebuild=True),
            "l1",
            env={"PATH": "/usr/bin"},
        )
        run_test_level(self.android_config(), "l1", env={"PATH": "/usr/bin"})

        commands = [command for _, command, _, _ in self.commands]
        compile_commands = [command for command in commands if command and command[0] == "npm"]
        validator_commands = [command for command in commands if command and command[0] == "node"]
        self.assertEqual(
            compile_commands,
            [
                (
                    "npm",
                    "--prefix",
                    str(extension_root.resolve()),
                    "run",
                    "compile",
                    "--",
                    "--pretty",
                    "false",
                )
            ],
        )
        self.assertEqual(len(validator_commands), 2)

    def test_run_l1_reports_missing_or_stale_validator(self) -> None:
        extension_root = self.repo_root / "FreeCM" / "vscode-extension"
        paths = self.write_validator_fixture(extension_root)
        paths["stamp"].unlink()

        with self.assertRaisesRegex(RuntimeError, "missing validator stamp"):
            run_test_level(self.android_config(), "l1", env={"PATH": "/usr/bin"})

        paths = self.write_validator_fixture(extension_root)
        paths["source"].write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "input content changed"):
            run_test_level(self.android_config(), "l1", env={"PATH": "/usr/bin"})

    def test_force_rebuild_must_produce_current_stamp(self) -> None:
        extension_root = self.repo_root / "FreeCM" / "vscode-extension"
        extension_root.mkdir(parents=True)
        (extension_root / "package.json").write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "forced extension compile"):
            run_test_level(
                self.android_config(force_validator_rebuild=True),
                "l1",
                env={"PATH": "/usr/bin"},
            )

        commands = [command for _, command, _, _ in self.commands]
        self.assertTrue(any(command and command[0] == "npm" for command in commands))

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
                    posixpath.join(str(self.repo_root), "gradlew"),
                    ":core:nativebridge:externalNativeBuildDebug",
                    ":app:assembleDebug",
                ),
            ],
        )

    def test_precommit_and_all_expand_test_levels(self) -> None:
        extension_root = self.repo_root / "FreeCM" / "vscode-extension"
        self.write_validator_fixture(extension_root)
        config = self.android_config()

        run_test_level(config, "precommit", env={"PATH": "/usr/bin"})
        precommit_labels = [label for label, _, _, _ in self.commands]
        self.commands.clear()
        run_test_level(config, "all", env={"PATH": "/usr/bin"})
        all_labels = [label for label, _, _, _ in self.commands]

        self.assertEqual(
            precommit_labels,
            ["l0", "l0", "l0", "l0", "l1", "l1", "l2", "l2"],
        )
        self.assertEqual(
            all_labels,
            ["l0", "l0", "l0", "l0", "l1", "l1", "l2", "l2", "l3", "l4"],
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
