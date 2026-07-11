from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from repomgrcpp import cmake_preset_context, cmake_workflow


class CMakePresetContextTests(unittest.TestCase):
    def test_workflow_facade_reexports_preset_context_api(self) -> None:
        exported_names = (
            "CMakeDependencyBuildContext",
            "configure_presets",
            "find_configure_preset",
            "cmake_executable_for_preset",
            "resolve_preset_string",
            "resolve_generator",
            "preset_environment",
            "build_dir_for_preset",
            "dependency_build_dir",
            "dependency_install_prefix",
            "build_dir_for_preset_name",
            "dependency_build_dir_for_name",
            "dependency_install_prefix_for_name",
            "multi_config_generator",
            "preset_generator_args",
            "forwarded_cache_args",
            "external_prefix_path",
            "combined_prefix_path",
            "single_config_build_type",
            "build_configurations_for_preset",
            "load_cmake_dependency_build_context",
        )

        for name in exported_names:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(cmake_workflow, name),
                    getattr(cmake_preset_context, name),
                )

    def test_preset_context_module_has_no_host_workflow_state(self) -> None:
        for name in (
            "REPO_ROOT",
            "REPO_DISPLAY_NAME",
            "CMAKE_DEPENDENCY_BUILD_ORDER",
            "CMAKE_DEPENDENCY_BUILD_SPEC_BY_NAME",
            "DEPENDENCY_STATE_FILENAME",
            "require_dependency_roots",
            "materialize_dependency_roots",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(cmake_preset_context, name))

    def test_load_context_normalizes_empty_build_configurations(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            context_path = Path(tempdir) / "context.json"
            context_path.write_text(
                json.dumps(
                    {
                        "presetName": "sample_release",
                        "generator": "Ninja",
                        "buildConfigurations": ["", "  "],
                        "cacheVariables": {"SAMPLE_FEATURE": "ON"},
                    }
                ),
                encoding="utf-8",
            )

            context = cmake_preset_context.load_cmake_dependency_build_context(context_path)

            self.assertEqual(context.preset_name, "sample_release")
            self.assertEqual(context.build_configurations, ("Release",))
            self.assertEqual(context.cache_variables, {"SAMPLE_FEATURE": "ON"})
