from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence, Union

PathValue = Union[str, Path]


@dataclass(frozen=True)
class DotnetCommandConfig:
    repo_root: Path
    solution: PathValue
    configuration: str | None = None
    platform: str | None = None
    verbosity: str | None = "minimal"
    dotnet_executable: str = "dotnet"
    msbuild_properties: Mapping[str, str] = field(default_factory=dict)
    max_cpu_count: int | None = None
    disable_workload_resolver: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_root", Path(self.repo_root).resolve())
        object.__setattr__(self, "msbuild_properties", dict(self.msbuild_properties))


def normalize_exit_code(returncode: int) -> int:
    if returncode == 0:
        return 0
    return returncode % 256


def set_env(env: dict[str, str], name: str, value: str) -> None:
    for key in list(env):
        if key.lower() == name.lower():
            del env[key]
    env[name] = value


def sanitize_existing_path_list(env: dict[str, str], name: str) -> None:
    value = _env_get(env, name)
    if not value:
        return

    existing_entries: list[str] = []
    for entry in value.split(os.pathsep):
        normalized = entry.strip().strip('"')
        if not normalized:
            continue
        expanded = os.path.expandvars(normalized)
        if ".." in Path(expanded).parts:
            continue
        if Path(expanded).exists():
            existing_entries.append(entry)
    set_env(env, name, os.pathsep.join(existing_entries))


def dotnet_environment(
    repo_root: Path,
    base_env: Mapping[str, str] | None = None,
    *,
    env_root: PathValue = "build/dotnet",
    cli_home: PathValue | None = None,
    local_app_data: PathValue | None = None,
    app_data: PathValue | None = None,
    nuget_packages: PathValue | None = None,
    nuget_http_cache: PathValue | None = None,
    set_profile_dirs: bool = os.name == "nt",
    create_directories: bool = True,
    sanitize_path_vars: Sequence[str] = ("LIB",),
) -> dict[str, str]:
    resolved_repo_root = Path(repo_root).resolve()
    env = dict(os.environ if base_env is None else base_env)
    env_root_value = env_root
    cli_home_value = cli_home
    local_app_data_value = local_app_data
    app_data_value = app_data
    nuget_packages_value = nuget_packages
    nuget_http_cache_value = nuget_http_cache

    resolved_env_root = _resolve_path(resolved_repo_root, env_root_value)
    resolved_cli_home = _resolve_path(
        resolved_repo_root,
        cli_home_value if cli_home_value is not None else resolved_env_root / "dotnet-home",
    )
    resolved_local_app_data = _resolve_path(
        resolved_repo_root,
        local_app_data_value if local_app_data_value is not None else resolved_env_root / "dotnet-localappdata",
    )
    resolved_app_data = _resolve_path(
        resolved_repo_root,
        app_data_value if app_data_value is not None else resolved_cli_home / "AppData" / "Roaming",
    )
    resolved_nuget_packages = _resolve_path(
        resolved_repo_root,
        nuget_packages_value if nuget_packages_value is not None else resolved_env_root / "nuget-packages",
    )
    resolved_nuget_http_cache = _resolve_path(
        resolved_repo_root,
        nuget_http_cache_value if nuget_http_cache_value is not None else resolved_env_root / "nuget-http-cache",
    )

    paths_to_create = [resolved_cli_home, resolved_nuget_packages, resolved_nuget_http_cache]
    if set_profile_dirs:
        paths_to_create.extend([resolved_local_app_data, resolved_app_data])
    if create_directories:
        for path in paths_to_create:
            path.mkdir(parents=True, exist_ok=True)

    set_env(env, "DOTNET_CLI_HOME", _env_path_value(cli_home_value, resolved_cli_home))
    set_env(env, "DOTNET_CLI_TELEMETRY_OPTOUT", "1")
    set_env(env, "DOTNET_SKIP_FIRST_TIME_EXPERIENCE", "1")
    set_env(env, "NUGET_PACKAGES", _env_path_value(nuget_packages_value, resolved_nuget_packages))
    set_env(env, "NUGET_HTTP_CACHE_PATH", _env_path_value(nuget_http_cache_value, resolved_nuget_http_cache))
    if set_profile_dirs:
        set_env(env, "LOCALAPPDATA", _env_path_value(local_app_data_value, resolved_local_app_data))
        set_env(env, "APPDATA", _env_path_value(app_data_value, resolved_app_data))
    for name in sanitize_path_vars:
        sanitize_existing_path_list(env, name)
    return env


def dotnet_restore_command(config: DotnetCommandConfig) -> list[str]:
    command = [config.dotnet_executable, "restore", _path_arg(config.solution)]
    _append_verbosity(command, config.verbosity)
    return command


def dotnet_build_command(
    config: DotnetCommandConfig,
    *,
    no_restore: bool = False,
) -> list[str]:
    command = [config.dotnet_executable, "build", _path_arg(config.solution)]
    if no_restore:
        command.append("--no-restore")
    command.extend(_msbuild_args(config))
    _append_verbosity(command, config.verbosity)
    return command


def dotnet_test_command(
    config: DotnetCommandConfig,
    *,
    no_build: bool = True,
    no_restore: bool = False,
) -> list[str]:
    command = [config.dotnet_executable, "test", _path_arg(config.solution)]
    if no_build:
        command.append("--no-build")
    if no_restore:
        command.append("--no-restore")
    command.extend(_msbuild_args(config))
    _append_verbosity(command, config.verbosity)
    return command


def dotnet_run_command(
    project: PathValue,
    *,
    dotnet_executable: str = "dotnet",
    configuration: str | None = None,
    no_build: bool = False,
    launch_args: Sequence[str] = (),
) -> list[str]:
    command = [dotnet_executable, "run", "--project", _path_arg(project)]
    if configuration:
        command.extend(["-c", configuration])
    if no_build:
        command.append("--no-build")
    if launch_args:
        command.append("--")
        command.extend(launch_args)
    return command





def _msbuild_args(config: DotnetCommandConfig) -> list[str]:
    args: list[str] = []
    if config.configuration:
        args.extend(["-c", config.configuration])
    if config.platform:
        args.append(f"-p:Platform={config.platform}")
    if config.disable_workload_resolver:
        args.append("-p:MSBuildEnableWorkloadResolver=false")
    for name, value in config.msbuild_properties.items():
        args.append(f"-p:{name}={value}")
    if config.max_cpu_count is not None:
        args.append(f"-m:{config.max_cpu_count}")
    return args


def _append_verbosity(command: list[str], verbosity: str | None) -> None:
    if verbosity:
        command.extend(["--verbosity", verbosity])


def _path_arg(path: PathValue) -> str:
    return str(path)


def _resolve_path(repo_root: Path, path: PathValue) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    path_text = str(path)
    if path_text.startswith("/") and not path_text.startswith("//"):
        return Path(path_text)
    return repo_root / candidate


def _env_path_value(source: PathValue | None, resolved: Path) -> str:
    if isinstance(source, str) and source.startswith("/") and not source.startswith("//"):
        return source
    return str(resolved)


def _env_get(env: Mapping[str, str], name: str) -> str:
    for key, value in env.items():
        if key.lower() == name.lower():
            return value
    return ""


__all__ = (
    "DotnetCommandConfig",
    "PathValue",
    "dotnet_build_command",
    "dotnet_environment",
    "dotnet_restore_command",
    "dotnet_run_command",
    "dotnet_test_command",
    "normalize_exit_code",
    "sanitize_existing_path_list",
    "set_env",
)
