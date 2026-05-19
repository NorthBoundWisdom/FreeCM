from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cpprepomgr import dependency_root_workflow as workflow  # noqa: E402
from cpprepomgr import preset_templates  # noqa: E402
from cpprepomgr.dependency_root_workflow import (  # noqa: E402
    ANSI_BLUE,
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_RED,
    ANSI_RESET,
    DependencyRootSummary,
    cmake_executable_for_preset,
    collect_template_tokens,
    configure_dependency_for_context,
    ensure_clangd_config,
    format_cli_exception,
    format_dependency_commit_change_lines,
    format_dependency_resolution_lines,
    default_repo_root,
    host_template_path,
    load_cmake_dependency_build_context,
    resolve_preset_models,
    shared_clangd_template_path,
)
from depsfixture.dependency_roots import DependencyCommitChange, dependency_commit_changes  # noqa: E402
from depsfixture.terminal_style import format_status_line  # noqa: E402


class DependencyRootManagerPresetTests(unittest.TestCase):
    def test_template_tokens_are_collected_recursively(self) -> None:
        self.assertEqual(
            collect_template_tokens(
                {
                    "one": "@CMAKE_EXECUTABLE@",
                    "nested": ["@DEV_MODE@", {"two": "@CMAKE_EXECUTABLE@"}],
                    "literal": "@not-a-token@",
                }
            ),
            {"CMAKE_EXECUTABLE", "DEV_MODE"},
        )

    def test_host_template_path_uses_shared_templates(self) -> None:
        template_path = host_template_path(Path("/unused/repo"), "linux")

        self.assertEqual(template_path.parent.name, "cmake_presets")
        self.assertEqual(template_path.name, "CMakePresets.json.linux.in")
        self.assertTrue(template_path.is_file())

    def test_shared_clangd_template_is_packaged_in_cpprepomgr(self) -> None:
        template_path = shared_clangd_template_path()

        self.assertEqual(template_path.parent.name, "clangd")
        self.assertEqual(template_path.name, ".clangd.in")
        self.assertTrue(template_path.is_file())
        self.assertIn("CompilationDatabase: <>", template_path.read_text(encoding="utf-8"))

    def test_ensure_clangd_config_creates_once_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)

            clangd_path, created = ensure_clangd_config(repo_root)
            first_content = clangd_path.read_text(encoding="utf-8")
            clangd_path.write_text("custom\n", encoding="utf-8")
            existing_path, existing_created = ensure_clangd_config(repo_root)

            self.assertTrue(created)
            self.assertEqual(clangd_path, (repo_root / ".clangd").resolve())
            self.assertIn("CompilationDatabase: <>", first_content)
            self.assertFalse(existing_created)
            self.assertEqual(existing_path, clangd_path)
            self.assertEqual(clangd_path.read_text(encoding="utf-8"), "custom\n")

    def test_lock_environment_and_cache_variables_are_injected(self) -> None:
        resolved = resolve_preset_models(
            Path("/unused/repo"),
            {
                "cmakeEnvironment": {
                    "CC": "/opt/toolchain/cc",
                    "CXX": "/opt/toolchain/c++",
                    "CUSTOM_ENV": "enabled",
                },
                "cmakeCacheVariables": {
                    "CMAKE_EXPORT_COMPILE_COMMANDS": "OFF",
                    "DEV_MODE": "ON",
                },
            },
            "mac",
            ("LibA", "LibB"),
        )

        model_text = str(resolved.generated_model)
        self.assertNotIn("@", model_text)
        self.assertNotIn("cmakeExecutable", model_text)

        xcode = next(
            preset
            for preset in resolved.generated_model["configurePresets"]
            if preset["name"] == "mac_xcode"
        )
        self.assertEqual(xcode["environment"]["CC"], "/opt/toolchain/cc")
        self.assertEqual(xcode["environment"]["CXX"], "/opt/toolchain/c++")
        self.assertEqual(xcode["environment"]["CUSTOM_ENV"], "enabled")
        self.assertEqual(xcode["cacheVariables"]["CMAKE_EXPORT_COMPILE_COMMANDS"], "OFF")
        self.assertEqual(xcode["cacheVariables"]["DEV_MODE"], "ON")
        self.assertEqual(
            xcode["cacheVariables"]["CMAKE_PREFIX_PATH"],
            "${sourceDir}/build/${presetName}/dependency_installs/LibA;"
            "${sourceDir}/build/${presetName}/dependency_installs/LibB",
        )

    def test_user_cmake_prefix_path_overrides_managed_prefixes(self) -> None:
        resolved = resolve_preset_models(
            Path("/unused/repo"),
            {
                "cmakeEnvironment": {},
                "cmakeCacheVariables": {
                    "CMAKE_PREFIX_PATH": "/deps/system",
                },
            },
            "linux",
            ("LibA", "LibB"),
        )

        clang_release = next(
            preset
            for preset in resolved.generated_model["configurePresets"]
            if preset["name"] == "linux_clang_release"
        )
        self.assertEqual(
            clang_release["cacheVariables"]["CMAKE_PREFIX_PATH"],
            "/deps/system",
        )

    def test_resolver_fails_when_template_contains_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            template_path = Path(tempdir) / "CMakePresets.json.in"
            template_path.write_text(
                json.dumps(
                    {
                        "version": 6,
                        "configurePresets": [
                            {
                                "name": "bad",
                                "generator": "Ninja",
                                "binaryDir": "${sourceDir}/build/${presetName}",
                                "cacheVariables": {"DEV_MODE": "@DEV_MODE@"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(preset_templates, "host_template_path", return_value=template_path):
                with self.assertRaisesRegex(Exception, "Unresolved preset template tokens"):
                    preset_templates.resolve_preset_models(
                        Path("/unused/repo"),
                        {"cmakeEnvironment": {}, "cmakeCacheVariables": {}},
                        "linux",
                        (),
                    )

    def test_generated_templates_have_no_tokens_or_cmake_executable(self) -> None:
        for os_group in ("linux", "mac", "win"):
            with self.subTest(os_group=os_group):
                resolved = resolve_preset_models(
                    Path("/unused/repo"),
                    {"cmakeEnvironment": {}, "cmakeCacheVariables": {}},
                    os_group,
                    (),
                )
                model_text = str(resolved.generated_model)
                self.assertNotIn("@", model_text)
                self.assertNotIn("cmakeExecutable", model_text)

    def test_cmake_executable_is_always_path_cmake(self) -> None:
        self.assertEqual(
            cmake_executable_for_preset(
                {"configurePresets": [{"name": "custom", "cmakeExecutable": "/opt/cmake"}]},
                "custom",
            ),
            "cmake",
        )

        with tempfile.TemporaryDirectory() as tempdir:
            context_path = Path(tempdir) / "context.json"
            context_path.write_text(
                json.dumps(
                    {
                        "presetName": "custom",
                        "generator": "Ninja",
                        "cmakeExecutable": "/opt/cmake",
                        "buildConfigurations": ["Release"],
                        "cacheVariables": {},
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                load_cmake_dependency_build_context(context_path).cmake_executable,
                "cmake",
            )


class DependencyRootManagerOutputTests(unittest.TestCase):
    def test_status_line_uses_semantic_color(self) -> None:
        self.assertEqual(
            format_status_line("init", "ready", level="error", use_color=True),
            f"{ANSI_DIM}[freecm]{ANSI_RESET} "
            f"{ANSI_BOLD}{ANSI_RED}init{ANSI_RESET}: ready",
        )

    def test_dependency_resolution_lines_without_color_match_existing_shape(self) -> None:
        lines = format_dependency_resolution_lines(
            (
                DependencyRootSummary(
                    dependency_name="LibA",
                    mode="pinned",
                    commit="abc123",
                    path=Path("/tmp/LibA"),
                ),
                DependencyRootSummary(
                    dependency_name="LibB",
                    mode="manual",
                    commit=None,
                    path=Path("/tmp/LibB"),
                ),
            )
        )

        self.assertEqual(
            lines,
            [
                "resolved direct dependencies:",
                "  LibA: pin sha=abc123",
                f"  LibB: manual path={Path('/tmp/LibB')}",
            ],
        )

    def test_dependency_resolution_lines_apply_semantic_color(self) -> None:
        lines = format_dependency_resolution_lines(
            (
                DependencyRootSummary(
                    dependency_name="LibA",
                    mode="latest",
                    commit="def456",
                    path=Path("/tmp/LibA"),
                ),
            ),
            use_color=True,
        )

        self.assertEqual(
            lines[1],
            f"  {ANSI_CYAN}LibA{ANSI_RESET}: "
            f"{ANSI_BOLD}{ANSI_BLUE}latest{ANSI_RESET} "
            f"{ANSI_DIM}sha{ANSI_RESET}={ANSI_BLUE}def456{ANSI_RESET}",
        )

    def test_dependency_commit_changes_compare_direct_lock_commits(self) -> None:
        changes = dependency_commit_changes(
            {
                "dependencies": {
                    "LibA": {"commit": "1111111111111111111111111111111111111111"},
                    "LibB": {"commit": "2222222222222222222222222222222222222222"},
                }
            },
            {
                "dependencies": {
                    "LibA": {"commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                    "LibB": {"commit": "2222222222222222222222222222222222222222"},
                }
            },
            ("LibA", "LibB"),
        )

        self.assertEqual(
            changes,
            (
                DependencyCommitChange(
                    dependency_name="LibA",
                    old_commit="1111111111111111111111111111111111111111",
                    new_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                ),
            ),
        )

    def test_dependency_commit_change_lines_report_changed_and_unchanged(self) -> None:
        self.assertEqual(
            format_dependency_commit_change_lines(()),
            ["dependency lock commits unchanged"],
        )

        lines = format_dependency_commit_change_lines(
            (
                DependencyCommitChange(
                    dependency_name="LibA",
                    old_commit="1111111111111111111111111111111111111111",
                    new_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                ),
            )
        )

        self.assertEqual(
            lines,
            [
                "updated dependency lock commits:",
                "  LibA: 111111111111 -> aaaaaaaaaaaa",
            ],
        )


class DependencyRootWorkflowEntryPointTests(unittest.TestCase):
    def test_default_repo_root_prefers_script_repo_when_workflow_markers_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            script_path = repo_root / "cpprepomgr" / "source_root_workflow.py"
            script_path.parent.mkdir()
            script_path.write_text("", encoding="utf-8")
            (repo_root / "source_roots.lock.jsonc.in").write_text("{}", encoding="utf-8")

            self.assertEqual(default_repo_root(script_path), repo_root.resolve())

    def test_default_binding_import_uses_source_roots_config(self) -> None:
        workflow_path = REPO_ROOT / "cpprepomgr" / "dependency_root_workflow.py"
        content = workflow_path.read_text(encoding="utf-8")

        self.assertIn("from configs import source_roots", content)
        self.assertNotIn("from configs.dependency_roots import", content)

    def test_swift_host_source_roots_config_leaves_cpp_helpers_unbound(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            configs_dir = repo_root / "configs"
            configs_dir.mkdir()
            (configs_dir / "source_roots.py").write_text(
                "class SourceRootWorkflow:\n"
                "    pass\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        f"sys.path.insert(0, {str(REPO_ROOT)!r}); "
                        "from cpprepomgr import dependency_root_workflow as workflow; "
                        "print(workflow.DependencyRootSummary.__name__); "
                        "\ntry:\n"
                        "    workflow.describe_dependency_roots()\n"
                        "except RuntimeError as error:\n"
                        "    print(error)\n"
                    ),
                ],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("DependencyRootSummary", completed.stdout)
            self.assertIn(
                "dependency-root workflow helpers have not been bound",
                completed.stdout,
            )

    def test_nested_dependency_workflow_prefers_configs_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            dependency_root = Path(tempdir) / "Dependency"
            configs_script = dependency_root / "configs" / "source_root_workflow.py"
            scripts_script = dependency_root / "scripts" / "source_root_workflow.py"
            configs_script.parent.mkdir(parents=True)
            scripts_script.parent.mkdir(parents=True)
            configs_script.write_text("", encoding="utf-8")
            scripts_script.write_text("", encoding="utf-8")
            (dependency_root / "source_roots.lock.jsonc.in").write_text("{}", encoding="utf-8")

            self.assertEqual(
                workflow._nested_dependency_workflow_script(dependency_root),
                configs_script,
            )
            self.assertTrue(workflow._has_nested_dependency_workflow(dependency_root))

    def test_legacy_script_entrypoint_still_prints_help(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "cpprepomgr" / "source_root_workflow.py"),
                "--help",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("--init", completed.stdout)
        self.assertIn("--update", completed.stdout)

    def test_cmd_init_generates_clangd_config(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            stdout = io.StringIO()
            with (
                mock.patch.object(workflow, "REPO_ROOT", repo_root),
                mock.patch.object(
                    workflow,
                    "ensure_active_lock_file",
                    return_value=(repo_root / "source_roots.lock.jsonc", False),
                ),
                mock.patch.object(
                    workflow,
                    "prepare_seed_repository_closure",
                    return_value=SimpleNamespace(topo_order=("LibA",)),
                ),
                redirect_stdout(stdout),
            ):
                result = workflow.cmd_init()

            self.assertEqual(result, 0)
            self.assertTrue((repo_root / ".clangd").is_file())
            self.assertIn("created clangd config", stdout.getvalue())

    def test_init_unexpected_python_exception_is_reported_without_traceback(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(
                workflow,
                "parse_args",
                return_value=SimpleNamespace(init=True, build_dependencies_from_cmake=None),
            ),
            mock.patch.object(workflow, "cmd_init", side_effect=KeyError("remote")),
            redirect_stderr(stderr),
        ):
            exit_code = workflow.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("Unexpected Python error (KeyError)", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_called_process_errors_are_formatted_for_cli(self) -> None:
        error = subprocess.CalledProcessError(
            128,
            ["git", "clone", "https://example.invalid/repo.git"],
            stderr="fatal: unable to access remote",
        )

        self.assertEqual(
            format_cli_exception(error),
            "Command failed with exit code 128:\n"
            "  git clone https://example.invalid/repo.git\n"
            "stderr:\n"
            "fatal: unable to access remote",
        )

    def test_managed_dependency_configure_inherits_repo_config_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir) / "HostRepo"
            dependency_root = repo_root / "build" / "dependency_source_roots" / "LibA"
            dependency_root.mkdir(parents=True)
            context = workflow.CMakeDependencyBuildContext(
                preset_name="mac_clang_release",
                generator="Ninja",
                generator_platform="",
                generator_toolset="",
                cmake_executable="cmake",
                build_configurations=("Release",),
                external_prefix_path="",
                cache_variables={"CMAKE_BUILD_TYPE": "Release"},
            )
            captured_env: list[dict[str, str]] = []

            def fake_run_command(
                cmd: list[str],
                *,
                cwd: Path | None = None,
                env: dict[str, str] | None = None,
            ) -> None:
                del cmd, cwd
                captured_env.append(dict(env or {}))

            with mock.patch.object(workflow, "run_command", side_effect=fake_run_command):
                configure_dependency_for_context(
                    repo_root=repo_root,
                    context=context,
                    dependency_name="LibA",
                    dependency_root=dependency_root,
                    install_prefix=repo_root / "build" / "install" / "LibA",
                    dependency_prefixes=(),
                    cmake_options=(),
                    available_dependency_roots={},
                )

            self.assertEqual(len(captured_env), 3)
            pythonpath = captured_env[0]["PYTHONPATH"].split(os.pathsep)
            self.assertEqual(pythonpath[0], str(REPO_ROOT))


if __name__ == "__main__":
    unittest.main()
