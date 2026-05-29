from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


import hooks.install as install_hook
import hooks.pre_commit as pre_commit
import hooks.commit_msg as commit_msg


REPO_ROOT = Path(__file__).resolve().parents[1]


class HookConfigTests(unittest.TestCase):
    def test_path_ini_parses_only_active_hook_settings(self) -> None:
        config = install_hook.parse_path_ini(REPO_ROOT / "hooks" / "path.ini.sample")

        self.assertEqual(config["SOURCE_ROOTS"], "SourceCode")
        self.assertEqual(config["EXCLUDE_DIRS"], "SourceCode/thirdparty")
        self.assertIn("CLANG_FORMAT_PATH", config)
        self.assertIn("QMLFORMAT_PATH", config)
        self.assertEqual(config["QMLFORMAT_PATH"], "")
        self.assertNotIn("QMLLINT_PATH", config)
        self.assertNotIn("QML_IMPORT_DIRS", config)
        self.assertNotIn("CLANG_TIDY_PATH", config)
        self.assertNotIn("CLANGD_PATH", config)

    def test_install_config_keys_match_active_tools(self) -> None:
        self.assertEqual(install_hook.CLANG_FORMAT_CONFIG_KEY, "freecm.clangFormatPath")
        self.assertEqual(install_hook.QMLFORMAT_CONFIG_KEY, "freecm.qmlformatPath")
        self.assertEqual(install_hook.SOURCE_ROOTS_CONFIG_KEY, "freecm.hooks.sourceRoots")
        self.assertEqual(install_hook.EXCLUDED_DIRS_CONFIG_KEY, "freecm.hooks.excludeDirs")
        self.assertFalse(hasattr(install_hook, "QMLLINT_CONFIG_KEY"))
        self.assertFalse(hasattr(install_hook, "QML_IMPORT_DIRS_CONFIG_KEY"))

    def test_pre_commit_filters_qml_with_excludes(self) -> None:
        roots = (Path("SourceCode"),)
        excludes = (Path("SourceCode/thirdparty"),)

        self.assertTrue(
            pre_commit.is_qml_formattable(
                Path("SourceCode/Gui/View.qml"),
                source_roots=roots,
                excluded_dirs=excludes,
            )
        )
        self.assertFalse(
            pre_commit.is_qml_formattable(
                Path("Docs/View.qml"),
                source_roots=roots,
                excluded_dirs=excludes,
            )
        )

    def test_normalize_text_file_converts_crlf_and_trailing_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "demo.txt"
            path.write_bytes(b"hello \r\nworld\t\r\nend  ")

            changed = pre_commit.normalize_text_file(path)

            self.assertTrue(changed)
            self.assertEqual(path.read_bytes(), b"hello\nworld\nend")

    def test_large_file_threshold_is_15mb(self) -> None:
        self.assertEqual(pre_commit.MAX_FILE_SIZE_BYTES, 15 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            small = repo_root / "small.bin"
            large = repo_root / "large.bin"
            small.write_bytes(b"0" * pre_commit.MAX_FILE_SIZE_BYTES)
            large.write_bytes(b"0" * (pre_commit.MAX_FILE_SIZE_BYTES + 1))

            large_files = pre_commit.find_large_files(repo_root, [Path("small.bin"), Path("large.bin")])

        self.assertEqual([item.path for item in large_files], [Path("large.bin")])

    def test_qmlformat_is_optional_for_staged_formatting(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            qml_path = Path("SourceCode/Gui/View.qml")
            (repo_root / qml_path).parent.mkdir(parents=True)
            (repo_root / qml_path).write_text("import QtQuick\n", encoding="utf-8")

            with (
                mock.patch.object(pre_commit, "get_git_config", return_value=None),
                mock.patch.object(pre_commit, "stage_path") as stage_mock,
                mock.patch.object(pre_commit, "format_file") as format_mock,
            ):
                success = pre_commit.format_staged_files(repo_root, [qml_path])

            self.assertTrue(success)
            stage_mock.assert_not_called()
            format_mock.assert_not_called()

    def test_pre_commit_skips_symlinked_staged_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            outside = root / "outside.cpp"
            outside.write_text("int main() { return 0; }\n", encoding="utf-8")
            link = repo_root / "SourceCode" / "link.cpp"
            link.parent.mkdir(parents=True)
            try:
                link.symlink_to(outside)
            except OSError as exc:
                if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows symlink privilege is not available")
                raise

            with mock.patch.object(pre_commit.subprocess, "run") as run_mock:
                success = pre_commit.format_file(
                    repo_root,
                    Path("SourceCode/link.cpp"),
                    "/usr/bin/clang-format",
                    qml=False,
                )

            self.assertTrue(success)
            run_mock.assert_not_called()

    def test_install_allows_empty_optional_qmlformat_path(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            clang_format = repo_root / "clang-format"
            clang_format.write_text("#!/bin/sh\n", encoding="utf-8")
            clang_format.chmod(0o755)

            with (
                mock.patch.object(install_hook, "set_tool_path", return_value=True) as set_mock,
                mock.patch.object(install_hook, "unset_tool_path", return_value=True) as unset_mock,
            ):
                success = install_hook.apply_tool_paths_from_ini(
                    repo_root,
                    {
                        "CLANG_FORMAT_PATH": str(clang_format),
                        "QMLFORMAT_PATH": "",
                        "SOURCE_ROOTS": "SourceCode",
                        "EXCLUDE_DIRS": "SourceCode/thirdparty",
                    },
                )

            self.assertTrue(success)
            configured_keys = [call.args[1] for call in set_mock.call_args_list]
            self.assertIn(install_hook.CLANG_FORMAT_CONFIG_KEY, configured_keys)
            self.assertNotIn(install_hook.QMLFORMAT_CONFIG_KEY, configured_keys)
            unset_mock.assert_called_once_with(
                repo_root,
                install_hook.QMLFORMAT_CONFIG_KEY,
                "qmlformat",
            )

    def test_pre_commit_shell_is_thin_and_drops_legacy_checks(self) -> None:
        script = (REPO_ROOT / "hooks" / "pre-commit").read_text(encoding="utf-8")

        self.assertIn("pre_commit.py", script)
        self.assertNotIn("qmllint", script)
        self.assertNotIn("cmd_action_guard", script)
        self.assertNotIn("fonts_token_guard", script)
        self.assertNotIn("20MB", script)

    def test_installer_copies_python_hook_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            hooks_dir = Path(tempdir)

            self.assertTrue(install_hook.install_hook(REPO_ROOT / "hooks", hooks_dir, "commit_msg.py"))
            self.assertTrue((hooks_dir / "commit_msg.py").is_file())


class CommitMessageHookTests(unittest.TestCase):
    def run_commit_msg_hook(self, message: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tempdir:
            msg_path = Path(tempdir) / "COMMIT_EDITMSG"
            msg_path.write_text(message, encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-m", "hooks.commit_msg", str(msg_path)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                env={**os.environ, "NO_COLOR": "1"},
                check=False,
            )

    def test_commit_message_accepts_valid_manual_merge_and_revert(self) -> None:
        for message in (
            "[feat]: add source root hook",
            "Merge branch 'main'",
            "Revert \"[fix]: bad change\"",
        ):
            with self.subTest(message=message):
                result = self.run_commit_msg_hook(message)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_commit_message_rejects_empty_invalid_type_and_missing_format(self) -> None:
        for message in ("", "[oops]: wrong type", "feat: missing brackets"):
            with self.subTest(message=message):
                result = self.run_commit_msg_hook(message)
                self.assertNotEqual(result.returncode, 0)

    def test_prepare_commit_template_types_match_validator(self) -> None:
        template = (REPO_ROOT / "hooks" / "prepare-commit-msg").read_text(encoding="utf-8")

        for commit_type in (
            "feat",
            "fix",
            "refactor",
            "style",
            "docs",
            "test",
            "chore",
            "perf",
            "ci",
            "build",
            "enhancement",
        ):
            self.assertIn(commit_type, commit_msg.VALID_TYPES)
            self.assertIn(f"[{commit_type}]", template)


if __name__ == "__main__":
    unittest.main()
