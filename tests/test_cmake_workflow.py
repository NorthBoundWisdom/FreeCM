from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, nullcontext, redirect_stderr, redirect_stdout
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from freecm.dependency_roots import DependencyCommitChange, dependency_commit_changes  # noqa: E402
from freecm.terminal_style import format_status_line  # noqa: E402
from repomgrcpp import cmake_workflow as workflow  # noqa: E402
from repomgrcpp import preset_templates  # noqa: E402
from repomgrcpp.cmake_workflow import (  # noqa: E402
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
    default_repo_root,
    dependency_source_dir,
    ensure_clangd_config,
    format_cli_exception,
    format_dependency_commit_change_lines,
    format_dependency_resolution_lines,
    host_template_path,
    load_cmake_dependency_build_context,
    resolve_preset_models,
    shared_clangd_template_path,
)


def atomic_sidecar_dir(path: Path) -> Path:
    return path.parent / ".freecm" / "atomic"


def assert_atomic_write_sidecars(testcase: unittest.TestCase, path: Path) -> None:
    sidecar_dir = atomic_sidecar_dir(path)
    testcase.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])
    testcase.assertFalse((path.parent / f".{path.name}.lock").exists())
    testcase.assertEqual(list(sidecar_dir.glob(f".{path.name}.*.tmp")), [])
    testcase.assertTrue((sidecar_dir / f".{path.name}.lock").is_file())


