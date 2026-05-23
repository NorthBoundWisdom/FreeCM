from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repomgrdotnet.workflow import (  # noqa: E402
    DotnetCommandConfig,
    dotnet_build_command,
    dotnet_environment,
    dotnet_restore_command,
    dotnet_run_command,
    dotnet_test_command,
    normalize_exit_code,
    run_command,
    sanitize_existing_path_list,
    set_env,
)


class DotnetWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name).resolve()
        self.repo_root = self.root / "HostDotnet"
        self.repo_root.mkdir()

    def test_dotnet_environment_creates_repo_local_dotnet_and_nuget_paths(self) -> None:
        env = dotnet_environment(
            self.repo_root,
            {"PATH": "/usr/bin", "DOTNET_CLI_TELEMETRY_OPTOUT": "0"},
            env_root="build/Windows",
            set_profile_dirs=True,
        )

        self.assertEqual(env["DOTNET_CLI_HOME"], str(self.repo_root / "build/Windows/dotnet-home"))
        self.assertEqual(env["LOCALAPPDATA"], str(self.repo_root / "build/Windows/dotnet-localappdata"))
        self.assertEqual(env["APPDATA"], str(self.repo_root / "build/Windows/dotnet-home/AppData/Roaming"))
        self.assertEqual(env["NUGET_PACKAGES"], str(self.repo_root / "build/Windows/nuget-packages"))
        self.assertEqual(env["NUGET_HTTP_CACHE_PATH"], str(self.repo_root / "build/Windows/nuget-http-cache"))
        self.assertEqual(env["DOTNET_CLI_TELEMETRY_OPTOUT"], "1")
        self.assertEqual(env["DOTNET_SKIP_FIRST_TIME_EXPERIENCE"], "1")
        self.assertTrue((self.repo_root / "build/Windows/dotnet-home").is_dir())
        self.assertTrue((self.repo_root / "build/Windows/dotnet-localappdata").is_dir())

    def test_dotnet_environment_supports_linewright_style_cli_home_without_profile_dirs(self) -> None:
        env = dotnet_environment(
            self.repo_root,
            {"PATH": "/usr/bin"},
            cli_home="/private/tmp/dotnet-cli-home",
            nuget_packages="build/nuget",
            nuget_http_cache="build/nuget-cache",
            set_profile_dirs=False,
            create_directories=False,
        )

        self.assertEqual(env["DOTNET_CLI_HOME"], "/private/tmp/dotnet-cli-home")
        self.assertEqual(env["NUGET_PACKAGES"], str(self.repo_root / "build/nuget"))
        self.assertEqual(env["NUGET_HTTP_CACHE_PATH"], str(self.repo_root / "build/nuget-cache"))
        self.assertNotIn("LOCALAPPDATA", env)
        self.assertNotIn("APPDATA", env)
        self.assertFalse((self.repo_root / "build/nuget").exists())

    def test_sanitize_existing_path_list_removes_missing_and_parent_entries_case_insensitively(self) -> None:
        keep = self.root / "sdk" / "lib"
        keep.mkdir(parents=True)
        env = {
            "lib": os.pathsep.join(
                [
                    str(keep),
                    str(self.root / "missing"),
                    str(self.root / ".." / "escape"),
                    "",
                ]
            )
        }

        sanitize_existing_path_list(env, "LIB")

        self.assertEqual(env, {"LIB": str(keep)})

    def test_set_env_replaces_case_insensitive_existing_key(self) -> None:
        env = {"Path": "/bin", "PATH": "/usr/bin"}

        set_env(env, "PATH", "/custom")

        self.assertEqual(env, {"PATH": "/custom"})

    def test_linewright_style_restore_build_and_test_commands(self) -> None:
        config = DotnetCommandConfig(repo_root=self.repo_root, solution="Linewright.sln")

        self.assertEqual(
            dotnet_restore_command(config),
            ["dotnet", "restore", "Linewright.sln", "--verbosity", "minimal"],
        )
        self.assertEqual(
            dotnet_build_command(config, no_restore=True),
            ["dotnet", "build", "Linewright.sln", "--no-restore", "--verbosity", "minimal"],
        )
        self.assertEqual(
            dotnet_test_command(config),
            ["dotnet", "test", "Linewright.sln", "--no-build", "--verbosity", "minimal"],
        )

    def test_astroform_style_build_and_test_commands(self) -> None:
        config = DotnetCommandConfig(
            repo_root=self.repo_root,
            solution=self.repo_root / "AstroformNetwork.slnx",
            configuration="Debug",
            platform="Any CPU",
            disable_workload_resolver=True,
            max_cpu_count=1,
            verbosity=None,
        )

        self.assertEqual(
            dotnet_build_command(config),
            [
                "dotnet",
                "build",
                str(self.repo_root / "AstroformNetwork.slnx"),
                "-c",
                "Debug",
                "-p:Platform=Any CPU",
                "-p:MSBuildEnableWorkloadResolver=false",
                "-m:1",
            ],
        )
        self.assertEqual(
            dotnet_test_command(config, no_restore=False),
            [
                "dotnet",
                "test",
                str(self.repo_root / "AstroformNetwork.slnx"),
                "--no-build",
                "-c",
                "Debug",
                "-p:Platform=Any CPU",
                "-p:MSBuildEnableWorkloadResolver=false",
                "-m:1",
            ],
        )

    def test_run_command_uses_repo_cwd_env_and_normalizes_exit_code(self) -> None:
        completed = mock.Mock(returncode=-1)
        with mock.patch("repomgrdotnet.workflow.subprocess.run", return_value=completed) as run:
            exit_code = run_command(["dotnet", "build"], cwd=self.repo_root, env={"PATH": "/bin"})

        self.assertEqual(exit_code, 255)
        run.assert_called_once_with(
            ["dotnet", "build"],
            cwd=self.repo_root,
            env={"PATH": "/bin"},
            check=False,
        )

    def test_dotnet_run_command_supports_configuration_no_build_and_launch_args(self) -> None:
        command = dotnet_run_command(
            "apps/Linewright.Api/Linewright.Api.csproj",
            configuration="Debug",
            no_build=True,
            launch_args=("--profile", "field-mock"),
        )

        self.assertEqual(
            command,
            [
                "dotnet",
                "run",
                "--project",
                "apps/Linewright.Api/Linewright.Api.csproj",
                "-c",
                "Debug",
                "--no-build",
                "--",
                "--profile",
                "field-mock",
            ],
        )

    def test_normalize_exit_code_preserves_zero_and_unsigned_windows_values(self) -> None:
        self.assertEqual(normalize_exit_code(0), 0)
        self.assertEqual(normalize_exit_code(7), 7)
        self.assertEqual(normalize_exit_code(-1), 255)


if __name__ == "__main__":
    unittest.main()
