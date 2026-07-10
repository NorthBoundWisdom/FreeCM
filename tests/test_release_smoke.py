import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.smoke_installed_wheel import (
    EXPECTED_CMAKE_RESOURCES,
    EXPECTED_CONSOLE_SCRIPTS,
    select_wheel,
    validate_cmake_resource_names,
    validate_console_script_names,
)


class ReleaseSmokeTests(unittest.TestCase):
    def test_wheel_smoke_rejects_missing_console_entry_point(self) -> None:
        missing_name = "repo-tool"
        with self.assertRaisesRegex(RuntimeError, missing_name):
            validate_console_script_names(EXPECTED_CONSOLE_SCRIPTS - {missing_name})

    def test_wheel_smoke_rejects_missing_cmake_resource(self) -> None:
        missing_name = "cmake/CppKitHeaderExport.cmake"
        with self.assertRaisesRegex(RuntimeError, "CppKitHeaderExport.cmake"):
            validate_cmake_resource_names(EXPECTED_CMAKE_RESOURCES - {missing_name})

    def test_wheel_selection_requires_one_matching_version(self) -> None:
        with TemporaryDirectory() as temp_dir:
            dist_dir = Path(temp_dir)
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                select_wheel(dist_dir, "1.2.3")
            first = dist_dir / "freecm-1.2.3-py3-none-any.whl"
            first.touch()
            self.assertEqual(select_wheel(dist_dir, "1.2.3"), first.resolve())
            (dist_dir / "freecm-1.2.3-py3-other.whl").touch()
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                select_wheel(dist_dir, "1.2.3")


if __name__ == "__main__":
    unittest.main()
