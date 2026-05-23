from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence, Union

TEST_LEVEL_L0 = "l0"
TEST_LEVEL_L1 = "l1"
TEST_LEVEL_L2 = "l2"
TEST_LEVEL_L3 = "l3"
TEST_LEVEL_L4 = "l4"
TEST_LEVEL_PRECOMMIT = "precommit"
TEST_LEVEL_ALL = "all"
TEST_LEVEL_CHOICES = (
    TEST_LEVEL_L0,
    TEST_LEVEL_L1,
    TEST_LEVEL_L2,
    TEST_LEVEL_L3,
    TEST_LEVEL_L4,
    TEST_LEVEL_PRECOMMIT,
    TEST_LEVEL_ALL,
)

PathValue = Union[str, Path]
CommandRunner = Callable[[str, Sequence[str], Path, Mapping[str, str]], None]


@dataclass(frozen=True)
class AndroidWorkflowConfig:
    repo_root: Path
    shell_check_scripts: Sequence[PathValue] = ()
    python_check_files: Sequence[PathValue] = ()
    l0_gradle_tasks: Sequence[str] = ()
    l1_gradle_tasks: Sequence[str] = ()
    l2_scripts: Sequence[PathValue] = ()
    l3_scripts: Sequence[PathValue] = ()
    l4_scripts: Sequence[PathValue] = ()
    validator_platform: str = sys.platform
    require_freecm_extension: bool = True
    gradle_wrapper: PathValue = "gradlew"

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_root", Path(self.repo_root).resolve())
        for field_name in (
            "shell_check_scripts",
            "python_check_files",
            "l0_gradle_tasks",
            "l1_gradle_tasks",
            "l2_scripts",
            "l3_scripts",
            "l4_scripts",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))


