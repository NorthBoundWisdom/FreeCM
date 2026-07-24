from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hooks.commit_msg as commit_msg
import hooks.install as install_hook
import hooks.pre_commit as pre_commit
from tests.git_test_helpers import run_git_fixture

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
        entries = [
            pre_commit.StagedEntry(
                path=Path("small.bin"),
                mode="100644",
                object_id="a" * 40,
                size_bytes=pre_commit.MAX_FILE_SIZE_BYTES,
            ),
            pre_commit.StagedEntry(
                path=Path("large.bin"),
                mode="100644",
                object_id="b" * 40,
                size_bytes=pre_commit.MAX_FILE_SIZE_BYTES + 1,
            ),
            pre_commit.StagedEntry(
                path=Path("link.bin"),
                mode="120000",
                object_id="c" * 40,
                size_bytes=pre_commit.MAX_FILE_SIZE_BYTES + 1,
            ),
        ]

        large_files = pre_commit.find_large_files(entries)

        self.assertEqual([item.path for item in large_files], [Path("large.bin")])

    def test_qmlformat_is_optional_for_staged_formatting(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            qml_path = Path("SourceCode/Gui/View.qml")
            object_id = "a" * 40
            entry = pre_commit.StagedEntry(
                path=qml_path,
                mode="100644",
                object_id=object_id,
                size_bytes=15,
            )

            with (
                mock.patch.object(pre_commit, "get_git_config", return_value=None),
                mock.patch.object(
                    pre_commit,
                    "read_staged_binary_overrides",
                    return_value={},
                ),
                mock.patch.object(pre_commit, "format_blob") as format_mock,
            ):
                prepared = pre_commit.prepare_staged_blobs(
                    repo_root,
                    [entry],
                    {object_id: b"import QtQuick\n"},
                )

            self.assertIsNotNone(prepared)
            assert prepared is not None
            self.assertIsNone(prepared[0].transformed)
            format_mock.assert_not_called()

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

            self.assertTrue(
                install_hook.install_hook(REPO_ROOT / "hooks", hooks_dir, "commit_msg.py")
            )
            self.assertTrue((hooks_dir / "commit_msg.py").is_file())


class HookInstallerTests(unittest.TestCase):
    def create_repo(self, tempdir: str) -> Path:
        repo_root = Path(tempdir) / "repo"
        repo_root.mkdir()
        run_git_fixture(repo_root, "init")
        run_git_fixture(repo_root, "config", "user.name", "Codex")
        run_git_fixture(repo_root, "config", "user.email", "codex@example.com")
        return repo_root

    def create_hook_sources(self, root: Path) -> Path:
        script_dir = root / "sources"
        script_dir.mkdir()
        (script_dir / "pre-commit").write_text("#!/bin/sh\necho freecm\n", encoding="utf-8")
        (script_dir / "commit-msg").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        return script_dir

    def temporary_install_files(self, hooks_dir: Path) -> list[Path]:
        return [path for path in hooks_dir.iterdir() if ".freecm-install-" in path.name]

    def test_effective_hooks_dir_supports_custom_paths_and_linked_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            run_git_fixture(repo_root, "config", "core.hooksPath", ".custom-hooks")

            self.assertEqual(
                (repo_root / ".custom-hooks").resolve(),
                install_hook.get_hooks_dir(repo_root),
            )

            (repo_root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            run_git_fixture(repo_root, "add", "tracked.txt")
            run_git_fixture(repo_root, "commit", "-m", "fixture")
            linked_root = Path(tempdir) / "linked"
            run_git_fixture(
                repo_root,
                "worktree",
                "add",
                "-b",
                "linked-hook-installer",
                str(linked_root),
            )
            self.assertEqual(
                (linked_root / ".custom-hooks").resolve(),
                install_hook.get_hooks_dir(linked_root),
            )

            absolute_hooks = Path(tempdir) / "shared-hooks"
            run_git_fixture(repo_root, "config", "core.hooksPath", str(absolute_hooks))
            self.assertEqual(absolute_hooks.resolve(), install_hook.get_hooks_dir(linked_root))

    def test_existing_hook_requires_explicit_policy_without_partial_install(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            script_dir = self.create_hook_sources(root)
            hooks_dir = root / "hooks"
            hooks_dir.mkdir()
            pre_commit_path = hooks_dir / "pre-commit"
            pre_commit_path.write_text("#!/bin/sh\necho existing\n", encoding="utf-8")

            with self.assertRaisesRegex(install_hook.HookInstallError, "--existing backup"):
                install_hook.install_hooks(
                    script_dir,
                    hooks_dir,
                    ("pre-commit", "commit-msg"),
                )

            self.assertEqual(
                pre_commit_path.read_text(encoding="utf-8"),
                "#!/bin/sh\necho existing\n",
            )
            self.assertFalse((hooks_dir / "commit-msg").exists())
            self.assertEqual(self.temporary_install_files(hooks_dir), [])

    def test_backup_and_replace_policies_are_explicit_and_idempotent(self) -> None:
        for policy in ("backup", "replace"):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                script_dir = self.create_hook_sources(root)
                hooks_dir = root / "hooks"
                hooks_dir.mkdir()
                target = hooks_dir / "pre-commit"
                target.write_text("existing\n", encoding="utf-8")

                installed = install_hook.install_hooks(
                    script_dir,
                    hooks_dir,
                    ("pre-commit",),
                    existing_policy=policy,
                )

                self.assertEqual(installed, ("pre-commit",))
                self.assertEqual(target.read_bytes(), (script_dir / "pre-commit").read_bytes())
                backups = list(hooks_dir.glob("pre-commit.freecm-backup-*"))
                if policy == "backup":
                    self.assertEqual(len(backups), 1)
                    self.assertEqual(backups[0].read_text(encoding="utf-8"), "existing\n")
                else:
                    self.assertEqual(backups, [])

                install_hook.install_hooks(script_dir, hooks_dir, ("pre-commit",))
                self.assertEqual(
                    sorted(path.name for path in hooks_dir.iterdir()),
                    sorted(["pre-commit", *(path.name for path in backups)]),
                )

    def test_matching_non_executable_hook_is_repaired_without_override_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            script_dir = self.create_hook_sources(root)
            hooks_dir = root / "hooks"
            hooks_dir.mkdir()
            target = hooks_dir / "pre-commit"
            target.write_bytes((script_dir / "pre-commit").read_bytes())
            target.chmod(0o644)

            install_hook.install_hooks(script_dir, hooks_dir, ("pre-commit",))

            self.assertTrue(os.access(target, os.X_OK))
            self.assertEqual(list(hooks_dir.glob("*freecm-backup*")), [])

    def test_copy_failure_changes_no_hook_and_cleans_staging_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            script_dir = self.create_hook_sources(root)
            hooks_dir = root / "hooks"
            hooks_dir.mkdir()
            (hooks_dir / "pre-commit").write_text("old pre\n", encoding="utf-8")
            (hooks_dir / "commit-msg").write_text("old commit\n", encoding="utf-8")
            original_copy = install_hook.shutil.copy2
            copy_count = 0

            def fail_second_copy(source: Path, target: Path) -> Path:
                nonlocal copy_count
                copy_count += 1
                if copy_count == 2:
                    raise OSError("copy failed")
                return Path(original_copy(source, target))

            with (
                mock.patch.object(install_hook.shutil, "copy2", side_effect=fail_second_copy),
                self.assertRaisesRegex(install_hook.HookInstallError, "Failed to stage"),
            ):
                install_hook.install_hooks(
                    script_dir,
                    hooks_dir,
                    ("pre-commit", "commit-msg"),
                    existing_policy="replace",
                )

            self.assertEqual((hooks_dir / "pre-commit").read_text(encoding="utf-8"), "old pre\n")
            self.assertEqual((hooks_dir / "commit-msg").read_text(encoding="utf-8"), "old commit\n")
            self.assertEqual(self.temporary_install_files(hooks_dir), [])

    def test_publication_failure_restores_all_existing_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            script_dir = self.create_hook_sources(root)
            hooks_dir = root / "hooks"
            hooks_dir.mkdir()
            pre_commit_path = hooks_dir / "pre-commit"
            commit_msg_path = hooks_dir / "commit-msg"
            pre_commit_path.write_text("old pre\n", encoding="utf-8")
            commit_msg_path.write_text("old commit\n", encoding="utf-8")
            original_replace = Path.replace

            def fail_second_publication(source: str | Path, target: str | Path) -> None:
                source_path = Path(source)
                target_path = Path(target)
                if (
                    ".commit-msg.freecm-install-" in source_path.name
                    and target_path.resolve() == commit_msg_path.resolve()
                ):
                    raise OSError("replace failed")
                original_replace(source_path, target_path)

            with (
                mock.patch.object(
                    Path,
                    "replace",
                    autospec=True,
                    side_effect=fail_second_publication,
                ),
                self.assertRaisesRegex(
                    install_hook.HookInstallError, "previous hooks were restored"
                ),
            ):
                install_hook.install_hooks(
                    script_dir,
                    hooks_dir,
                    ("pre-commit", "commit-msg"),
                    existing_policy="replace",
                )

            self.assertEqual(pre_commit_path.read_text(encoding="utf-8"), "old pre\n")
            self.assertEqual(commit_msg_path.read_text(encoding="utf-8"), "old commit\n")
            self.assertEqual(
                sorted(path.name for path in hooks_dir.iterdir()), ["commit-msg", "pre-commit"]
            )


class PreCommitIndexIntegrationTests(unittest.TestCase):
    def create_repo(self, tempdir: str) -> Path:
        repo_root = Path(tempdir) / "repo"
        repo_root.mkdir()
        run_git_fixture(repo_root, "init")
        run_git_fixture(repo_root, "config", "user.name", "Codex")
        run_git_fixture(repo_root, "config", "user.email", "codex@example.com")
        return repo_root

    def commit_all(self, repo_root: Path, message: str = "fixture") -> None:
        run_git_fixture(repo_root, "add", ".")
        run_git_fixture(repo_root, "commit", "-m", message)

    def index_blob(self, repo_root: Path, path: Path) -> bytes:
        result = subprocess.run(
            ["git", "show", f":{path.as_posix()}"],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
        return result.stdout

    def index_record(self, repo_root: Path, path: Path) -> bytes:
        result = subprocess.run(
            ["git", "ls-files", "--stage", "-z", "--", str(path)],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
        return result.stdout

    def test_fully_staged_normalization_updates_index_and_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            path = Path("notes.txt")
            original = b"first \r\nsecond\t \r\n"
            (repo_root / path).write_bytes(original)
            run_git_fixture(repo_root, "add", "--", str(path))

            self.assertEqual(pre_commit.run_pre_commit(repo_root), 0)
            self.assertEqual(self.index_blob(repo_root, path), b"first\nsecond\n")
            self.assertEqual((repo_root / path).read_bytes(), b"first\nsecond\n")
            run_git_fixture(repo_root, "commit", "-m", "normalized")
            self.assertEqual(run_git_fixture(repo_root, "status", "--short"), "")

    def test_partial_staging_updates_only_the_index_blob(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            path = Path("notes.txt")
            (repo_root / path).write_bytes(b"first\nsecond\n")
            self.commit_all(repo_root)

            staged = b"first \r\nsecond\n"
            worktree = staged + b"unstaged \r\n"
            (repo_root / path).write_bytes(staged)
            run_git_fixture(repo_root, "add", "--", str(path))
            (repo_root / path).write_bytes(worktree)

            self.assertEqual(pre_commit.run_pre_commit(repo_root), 0)
            self.assertEqual(self.index_blob(repo_root, path), b"first\nsecond\n")
            self.assertEqual((repo_root / path).read_bytes(), worktree)

    def test_partial_staging_works_from_a_linked_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            path = Path("notes.txt")
            (repo_root / path).write_text("base\n", encoding="utf-8")
            self.commit_all(repo_root)
            linked_root = Path(tempdir) / "linked"
            run_git_fixture(
                repo_root,
                "worktree",
                "add",
                "-b",
                "linked-hook-test",
                str(linked_root),
            )

            staged = b"linked \r\n"
            worktree = staged + b"unstaged\n"
            (linked_root / path).write_bytes(staged)
            run_git_fixture(linked_root, "add", "--", str(path))
            (linked_root / path).write_bytes(worktree)

            self.assertEqual(pre_commit.run_pre_commit(linked_root), 0)
            self.assertEqual(self.index_blob(linked_root, path), b"linked\n")
            self.assertEqual((linked_root / path).read_bytes(), worktree)

    def test_fully_staged_file_is_updated_from_a_linked_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            path = Path("notes.txt")
            (repo_root / path).write_text("base\n", encoding="utf-8")
            self.commit_all(repo_root)
            linked_root = Path(tempdir) / "linked"
            run_git_fixture(
                repo_root,
                "worktree",
                "add",
                "-b",
                "linked-hook-sync-test",
                str(linked_root),
            )

            (linked_root / path).write_bytes(b"linked \r\n")
            run_git_fixture(linked_root, "add", "--", str(path))

            self.assertEqual(pre_commit.run_pre_commit(linked_root), 0)
            self.assertEqual(self.index_blob(linked_root, path), b"linked\n")
            self.assertEqual((linked_root / path).read_bytes(), b"linked\n")
            run_git_fixture(linked_root, "commit", "-m", "normalized")
            self.assertEqual(run_git_fixture(linked_root, "status", "--short"), "")

    def test_index_only_new_file_is_normalized_without_recreating_worktree_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            path = Path("index-only.txt")
            (repo_root / path).write_bytes(b"staged \r\n")
            run_git_fixture(repo_root, "add", "--", str(path))
            (repo_root / path).unlink()

            self.assertEqual(pre_commit.run_pre_commit(repo_root), 0)
            self.assertEqual(self.index_blob(repo_root, path), b"staged\n")
            self.assertFalse((repo_root / path).exists())

    def test_deleted_and_renamed_files_are_handled_from_the_index(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            deleted = Path("deleted.txt")
            old_path = Path("old.txt")
            new_path = Path("renamed.txt")
            (repo_root / deleted).write_text("delete me\n", encoding="utf-8")
            (repo_root / old_path).write_text("rename me\n", encoding="utf-8")
            self.commit_all(repo_root)

            run_git_fixture(repo_root, "rm", "--", str(deleted))
            run_git_fixture(repo_root, "mv", str(old_path), str(new_path))
            (repo_root / new_path).write_bytes(b"rename me \r\n")
            run_git_fixture(repo_root, "add", "--", str(new_path))
            worktree = b"rename me \r\nunstaged\n"
            (repo_root / new_path).write_bytes(worktree)

            self.assertEqual(pre_commit.run_pre_commit(repo_root), 0)
            self.assertEqual(self.index_blob(repo_root, new_path), b"rename me\n")
            self.assertEqual((repo_root / new_path).read_bytes(), worktree)
            self.assertEqual(self.index_record(repo_root, deleted), b"")

    def test_symlink_binary_and_gitlink_entries_are_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            tracked = Path("tracked.txt")
            (repo_root / tracked).write_text("tracked\n", encoding="utf-8")
            self.commit_all(repo_root)

            binary_path = Path("SourceCode/binary.cpp")
            (repo_root / binary_path).parent.mkdir()
            binary_content = b"raw \r\n\0payload \r\n"
            (repo_root / binary_path).write_bytes(binary_content)
            run_git_fixture(repo_root, "add", "--", str(binary_path))

            link_path = Path("SourceCode/link.cpp")
            try:
                (repo_root / link_path).symlink_to("binary.cpp")
            except OSError as exc:
                if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows symlink privilege is not available")
                raise
            run_git_fixture(repo_root, "add", "--", str(link_path))

            commit_oid = run_git_fixture(repo_root, "rev-parse", "HEAD")
            run_git_fixture(
                repo_root,
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{commit_oid},vendor/dependency",
            )
            binary_record = self.index_record(repo_root, binary_path)
            link_record = self.index_record(repo_root, link_path)
            gitlink_record = self.index_record(repo_root, Path("vendor/dependency"))

            self.assertEqual(pre_commit.run_pre_commit(repo_root), 0)
            self.assertEqual(self.index_blob(repo_root, binary_path), binary_content)
            self.assertEqual(self.index_record(repo_root, binary_path), binary_record)
            self.assertEqual(self.index_record(repo_root, link_path), link_record)
            self.assertEqual(
                self.index_record(repo_root, Path("vendor/dependency")),
                gitlink_record,
            )

    def test_staged_attributes_control_binary_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            attributes = Path(".gitattributes")
            binary_path = Path("payload.dat")
            text_path = Path("payload.custom")
            binary_content = b"binary-like \r\nbytes \r\n"
            text_content = b"text \r\nwith-nul\0 \r\n"
            (repo_root / attributes).write_text(
                "*.dat -diff -text\n*.custom text\n",
                encoding="utf-8",
            )
            (repo_root / binary_path).write_bytes(binary_content)
            (repo_root / text_path).write_bytes(text_content)
            run_git_fixture(repo_root, "add", ".")
            (repo_root / attributes).write_text(
                "*.dat text\n*.custom -text\n",
                encoding="utf-8",
            )

            self.assertEqual(pre_commit.run_pre_commit(repo_root), 0)
            self.assertEqual(self.index_blob(repo_root, binary_path), binary_content)
            self.assertEqual(self.index_blob(repo_root, text_path), b"text\nwith-nul\0\n")

    def test_large_file_check_uses_staged_size_in_both_directions(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            large_path = Path("large.bin")
            large_content = b"x" * (pre_commit.MAX_FILE_SIZE_BYTES + 1)
            (repo_root / large_path).write_bytes(large_content)
            run_git_fixture(repo_root, "add", "--", str(large_path))
            (repo_root / large_path).write_bytes(b"small worktree\n")

            self.assertEqual(pre_commit.run_pre_commit(repo_root), 1)
            size = run_git_fixture(repo_root, "cat-file", "-s", f":{large_path}")
            self.assertEqual(int(size), len(large_content))

            run_git_fixture(repo_root, "reset")
            small_path = Path("small.txt")
            (repo_root / small_path).write_bytes(b"small \r\n")
            run_git_fixture(repo_root, "add", "--", str(small_path))
            (repo_root / small_path).write_bytes(large_content)

            self.assertEqual(pre_commit.run_pre_commit(repo_root), 0)
            self.assertEqual(self.index_blob(repo_root, small_path), b"small\n")
            self.assertEqual((repo_root / small_path).read_bytes(), large_content)

    @unittest.skipIf(os.name == "nt", "Git executable mode is platform-dependent on Windows")
    def test_normalization_preserves_executable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            path = Path("script.sh")
            (repo_root / path).write_bytes(b"#!/bin/sh \r\n")
            (repo_root / path).chmod(0o755)
            run_git_fixture(repo_root, "add", "--", str(path))

            self.assertEqual(pre_commit.run_pre_commit(repo_root), 0)
            self.assertEqual(self.index_blob(repo_root, path), b"#!/bin/sh\n")
            self.assertTrue(self.index_record(repo_root, path).startswith(b"100755 "))

    def test_formatter_failure_leaves_every_index_entry_and_worktree_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            paths = [Path("SourceCode/a.cpp"), Path("SourceCode/b.cpp")]
            for path in paths:
                (repo_root / path).parent.mkdir(parents=True, exist_ok=True)
                (repo_root / path).write_bytes(f"int {path.stem}; \r\n".encode())
            run_git_fixture(repo_root, "add", ".")
            original_blobs = [self.index_blob(repo_root, path) for path in paths]
            original_worktrees = [(repo_root / path).read_bytes() for path in paths]

            with (
                mock.patch.object(pre_commit, "resolve_tool_cmd", return_value="formatter"),
                mock.patch.object(
                    pre_commit,
                    "format_blob",
                    side_effect=[b"int a;\n", None],
                ),
            ):
                self.assertEqual(pre_commit.run_pre_commit(repo_root), 1)

            self.assertEqual(
                [self.index_blob(repo_root, path) for path in paths],
                original_blobs,
            )
            self.assertEqual(
                [(repo_root / path).read_bytes() for path in paths],
                original_worktrees,
            )

    def test_worktree_write_failure_restores_index_and_updated_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            paths = [Path("a.txt"), Path("b.txt")]
            original_worktrees = [b"a \r\n", b"b \r\n"]
            self.assertEqual(len(paths), len(original_worktrees))
            for path, content in zip(paths, original_worktrees, strict=True):
                (repo_root / path).write_bytes(content)
                run_git_fixture(repo_root, "add", "--", str(path))
            original_blobs = [self.index_blob(repo_root, path) for path in paths]
            write_worktree_blob = pre_commit._write_worktree_blob

            def fail_second_update(path: Path, content: bytes) -> None:
                if path.name == "b.txt" and content == b"b\n":
                    raise OSError("simulated write failure")
                write_worktree_blob(path, content)

            with mock.patch.object(
                pre_commit,
                "_write_worktree_blob",
                side_effect=fail_second_update,
            ):
                with self.assertRaisesRegex(RuntimeError, "Worktree formatting update failed"):
                    pre_commit.run_pre_commit(repo_root)

            self.assertEqual(
                [self.index_blob(repo_root, path) for path in paths],
                original_blobs,
            )
            self.assertEqual(
                [(repo_root / path).read_bytes() for path in paths],
                original_worktrees,
            )

    def test_missing_formatter_and_hash_failure_leave_index_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            cpp_path = Path("SourceCode/main.cpp")
            (repo_root / cpp_path).parent.mkdir(parents=True)
            (repo_root / cpp_path).write_bytes(b"int main; \r\n")
            run_git_fixture(repo_root, "add", ".")
            cpp_before = self.index_record(repo_root, cpp_path)

            with mock.patch.object(pre_commit, "resolve_tool_cmd", return_value=None):
                self.assertEqual(pre_commit.run_pre_commit(repo_root), 1)
            self.assertEqual(self.index_record(repo_root, cpp_path), cpp_before)

            run_git_fixture(repo_root, "reset")
            text_path = Path("notes.txt")
            (repo_root / text_path).write_bytes(b"notes \r\n")
            run_git_fixture(repo_root, "add", "--", str(text_path))
            text_before = self.index_record(repo_root, text_path)
            hash_failure = subprocess.CalledProcessError(1, ["git", "hash-object"])
            with (
                mock.patch.object(
                    pre_commit,
                    "hash_prepared_blobs",
                    side_effect=hash_failure,
                ),
                mock.patch.object(pre_commit, "apply_index_updates") as update_mock,
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    pre_commit.run_pre_commit(repo_root)
            update_mock.assert_not_called()
            self.assertEqual(self.index_record(repo_root, text_path), text_before)

    def test_unmerged_index_entries_are_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            path = Path("conflict.txt")
            (repo_root / path).write_text("base\n", encoding="utf-8")
            self.commit_all(repo_root)

            base_oid = run_git_fixture(repo_root, "rev-parse", f"HEAD:{path}")

            def hash_blob(content: bytes) -> str:
                result = subprocess.run(
                    ["git", "hash-object", "-w", "--stdin"],
                    cwd=repo_root,
                    input=content,
                    capture_output=True,
                    check=True,
                )
                return result.stdout.decode("ascii").strip()

            ours_oid = hash_blob(b"ours \r\n")
            theirs_oid = hash_blob(b"theirs \r\n")
            payload = (
                f"0 {'0' * len(base_oid)}\t{path}\n"
                f"100644 {base_oid} 1\t{path}\n"
                f"100644 {ours_oid} 2\t{path}\n"
                f"100644 {theirs_oid} 3\t{path}\n"
            )
            subprocess.run(
                ["git", "update-index", "--index-info"],
                cwd=repo_root,
                input=payload,
                text=True,
                capture_output=True,
                check=True,
            )
            unmerged_before = subprocess.run(
                ["git", "ls-files", "--unmerged", "-z"],
                cwd=repo_root,
                capture_output=True,
                check=True,
            ).stdout

            self.assertEqual(pre_commit.run_pre_commit(repo_root), 0)
            unmerged_after = subprocess.run(
                ["git", "ls-files", "--unmerged", "-z"],
                cwd=repo_root,
                capture_output=True,
                check=True,
            ).stdout
            self.assertEqual(unmerged_after, unmerged_before)

    def test_formatter_output_is_normalized_and_size_checked_before_index_update(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            path = Path("SourceCode/main.cpp")
            (repo_root / path).parent.mkdir(parents=True)
            (repo_root / path).write_bytes(b"int main( ){return 0;}\n")
            run_git_fixture(repo_root, "add", ".")

            with (
                mock.patch.object(pre_commit, "resolve_tool_cmd", return_value="formatter"),
                mock.patch.object(
                    pre_commit,
                    "format_blob",
                    return_value=b"int main() {} \r\n",
                ),
            ):
                self.assertEqual(pre_commit.run_pre_commit(repo_root), 0)
            self.assertEqual(self.index_blob(repo_root, path), b"int main() {}\n")
            self.assertEqual((repo_root / path).read_bytes(), b"int main() {}\n")

            oversized = b"x" * (pre_commit.MAX_FILE_SIZE_BYTES + 1)
            (repo_root / path).write_bytes(b"int changed;\n")
            run_git_fixture(repo_root, "add", ".")
            staged_before = self.index_blob(repo_root, path)
            with (
                mock.patch.object(pre_commit, "resolve_tool_cmd", return_value="formatter"),
                mock.patch.object(pre_commit, "format_blob", return_value=oversized),
            ):
                self.assertEqual(pre_commit.run_pre_commit(repo_root), 1)
            self.assertEqual(self.index_blob(repo_root, path), staged_before)

    def test_space_unicode_dash_and_newline_paths_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            paths = [
                Path("space name.txt"),
                Path("\u6d4b\u8bd5.txt"),
                Path("-leading.txt"),
            ]
            if os.name != "nt":
                paths.extend([Path("tab\tname.txt"), Path("line\nname.txt")])
            for path in paths:
                (repo_root / path).write_bytes(b"value \r\n")
                run_git_fixture(repo_root, "add", "--", str(path))

            self.assertEqual(pre_commit.run_pre_commit(repo_root), 0)
            for path in paths:
                self.assertEqual(self.index_blob(repo_root, path), b"value\n")

    def test_unicode_path_diagnostics_escape_for_legacy_console_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            path = Path("\u6d4b\u8bd5.txt")
            (repo_root / path).write_bytes(b"value \r\n")
            run_git_fixture(repo_root, "add", "--", str(path))
            output_bytes = io.BytesIO()
            output = io.TextIOWrapper(output_bytes, encoding="cp1252", errors="strict")

            with mock.patch.object(pre_commit.sys, "stdout", output):
                self.assertEqual(pre_commit.run_pre_commit(repo_root), 0)
                output.flush()

            diagnostic = output_bytes.getvalue().decode("cp1252")
            self.assertIn(r"\u6d4b\u8bd5.txt", diagnostic)
            self.assertEqual(self.index_blob(repo_root, path), b"value\n")

    def test_qml_formatter_uses_git_metadata_temp_and_copies_path_configs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = self.create_repo(tempdir)
            git_dir = Path(run_git_fixture(repo_root, "rev-parse", "--absolute-git-dir"))
            qml_path = Path("SourceCode/Gui/View.qml")
            (repo_root / ".qmlformat.ini").write_text("[General]\n", encoding="utf-8")
            (repo_root / qml_path).parent.mkdir(parents=True)
            (repo_root / qml_path).parent.joinpath(".qmlformat.ini").write_text(
                "[General]\nUseTabs=true\n",
                encoding="utf-8",
            )
            temp_paths: list[Path] = []

            def fake_formatter(
                command: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess[bytes]:
                temp_path = Path(command[-1])
                temp_paths.append(temp_path)
                self.assertTrue(temp_path.is_relative_to(git_dir))
                self.assertTrue((temp_path.parents[2] / ".qmlformat.ini").is_file())
                self.assertTrue((temp_path.parent / ".qmlformat.ini").is_file())
                temp_path.write_bytes(b"formatted\r\n")
                return subprocess.CompletedProcess(command, 0, b"", b"")

            git_dir_result = subprocess.CompletedProcess(
                ["git", "rev-parse"],
                0,
                f"{git_dir}\n",
                "",
            )
            with (
                mock.patch.object(pre_commit, "run_git", return_value=git_dir_result),
                mock.patch.object(pre_commit.subprocess, "run", side_effect=fake_formatter),
            ):
                result = pre_commit.format_blob(
                    repo_root,
                    qml_path,
                    b"source\n",
                    "qmlformat",
                    qml=True,
                )

            self.assertEqual(result, b"formatted\r\n")
            self.assertTrue(temp_paths)
            self.assertFalse(temp_paths[0].parents[2].exists())

    def test_clang_formatter_reads_staged_bytes_from_stdin_with_assumed_path(self) -> None:
        repo_root = Path("/workspace/repo")
        path = Path("SourceCode/main.cpp")
        completed = subprocess.CompletedProcess(
            ["clang-format"],
            0,
            b"formatted\n",
            b"",
        )
        with mock.patch.object(
            pre_commit.subprocess,
            "run",
            return_value=completed,
        ) as run_mock:
            result = pre_commit.format_blob(
                repo_root,
                path,
                b"staged\n",
                "clang-format",
                qml=False,
            )

        self.assertEqual(result, b"formatted\n")
        self.assertEqual(
            run_mock.call_args.args[0],
            [
                "clang-format",
                "-style=file",
                f"-assume-filename={repo_root / path}",
            ],
        )
        self.assertEqual(run_mock.call_args.kwargs["input"], b"staged\n")


class PreCommitIndexTransactionTests(unittest.TestCase):
    def test_index_change_before_update_fails_without_writing(self) -> None:
        entry = pre_commit.StagedEntry(Path("a.txt"), "100644", "a" * 40, 1)
        update = pre_commit.IndexUpdate(entry, "b" * 40)
        with (
            mock.patch.object(
                pre_commit,
                "_read_stage_zero_entries",
                return_value={entry.git_path: (entry.mode, "c" * 40)},
            ),
            mock.patch.object(pre_commit, "_write_index_updates") as write_mock,
        ):
            with self.assertRaisesRegex(RuntimeError, "index changed"):
                pre_commit.apply_index_updates(Path("."), [update])
        write_mock.assert_not_called()

    def test_failed_index_update_restores_only_proposed_entries(self) -> None:
        entry = pre_commit.StagedEntry(Path("a.txt"), "100644", "a" * 40, 1)
        update = pre_commit.IndexUpdate(entry, "b" * 40)
        expected = {entry.git_path: (entry.mode, entry.object_id)}
        proposed = {entry.git_path: (entry.mode, update.object_id)}
        failure = subprocess.CalledProcessError(1, ["git", "update-index"])
        with (
            mock.patch.object(
                pre_commit,
                "_read_stage_zero_entries",
                side_effect=[expected, proposed],
            ),
            mock.patch.object(
                pre_commit,
                "_write_index_updates",
                side_effect=[failure, None],
            ) as write_mock,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                pre_commit.apply_index_updates(Path("."), [update])

        restored = write_mock.call_args_list[1].args[1]
        self.assertEqual(restored, [pre_commit.IndexUpdate(entry, entry.object_id)])


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
            'Revert "[fix]: bad change"',
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