class DependencyRootManagerPresetTests(unittest.TestCase):
    def test_default_dependency_build_order_is_generic(self) -> None:
        self.assertEqual(workflow.CMAKE_DEPENDENCY_BUILD_ORDER, ())

    def test_cmake_dependency_build_spec_keeps_parent_owned_fields_only(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(workflow.CMakeDependencyBuildSpec)),
            (
                "dependency_name",
                "uses_c_language",
                "cmake_options",
                "uses_cxx_language",
                "source_subdir",
            ),
        )

    def test_ordered_dependency_build_specs_uses_host_supplied_specs(self) -> None:
        spec = workflow.CMakeDependencyBuildSpec(
            dependency_name="LibA",
            uses_c_language=True,
            cmake_options=("-DLIBA_BUILD_TESTS=OFF",),
        )
        dependency_roots = SimpleNamespace(closure_order=("LibA",))

        with (
            mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", (spec,)),
            mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME", {"LibA": spec}),
        ):
            self.assertEqual(workflow.ordered_dependency_build_specs(dependency_roots), [spec])

    def test_dependency_language_filtering_uses_host_supplied_specs(self) -> None:
        c_only_spec = workflow.CMakeDependencyBuildSpec(
            dependency_name="LibA",
            uses_c_language=True,
            cmake_options=(),
            uses_cxx_language=False,
        )
        context = workflow.CMakeDependencyBuildContext(
            preset_name="linux_clang_release",
            generator="Ninja",
            generator_platform="",
            generator_toolset="",
            cmake_executable="cmake",
            build_configurations=("Release",),
            external_prefix_path="",
            cache_variables={
                "CMAKE_BUILD_TYPE": "Release",
                "CMAKE_C_COMPILER": "clang",
                "CMAKE_CXX_COMPILER": "clang++",
            },
        )
        captured_commands: list[list[str]] = []

        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir) / "HostRepo"
            dependency_root = Path(tempdir) / "LibA"
            dependency_root.mkdir()

            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", (c_only_spec,)),
                mock.patch.object(
                    workflow,
                    "run_command",
                    side_effect=lambda cmd, **_: captured_commands.append(cmd),
                ),
            ):
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

        configure_command = captured_commands[0]
        self.assertIn("-DCMAKE_C_COMPILER=clang", configure_command)
        self.assertNotIn("-DCMAKE_CXX_COMPILER=clang++", configure_command)

    def test_dependency_source_subdir_is_used_for_context_builds(self) -> None:
        spec = workflow.CMakeDependencyBuildSpec(
            dependency_name="LibA",
            uses_c_language=True,
            cmake_options=(),
            source_subdir="CPP",
        )
        context = workflow.CMakeDependencyBuildContext(
            preset_name="linux_clang_release",
            generator="Ninja",
            generator_platform="",
            generator_toolset="",
            cmake_executable="cmake",
            build_configurations=("Release",),
            external_prefix_path="",
            cache_variables={"CMAKE_BUILD_TYPE": "Release"},
        )
        captured_commands: list[tuple[list[str], Path]] = []

        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir) / "HostRepo"
            dependency_root = Path(tempdir) / "LibA"
            (dependency_root / "CPP").mkdir(parents=True)

            def capture(cmd: list[str], *, cwd: Path, **_: object) -> None:
                captured_commands.append((cmd, cwd))

            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", (spec,)),
                mock.patch.object(workflow, "run_command", side_effect=capture),
            ):
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

        source_dir = (dependency_root / "CPP").resolve()
        self.assertEqual(captured_commands[0][0][2], str(source_dir))
        self.assertEqual(captured_commands[0][1], source_dir)
        self.assertEqual(captured_commands[1][1], source_dir)
        self.assertEqual(captured_commands[2][1], source_dir)

    def test_dependency_source_subdir_must_stay_under_dependency_root(self) -> None:
        spec = workflow.CMakeDependencyBuildSpec(
            dependency_name="LibA",
            uses_c_language=True,
            cmake_options=(),
            source_subdir="../Other",
        )

        with tempfile.TemporaryDirectory() as tempdir:
            dependency_root = Path(tempdir) / "LibA"
            dependency_root.mkdir()

            with mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", (spec,)):
                with self.assertRaises(workflow.WorkflowError):
                    dependency_source_dir(dependency_root, "LibA")

    def test_cmake_self_describing_metadata_boundary_is_documented(self) -> None:
        architecture = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")

        self.assertIn("CMake Build Metadata Boundary", architecture)
        self.assertIn("parent repository supplies `CMakeDependencyBuildSpec`", architecture)
        self.assertIn("whether the dependency supports install", architecture)
        self.assertIn("default CMake options", architecture)
        self.assertIn("required package names", architecture)
        self.assertIn("must not launch a second dependency graph", architecture)

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

    def test_shared_clangd_template_is_packaged_in_repomgrcpp(self) -> None:
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

    def test_cmd_init_prepares_asset_seeds(self) -> None:
        with (
            mock.patch.object(
                workflow,
                "ensure_active_lock_file",
                return_value=(Path("/tmp/source_roots.lock.jsonc"), False),
            ),
            mock.patch.object(
                workflow, "ensure_clangd_config", return_value=(Path("/tmp/.clangd"), False)
            ),
            mock.patch.object(
                workflow,
                "prepare_seed_repository_closure",
                return_value=SimpleNamespace(topo_order=()),
            ),
            mock.patch.object(
                workflow,
                "prepare_asset_seeds",
                return_value=(
                    SimpleNamespace(
                        asset_name="AssetBundle",
                        files=(object(),),
                        seed_root=Path("/tmp/build/dependency_seed_repos/AssetBundle"),
                    ),
                ),
            ) as prepare_assets,
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = workflow.cmd_init()

        self.assertEqual(0, result)
        prepare_assets.assert_called_once_with(workflow.REPO_ROOT)
        self.assertIn("AssetBundle", stdout.getvalue())

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

    def test_platform_cmake_cache_variables_override_common_values(self) -> None:
        resolved = resolve_preset_models(
            Path("/unused/repo"),
            {
                "cmakeEnvironment": {},
                "cmakeCacheVariables": {
                    "DEV_MODE": "ON",
                    "COMMON_ONLY": "1",
                    "mac": {
                        "DEV_MODE": "OFF",
                        "MAC_ONLY": "1",
                    },
                    "linux": {
                        "DEV_MODE": "LINUX",
                        "LINUX_ONLY": "1",
                    },
                    "win": {
                        "WIN_ONLY": "1",
                    },
                },
            },
            "mac",
            (),
        )

        xcode = next(
            preset
            for preset in resolved.generated_model["configurePresets"]
            if preset["name"] == "mac_xcode"
        )
        self.assertEqual(xcode["cacheVariables"]["DEV_MODE"], "OFF")
        self.assertEqual(xcode["cacheVariables"]["COMMON_ONLY"], "1")
        self.assertEqual(xcode["cacheVariables"]["MAC_ONLY"], "1")
        self.assertNotIn("LINUX_ONLY", xcode["cacheVariables"])
        self.assertNotIn("WIN_ONLY", xcode["cacheVariables"])

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

            with mock.patch.object(
                preset_templates, "host_template_path", return_value=template_path
            ):
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
            f"{ANSI_DIM}[freecm]{ANSI_RESET} " f"{ANSI_BOLD}{ANSI_RED}init{ANSI_RESET}: ready",
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


class CMakeWorkflowEntryPointTests(unittest.TestCase):
    def _preserve_shared_workflow_globals(self) -> None:
        names = (
            "REPO_ROOT",
            "REPO_DISPLAY_NAME",
            "CMAKE_DEPENDENCY_BUILD_ORDER",
            "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
            "DEPENDENCY_STATE_FILENAME",
            *workflow._DEPENDENCY_ROOT_HELPER_NAMES,
            *workflow._OPTIONAL_DEPENDENCY_ROOT_HELPER_NAMES,
            *workflow._SCRIPT_FUNCTION_NAMES,
        )
        original_values = {
            name: getattr(workflow, name) for name in names if hasattr(workflow, name)
        }
        originally_missing = {name for name in names if not hasattr(workflow, name)}

        def restore() -> None:
            for name, value in original_values.items():
                setattr(workflow, name, value)
            for name in originally_missing:
                if hasattr(workflow, name):
                    delattr(workflow, name)

        self.addCleanup(restore)

    def _write_nested_template(self, dependency_root: Path, child_name: str = "LibB") -> None:
        (dependency_root / "source_roots.lock.jsonc.in").write_text(
            json.dumps(
                {
                    "schemaVersion": 5,
                    "depsMode": "pinned",
                    "cmakeEnvironment": {},
                    "cmakeCacheVariables": {},
                    "depsManualPath": {child_name: ""},
                    "dependencies": {
                        child_name: {
                            "remote": f"file:///{child_name}",
                            "commit": "b" * 40,
                        },
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_default_repo_root_prefers_script_repo_when_workflow_markers_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            script_path = repo_root / "repomgrcpp" / "source_root_workflow.py"
            script_path.parent.mkdir()
            script_path.write_text("", encoding="utf-8")
            (repo_root / "source_roots.lock.jsonc.in").write_text("{}", encoding="utf-8")

            self.assertEqual(default_repo_root(script_path), repo_root.resolve())

    def test_default_binding_import_uses_source_roots_config(self) -> None:
        workflow_path = REPO_ROOT / "repomgrcpp" / "cmake_workflow.py"
        content = workflow_path.read_text(encoding="utf-8")

        self.assertIn("from configs import source_roots", content)
        self.assertNotIn("from configs.dependency_roots import", content)

    def test_swift_host_source_roots_config_leaves_cpp_helpers_unbound(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            configs_dir = repo_root / "configs"
            configs_dir.mkdir()
            (configs_dir / "source_roots.py").write_text(
                "class SourceRootWorkflow:\n" "    pass\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        f"sys.path.insert(0, {str(REPO_ROOT)!r}); "
                        "from repomgrcpp import cmake_workflow as workflow; "
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

    def test_nested_dependency_workflow_rejects_scripts_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            dependency_root = Path(tempdir) / "Dependency"
            scripts_script = dependency_root / "scripts" / "source_root_workflow.py"
            scripts_script.parent.mkdir(parents=True)
            scripts_script.write_text("", encoding="utf-8")
            (dependency_root / "source_roots.lock.jsonc.in").write_text("{}", encoding="utf-8")

            self.assertEqual(
                workflow._nested_dependency_workflow_script(dependency_root),
                dependency_root / "configs" / "source_root_workflow.py",
            )
            self.assertFalse(workflow._has_nested_dependency_workflow(dependency_root))

    def test_prepare_nested_dependency_workflows_writes_manual_lock_for_managed_configs_workflow(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir) / "HostRepo"
            dependency_root = repo_root / "build" / "dependency_source_roots" / "LibA"
            child_root = repo_root / "build" / "dependency_source_roots" / "LibB"
            (dependency_root / "configs").mkdir(parents=True)
            child_root.mkdir(parents=True)
            (dependency_root / "configs" / "source_root_workflow.py").write_text(
                "", encoding="utf-8"
            )
            self._write_nested_template(dependency_root)
            dependency_roots = SimpleNamespace(
                closure_order=("LibA",),
                dependency_root_for=lambda name: dependency_root if name == "LibA" else child_root,
            )

            workflow.prepare_nested_dependency_workflows(dependency_roots, repo_root=repo_root)

            lock_path = dependency_root / "source_roots.lock.jsonc"
            lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(lock_data["depsMode"], "manual")
            self.assertEqual(lock_data["depsManualPath"]["LibB"], str(child_root))
            assert_atomic_write_sidecars(self, lock_path)

    def test_prepare_nested_dependency_workflows_skips_template_without_configs_workflow(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir) / "HostRepo"
            dependency_root = repo_root / "build" / "dependency_source_roots" / "LibA"
            child_root = repo_root / "build" / "dependency_source_roots" / "LibB"
            dependency_root.mkdir(parents=True)
            child_root.mkdir(parents=True)
            self._write_nested_template(dependency_root)
            dependency_roots = SimpleNamespace(
                closure_order=("LibA",),
                dependency_root_for=lambda name: dependency_root if name == "LibA" else child_root,
            )

            workflow.prepare_nested_dependency_workflows(dependency_roots, repo_root=repo_root)

            self.assertFalse((dependency_root / "source_roots.lock.jsonc").exists())

    def test_prepare_nested_dependency_workflows_skips_unmanaged_dependency_root(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir) / "HostRepo"
            dependency_root = Path(tempdir) / "Manual" / "LibA"
            child_root = repo_root / "build" / "dependency_source_roots" / "LibB"
            (dependency_root / "configs").mkdir(parents=True)
            child_root.mkdir(parents=True)
            (dependency_root / "configs" / "source_root_workflow.py").write_text(
                "", encoding="utf-8"
            )
            self._write_nested_template(dependency_root)
            dependency_roots = SimpleNamespace(
                closure_order=("LibA",),
                dependency_root_for=lambda name: dependency_root if name == "LibA" else child_root,
            )

            workflow.prepare_nested_dependency_workflows(dependency_roots, repo_root=repo_root)

            self.assertFalse((dependency_root / "source_roots.lock.jsonc").exists())

    def test_prepare_nested_dependency_workflows_wraps_unsupported_nested_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir) / "HostRepo"
            dependency_root = repo_root / "build" / "dependency_source_roots" / "LibA"
            (dependency_root / "configs").mkdir(parents=True)
            (dependency_root / "configs" / "source_root_workflow.py").write_text(
                "", encoding="utf-8"
            )
            self._write_nested_template(dependency_root, child_name="LibMissing")

            def dependency_root_for(name: str) -> Path:
                if name == "LibA":
                    return dependency_root
                raise KeyError(name)

            dependency_roots = SimpleNamespace(
                closure_order=("LibA",),
                dependency_root_for=dependency_root_for,
            )

            with self.assertRaisesRegex(
                workflow.WorkflowError, "unsupported dependency 'LibMissing'"
            ):
                workflow.prepare_nested_dependency_workflows(dependency_roots, repo_root=repo_root)

    def test_bind_cmake_workflow_script_preserves_host_helper_overrides(self) -> None:
        self._preserve_shared_workflow_globals()

        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            module_globals: dict[str, object] = {}
            calls: list[str] = []
            build_spec = workflow.CMakeDependencyBuildSpec(
                dependency_name="LibA",
                uses_c_language=True,
                cmake_options=(),
            )

            def prepare_seed_repository_closure(
                *, repo_root: Path, progress: object, quiet: bool = False
            ):
                del repo_root, progress
                calls.append(f"quiet={str(quiet).lower()}")
                return SimpleNamespace(topo_order=("LibA",))

            module_globals["prepare_seed_repository_closure"] = prepare_seed_repository_closure
            workflow.bind_cmake_workflow_script(
                module_globals,
                repo_root=repo_root,
                repo_display_name="SampleApp",
                dependency_build_order=(build_spec,),
            )
            with (
                mock.patch.object(
                    workflow,
                    "ensure_active_lock_file",
                    return_value=(repo_root / "source_roots.lock.jsonc", False),
                ),
                mock.patch.object(
                    workflow, "ensure_clangd_config", return_value=(repo_root / ".clangd", False)
                ),
                mock.patch.object(workflow, "prepare_asset_seeds", return_value=()),
                redirect_stdout(io.StringIO()),
            ):
                result = module_globals["cmd_init"](quiet=True)

            self.assertEqual(result, 0)
            self.assertEqual(calls, ["quiet=true"])
            self.assertEqual(module_globals["REPO_ROOT"], repo_root.resolve())
            self.assertIs(
                module_globals["CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME"]["LibA"],
                build_spec,
            )

    def test_bound_cmake_workflow_command_uses_optional_unlocked_seed_helper(self) -> None:
        self._preserve_shared_workflow_globals()

        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            module_globals: dict[str, object] = {}
            calls: list[str] = []

            def prepare_seed_repository_closure(*_: object, **__: object) -> object:
                raise AssertionError("locked public helper should not be used by bound command")

            def prepare_seed_repository_closure_unlocked(
                repo_root: Path, *, progress: object, quiet: bool = False
            ):
                del repo_root, progress
                calls.append(f"unlocked quiet={str(quiet).lower()}")
                return SimpleNamespace(topo_order=("LibA",))

            module_globals["prepare_seed_repository_closure"] = prepare_seed_repository_closure
            module_globals["_prepare_seed_repository_closure_unlocked"] = (
                prepare_seed_repository_closure_unlocked
            )
            workflow.bind_cmake_workflow_script(
                module_globals,
                repo_root=repo_root,
                repo_display_name="SampleApp",
                dependency_build_order=(),
            )

            with (
                mock.patch.object(
                    workflow, "workspace_mutation_lock", side_effect=lambda _: nullcontext()
                ),
                mock.patch.object(
                    workflow,
                    "ensure_active_lock_file",
                    return_value=(repo_root / "source_roots.lock.jsonc", False),
                ),
                mock.patch.object(
                    workflow, "ensure_clangd_config", return_value=(repo_root / ".clangd", False)
                ),
                mock.patch.object(workflow, "prepare_asset_seeds", return_value=()),
                redirect_stdout(io.StringIO()),
            ):
                result = module_globals["cmd_init"](quiet=True)

            self.assertEqual(result, 0)
            self.assertEqual(calls, ["unlocked quiet=true"])

    def test_cmd_update_keeps_full_workspace_mutation_under_lock(self) -> None:
        lock_active = False
        observed: list[str] = []
        dependency_roots = SimpleNamespace(
            closure_order=("LibA",),
            lock_data={
                "depsMode": "pinned",
                "dependencies": {"LibA": {"remote": "file:///LibA", "commit": "a" * 40}},
            },
            direct_dependency_names=("LibA",),
        )

        def workspace_lock(_: Path):
            @contextmanager
            def lock():
                nonlocal lock_active
                self.assertFalse(lock_active)
                lock_active = True
                observed.append("lock:start")
                try:
                    yield
                finally:
                    observed.append("lock:end")
                    lock_active = False

            return lock()

        def require_asset_seeds(_: Path) -> tuple[object, ...]:
            self.assertTrue(lock_active)
            observed.append("assets")
            return ()

        def prepare_nested_dependency_workflows(_: object, *, repo_root: Path) -> None:
            del repo_root
            self.assertTrue(lock_active)
            observed.append("nested")

        def write_generated_cmake_presets(_: Path, __: object) -> None:
            self.assertTrue(lock_active)
            observed.append("presets")

        with (
            mock.patch.object(workflow, "workspace_mutation_lock", side_effect=workspace_lock),
            mock.patch.object(workflow, "load_lock_file", return_value=dependency_roots.lock_data),
            mock.patch.object(
                workflow,
                "_materialize_dependency_roots_for_command",
                return_value=dependency_roots,
            ),
            mock.patch.object(workflow, "describe_dependency_roots", return_value=()),
            mock.patch.object(workflow, "host_os_group", return_value="linux"),
            mock.patch.object(
                workflow,
                "resolve_preset_models",
                return_value=SimpleNamespace(generated_model={"version": 6}),
            ),
            mock.patch.object(workflow, "require_asset_seeds", side_effect=require_asset_seeds),
            mock.patch.object(
                workflow,
                "prepare_nested_dependency_workflows",
                side_effect=prepare_nested_dependency_workflows,
            ),
            mock.patch.object(
                workflow,
                "write_generated_cmake_presets",
                side_effect=write_generated_cmake_presets,
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(workflow.cmd_update(), 0)

        self.assertEqual(observed, ["lock:start", "assets", "nested", "presets", "lock:end"])

    def test_managed_dependency_active_lock_is_written_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            dependency_root = (
                Path(tempdir) / "HostRepo" / "build" / "dependency_source_roots" / "LibA"
            )
            dependency_root.mkdir(parents=True)
            template_path = dependency_root / "source_roots.lock.jsonc.in"
            template_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 5,
                        "depsMode": "pinned",
                        "cmakeEnvironment": {},
                        "cmakeCacheVariables": {},
                        "depsManualPath": {"LibB": ""},
                        "dependencies": {"LibB": {"remote": "file:///LibB", "commit": "b" * 40}},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            libb_root = Path(tempdir) / "HostRepo" / "build" / "dependency_source_roots" / "LibB"
            workflow.ensure_dependency_root_active_lock(dependency_root, {"LibB": libb_root})

            lock_path = dependency_root / "source_roots.lock.jsonc"
            lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(lock_data["depsMode"], "manual")
            self.assertEqual(lock_data["depsManualPath"]["LibB"], str(libb_root))
            assert_atomic_write_sidecars(self, lock_path)

    def test_generated_cmake_presets_are_written_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)

            workflow.write_generated_cmake_presets(
                repo_root,
                {
                    "version": 6,
                    "configurePresets": [
                        {
                            "name": "default",
                            "generator": "Ninja",
                        }
                    ],
                },
            )

            presets_path = repo_root / "CMakePresets.json"
            self.assertEqual(json.loads(presets_path.read_text(encoding="utf-8"))["version"], 6)
            assert_atomic_write_sidecars(self, presets_path)

    def test_dependency_state_file_is_written_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            context = workflow.CMakeDependencyBuildContext(
                preset_name="linux_clang_release",
                generator="Ninja",
                generator_platform="",
                generator_toolset="",
                cmake_executable="cmake",
                build_configurations=("Release",),
                external_prefix_path="",
                cache_variables={"CMAKE_BUILD_TYPE": "Release"},
            )
            dependency_roots = SimpleNamespace(
                mode="pinned",
                closure_order=("LibA",),
                resolved_commits={"LibA": "a" * 40},
                dependency_root_for=lambda name: repo_root
                / "build"
                / "dependency_source_roots"
                / name,
            )

            workflow.write_dependency_state_file(repo_root, context, dependency_roots)

            state_path = workflow.dependency_state_file_path(repo_root, context.preset_name)
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state_data["mode"], "pinned")
            self.assertEqual(state_data["resolved"]["LibA"], "a" * 40)
            assert_atomic_write_sidecars(self, state_path)

    def test_cmake_adapter_script_entrypoint_prints_help(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "repomgrcpp" / "source_root_workflow.py"),
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