def android_environment(
    base_env: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
    homebrew_jdk_path: Path = Path("/opt/homebrew/opt/openjdk@17"),
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    home_root = Path.home() if home is None else Path(home)
    sdk_root = env.get("ANDROID_SDK_ROOT") or env.get("ANDROID_HOME")
    if not sdk_root:
        sdk_root = str(home_root / "Library/Android/sdk")
    env["ANDROID_HOME"] = sdk_root
    env["ANDROID_SDK_ROOT"] = sdk_root

    java_home = env.get("JAVA_HOME")
    if not java_home and homebrew_jdk_path.is_dir():
        java_home = str(homebrew_jdk_path)
        env["JAVA_HOME"] = java_home

    path_entries: list[str] = []
    if java_home:
        path_entries.append(str(Path(java_home) / "bin"))
    sdk_path = Path(sdk_root)
    path_entries.extend(
        [
            str(sdk_path / "platform-tools"),
            str(sdk_path / "emulator"),
            str(sdk_path / "cmdline-tools/latest/bin"),
        ]
    )
    existing_path = env.get("PATH", "")
    if existing_path:
        path_entries.append(existing_path)
    env["PATH"] = os.pathsep.join(path_entries)
    return env


def gradlew_command(
    repo_root: Path,
    args: Iterable[str],
    *,
    gradle_wrapper: PathValue = "gradlew",
) -> list[str]:
    wrapper_path = Path(gradle_wrapper)
    if not wrapper_path.is_absolute():
        wrapper_path = Path(repo_root) / wrapper_path
    return [str(wrapper_path), *args]


def find_freecm_extension_root(
    repo_root: Path,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    env_map = os.environ if env is None else env
    resolved_repo_root = Path(repo_root).resolve()
    candidates: list[Path] = []
    if env_map.get("FREECM_EXTENSION_ROOT"):
        candidates.append(Path(env_map["FREECM_EXTENSION_ROOT"]))
    candidates.extend(
        [
            resolved_repo_root / "FreeCM/vscode-extension",
            resolved_repo_root.parent / "FreeCM/vscode-extension",
        ]
    )
    for candidate in candidates:
        if (candidate / "package.json").is_file():
            return candidate.resolve()
    return None


def default_command_runner(
    label: str,
    command: Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
) -> None:
    command_list = [str(part) for part in command]
    print(f"\n[{label}] {' '.join(command_list)}", flush=True)
    subprocess.run(command_list, cwd=cwd, env=dict(env), check=True)


def run_test_level(
    config: AndroidWorkflowConfig,
    level: str,
    *,
    runner: CommandRunner = default_command_runner,
    env: Mapping[str, str] | None = None,
) -> None:
    workflow_env = android_environment(env)
    if level == TEST_LEVEL_L0:
        _run_l0(config, runner, workflow_env)
    elif level == TEST_LEVEL_L1:
        _run_l1(config, runner, workflow_env)
    elif level == TEST_LEVEL_L2:
        _run_scripts(config, TEST_LEVEL_L2, config.l2_scripts, runner, workflow_env)
    elif level == TEST_LEVEL_L3:
        _run_scripts(config, TEST_LEVEL_L3, config.l3_scripts, runner, workflow_env)
    elif level == TEST_LEVEL_L4:
        _run_scripts(config, TEST_LEVEL_L4, config.l4_scripts, runner, workflow_env)
    elif level == TEST_LEVEL_PRECOMMIT:
        _run_l0(config, runner, workflow_env)
        _run_l1(config, runner, workflow_env)
        _run_scripts(config, TEST_LEVEL_L2, config.l2_scripts, runner, workflow_env)
    elif level == TEST_LEVEL_ALL:
        _run_l0(config, runner, workflow_env)
        _run_l1(config, runner, workflow_env)
        _run_scripts(config, TEST_LEVEL_L2, config.l2_scripts, runner, workflow_env)
        _run_scripts(config, TEST_LEVEL_L3, config.l3_scripts, runner, workflow_env)
        _run_scripts(config, TEST_LEVEL_L4, config.l4_scripts, runner, workflow_env)
    else:
        raise ValueError(f"Unsupported test level: {level}")


def _run_l0(
    config: AndroidWorkflowConfig,
    runner: CommandRunner,
    env: Mapping[str, str],
) -> None:
    for script in config.shell_check_scripts:
        runner(
            TEST_LEVEL_L0,
            ["bash", "-n", _repo_path(config.repo_root, script)],
            config.repo_root,
            env,
        )
    if config.python_check_files:
        runner(
            TEST_LEVEL_L0,
            [
                "python3",
                "-m",
                "py_compile",
                *(_repo_path(config.repo_root, path) for path in config.python_check_files),
            ],
            config.repo_root,
            env,
        )
    runner(
        TEST_LEVEL_L0,
        ["git", "-C", str(config.repo_root), "diff", "--check"],
        config.repo_root,
        env,
    )
    if config.l0_gradle_tasks:
        runner(
            TEST_LEVEL_L0,
            gradlew_command(
                config.repo_root,
                config.l0_gradle_tasks,
                gradle_wrapper=config.gradle_wrapper,
            ),
            config.repo_root,
            env,
        )


def _run_l1(
    config: AndroidWorkflowConfig,
    runner: CommandRunner,
    env: Mapping[str, str],
) -> None:
    if config.l1_gradle_tasks:
        runner(
            TEST_LEVEL_L1,
            gradlew_command(
                config.repo_root,
                config.l1_gradle_tasks,
                gradle_wrapper=config.gradle_wrapper,
            ),
            config.repo_root,
            env,
        )

    extension_root = find_freecm_extension_root(config.repo_root, env)
    if extension_root is None:
        if config.require_freecm_extension:
            raise RuntimeError("FreeCM VS Code extension root was not found for L1 command validation")
        return

    runner(
        TEST_LEVEL_L1,
        ["npm", "--prefix", str(extension_root), "run", "compile", "--", "--pretty", "false"],
        config.repo_root,
        env,
    )
    runner(
        TEST_LEVEL_L1,
        [
            "node",
            str(extension_root / "out/validateRepoCommands.js"),
            "--preview",
            "--platform",
            config.validator_platform,
            str(config.repo_root),
        ],
        config.repo_root,
        env,
    )


def _run_scripts(
    config: AndroidWorkflowConfig,
    label: str,
    scripts: Sequence[PathValue],
    runner: CommandRunner,
    env: Mapping[str, str],
) -> None:
    for script in scripts:
        runner(label, [_repo_path(config.repo_root, script)], config.repo_root, env)


def _repo_path(repo_root: Path, path: PathValue) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        return str(candidate)
    return str(repo_root / candidate)


__all__ = (
    "TEST_LEVEL_ALL",
    "TEST_LEVEL_CHOICES",
    "TEST_LEVEL_L0",
    "TEST_LEVEL_L1",
    "TEST_LEVEL_L2",
    "TEST_LEVEL_L3",
    "TEST_LEVEL_L4",
    "TEST_LEVEL_PRECOMMIT",
    "AndroidWorkflowConfig",
    "CommandRunner",
    "android_environment",
    "default_command_runner",
    "find_freecm_extension_root",
    "gradlew_command",
    "run_test_level",
)
