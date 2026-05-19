from __future__ import annotations

import datetime as dt
import importlib.resources
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.cleanup import collect_empty_dirs, remove_empty_dirs  # noqa: E402
from tools.file_lists import list_filenames  # noqa: E402
from tools.git_summary import collect_daily_stats  # noqa: E402
from tools.host_clang_format import collect_candidate_files, main as host_clang_format_main  # noqa: E402
from tools.json_codegen import (  # noqa: E402
    collect_json_keys,
    deduplicate_json_array,
)
from tools.remove_old_build import remove_old_build  # noqa: E402
from cpprepomgr.tools.ci_targets import selected_ci_targets  # noqa: E402
from cpprepomgr.tools.comments import simplify_brief_comments_in_file  # noqa: E402
from cpprepomgr.tools.file_lists import generate_qrc_entries  # noqa: E402
from cpprepomgr.tools.header_guards import header_guard_macro_for_path, update_header_guard_file  # noqa: E402
from cpprepomgr.tools.json_codegen import generate_cpp_string_key_header  # noqa: E402
from cpprepomgr.tools.markdown_catalog import (  # noqa: E402
    collect_markdown_catalog_docs,
    generate_cpp_catalog_entries,
    order_catalog_entries,
)


class RepoToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def git(self, cwd: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def test_list_filenames_filters_and_prefixes(self) -> None:
        (self.root / "a.cpp").write_text("", encoding="utf-8")
        (self.root / "b.qml").write_text("", encoding="utf-8")
        (self.root / "c.txt").write_text("", encoding="utf-8")

        names = list_filenames(self.root, suffixes=(".cpp", "qml"), prefix="Gui")

        self.assertEqual(names, ["Gui/a.cpp", "Gui/b.qml"])

    def test_generate_qrc_entries_groups_by_directory(self) -> None:
        (self.root / "Gui" / "A").mkdir(parents=True)
        (self.root / "Gui" / "B").mkdir(parents=True)
        (self.root / "Gui" / "A" / "View.qml").write_text("", encoding="utf-8")
        (self.root / "Gui" / "B" / "icon.svg").write_text("", encoding="utf-8")
        (self.root / "Gui" / "skip.txt").write_text("", encoding="utf-8")

        entries = generate_qrc_entries(self.root / "Gui", [".qml", ".svg"], base_path=self.root / "Gui")

        self.assertEqual(
            entries,
            [
                "    <file>A/View.qml</file>",
                "",
                "    <file>B/icon.svg</file>",
            ],
        )

    def test_collect_and_remove_empty_dirs(self) -> None:
        keep = self.root / "keep"
        empty = self.root / "a" / "b"
        git_dir = self.root / ".git" / "objects"
        keep.mkdir()
        (keep / "file.txt").write_text("x", encoding="utf-8")
        empty.mkdir(parents=True)
        git_dir.mkdir(parents=True)

        resolved_root = self.root.resolve()
        candidates = [
            path.relative_to(resolved_root).as_posix()
            for path in collect_empty_dirs(self.root)
        ]

        self.assertEqual(candidates, ["a/b", "a"])
        removed = remove_empty_dirs(self.root)
        self.assertEqual(
            [path.relative_to(resolved_root).as_posix() for path in removed],
            ["a/b", "a"],
        )
        self.assertTrue(git_dir.exists())

    def test_remove_old_build_preserves_dependency_roots(self) -> None:
        build = self.root / "build"
        preserved_seed = build / "dependency_seed_repos"
        preserved_roots = build / "dependency_source_roots"
        stale_build = build / "generated"
        for path in (preserved_seed, preserved_roots, stale_build):
            path.mkdir(parents=True)
            (path / "stamp.txt").write_text("x", encoding="utf-8")
        for path in (self.root / "DerivedData", self.root / ".build", self.root / ".swiftpm"):
            path.mkdir()
            (path / "stamp.txt").write_text("x", encoding="utf-8")

        result = remove_old_build(repo_root=self.root)

        self.assertEqual(result.removed_count, 4)
        self.assertEqual(result.preserved_count, 2)
        self.assertTrue(preserved_seed.exists())
        self.assertTrue(preserved_roots.exists())
        self.assertFalse(stale_build.exists())
        self.assertFalse((self.root / "DerivedData").exists())
        self.assertFalse((self.root / ".build").exists())
        self.assertFalse((self.root / ".swiftpm").exists())

    def test_remove_old_build_requires_explicit_xcodeproj_removal(self) -> None:
        project = self.root / "Demo.xcodeproj"
        project.mkdir()
        (project / "project.pbxproj").write_text("// generated\n", encoding="utf-8")

        remove_old_build(repo_root=self.root)
        self.assertTrue(project.exists())

        result = remove_old_build(repo_root=self.root, remove_root_paths=("Demo.xcodeproj",))
        self.assertEqual(result.removed_count, 1)
        self.assertFalse(project.exists())

    def test_simplify_brief_comment(self) -> None:
        header = self.root / "Widget.h"
        header.write_text("/**\n * @brief Hello world\n */\nclass Widget {};\n", encoding="utf-8")

        self.assertTrue(simplify_brief_comments_in_file(header))
        self.assertEqual(header.read_text(encoding="utf-8"), "/** @brief Hello world */\nclass Widget {};\n")

    def test_update_header_guard(self) -> None:
        header = self.root / "Gui" / "MainWidget.h"
        header.parent.mkdir()
        header.write_text("class MainWidget {};\n", encoding="utf-8")

        self.assertEqual(header_guard_macro_for_path(header, root=self.root), "GUI_MAIN_WIDGET_H")
        update = update_header_guard_file(header, root=self.root)

        self.assertTrue(update.changed)
        self.assertIn("#ifndef GUI_MAIN_WIDGET_H", header.read_text(encoding="utf-8"))

    def test_user_callable_python_scripts_document_usage_at_top(self) -> None:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        console_script_paths = {
            REPO_ROOT / (module.replace(".", "/") + ".py")
            for module in re.findall(r'=\s*"([A-Za-z0-9_.]+):[A-Za-z0-9_]+"', pyproject)
        }
        user_callable_paths: set[Path] = set(console_script_paths)
        user_callable_paths.update(
            path
            for path in (REPO_ROOT / "tools").glob("*.py")
            if path.name != "__init__.py"
        )
        user_callable_paths.update(
            path
            for path in (REPO_ROOT / "cpprepomgr" / "tools").glob("*.py")
            if path.name != "__init__.py"
        )
        for path in REPO_ROOT.rglob("*.py"):
            relative = path.relative_to(REPO_ROOT)
            if relative.parts[0] == "tests":
                continue
            if relative.parts[:2] == ("vscode-extension", ".vscode-test"):
                continue
            if relative.parts[0] == "hooks" and path.name != "install.py":
                continue
            if path.name == "__init__.py":
                continue
            content = path.read_text(encoding="utf-8")
            if content.startswith("#!") or 'if __name__ == "__main__"' in content:
                user_callable_paths.add(path)

        missing = []
        for path in sorted(user_callable_paths):
            leading_lines = path.read_text(encoding="utf-8").splitlines()[:8]
            if not any(line.strip() == "# Usage:" for line in leading_lines):
                missing.append(path.relative_to(REPO_ROOT).as_posix())

        self.assertEqual(missing, [])

    def test_hook_python_scripts_are_marked_internal_at_top(self) -> None:
        missing = []
        for path in sorted((REPO_ROOT / "hooks").glob("*.py")):
            if path.name == "install.py":
                continue
            leading_lines = path.read_text(encoding="utf-8").splitlines()[:8]
            if not any(line.strip() == "# Internal:" for line in leading_lines):
                missing.append(path.relative_to(REPO_ROOT).as_posix())

        self.assertEqual(missing, [])

    def test_collect_daily_stats_filters_source_suffixes(self) -> None:
        repo = self.root / "repo"
        repo.mkdir()
        self.git(repo, "init")
        self.git(repo, "config", "user.name", "Codex")
        self.git(repo, "config", "user.email", "codex@example.com")
        (repo / "src").mkdir()
        (repo / "src" / "a.cpp").write_text("int a;\n", encoding="utf-8")
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-m", "init")

        rows = collect_daily_stats(repo, days=1, scope_path=".", today=dt.date.today())

        self.assertEqual(rows[0][1].commits, 1)
        self.assertEqual(rows[0][1].files, 1)

    def test_cpprepomgr_cmake_pkg_config_debug_script_is_packaged(self) -> None:
        script = importlib.resources.files("cpprepomgr").joinpath(
            "cmake/debug_pkg_config.cmake"
        )

        self.assertTrue(script.is_file())
        content = script.read_text(encoding="utf-8")
        self.assertIn("REPOCONFIGSMGR_PKG_CONFIG_MODULES", content)

    def test_generate_cpp_string_key_header_from_json_keys(self) -> None:
        keys = collect_json_keys({"box_3d": 1, "nested": {"viewer-msaa": True}})
        header = generate_cpp_string_key_header(
            keys,
            namespace="demo::Keys",
            header_guard="DEMO_KEYS_H",
            special_names={"box_3d": "kBox3d"},
        )

        self.assertIn("namespace demo", header)
        self.assertIn("namespace Keys", header)
        self.assertIn('const std::string kBox3d = "box_3d";', header)
        self.assertIn('const std::string kViewerMsaa = "viewer-msaa";', header)

    def test_deduplicate_json_array_by_nested_key(self) -> None:
        result = deduplicate_json_array(
            {
                "items": [
                    {"command": {"name": "open"}},
                    {"command": {"name": "save"}},
                    {"command": {"name": "open"}},
                ]
            },
            array_key="items",
            dedup_key="command",
        )

        self.assertEqual(result.original_count, 3)
        self.assertEqual(result.deduplicated_count, 2)
        self.assertEqual(result.removed_indices, (2,))

    def test_markdown_catalog_generates_cpp_entries(self) -> None:
        docs_root = self.root / "docs"
        docs_root.mkdir()
        (docs_root / "CmdOpenDoc.md").write_text(
            "## CmdOpen(Open file)\n\n- CmdId: `Open`\n\nBody",
            encoding="utf-8",
        )

        docs = collect_markdown_catalog_docs(docs_root)
        entries = order_catalog_entries(docs, ["Open"])
        content = generate_cpp_catalog_entries(entries)

        self.assertIn('{"Open"', content)
        self.assertIn("Open file", content)
        self.assertIn("Body", content)

    def test_selected_ci_targets_uses_quick_targets_for_merge_requests(self) -> None:
        self.assertEqual(
            selected_ci_targets(
                regular_targets=["AppUnitTest", "AppRegression"],
                quick_targets=["AppUnitTest", "AppRegressionQuick"],
                pipeline_source="merge_request_event",
            ),
            ("AppUnitTest", "AppRegressionQuick"),
        )
        self.assertEqual(
            selected_ci_targets(
                regular_targets=["AppUnitTest", "AppRegression"],
                quick_targets=["AppRegressionQuick"],
                pipeline_source="schedule",
            ),
            ("AppUnitTest", "AppRegression"),
        )

    def test_host_clang_format_collects_cpp_files_with_excludes(self) -> None:
        source = self.root / "Source"
        source.mkdir()
        keep = source / "main.cpp"
        header = source / "Widget.hpp"
        skipped_suffix = source / "notes.txt"
        skipped_dir = source / "thirdparty" / "lib.cpp"
        skipped_build = source / "build" / "generated.cpp"
        for path in (keep, header, skipped_suffix, skipped_dir, skipped_build):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

        files = collect_candidate_files(
            (source,),
            suffixes=frozenset({".cpp", ".hpp"}),
            excluded_dirs=(source / "thirdparty",),
            excluded_dir_names=frozenset({"build"}),
        )

        self.assertEqual(files, tuple(sorted((keep.resolve(), header.resolve()))))

    def test_host_clang_format_cli_uses_host_style_file(self) -> None:
        host_root = self.root / "HostRepo"
        target_root = self.root / "Target"
        host_root.mkdir()
        target_root.mkdir()
        style_file = host_root / ".clang-format"
        source_file = target_root / "main.cpp"
        log_file = self.root / "clang-format.args"
        fake_clang_format_script = self.root / "fake-clang-format.py"
        fake_clang_format = self.root / ("fake-clang-format.cmd" if os.name == "nt" else "fake-clang-format")
        style_file.write_text("BasedOnStyle: LLVM\n", encoding="utf-8")
        source_file.write_text("int main(){return 0;}\n", encoding="utf-8")
        fake_clang_format_script.write_text(
            "import pathlib, sys\n"
            f"pathlib.Path({str(log_file)!r}).write_text('\\n'.join(sys.argv[1:]), encoding='utf-8')\n",
            encoding="utf-8",
        )
        if os.name == "nt":
            fake_clang_format.write_text(
                f'@echo off\r\n"{sys.executable}" "{fake_clang_format_script}" %*\r\n',
                encoding="utf-8",
            )
        else:
            fake_clang_format.write_text(
                f"#!{sys.executable}\n"
                f"exec(open({str(fake_clang_format_script)!r}, encoding='utf-8').read())\n",
                encoding="utf-8",
            )
            fake_clang_format.chmod(fake_clang_format.stat().st_mode | 0o111)

        original_cwd = Path.cwd()
        try:
            os.chdir(self.root)
            exit_code = host_clang_format_main(
                [
                    str(target_root),
                    "--host-root",
                    str(host_root),
                    "--clang-format",
                    str(fake_clang_format),
                    "--quiet",
                ]
            )
        finally:
            os.chdir(original_cwd)

        self.assertEqual(exit_code, 0)
        args = log_file.read_text(encoding="utf-8").splitlines()
        self.assertIn(f"-style=file:{style_file.resolve()}", args)
        self.assertIn("-i", args)
        self.assertIn(str(source_file.resolve()), args)


class RepoToolCliTests(unittest.TestCase):
    def test_cli_list_files_outputs_json_free_text(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "a.cpp").write_text("", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cpprepomgr.tools.repo_tool",
                    "list-files",
                    str(root),
                    "--cpptype",
                ],
                cwd=REPO_ROOT,
                env={"PYTHONPATH": str(REPO_ROOT)},
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "a.cpp\n")
        self.assertIn("total files: 1", completed.stderr)

    def test_cli_qrc_entries_writes_output(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "Gui").mkdir()
            (root / "Gui" / "A.qml").write_text("", encoding="utf-8")
            output = root / "qrc.txt"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cpprepomgr.tools.repo_tool",
                    "qrc-entries",
                    str(root / "Gui"),
                    ".qml",
                    "--output",
                    str(output),
                ],
                cwd=REPO_ROOT,
                env={"PYTHONPATH": str(REPO_ROOT)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "    <file>A.qml</file>\n")

    def test_cli_generate_json_keys_writes_header(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "config.json"
            output = root / "Keys.h"
            source.write_text('{"viewer-msaa": true}', encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cpprepomgr.tools.repo_tool",
                    "generate-json-keys",
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--namespace",
                    "demo::Keys",
                    "--header-guard",
                    "DEMO_KEYS_H",
                ],
                cwd=REPO_ROOT,
                env={"PYTHONPATH": str(REPO_ROOT)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('kViewerMsaa = "viewer-msaa"', output.read_text(encoding="utf-8"))

    def test_cli_dedup_json_array_writes_output(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            source = root / "actions.json"
            output = root / "deduped.json"
            source.write_text(
                '{"action_configs":[{"command":{"id":"a"}},{"command":{"id":"a"}}]}',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cpprepomgr.tools.repo_tool",
                    "dedup-json-array",
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--array-key",
                    "action_configs",
                    "--dedup-key",
                    "command",
                ],
                cwd=REPO_ROOT,
                env={"PYTHONPATH": str(REPO_ROOT)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(len(__import__("json").loads(output.read_text())["action_configs"]), 1)

    def test_cli_markdown_catalog_writes_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            docs = root / "docs"
            docs.mkdir()
            (docs / "CmdOpenDoc.md").write_text(
                "## CmdOpen(Open)\n\n- CmdId: `Open`\n\nBody",
                encoding="utf-8",
            )
            output = root / "Entries.inc"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cpprepomgr.tools.repo_tool",
                    "markdown-catalog",
                    "--root",
                    str(docs),
                    "--output",
                    str(output),
                ],
                cwd=REPO_ROOT,
                env={"PYTHONPATH": str(REPO_ROOT)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('{"Open"', output.read_text(encoding="utf-8"))

    def test_cli_ci_targets_dry_run_selects_quick_targets(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "cpprepomgr.tools.repo_tool",
                "ci-targets",
                "--build-dir",
                str(REPO_ROOT),
                "--target",
                "AppUnitTest",
                "--target",
                "AppRegression",
                "--quick-target",
                "AppUnitTest",
                "--quick-target",
                "AppRegressionQuick",
                "--pipeline-source",
                "merge_request_event",
                "--dry-run",
            ],
            cwd=REPO_ROOT,
            env={"PYTHONPATH": str(REPO_ROOT)},
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("selected targets: AppUnitTest, AppRegressionQuick", completed.stdout)

    def test_cli_remove_old_build_includes_xcodeproj_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "build" / "dependency_source_roots").mkdir(parents=True)
            (root / "build" / "generated").mkdir(parents=True)
            (root / "DerivedData").mkdir()
            (root / "Demo.xcodeproj").mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cpprepomgr.tools.repo_tool",
                    "remove-old-build",
                    "--repo-root",
                    str(root),
                    "--include-xcodeproj",
                ],
                cwd=REPO_ROOT,
                env={"PYTHONPATH": str(REPO_ROOT)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((root / "build" / "dependency_source_roots").exists())
            self.assertFalse((root / "build" / "generated").exists())
            self.assertFalse((root / "DerivedData").exists())
            self.assertFalse((root / "Demo.xcodeproj").exists())


if __name__ == "__main__":
    unittest.main()
