import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CMAKE_DIR = REPO_ROOT / "repomgrcpp" / "cmake"


class CMakeToolsTests(unittest.TestCase):
    def test_reusable_cmake_modules_are_packaged_data(self):
        expected_modules = {
            "CppKitAddExecutable.cmake",
            "CppKitBundleResources.cmake",
            "CppKitCompilerFlags.cmake",
            "CppKitCoverage.cmake",
            "CppKitDeployQt.cmake",
            "CppKitDoxygen.cmake",
            "CppKitHeaderExport.cmake",
            "CppKitMemcheck.cmake",
            "CppKitPackage.cmake",
            "CppKitRunMemcheck.cmake",
            "CppKitRust.cmake",
            "CppKitThirdPartyChecks.cmake",
            "debug_pkg_config.cmake",
        }

        actual_modules = {path.name for path in CMAKE_DIR.glob("*.cmake")}
        self.assertTrue(expected_modules.issubset(actual_modules))

        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(
            'repomgrcpp = ["cmake_presets/*.in", "cmake/*.cmake", "cmake/*.json.in", "clangd/*.in"]',
            pyproject,
        )

    def test_modules_use_cppkit_namespace_not_downstream_product_project_names(self):
        banned_terms = [
            "DownstreamProduct",
            "downstream_product",
            "poly_add_executable",
            "cavalier_contours_ffi",
            "SourceCode",
        ]

        for module in CMAKE_DIR.glob("CppKit*.cmake"):
            text = module.read_text(encoding="utf-8")
            for term in banned_terms:
                self.assertNotIn(term, text, f"{term!r} leaked into {module.name}")

    def test_cmake_modules_include_cleanly(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        modules = [
            "CppKitAddExecutable.cmake",
            "CppKitBundleResources.cmake",
            "CppKitCompilerFlags.cmake",
            "CppKitCoverage.cmake",
            "CppKitDeployQt.cmake",
            "CppKitDoxygen.cmake",
            "CppKitHeaderExport.cmake",
            "CppKitMemcheck.cmake",
            "CppKitPackage.cmake",
            "CppKitRust.cmake",
            "CppKitThirdPartyChecks.cmake",
        ]
        include_lines = "\n".join(f'include("{(CMAKE_DIR / name).as_posix()}")' for name in modules)

        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "include_modules.cmake"
            script.write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                f"{include_lines}\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [cmake, "-P", str(script)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_third_party_header_check_accepts_existing_header(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "dep"
            include_dir = root / "include"
            include_dir.mkdir(parents=True)
            (include_dir / "demo.h").write_text("#pragma once\n", encoding="utf-8")

            script = Path(temp_dir) / "check_header.cmake"
            script.write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                f'include("{(CMAKE_DIR / "CppKitThirdPartyChecks.cmake").as_posix()}")\n'
                f'cppkit_assert_dependency_header("Demo" "{root.as_posix()}" "include/demo.h")\n',
                encoding="utf-8",
            )

            result = subprocess.run(
                [cmake, "-P", str(script)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_third_party_header_check_rejects_missing_header(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "dep"
            root.mkdir()

            script = Path(temp_dir) / "check_missing_header.cmake"
            script.write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                f'include("{(CMAKE_DIR / "CppKitThirdPartyChecks.cmake").as_posix()}")\n'
                f'cppkit_assert_dependency_header("Demo" "{root.as_posix()}" "include/missing.h")\n',
                encoding="utf-8",
            )

            result = subprocess.run(
                [cmake, "-P", str(script)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing required header", result.stdout + result.stderr)

    def test_repomgrcpp_cmake_bootstrap_resources_include_cleanly(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        self.assertTrue((CMAKE_DIR / "DependencyBootstrap.cmake").is_file())
        self.assertTrue((CMAKE_DIR / "DependencyBuildContext.json.in").is_file())
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"cmake/*.cmake"', pyproject)
        self.assertIn('"cmake/*.json.in"', pyproject)

        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "include_bootstrap.cmake"
            script.write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                f'include("{(CMAKE_DIR / "DependencyBootstrap.cmake").as_posix()}")\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [cmake, "-P", str(script)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cppkit_dependency_bootstrap_collects_external_prefix_path(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        bootstrap = (REPO_ROOT / "repomgrcpp" / "cmake" / "DependencyBootstrap.cmake").resolve().as_posix()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            build_dir = root / "build" / "mac_clang_release"
            managed_root = build_dir / "dependency_installs"
            (managed_root / "LibA").mkdir(parents=True)
            (managed_root / "LibB").mkdir(parents=True)

            script = root / "refresh_prefix.cmake"
            script.write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                f'include("{bootstrap}")\n'
                f'set(CMAKE_PREFIX_PATH "/deps/system")\n'
                f'set(CMAKE_SOURCE_DIR "{root.as_posix()}")\n'
                f'set(CMAKE_BINARY_DIR "{build_dir.as_posix()}")\n'
                'cppkit_collect_external_prefix_path(result "mac_clang_release")\n'
                'message(STATUS "prefix=${result}")\n',
                encoding="utf-8",
            )

            result = subprocess.run(
                [cmake, "-P", str(script)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout.strip().split("prefix=", 1)[-1],
            "/deps/system",
        )

    def test_package_module_json_string_array_escapes_list_values(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "array.txt"
            script = Path(temp_dir) / "json_array.cmake"
            script.write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                f'include("{(CMAKE_DIR / "CppKitPackage.cmake").as_posix()}")\n'
                'set(values "alpha" "with \\" quote" "back\\\\slash")\n'
                "cppkit_json_string_array(result values)\n"
                f'file(WRITE "{output.as_posix()}" "${{result}}")\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [cmake, "-P", str(script)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            actual = output.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(actual, '["alpha", "with \\" quote", "back\\\\slash"]')

    def test_compiler_flags_target_api_applies_target_scoped_flags(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        compiler_flags = (CMAKE_DIR / "CppKitCompilerFlags.cmake").resolve().as_posix()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "project"
            build_dir = root / "build"
            project_dir.mkdir()
            (project_dir / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
            (project_dir / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(TargetFlags LANGUAGES CXX)\n"
                f'include("{compiler_flags}")\n'
                "add_executable(app main.cpp)\n"
                "cppkit_apply_common_compile_flags_to_target(app EIGEN_MAX_ALIGN_BYTES 64)\n"
                'get_target_property(_definitions app COMPILE_DEFINITIONS)\n'
                'get_target_property(_options app COMPILE_OPTIONS)\n'
                'message(STATUS "defs=${_definitions}")\n'
                'message(STATUS "opts=${_options}")\n'
                'if(NOT _definitions MATCHES "EIGEN_MAX_ALIGN_BYTES=64")\n'
                '    message(FATAL_ERROR "missing target definition")\n'
                "endif()\n"
                'if(NOT _options MATCHES "-Wall")\n'
                '    message(FATAL_ERROR "missing target compile option")\n'
                "endif()\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [cmake, "-S", str(project_dir), "-B", str(build_dir)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_compiler_flags_target_api_preserves_msvc_embedded_debug_option(self):
        text = (CMAKE_DIR / "CppKitCompilerFlags.cmake").read_text(encoding="utf-8")

        self.assertIn("MSVC_EMBEDDED_DEBUG_INFO", text)
        self.assertIn("cppkit_apply_msvc_embedded_debug_info_to_target", text)
        self.assertIn('PROPERTY MSVC_DEBUG_INFORMATION_FORMAT "Embedded"', text)


if __name__ == "__main__":
    unittest.main()
