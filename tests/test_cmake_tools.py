import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CMAKE_DIR = REPO_ROOT / "repomgrcpp" / "cmake"


class CMakeToolsTests(unittest.TestCase):
    def _run_native_coverage_project(self, compiler_family: str) -> None:
        enforce = os.environ.get("FREECM_RUN_NATIVE_COVERAGE_TESTS") == "1"
        if not sys.platform.startswith("linux"):
            if enforce:
                self.fail("native CMake coverage tests require Linux")
            self.skipTest("native CMake coverage tests run on Linux")

        required_tools = ["cmake"]
        cmake_args = []
        if compiler_family == "GNU":
            required_tools.extend(["g++", "lcov", "genhtml"])
            compiler = shutil.which("g++")
        else:
            required_tools.extend(["clang++", "llvm-cov", "llvm-profdata"])
            compiler = shutil.which("clang++")
            cmake_args.extend(
                [
                    f"-DLLVM_COV_PATH={shutil.which('llvm-cov') or ''}",
                    f"-DLLVM_PROFDATA_PATH={shutil.which('llvm-profdata') or ''}",
                ]
            )

        missing = [tool for tool in required_tools if not shutil.which(tool)]
        if missing:
            message = f"native {compiler_family} coverage tools missing: {', '.join(missing)}"
            if enforce:
                self.fail(message)
            self.skipTest(message)

        add_executable = (CMAKE_DIR / "CppKitAddExecutable.cmake").resolve().as_posix()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "project"
            build_dir = root / "build"
            (project_dir / "one").mkdir(parents=True)
            (project_dir / "two").mkdir()
            (project_dir / "main.cpp").write_text(
                "int first(bool); int second(bool);\n"
                "int main() { return first(true) + second(false) == 3 ? 0 : 1; }\n",
                encoding="utf-8",
            )
            (project_dir / "one/shared.cpp").write_text(
                "int first(bool value) { return value ? 1 : 0; }\n",
                encoding="utf-8",
            )
            (project_dir / "two/shared.cpp").write_text(
                "int second(bool value) { return value ? 0 : 2; }\n",
                encoding="utf-8",
            )
            (project_dir / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(NativeCoverage LANGUAGES CXX)\n"
                "set(CPPKIT_ADD_COVERAGE ON)\n"
                f'include("{add_executable}")\n'
                "cppkit_add_executable(coverage_test\n"
                "    main.cpp one/shared.cpp two/shared.cpp\n"
                "    IS_TEST\n"
                '    COVERAGE_FILES "${CMAKE_CURRENT_SOURCE_DIR}/one/shared.cpp"\n'
                '                   "${CMAKE_CURRENT_SOURCE_DIR}/two/shared.cpp"\n'
                ")\n",
                encoding="utf-8",
            )

            configure = subprocess.run(
                [
                    shutil.which("cmake") or "cmake",
                    "-S",
                    str(project_dir),
                    "-B",
                    str(build_dir),
                    f"-DCMAKE_CXX_COMPILER={compiler}",
                    *cmake_args,
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(configure.returncode, 0, configure.stdout + configure.stderr)
            build = subprocess.run(
                [
                    shutil.which("cmake") or "cmake",
                    "--build",
                    str(build_dir),
                    "--target",
                    "Coverage_coverage_test",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            self.assertTrue((build_dir / "coverage_test").is_file())
            self.assertTrue((build_dir / "Coverage_coverage_test/index.html").is_file())

    @staticmethod
    def _write_fake_cargo(root: Path, counter: Path) -> Path:
        if os.name == "nt":
            script = root / "fake-cargo.cmd"
            script.write_text(
                "@echo off\r\n"
                'if not exist "%CARGO_TARGET_DIR%\\release" mkdir "%CARGO_TARGET_DIR%\\release"\r\n'
                'type nul > "%CARGO_TARGET_DIR%\\release\\demo.lib"\r\n'
                f'echo build>>"{counter}"\r\n',
                encoding="utf-8",
            )
            return script

        script = root / "fake-cargo"
        script.write_text(
            "#!/bin/sh\n"
            'mkdir -p "$CARGO_TARGET_DIR/release"\n'
            ': > "$CARGO_TARGET_DIR/release/libdemo.a"\n'
            f'echo build >> "{counter}"\n',
            encoding="utf-8",
        )
        script.chmod(0o755)
        return script

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
                "cmake_minimum_required(VERSION 3.20)\n" f"{include_lines}\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [cmake, "-P", str(script)],
                check=False,
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_header_exports_build_valid_flat_and_tree_layouts(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "project"
            source_dir = project_dir / "headers"
            (source_dir / "public").mkdir(parents=True)
            (source_dir / "nested").mkdir()
            (source_dir / "public/Alpha.hpp").write_text("alpha\n", encoding="utf-8")
            (source_dir / "nested/Beta.hpp").write_text("beta\n", encoding="utf-8")
            (source_dir / "public/Shared.hpp").write_text("public\n", encoding="utf-8")
            (source_dir / "nested/Shared.hpp").write_text("nested\n", encoding="utf-8")
            flat_dir = root / "flat"
            tree_dir = root / "tree"
            build_dir = root / "build"
            (project_dir / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(HeaderExports LANGUAGES NONE)\n"
                f'include("{(CMAKE_DIR / "CppKitHeaderExport.cmake").as_posix()}")\n'
                f'cppkit_export_headers_flat(flat "{source_dir.as_posix()}" '
                f'"{flat_dir.as_posix()}" "public/Alpha.hpp" "nested/Beta.hpp")\n'
                f'cppkit_export_headers_tree(tree "{source_dir.as_posix()}" '
                f'"{tree_dir.as_posix()}" "public/Alpha.hpp" "nested/Beta.hpp" '
                '"public/Shared.hpp" "nested/Shared.hpp")\n',
                encoding="utf-8",
            )

            configure = subprocess.run(
                [cmake, "-S", str(project_dir), "-B", str(build_dir)],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(configure.returncode, 0, configure.stdout + configure.stderr)
            build = subprocess.run(
                [cmake, "--build", str(build_dir), "--target", "flat", "tree"],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            self.assertEqual((flat_dir / "Alpha.hpp").read_text(encoding="utf-8"), "alpha\n")
            self.assertEqual((flat_dir / "Beta.hpp").read_text(encoding="utf-8"), "beta\n")
            self.assertEqual(
                (tree_dir / "public/Alpha.hpp").read_text(encoding="utf-8"),
                "alpha\n",
            )
            self.assertEqual(
                (tree_dir / "nested/Beta.hpp").read_text(encoding="utf-8"),
                "beta\n",
            )
            self.assertEqual(
                (tree_dir / "public/Shared.hpp").read_text(encoding="utf-8"),
                "public\n",
            )
            self.assertEqual(
                (tree_dir / "nested/Shared.hpp").read_text(encoding="utf-8"),
                "nested\n",
            )

    def test_flat_header_export_rejects_duplicate_output_basenames(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "project"
            source_dir = project_dir / "headers"
            (source_dir / "LibA").mkdir(parents=True)
            (source_dir / "LibB").mkdir()
            (source_dir / "LibC").mkdir()
            first_header = source_dir / "LibA/Shared.hpp"
            second_header = source_dir / "LibB/Shared.hpp"
            third_header = source_dir / "LibC/Shared.hpp"
            first_header.write_text("first\n", encoding="utf-8")
            second_header.write_text("second\n", encoding="utf-8")
            third_header.write_text("third\n", encoding="utf-8")
            (project_dir / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(HeaderCollision LANGUAGES NONE)\n"
                f'include("{(CMAKE_DIR / "CppKitHeaderExport.cmake").as_posix()}")\n'
                f'cppkit_export_headers_flat(flat "{source_dir.as_posix()}" '
                f'"{(root / "flat").as_posix()}" "LibA/Shared.hpp" '
                '"LibB/Shared.hpp" "LibC/Shared.hpp")\n',
                encoding="utf-8",
            )

            configure = subprocess.run(
                [cmake, "-S", str(project_dir), "-B", str(root / "build")],
                check=False,
                text=True,
                capture_output=True,
            )

            output = configure.stdout + configure.stderr
            normalized_output = " ".join(output.split())
            self.assertNotEqual(configure.returncode, 0)
            self.assertIn(
                "multiple source headers map to the same output basename",
                normalized_output,
            )
            self.assertIn(first_header.as_posix(), output)
            self.assertIn(second_header.as_posix(), output)
            self.assertIn(third_header.as_posix(), output)

    def test_flat_header_export_reports_duplicate_source_separately(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "project"
            source_dir = project_dir / "headers"
            source_dir.mkdir(parents=True)
            source_header = source_dir / "Shared.hpp"
            source_header.write_text("shared\n", encoding="utf-8")
            (project_dir / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(HeaderDuplicate LANGUAGES NONE)\n"
                f'include("{(CMAKE_DIR / "CppKitHeaderExport.cmake").as_posix()}")\n'
                f'cppkit_export_headers_flat(flat "{source_dir.as_posix()}" '
                f'"{(root / "flat").as_posix()}" "Shared.hpp" "./Shared.hpp")\n',
                encoding="utf-8",
            )

            configure = subprocess.run(
                [cmake, "-S", str(project_dir), "-B", str(root / "build")],
                check=False,
                text=True,
                capture_output=True,
            )

            output = configure.stdout + configure.stderr
            normalized_output = " ".join(output.split())
            self.assertNotEqual(configure.returncode, 0)
            self.assertIn("same source header was passed more than once", normalized_output)
            self.assertNotIn("multiple source headers map", normalized_output)
            self.assertIn(source_header.as_posix(), output)

    def test_qt_deploy_tool_discovery_is_required_unless_explicitly_optional(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        deploy_module = (CMAKE_DIR / "CppKitDeployQt.cmake").as_posix()
        platforms = (
            ("windows", "WIN32", "windeployqt"),
            ("macos", "APPLE", "macdeployqt"),
            ("linux", "UNIX", "linuxdeployqt"),
        )
        for platform_name, platform_variable, tool_name in platforms:
            for optional in (False, True):
                with self.subTest(platform=platform_name, optional=optional):
                    with tempfile.TemporaryDirectory() as temp_dir:
                        root = Path(temp_dir)
                        project_dir = root / "project"
                        project_dir.mkdir()
                        optional_arg = " OPTIONAL_TOOL" if optional else ""
                        (project_dir / "CMakeLists.txt").write_text(
                            "cmake_minimum_required(VERSION 3.20)\n"
                            "project(DeployToolDiscovery LANGUAGES NONE)\n"
                            "set(CMAKE_FIND_USE_SYSTEM_ENVIRONMENT_PATH FALSE)\n"
                            "set(CMAKE_FIND_USE_CMAKE_SYSTEM_PATH FALSE)\n"
                            "set(WIN32 FALSE)\n"
                            "set(APPLE FALSE)\n"
                            "set(UNIX FALSE)\n"
                            f"set({platform_variable} TRUE)\n"
                            f'include("{deploy_module}")\n'
                            "add_custom_target(sample)\n"
                            f"cppkit_deploy_qt_dependencies(sample{optional_arg} "
                            f'QT_BIN_DIR "{(root / "missing-qt-bin").as_posix()}")\n',
                            encoding="utf-8",
                        )

                        configure = subprocess.run(
                            [cmake, "-S", str(project_dir), "-B", str(root / "build")],
                            check=False,
                            text=True,
                            capture_output=True,
                        )

                        output = configure.stdout + configure.stderr
                        normalized_output = " ".join(output.split())
                        self.assertIn(tool_name, output)
                        if optional:
                            self.assertEqual(configure.returncode, 0, output)
                            self.assertIn("skipped explicitly", normalized_output)
                            self.assertIn("OPTIONAL_TOOL", normalized_output)
                        else:
                            self.assertNotEqual(configure.returncode, 0, output)
                            self.assertIn("install the Qt deployment tool", normalized_output)

    def test_qt_optional_tool_does_not_mask_deploy_command_failure(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        if sys.platform.startswith("win"):
            tool_name = "windeployqt.exe"
        elif sys.platform == "darwin":
            tool_name = "macdeployqt"
        else:
            tool_name = "linuxdeployqt"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "project"
            qt_bin_dir = root / "qt" / "bin"
            project_dir.mkdir()
            qt_bin_dir.mkdir(parents=True)
            marker = root / "deploy-tool-ran.txt"
            fake_tool = qt_bin_dir / tool_name
            if os.name == "nt":
                helper_project = root / "deploy-tool-project"
                helper_project.mkdir()
                (helper_project / "main.c").write_text(
                    "#include <stdio.h>\n"
                    "int main(void) {\n"
                    f'    FILE *marker = fopen("{marker.as_posix()}", "wb");\n'
                    '    if (marker != NULL) { fputs("invoked", marker); fclose(marker); }\n'
                    "    return 23;\n"
                    "}\n",
                    encoding="utf-8",
                )
                (helper_project / "CMakeLists.txt").write_text(
                    "cmake_minimum_required(VERSION 3.20)\n"
                    "project(FakeWinDeployQt LANGUAGES C)\n"
                    f'set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "{qt_bin_dir.as_posix()}")\n'
                    f'set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_DEBUG "{qt_bin_dir.as_posix()}")\n'
                    f'set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_RELEASE "{qt_bin_dir.as_posix()}")\n'
                    f'set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO "{qt_bin_dir.as_posix()}")\n'
                    f'set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_MINSIZEREL "{qt_bin_dir.as_posix()}")\n'
                    "add_executable(windeployqt main.c)\n",
                    encoding="utf-8",
                )
                helper_build_dir = root / "deploy-tool-build"
                helper_configure = subprocess.run(
                    [cmake, "-S", str(helper_project), "-B", str(helper_build_dir)],
                    check=False,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(
                    helper_configure.returncode,
                    0,
                    helper_configure.stdout + helper_configure.stderr,
                )
                helper_build = subprocess.run(
                    [
                        cmake,
                        "--build",
                        str(helper_build_dir),
                        "--target",
                        "windeployqt",
                        "--config",
                        "Release",
                    ],
                    check=False,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(
                    helper_build.returncode,
                    0,
                    helper_build.stdout + helper_build.stderr,
                )
                self.assertTrue(fake_tool.is_file())
            else:
                fake_tool.write_text(
                    "#!/bin/sh\n" f'printf invoked > "{marker.as_posix()}"\n' "exit 23\n",
                    encoding="utf-8",
                )
                fake_tool.chmod(0o755)
            (project_dir / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            target_declaration = (
                "add_executable(sample MACOSX_BUNDLE main.c)"
                if sys.platform == "darwin"
                else "add_executable(sample main.c)"
            )
            (project_dir / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(DeployCommandFailure LANGUAGES C)\n"
                f"{target_declaration}\n"
                f'include("{(CMAKE_DIR / "CppKitDeployQt.cmake").as_posix()}")\n'
                f"cppkit_deploy_qt_dependencies(sample OPTIONAL_TOOL "
                f'QT_BIN_DIR "{qt_bin_dir.as_posix()}")\n',
                encoding="utf-8",
            )
            build_dir = root / "build"
            configure = subprocess.run(
                [cmake, "-S", str(project_dir), "-B", str(build_dir)],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(configure.returncode, 0, configure.stdout + configure.stderr)

            build = subprocess.run(
                [
                    cmake,
                    "--build",
                    str(build_dir),
                    "--target",
                    "Deploy_Qt_sample",
                    "--config",
                    "Release",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(build.returncode, 0, build.stdout + build.stderr)
            self.assertTrue(marker.is_file(), build.stdout + build.stderr)

    def test_rust_library_rebuilds_for_source_and_explicit_input_changes(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "project"
            crate_dir = project_dir / "crate"
            source_dir = crate_dir / "src"
            source_dir.mkdir(parents=True)
            (crate_dir / "Cargo.toml").write_text(
                '[package]\nname = "demo"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            rust_source = source_dir / "lib.rs"
            rust_source.write_text("pub fn value() -> i32 { 1 }\n", encoding="utf-8")
            explicit_input = crate_dir / "ffi-contract.txt"
            explicit_input.write_text("v1\n", encoding="utf-8")
            counter = root / "cargo-invocations.txt"
            fake_cargo = self._write_fake_cargo(root, counter)
            build_dir = root / "build"
            rust_module = (CMAKE_DIR / "CppKitRust.cmake").resolve().as_posix()
            (project_dir / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(RustInputTracking LANGUAGES NONE)\n"
                f'set(CARGO_EXECUTABLE "{fake_cargo.as_posix()}" CACHE FILEPATH "" FORCE)\n'
                f'set(RUSTC_EXECUTABLE "{fake_cargo.as_posix()}" CACHE FILEPATH "" FORCE)\n'
                f'include("{rust_module}")\n'
                "cppkit_build_rust_library(\n"
                "    NAME demo\n"
                f'    ROOT_DIR "{crate_dir.as_posix()}"\n'
                f'    TARGET_DIR "{(build_dir / "rust-target").as_posix()}"\n'
                f'    DEPENDS "{explicit_input.as_posix()}"\n'
                ")\n",
                encoding="utf-8",
            )

            configure = subprocess.run(
                [
                    cmake,
                    "-S",
                    str(project_dir),
                    "-B",
                    str(build_dir),
                    "-DCMAKE_BUILD_TYPE=Release",
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(configure.returncode, 0, configure.stdout + configure.stderr)
            self.assertFalse(counter.exists(), "Cargo must not run during configure")

            def build() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [cmake, "--build", str(build_dir), "--config", "Release"],
                    check=False,
                    text=True,
                    capture_output=True,
                )

            first_build = build()
            self.assertEqual(first_build.returncode, 0, first_build.stdout + first_build.stderr)
            self.assertEqual(counter.read_text(encoding="utf-8").splitlines(), ["build"])

            second_build = build()
            self.assertEqual(second_build.returncode, 0, second_build.stdout + second_build.stderr)
            self.assertEqual(counter.read_text(encoding="utf-8").splitlines(), ["build"])

            time.sleep(1.05)
            rust_source.write_text("pub fn value() -> i32 { 2 }\n", encoding="utf-8")
            source_build = build()
            self.assertEqual(source_build.returncode, 0, source_build.stdout + source_build.stderr)
            self.assertEqual(counter.read_text(encoding="utf-8").splitlines(), ["build", "build"])

            time.sleep(1.05)
            (crate_dir / "build.rs").write_text("fn main() {}\n", encoding="utf-8")
            optional_input_build = build()
            self.assertEqual(
                optional_input_build.returncode,
                0,
                optional_input_build.stdout + optional_input_build.stderr,
            )
            self.assertEqual(
                counter.read_text(encoding="utf-8").splitlines(),
                ["build", "build", "build"],
            )

            time.sleep(1.05)
            explicit_input.write_text("v2\n", encoding="utf-8")
            explicit_build = build()
            self.assertEqual(
                explicit_build.returncode, 0, explicit_build.stdout + explicit_build.stderr
            )
            self.assertEqual(
                counter.read_text(encoding="utf-8").splitlines(),
                ["build", "build", "build", "build"],
            )

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
                capture_output=True,
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
                capture_output=True,
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
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cppkit_dependency_bootstrap_collects_external_prefix_path(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        bootstrap = (
            (REPO_ROOT / "repomgrcpp" / "cmake" / "DependencyBootstrap.cmake").resolve().as_posix()
        )
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
                capture_output=True,
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
                capture_output=True,
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
                "get_target_property(_definitions app COMPILE_DEFINITIONS)\n"
                "get_target_property(_options app COMPILE_OPTIONS)\n"
                'message(STATUS "defs=${_definitions}")\n'
                'message(STATUS "opts=${_options}")\n'
                'if(NOT _definitions MATCHES "EIGEN_MAX_ALIGN_BYTES=64")\n'
                '    message(FATAL_ERROR "missing target definition")\n'
                "endif()\n"
                "if(MSVC)\n"
                '    if(NOT _options MATCHES "/utf-8")\n'
                '        message(FATAL_ERROR "missing MSVC target compile option")\n'
                "    endif()\n"
                "else()\n"
                '    if(NOT _options MATCHES "-Wall")\n'
                '        message(FATAL_ERROR "missing target compile option")\n'
                "    endif()\n"
                "endif()\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [cmake, "-S", str(project_dir), "-B", str(build_dir)],
                check=False,
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_compiler_flags_target_api_preserves_msvc_embedded_debug_option(self):
        text = (CMAKE_DIR / "CppKitCompilerFlags.cmake").read_text(encoding="utf-8")

        self.assertIn("MSVC_EMBEDDED_DEBUG_INFO", text)
        self.assertIn("cppkit_apply_msvc_embedded_debug_info_to_target", text)
        self.assertIn('PROPERTY MSVC_DEBUG_INFORMATION_FORMAT "Embedded"', text)

    def test_compiler_flag_model_matches_target_and_legacy_entry_points(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        compiler_flags = (CMAKE_DIR / "CppKitCompilerFlags.cmake").resolve().as_posix()
        scenarios = [
            ("Clang", "Clang", "GNU", "OFF", "OFF", "COMPILER_CLANG", "-Wall"),
            (
                "clang-cl",
                "Clang",
                "MSVC",
                "ON",
                "ON",
                "COMPILER_CLANG",
                "/clang:-msse3",
            ),
            ("GCC", "GNU", "GNU", "OFF", "OFF", "COMPILER_GCC", "-fdiagnostics-color"),
            (
                "IntelLLVM",
                "IntelLLVM",
                "GNU",
                "OFF",
                "OFF",
                "COMPILER_INTEL",
                "$<$<CONFIG:Release>:-O3>",
            ),
            ("MSVC", "MSVC", "MSVC", "ON", "ON", "COMPILER_MSVC", "/utf-8"),
        ]

        for name, compiler_id, frontend, msvc, win32, marker, expected_option in scenarios:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                project_dir = root / "project"
                build_dir = root / "build"
                project_dir.mkdir()
                (project_dir / "main.cpp").write_text(
                    "int main() { return 0; }\n", encoding="utf-8"
                )
                target_args = ""
                legacy_args = ""
                side_effect_checks = ""
                if name == "MSVC":
                    target_args = " ENABLE_LINK_WHAT_YOU_USE MSVC_EMBEDDED_DEBUG_INFO"
                    legacy_args = " MSVC_EMBEDDED_DEBUG_INFO"
                    side_effect_checks = (
                        "get_target_property(_link_what_you_use app LINK_WHAT_YOU_USE)\n"
                        "get_target_property(_debug_format app MSVC_DEBUG_INFORMATION_FORMAT)\n"
                        'if(NOT _link_what_you_use OR NOT _debug_format STREQUAL "Embedded")\n'
                        '    message(FATAL_ERROR "missing target-only MSVC side effects")\n'
                        "endif()\n"
                    )
                (project_dir / "CMakeLists.txt").write_text(
                    "cmake_minimum_required(VERSION 3.20)\n"
                    f"project(FlagParity{name} LANGUAGES CXX)\n"
                    f'set(CMAKE_CXX_COMPILER_ID "{compiler_id}")\n'
                    f'set(CMAKE_CXX_COMPILER_FRONTEND_VARIANT "{frontend}")\n'
                    f"set(MSVC {msvc})\n"
                    f"set(WIN32 {win32})\n"
                    "set(APPLE OFF)\n"
                    f"set(UNIX {'OFF' if win32 == 'ON' else 'ON'})\n"
                    'set(CMAKE_SYSTEM_PROCESSOR "x86_64")\n'
                    f'include("{compiler_flags}")\n'
                    "cppkit_common_compile_flags_values(\n"
                    "    _model_definitions _model_options _model_link_options USE_AVX\n"
                    ")\n"
                    "add_executable(app main.cpp)\n"
                    f"cppkit_apply_common_compile_flags_to_target(app USE_AVX{target_args})\n"
                    "get_target_property(_target_definitions app COMPILE_DEFINITIONS)\n"
                    "get_target_property(_target_options app COMPILE_OPTIONS)\n"
                    "get_target_property(_target_link_options app LINK_OPTIONS)\n"
                    'if(_target_link_options STREQUAL "_target_link_options-NOTFOUND")\n'
                    '    set(_target_link_options "")\n'
                    "endif()\n"
                    'if(NOT "${_target_definitions}" STREQUAL "${_model_definitions}" OR\n'
                    '   NOT "${_target_options}" STREQUAL "${_model_options}" OR\n'
                    '   NOT "${_target_link_options}" STREQUAL "${_model_link_options}")\n'
                    '    message(FATAL_ERROR "target flags differ from model")\n'
                    "endif()\n"
                    f'list(FIND _target_definitions "{marker}" _marker_index)\n'
                    f'string(FIND "${{_target_options}}" "{expected_option}" _option_index)\n'
                    "if(_marker_index EQUAL -1 OR _option_index EQUAL -1)\n"
                    f'    message(FATAL_ERROR "missing {name} branch values")\n'
                    "endif()\n"
                    "foreach(_foreign_marker IN ITEMS COMPILER_CLANG COMPILER_GCC COMPILER_INTEL COMPILER_MSVC)\n"
                    f'    if(NOT _foreign_marker STREQUAL "{marker}")\n'
                    '        list(FIND _target_definitions "${_foreign_marker}" _foreign_index)\n'
                    "        if(NOT _foreign_index EQUAL -1)\n"
                    '            message(FATAL_ERROR "foreign compiler marker present")\n'
                    "        endif()\n"
                    "    endif()\n"
                    "endforeach()\n"
                    f"{side_effect_checks}"
                    f"cppkit_apply_common_compile_flags(USE_AVX{legacy_args})\n"
                    "get_directory_property(_legacy_definitions COMPILE_DEFINITIONS)\n"
                    "get_directory_property(_legacy_options COMPILE_OPTIONS)\n"
                    "get_directory_property(_legacy_link_options LINK_OPTIONS)\n"
                    'if(NOT "${_legacy_definitions}" STREQUAL "${_model_definitions}" OR\n'
                    '   NOT "${_legacy_options}" STREQUAL "${_model_options}" OR\n'
                    '   NOT "${_legacy_link_options}" STREQUAL "${_model_link_options}")\n'
                    '    message(FATAL_ERROR "legacy flags differ from model")\n'
                    "endif()\n"
                    + (
                        "get_property(_use_folders GLOBAL PROPERTY USE_FOLDERS)\n"
                        'if(NOT CMAKE_MSVC_DEBUG_INFORMATION_FORMAT STREQUAL "Embedded" OR\n'
                        "   NOT _use_folders)\n"
                        '    message(FATAL_ERROR "missing legacy MSVC side effects")\n'
                        "endif()\n"
                        if name == "MSVC"
                        else ""
                    ),
                    encoding="utf-8",
                )

                result = subprocess.run(
                    [cmake, "-S", str(project_dir), "-B", str(build_dir)],
                    check=False,
                    text=True,
                    capture_output=True,
                )

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_gcc_coverage_commands_repeat_archive_inputs_and_instrument_once(self):
        cmake = shutil.which("cmake")
        if not cmake:
            self.skipTest("cmake is not available")

        add_executable = (CMAKE_DIR / "CppKitAddExecutable.cmake").resolve().as_posix()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_dir = root / "project"
            build_dir = root / "build"
            (project_dir / "one").mkdir(parents=True)
            (project_dir / "two").mkdir()
            (project_dir / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
            (project_dir / "one/shared.cpp").write_text(
                "int first() { return 1; }\n", encoding="utf-8"
            )
            (project_dir / "two/shared.cpp").write_text(
                "int second() { return 2; }\n", encoding="utf-8"
            )
            (project_dir / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\n"
                "project(CoverageCommands LANGUAGES CXX)\n"
                "set(CPPKIT_ADD_COVERAGE ON)\n"
                "set(WIN32 OFF)\n"
                "set(APPLE OFF)\n"
                'set(CMAKE_CXX_COMPILER_ID "GNU")\n'
                'set(LCOV_PATH "${CMAKE_COMMAND}" CACHE FILEPATH "")\n'
                'set(GENHTML_PATH "${CMAKE_COMMAND}" CACHE FILEPATH "")\n'
                f'include("{add_executable}")\n'
                "cppkit_add_executable(sample_test main.cpp one/shared.cpp two/shared.cpp\n"
                "    IS_TEST\n"
                '    COVERAGE_FILES "${CMAKE_CURRENT_SOURCE_DIR}/one/shared.cpp"\n'
                '                   "${CMAKE_CURRENT_SOURCE_DIR}/two/shared.cpp")\n'
                "cppkit_add_coverage_compile_options(sample_test)\n"
                "get_target_property(_compile_options sample_test COMPILE_OPTIONS)\n"
                "get_target_property(_link_options sample_test LINK_OPTIONS)\n"
                'list(FILTER _compile_options INCLUDE REGEX "^--coverage$")\n'
                'list(FILTER _link_options INCLUDE REGEX "^--coverage$")\n'
                "list(LENGTH _compile_options _compile_count)\n"
                "list(LENGTH _link_options _link_count)\n"
                "if(NOT _compile_count EQUAL 1 OR NOT _link_count EQUAL 1)\n"
                '    message(FATAL_ERROR "coverage options are missing or duplicated")\n'
                "endif()\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [cmake, "-S", str(project_dir), "-B", str(build_dir)],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            generated_text = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in build_dir.rglob("*")
                if path.is_file()
                and path.suffix.lower() in {".cmake", ".make", ".ninja", ".txt", ".vcxproj"}
            )

        command_lines = [
            line
            for line in generated_text.splitlines()
            if "Coverage_sample_test_1_shared_" in line and "Coverage_sample_test_2_shared_" in line
        ]
        self.assertTrue(command_lines, "generated lcov combine command was not found")
        self.assertGreaterEqual(command_lines[0].count("-a"), 2)

    def test_gcc_coverage_project_builds_and_produces_html_report(self):
        self._run_native_coverage_project("GNU")

    def test_clang_coverage_project_builds_and_produces_html_report(self):
        self._run_native_coverage_project("Clang")


if __name__ == "__main__":
    unittest.main()
