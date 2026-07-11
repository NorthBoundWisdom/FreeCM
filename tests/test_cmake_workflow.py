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
                "CMAKE_C_COMPILER_LAUNCHER": "sccache",
                "CMAKE_CXX_COMPILER": "clang++",
                "CMAKE_CXX_COMPILER_LAUNCHER": "ccache",
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
        self.assertIn("-DCMAKE_C_COMPILER_LAUNCHER=sccache", configure_command)
        self.assertNotIn("-DCMAKE_CXX_COMPILER=clang++", configure_command)
        self.assertNotIn("-DCMAKE_CXX_COMPILER_LAUNCHER=ccache", configure_command)

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
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            namespace = cmake_binding_namespace(repo_root)
            prepare_assets = mock.Mock(
                return_value=(
                    SimpleNamespace(
                        asset_name="AssetBundle",
                        files=(object(),),
                        seed_root=repo_root / "build/dependency_seed_repos/AssetBundle",
                    ),
                )
            )
            namespace.update(
                {
                    "ensure_clangd_config": lambda _: (repo_root / ".clangd", False),
                    "prepare_asset_seeds": prepare_assets,
                    "_prepare_seed_repository_closure_unlocked": lambda *_, **__: SimpleNamespace(
                        topo_order=()
                    ),
                }
            )
            script = workflow.bind_cmake_workflow_script(
                namespace,
                repo_root=repo_root,
                repo_display_name="SampleApp",
                dependency_build_order=(),
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = script.cmd_init()

            self.assertEqual(0, result)
            prepare_assets.assert_called_once_with(repo_root.resolve())
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


def cmake_binding_namespace(repo_root: Path) -> dict[str, object]:
    def unavailable(*_: object, **__: object) -> object:
        raise AssertionError("unexpected dependency-root helper call")

    return {
        "ensure_active_lock_file": lambda **_: (
            repo_root / "source_roots.lock.jsonc",
            False,
        ),
        "load_lock_file": lambda **_: {},
        "require_dependency_roots": unavailable,
        "describe_dependency_roots": lambda _: (),
        "prepare_nested_dependency_workflows": lambda _, **__: None,
        "prepare_seed_repository_closure": unavailable,
        "materialize_dependency_roots": unavailable,
        "_prepare_seed_repository_closure_unlocked": unavailable,
        "_materialize_dependency_roots_unlocked": unavailable,
        "workspace_mutation_lock": lambda _: nullcontext(),
        "prepare_asset_seeds": lambda _: (),
        "require_asset_seeds": lambda _: (),
    }


class CMakeWorkflowEntryPointTests(unittest.TestCase):
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

    def test_default_facade_does_not_import_host_source_roots_config(self) -> None:
        workflow_path = REPO_ROOT / "repomgrcpp" / "cmake_workflow.py"
        content = workflow_path.read_text(encoding="utf-8")

        self.assertNotIn("from configs import source_roots", content)
        self.assertNotIn("sys.path.insert(0, str(REPO_ROOT))", content)

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
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            module_globals = cmake_binding_namespace(repo_root)
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

            module_globals["_prepare_seed_repository_closure_unlocked"] = (
                lambda root, *, progress, quiet=False: prepare_seed_repository_closure(
                    repo_root=root, progress=progress, quiet=quiet
                )
            )
            script = workflow.bind_cmake_workflow_script(
                module_globals,
                repo_root=repo_root,
                repo_display_name="SampleApp",
                dependency_build_order=(build_spec,),
            )
            with redirect_stdout(io.StringIO()):
                result = module_globals["cmd_init"](quiet=True)

            self.assertEqual(result, 0)
            self.assertEqual(calls, ["quiet=true"])
            self.assertEqual(module_globals["REPO_ROOT"], repo_root.resolve())
            self.assertIs(
                module_globals["CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME"]["LibA"],
                build_spec,
            )
            self.assertIs(module_globals["cmd_init"].__self__, script)

    def test_bound_cmake_workflow_command_uses_optional_unlocked_seed_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            module_globals = cmake_binding_namespace(repo_root)
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

            with redirect_stdout(io.StringIO()):
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
            self.assertTrue(lock_active)
            self.assertEqual(repo_root, Path("/repo"))
            observed.append("nested")

        def write_generated_cmake_presets(_: Path, __: object) -> None:
            self.assertTrue(lock_active)
            observed.append("presets")

        def materialize(_: Path, *, allow_network: bool) -> object:
            self.assertTrue(lock_active)
            self.assertFalse(allow_network)
            return dependency_roots

        namespace = cmake_binding_namespace(Path("/repo"))
        namespace.update(
            {
                "workspace_mutation_lock": workspace_lock,
                "load_lock_file": lambda **_: dependency_roots.lock_data,
                "_materialize_dependency_roots_unlocked": materialize,
                "describe_dependency_roots": lambda _: (),
                "prepare_nested_dependency_workflows": prepare_nested_dependency_workflows,
                "host_os_group": lambda: "linux",
                "resolve_preset_models": lambda *_: SimpleNamespace(generated_model={"version": 6}),
                "require_asset_seeds": require_asset_seeds,
                "write_generated_cmake_presets": write_generated_cmake_presets,
            }
        )
        script = workflow.bind_cmake_workflow_script(
            namespace,
            repo_root=Path("/repo"),
            repo_display_name="SampleApp",
            dependency_build_order=(),
        )
        with redirect_stdout(io.StringIO()):
            self.assertEqual(script.cmd_update(), 0)

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
            build_spec = workflow.CMakeDependencyBuildSpec(
                dependency_name="LibA",
                uses_c_language=True,
                uses_cxx_language=False,
                cmake_options=("-DLIBA_BUILD_TESTS=OFF",),
                source_subdir="native",
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
            dependency_roots = SimpleNamespace(
                mode="pinned",
                closure_order=("LibA",),
                resolved_commits={"LibA": "a" * 40},
                dependency_names_by_parent={"LibA": ()},
                dependency_root_for=lambda name: repo_root
                / "build"
                / "dependency_source_roots"
                / name,
            )

            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", (build_spec,)),
                mock.patch.object(
                    workflow,
                    "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
                    {"LibA": build_spec},
                ),
            ):
                workflow.write_dependency_state_file(repo_root, context, dependency_roots)

            state_path = workflow.dependency_state_file_path(repo_root, context.preset_name)
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                state_data["schemaVersion"],
                workflow.DEPENDENCY_BUILD_STATE_SCHEMA_VERSION,
            )
            self.assertEqual(state_data["mode"], "pinned")
            dependency_receipt = state_data["dependencies"]["LibA"]
            self.assertEqual(len(dependency_receipt["fingerprint"]), 64)
            dependency_state = dependency_receipt["inputs"]
            self.assertEqual(dependency_state["resolvedCommit"], "a" * 40)
            self.assertEqual(
                dependency_state["buildDir"],
                str(
                    workflow.dependency_build_dir_for_name(
                        repo_root,
                        context.preset_name,
                        "LibA",
                    )
                ),
            )
            self.assertEqual(
                dependency_state["installPrefix"],
                str(
                    workflow.dependency_install_prefix_for_name(
                        repo_root,
                        context.preset_name,
                        "LibA",
                    )
                ),
            )
            self.assertEqual(
                dependency_state["buildSpec"],
                {
                    "dependency_name": "LibA",
                    "uses_c_language": True,
                    "cmake_options": ["-DLIBA_BUILD_TESTS=OFF"],
                    "uses_cxx_language": False,
                    "source_subdir": "native",
                },
            )
            self.assertEqual(
                tuple(dependency_state["buildSpec"]),
                tuple(field.name for field in fields(workflow.CMakeDependencyBuildSpec)),
            )
            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", (build_spec,)),
                mock.patch.object(
                    workflow,
                    "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
                    {"LibA": build_spec},
                ),
            ):
                moved_state = workflow.dependency_build_state(
                    context,
                    dependency_roots,
                    repo_root=repo_root / "moved-host",
                )
            self.assertNotEqual(
                dependency_receipt["fingerprint"],
                moved_state["dependencies"]["LibA"]["fingerprint"],
            )
            assert_atomic_write_sidecars(self, state_path)

    def test_dependency_rebuild_selection_tracks_specs_context_and_commits(self) -> None:
        def context(*, compiler: str = "clang") -> workflow.CMakeDependencyBuildContext:
            return workflow.CMakeDependencyBuildContext(
                preset_name="linux_clang_release",
                generator="Ninja",
                generator_platform="",
                generator_toolset="",
                cmake_executable="cmake",
                build_configurations=("Release",),
                external_prefix_path="/opt/sdk",
                cache_variables={
                    "CMAKE_BUILD_TYPE": "Release",
                    "CMAKE_CXX_COMPILER": compiler,
                },
            )

        original_specs = (
            workflow.CMakeDependencyBuildSpec("LibLeaf", False, ("-DLEAF_MODE=ON",)),
            workflow.CMakeDependencyBuildSpec("LibIndependent", False, ()),
            workflow.CMakeDependencyBuildSpec("LibParent", False, ()),
            workflow.CMakeDependencyBuildSpec("SampleApp", False, ()),
        )
        edges = {
            "LibLeaf": (),
            "LibIndependent": (),
            "LibParent": ("LibLeaf",),
            "SampleApp": ("LibParent",),
        }
        parents = {
            "LibLeaf": ("LibParent",),
            "LibParent": ("SampleApp",),
        }

        for change_name in ("cmake-options", "source-subdir", "language-selection"):
            with self.subTest(change=change_name), tempfile.TemporaryDirectory() as tempdir:
                repo_root = Path(tempdir)
                commits = {
                    spec.dependency_name: spec.dependency_name * 4 for spec in original_specs
                }
                dependency_roots = SimpleNamespace(
                    mode="pinned",
                    closure_order=tuple(spec.dependency_name for spec in original_specs),
                    resolved_commits=commits,
                    dependency_names_by_parent=edges,
                    dependency_parent_names_by_name=parents,
                    dependency_root_for=lambda name, root=repo_root: root / "sources" / name,
                )
                original_map = {spec.dependency_name: spec for spec in original_specs}
                with (
                    mock.patch.object(
                        workflow,
                        "CMAKE_DEPENDENCY_BUILD_ORDER",
                        original_specs,
                    ),
                    mock.patch.object(
                        workflow,
                        "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
                        original_map,
                    ),
                ):
                    for spec in original_specs:
                        workflow.dependency_install_prefix_for_name(
                            repo_root,
                            context().preset_name,
                            spec.dependency_name,
                        ).mkdir(parents=True)
                    workflow.write_dependency_state_file(repo_root, context(), dependency_roots)

                leaf = original_specs[0]
                if change_name == "cmake-options":
                    changed_leaf = workflow.CMakeDependencyBuildSpec(
                        leaf.dependency_name,
                        leaf.uses_c_language,
                        ("-DLEAF_MODE=OFF",),
                    )
                elif change_name == "source-subdir":
                    changed_leaf = workflow.CMakeDependencyBuildSpec(
                        leaf.dependency_name,
                        leaf.uses_c_language,
                        leaf.cmake_options,
                        source_subdir="native",
                    )
                else:
                    changed_leaf = workflow.CMakeDependencyBuildSpec(
                        leaf.dependency_name,
                        True,
                        leaf.cmake_options,
                        uses_cxx_language=False,
                    )
                changed_specs = (changed_leaf, *original_specs[1:])
                changed_map = {spec.dependency_name: spec for spec in changed_specs}
                with (
                    mock.patch.object(
                        workflow,
                        "CMAKE_DEPENDENCY_BUILD_ORDER",
                        changed_specs,
                    ),
                    mock.patch.object(
                        workflow,
                        "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
                        changed_map,
                    ),
                ):
                    self.assertEqual(
                        workflow.dependency_rebuild_names(
                            repo_root,
                            context(),
                            dependency_roots,
                        ),
                        {"LibLeaf", "LibParent", "SampleApp"},
                    )

        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            commits = {spec.dependency_name: spec.dependency_name * 4 for spec in original_specs}
            dependency_roots = SimpleNamespace(
                mode="pinned",
                closure_order=tuple(spec.dependency_name for spec in original_specs),
                resolved_commits=commits,
                dependency_names_by_parent=edges,
                dependency_parent_names_by_name=parents,
                dependency_root_for=lambda name: repo_root / "sources" / name,
            )
            spec_map = {spec.dependency_name: spec for spec in original_specs}
            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", original_specs),
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME", spec_map),
            ):
                for spec in original_specs:
                    workflow.dependency_install_prefix_for_name(
                        repo_root,
                        context().preset_name,
                        spec.dependency_name,
                    ).mkdir(parents=True)
                workflow.write_dependency_state_file(repo_root, context(), dependency_roots)
                self.assertEqual(
                    workflow.dependency_rebuild_names(
                        repo_root,
                        context(compiler="clang++-18"),
                        dependency_roots,
                    ),
                    {spec.dependency_name for spec in original_specs},
                )

                changed_roots = SimpleNamespace(
                    **{
                        **dependency_roots.__dict__,
                        "resolved_commits": {**commits, "LibLeaf": "f" * 40},
                    }
                )
                self.assertEqual(
                    workflow.dependency_rebuild_names(
                        repo_root,
                        context(),
                        changed_roots,
                    ),
                    {"LibLeaf", "LibParent", "SampleApp"},
                )

    def test_dependency_build_reuses_unchanged_independent_install(self) -> None:
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
            original_specs = (
                workflow.CMakeDependencyBuildSpec("LibLeaf", False, ("-DLEAF_MODE=ON",)),
                workflow.CMakeDependencyBuildSpec("LibIndependent", False, ()),
                workflow.CMakeDependencyBuildSpec("LibParent", False, ()),
            )
            edges = {
                "LibLeaf": (),
                "LibIndependent": (),
                "LibParent": ("LibLeaf",),
            }
            dependency_roots = SimpleNamespace(
                mode="pinned",
                closure_order=tuple(spec.dependency_name for spec in original_specs),
                resolved_commits={spec.dependency_name: "a" * 40 for spec in original_specs},
                dependency_names_by_parent=edges,
                dependency_parent_names_by_name={"LibLeaf": ("LibParent",)},
                dependency_root_for=lambda name: repo_root / "sources" / name,
            )
            original_map = {spec.dependency_name: spec for spec in original_specs}
            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", original_specs),
                mock.patch.object(
                    workflow,
                    "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
                    original_map,
                ),
            ):
                for spec in original_specs:
                    install_prefix = workflow.dependency_install_prefix_for_name(
                        repo_root,
                        context.preset_name,
                        spec.dependency_name,
                    )
                    install_prefix.mkdir(parents=True)
                    (install_prefix / "installed.txt").write_text(
                        spec.dependency_name,
                        encoding="utf-8",
                    )
                workflow.write_dependency_state_file(repo_root, context, dependency_roots)

            changed_specs = (
                workflow.CMakeDependencyBuildSpec("LibLeaf", False, ("-DLEAF_MODE=OFF",)),
                *original_specs[1:],
            )
            changed_map = {spec.dependency_name: spec for spec in changed_specs}
            configured: list[tuple[str, tuple[Path, ...]]] = []
            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", changed_specs),
                mock.patch.object(
                    workflow,
                    "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
                    changed_map,
                ),
                mock.patch.object(
                    workflow,
                    "require_dependency_roots",
                    return_value=dependency_roots,
                ),
                mock.patch.object(
                    workflow,
                    "configure_dependency_for_context",
                    side_effect=lambda **kwargs: configured.append(
                        (
                            kwargs["dependency_name"],
                            tuple(kwargs["dependency_prefixes"]),
                        )
                    ),
                ),
            ):
                workflow.build_dependencies_for_cmake_context(context, repo_root=repo_root)

            leaf_prefix = workflow.dependency_install_prefix_for_name(
                repo_root,
                context.preset_name,
                "LibLeaf",
            )
            self.assertEqual(
                configured,
                [("LibLeaf", ()), ("LibParent", (leaf_prefix,))],
            )
            independent_marker = (
                workflow.dependency_install_prefix_for_name(
                    repo_root,
                    context.preset_name,
                    "LibIndependent",
                )
                / "installed.txt"
            )
            self.assertEqual(independent_marker.read_text(encoding="utf-8"), "LibIndependent")

            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", changed_specs),
                mock.patch.object(
                    workflow,
                    "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
                    changed_map,
                ),
                mock.patch.object(
                    workflow,
                    "require_dependency_roots",
                    return_value=dependency_roots,
                ),
                mock.patch.object(
                    workflow,
                    "configure_dependency_for_context",
                ) as configure,
                mock.patch.object(workflow, "remove_path") as remove_path,
                mock.patch.object(
                    workflow,
                    "_write_dependency_receipts",
                ) as write_receipts,
            ):
                workflow.build_dependencies_for_cmake_context(context, repo_root=repo_root)

            configure.assert_not_called()
            remove_path.assert_not_called()
            write_receipts.assert_not_called()

    def test_dependency_rebuild_selection_handles_diamond_graph(self) -> None:
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
            specs = tuple(
                workflow.CMakeDependencyBuildSpec(name, False, ())
                for name in ("LibD", "LibB", "LibC", "SampleApp")
            )
            commits = {spec.dependency_name: "a" * 40 for spec in specs}
            dependency_roots = SimpleNamespace(
                mode="pinned",
                repo_root=repo_root,
                closure_order=tuple(spec.dependency_name for spec in specs),
                resolved_commits=commits,
                dependency_names_by_parent={
                    "LibD": (),
                    "LibB": ("LibD",),
                    "LibC": ("LibD",),
                    "SampleApp": ("LibB", "LibC"),
                },
                dependency_parent_names_by_name={
                    "LibD": ("LibB", "LibC"),
                    "LibB": ("SampleApp",),
                    "LibC": ("SampleApp",),
                },
                dependency_root_for=lambda name: repo_root / "sources" / name,
            )
            spec_map = {spec.dependency_name: spec for spec in specs}
            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", specs),
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME", spec_map),
            ):
                for spec in specs:
                    workflow.dependency_install_prefix_for_name(
                        repo_root,
                        context.preset_name,
                        spec.dependency_name,
                    ).mkdir(parents=True)
                workflow.write_dependency_state_file(repo_root, context, dependency_roots)

                changed_d = SimpleNamespace(
                    **{
                        **dependency_roots.__dict__,
                        "resolved_commits": {**commits, "LibD": "d" * 40},
                    }
                )
                self.assertEqual(
                    workflow.dependency_rebuild_names(repo_root, context, changed_d),
                    {"LibD", "LibB", "LibC", "SampleApp"},
                )

                changed_b = SimpleNamespace(
                    **{
                        **dependency_roots.__dict__,
                        "resolved_commits": {**commits, "LibB": "b" * 40},
                    }
                )
                self.assertEqual(
                    workflow.dependency_rebuild_names(repo_root, context, changed_b),
                    {"LibB", "SampleApp"},
                )

                workflow.remove_path(
                    workflow.dependency_install_prefix_for_name(
                        repo_root,
                        context.preset_name,
                        "LibD",
                    )
                )
                self.assertEqual(
                    workflow.dependency_rebuild_names(
                        repo_root,
                        context,
                        dependency_roots,
                    ),
                    {"LibD", "LibB", "LibC", "SampleApp"},
                )

    def test_legacy_dependency_state_is_a_one_time_full_miss(self) -> None:
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
            specs = tuple(
                workflow.CMakeDependencyBuildSpec(name, False, ()) for name in ("LibA", "SampleApp")
            )
            dependency_roots = SimpleNamespace(
                mode="pinned",
                repo_root=repo_root,
                closure_order=("LibA", "SampleApp"),
                resolved_commits={"LibA": "a" * 40, "SampleApp": "b" * 40},
                dependency_names_by_parent={"LibA": (), "SampleApp": ("LibA",)},
                dependency_parent_names_by_name={"LibA": ("SampleApp",)},
                dependency_root_for=lambda name: repo_root / "sources" / name,
            )
            state_path = workflow.dependency_state_file_path(repo_root, context.preset_name)
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "mode": "pinned",
                        "roots": {"LibA": "/legacy/LibA"},
                        "resolved": {"LibA": "a" * 40},
                    }
                ),
                encoding="utf-8",
            )
            spec_map = {spec.dependency_name: spec for spec in specs}
            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", specs),
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME", spec_map),
            ):
                for spec in specs:
                    workflow.dependency_install_prefix_for_name(
                        repo_root,
                        context.preset_name,
                        spec.dependency_name,
                    ).mkdir(parents=True)
                self.assertEqual(
                    workflow.dependency_rebuild_names(
                        repo_root,
                        context,
                        dependency_roots,
                    ),
                    {"LibA", "SampleApp"},
                )

    def test_dependency_receipts_preserve_successful_lower_build_after_failure(self) -> None:
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
            specs = tuple(
                workflow.CMakeDependencyBuildSpec(name, False, ())
                for name in ("LibLeaf", "LibParent", "SampleApp")
            )
            dependency_roots = SimpleNamespace(
                mode="pinned",
                repo_root=repo_root,
                closure_order=tuple(spec.dependency_name for spec in specs),
                resolved_commits={spec.dependency_name: "a" * 40 for spec in specs},
                dependency_names_by_parent={
                    "LibLeaf": (),
                    "LibParent": ("LibLeaf",),
                    "SampleApp": ("LibParent",),
                },
                dependency_parent_names_by_name={
                    "LibLeaf": ("LibParent",),
                    "LibParent": ("SampleApp",),
                },
                dependency_root_for=lambda name: repo_root / "sources" / name,
            )
            spec_map = {spec.dependency_name: spec for spec in specs}
            first_calls: list[str] = []

            def fail_parent(**kwargs: object) -> None:
                dependency_name = str(kwargs["dependency_name"])
                first_calls.append(dependency_name)
                if dependency_name == "LibParent":
                    raise RuntimeError("parent build failed")

            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", specs),
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME", spec_map),
                mock.patch.object(
                    workflow,
                    "require_dependency_roots",
                    return_value=dependency_roots,
                ),
                mock.patch.object(
                    workflow,
                    "configure_dependency_for_context",
                    side_effect=fail_parent,
                ),
                self.assertRaisesRegex(RuntimeError, "parent build failed"),
            ):
                workflow.build_dependencies_for_cmake_context(context, repo_root=repo_root)

            self.assertEqual(first_calls, ["LibLeaf", "LibParent"])
            state_path = workflow.dependency_state_file_path(repo_root, context.preset_name)
            partial_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(tuple(partial_state["dependencies"]), ("LibLeaf",))

            retry_calls: list[str] = []
            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", specs),
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME", spec_map),
                mock.patch.object(
                    workflow,
                    "require_dependency_roots",
                    return_value=dependency_roots,
                ),
                mock.patch.object(
                    workflow,
                    "configure_dependency_for_context",
                    side_effect=lambda **kwargs: retry_calls.append(kwargs["dependency_name"]),
                ),
            ):
                workflow.build_dependencies_for_cmake_context(context, repo_root=repo_root)

            self.assertEqual(retry_calls, ["LibParent", "SampleApp"])

    def test_dependency_rebuild_selection_scopes_manual_and_language_inputs(self) -> None:
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
                cache_variables={
                    "CMAKE_BUILD_TYPE": "Release",
                    "CMAKE_C_COMPILER": "clang",
                    "CMAKE_C_COMPILER_LAUNCHER": "sccache",
                    "CMAKE_CXX_COMPILER": "clang++",
                    "CMAKE_CXX_COMPILER_LAUNCHER": "ccache",
                },
            )
            specs = (
                workflow.CMakeDependencyBuildSpec(
                    "LibManual",
                    True,
                    (),
                    uses_cxx_language=False,
                ),
                workflow.CMakeDependencyBuildSpec("LibPinned", False, ()),
                workflow.CMakeDependencyBuildSpec("SampleApp", False, ()),
            )
            dependency_roots = SimpleNamespace(
                mode="manual",
                repo_root=repo_root,
                closure_order=tuple(spec.dependency_name for spec in specs),
                resolved_commits={spec.dependency_name: "a" * 40 for spec in specs},
                dependency_names_by_parent={
                    "LibManual": (),
                    "LibPinned": (),
                    "SampleApp": ("LibManual",),
                },
                dependency_parent_names_by_name={"LibManual": ("SampleApp",)},
                dependency_root_for=lambda name: repo_root / "sources" / name,
                uses_manual_root_override_for=lambda name: name == "LibManual",
            )
            spec_map = {spec.dependency_name: spec for spec in specs}
            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", specs),
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME", spec_map),
            ):
                for spec in specs:
                    workflow.dependency_install_prefix_for_name(
                        repo_root,
                        context.preset_name,
                        spec.dependency_name,
                    ).mkdir(parents=True)
                workflow.write_dependency_state_file(repo_root, context, dependency_roots)
                self.assertEqual(
                    workflow.dependency_rebuild_names(
                        repo_root,
                        context,
                        dependency_roots,
                    ),
                    {"LibManual", "SampleApp"},
                )

                pinned_roots = SimpleNamespace(
                    **{
                        **dependency_roots.__dict__,
                        "mode": "pinned",
                        "uses_manual_root_override_for": lambda _name: False,
                    }
                )
                workflow.write_dependency_state_file(repo_root, context, pinned_roots)
                cxx_context = workflow.CMakeDependencyBuildContext(
                    **{
                        **context.__dict__,
                        "cache_variables": {
                            **context.cache_variables,
                            "CMAKE_CXX_COMPILER": "clang++-18",
                            "CMAKE_CXX_COMPILER_LAUNCHER": "sccache",
                        },
                    }
                )
                self.assertEqual(
                    workflow.dependency_rebuild_names(
                        repo_root,
                        cxx_context,
                        pinned_roots,
                    ),
                    {"LibPinned", "SampleApp"},
                )

    def test_dependency_state_is_invalidated_before_cleanup(self) -> None:
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
            spec = workflow.CMakeDependencyBuildSpec("LibA", False, ())
            dependency_roots = SimpleNamespace(
                mode="pinned",
                repo_root=repo_root,
                closure_order=("LibA",),
                resolved_commits={"LibA": "a" * 40},
                dependency_names_by_parent={"LibA": ()},
                dependency_parent_names_by_name={},
                dependency_root_for=lambda name: repo_root / "sources" / name,
            )
            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", (spec,)),
                mock.patch.object(
                    workflow,
                    "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
                    {"LibA": spec},
                ),
                mock.patch.object(
                    workflow,
                    "require_dependency_roots",
                    return_value=dependency_roots,
                ),
                mock.patch.object(
                    workflow,
                    "_write_dependency_receipts",
                    side_effect=OSError("state write failed"),
                ),
                mock.patch.object(workflow, "remove_path") as remove_path,
                self.assertRaisesRegex(OSError, "state write failed"),
            ):
                workflow.build_dependencies_for_cmake_context(context, repo_root=repo_root)

            remove_path.assert_not_called()

    def test_dependency_receipt_write_failure_forces_safe_retry(self) -> None:
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
            spec = workflow.CMakeDependencyBuildSpec("LibA", False, ())
            dependency_roots = SimpleNamespace(
                mode="pinned",
                repo_root=repo_root,
                closure_order=("LibA",),
                resolved_commits={"LibA": "a" * 40},
                dependency_names_by_parent={"LibA": ()},
                dependency_parent_names_by_name={},
                dependency_root_for=lambda name: repo_root / "sources" / name,
            )
            original_write_receipts = workflow._write_dependency_receipts
            write_count = 0

            def fail_second_write(*args: object, **kwargs: object) -> None:
                nonlocal write_count
                write_count += 1
                if write_count == 2:
                    raise OSError("receipt write failed")
                original_write_receipts(*args, **kwargs)

            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", (spec,)),
                mock.patch.object(
                    workflow,
                    "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
                    {"LibA": spec},
                ),
                mock.patch.object(
                    workflow,
                    "require_dependency_roots",
                    return_value=dependency_roots,
                ),
                mock.patch.object(workflow, "configure_dependency_for_context"),
                mock.patch.object(
                    workflow,
                    "_write_dependency_receipts",
                    side_effect=fail_second_write,
                ),
                self.assertRaisesRegex(OSError, "receipt write failed"),
            ):
                workflow.build_dependencies_for_cmake_context(context, repo_root=repo_root)

            state_path = workflow.dependency_state_file_path(repo_root, context.preset_name)
            failed_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(failed_state["dependencies"], {})

            retry_calls: list[str] = []
            with (
                mock.patch.object(workflow, "CMAKE_DEPENDENCY_BUILD_ORDER", (spec,)),
                mock.patch.object(
                    workflow,
                    "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
                    {"LibA": spec},
                ),
                mock.patch.object(
                    workflow,
                    "require_dependency_roots",
                    return_value=dependency_roots,
                ),
                mock.patch.object(
                    workflow,
                    "configure_dependency_for_context",
                    side_effect=lambda **kwargs: retry_calls.append(kwargs["dependency_name"]),
                ),
            ):
                workflow.build_dependencies_for_cmake_context(context, repo_root=repo_root)

            self.assertEqual(retry_calls, ["LibA"])

    def test_dependency_build_holds_shared_workspace_lock(self) -> None:
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
                cache_variables={},
            )
            lock_active = False

            @contextmanager
            def shared_lock(path: Path):
                nonlocal lock_active
                self.assertEqual(path, repo_root.resolve())
                lock_active = True
                try:
                    yield
                finally:
                    lock_active = False

            def build(_: object, *, repo_root: Path) -> None:
                self.assertTrue(lock_active)
                self.assertEqual(repo_root, Path(tempdir).resolve())

            with (
                mock.patch.object(
                    workflow,
                    "workspace_mutation_lock",
                    side_effect=shared_lock,
                ),
                mock.patch.object(
                    workflow,
                    "_build_dependencies_for_cmake_context_unlocked",
                    side_effect=build,
                ),
            ):
                workflow.build_dependencies_for_cmake_context(context, repo_root=repo_root)

            self.assertFalse(lock_active)

    def test_bound_dependency_build_defaults_to_bound_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo_root = Path(tempdir)
            module_globals = cmake_binding_namespace(repo_root)
            context = workflow.CMakeDependencyBuildContext(
                preset_name="linux_clang_release",
                generator="Ninja",
                generator_platform="",
                generator_toolset="",
                cmake_executable="cmake",
                build_configurations=("Release",),
                external_prefix_path="",
                cache_variables={},
            )
            observed: list[tuple[str, Path]] = []

            @contextmanager
            def shared_lock(path: Path):
                observed.append(("lock", path))
                yield

            module_globals["workspace_mutation_lock"] = shared_lock
            script = workflow.bind_cmake_workflow_script(
                module_globals,
                repo_root=repo_root,
                repo_display_name="SampleApp",
                dependency_build_order=(),
                dependency_state_filename=".sample_dependency_state.json",
            )

            def build(_: object, *, repo_root: Path) -> None:
                observed.append(("build", repo_root))
                self.assertEqual(
                    script.context.builder.config.state_filename,
                    ".sample_dependency_state.json",
                )

            with mock.patch.object(
                script,
                "_build_dependencies_for_cmake_context_unlocked",
                side_effect=build,
            ):
                module_globals["build_dependencies_for_cmake_context"](context)

            self.assertEqual(
                observed,
                [("lock", repo_root.resolve()), ("build", repo_root.resolve())],
            )

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
            namespace = cmake_binding_namespace(repo_root)
            namespace["_prepare_seed_repository_closure_unlocked"] = (
                lambda *_, **__: SimpleNamespace(topo_order=("LibA",))
            )
            script = workflow.bind_cmake_workflow_script(
                namespace,
                repo_root=repo_root,
                repo_display_name="SampleApp",
                dependency_build_order=(),
            )
            with redirect_stdout(stdout):
                result = script.cmd_init()

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
