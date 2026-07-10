from __future__ import annotations

import hashlib
import json
import ntpath
import os
import posixpath
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from freecm.subprocess_utils import run_logged_command

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
VALIDATOR_BUILD_CONTRACT_NAME = "validator-build-contract.json"

if TYPE_CHECKING:
    PathValue = str | Path
else:
    PathValue = Any


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
    gradle_wrapper: PathValue | None = None
    host_platform: str = sys.platform
    force_validator_rebuild: bool = False

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


@dataclass(frozen=True)
class FreeCMValidatorBuildStatus:
    ready: bool
    reason: str | None = None


def android_environment(
    base_env: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
    homebrew_jdk_path: Path = Path("/opt/homebrew/opt/openjdk@17"),
    platform: str = sys.platform,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    home_root = Path.home() if home is None else Path(home)
    sdk_root = env.get("ANDROID_SDK_ROOT") or env.get("ANDROID_HOME")
    if not sdk_root:
        sdk_root = _default_android_sdk_root(env, home_root, platform)
    env["ANDROID_HOME"] = sdk_root
    env["ANDROID_SDK_ROOT"] = sdk_root

    java_home = env.get("JAVA_HOME")
    if not java_home and platform == "darwin" and homebrew_jdk_path.is_dir():
        java_home = str(homebrew_jdk_path)
        env["JAVA_HOME"] = java_home

    path_entries: list[str] = []
    if java_home:
        path_entries.append(_join_env_path(java_home, "bin", platform))
    path_entries.extend(
        [
            _join_env_path(sdk_root, "platform-tools", platform),
            _join_env_path(sdk_root, "emulator", platform),
            _join_env_path(sdk_root, "cmdline-tools/latest/bin", platform),
        ]
    )
    path_key = _environment_path_key(env, platform)
    existing_path = env.get(path_key, "")
    if existing_path:
        path_entries.append(existing_path)
    env[path_key] = _path_separator(platform).join(path_entries)
    return env


def gradlew_command(
    repo_root: Path,
    args: Iterable[str],
    *,
    gradle_wrapper: PathValue | None = None,
    platform: str = sys.platform,
) -> list[str]:
    if gradle_wrapper is None:
        gradle_wrapper = "gradlew.bat" if _is_windows(platform) else "gradlew"
    path_module = ntpath if _is_windows(platform) else posixpath
    wrapper_path = path_module.normpath(str(gradle_wrapper))
    if not path_module.isabs(wrapper_path):
        wrapper_path = path_module.normpath(path_module.join(str(repo_root), wrapper_path))
    return [wrapper_path, *args]


def freecm_validator_build_status(extension_root: Path) -> FreeCMValidatorBuildStatus:
    root = Path(extension_root).resolve()
    contract_path = root / VALIDATOR_BUILD_CONTRACT_NAME
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return FreeCMValidatorBuildStatus(False, f"missing {VALIDATOR_BUILD_CONTRACT_NAME}")
    except (OSError, json.JSONDecodeError) as exc:
        return FreeCMValidatorBuildStatus(False, f"invalid {VALIDATOR_BUILD_CONTRACT_NAME}: {exc}")
    try:
        schema_version, algorithm, stamp_path, inputs, outputs = _validator_contract_fields(
            root, contract
        )
    except ValueError as exc:
        return FreeCMValidatorBuildStatus(False, str(exc))

    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return FreeCMValidatorBuildStatus(False, f"missing validator stamp {stamp_path}")
    except (OSError, json.JSONDecodeError) as exc:
        return FreeCMValidatorBuildStatus(False, f"invalid validator stamp {stamp_path}: {exc}")
    if not isinstance(stamp, dict):
        return FreeCMValidatorBuildStatus(
            False, f"invalid validator stamp {stamp_path}: expected object"
        )
    if stamp.get("schemaVersion") != schema_version or stamp.get("algorithm") != algorithm:
        return FreeCMValidatorBuildStatus(False, f"unsupported validator stamp {stamp_path}")

    for field_name, relative_paths in (("inputs", inputs), ("outputs", outputs)):
        recorded = stamp.get(field_name)
        expected_names = set(relative_paths)
        if not isinstance(recorded, dict) or set(recorded) != expected_names:
            return FreeCMValidatorBuildStatus(
                False,
                f"validator stamp {field_name} do not match {VALIDATOR_BUILD_CONTRACT_NAME}",
            )
        for relative_path in relative_paths:
            try:
                file_path = _validator_contract_path(root, relative_path)
            except ValueError as exc:
                return FreeCMValidatorBuildStatus(False, str(exc))
            if not file_path.is_file():
                return FreeCMValidatorBuildStatus(
                    False, f"missing validator {field_name[:-1]} {file_path}"
                )
            digest = recorded.get(relative_path)
            try:
                actual_digest = _sha256_file(file_path)
            except OSError as exc:
                return FreeCMValidatorBuildStatus(
                    False,
                    f"unable to read validator {field_name[:-1]} {file_path}: {exc}",
                )
            if not isinstance(digest, str) or digest != actual_digest:
                return FreeCMValidatorBuildStatus(
                    False,
                    f"validator {field_name[:-1]} content changed: {relative_path}",
                )
    return FreeCMValidatorBuildStatus(True)


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


def run_test_level(
    config: AndroidWorkflowConfig,
    level: str,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    workflow_env = android_environment(env, platform=config.host_platform)
    if level == TEST_LEVEL_L0:
        _run_l0(config, workflow_env)
    elif level == TEST_LEVEL_L1:
        _run_l1(config, workflow_env)
    elif level == TEST_LEVEL_L2:
        _run_scripts(config, TEST_LEVEL_L2, config.l2_scripts, workflow_env)
    elif level == TEST_LEVEL_L3:
        _run_scripts(config, TEST_LEVEL_L3, config.l3_scripts, workflow_env)
    elif level == TEST_LEVEL_L4:
        _run_scripts(config, TEST_LEVEL_L4, config.l4_scripts, workflow_env)
    elif level == TEST_LEVEL_PRECOMMIT:
        _run_l0(config, workflow_env)
        _run_l1(config, workflow_env)
        _run_scripts(config, TEST_LEVEL_L2, config.l2_scripts, workflow_env)
    elif level == TEST_LEVEL_ALL:
        _run_l0(config, workflow_env)
        _run_l1(config, workflow_env)
        _run_scripts(config, TEST_LEVEL_L2, config.l2_scripts, workflow_env)
        _run_scripts(config, TEST_LEVEL_L3, config.l3_scripts, workflow_env)
        _run_scripts(config, TEST_LEVEL_L4, config.l4_scripts, workflow_env)
    else:
        raise ValueError(f"Unsupported test level: {level}")


def _run_l0(
    config: AndroidWorkflowConfig,
    env: Mapping[str, str],
) -> None:
    for script in config.shell_check_scripts:
        run_logged_command(
            ["bash", "-n", _repo_path(config.repo_root, script)],
            cwd=config.repo_root,
            env=dict(env),
            prefix=f"\n[{TEST_LEVEL_L0}] ",
        )
    if config.python_check_files:
        run_logged_command(
            [
                "python3",
                "-m",
                "py_compile",
                *(_repo_path(config.repo_root, path) for path in config.python_check_files),
            ],
            cwd=config.repo_root,
            env=dict(env),
            prefix=f"\n[{TEST_LEVEL_L0}] ",
        )
    run_logged_command(
        ["git", "-C", str(config.repo_root), "diff", "--check"],
        cwd=config.repo_root,
        env=dict(env),
        prefix=f"\n[{TEST_LEVEL_L0}] ",
    )
    if config.l0_gradle_tasks:
        run_logged_command(
            gradlew_command(
                config.repo_root,
                config.l0_gradle_tasks,
                gradle_wrapper=config.gradle_wrapper,
                platform=config.host_platform,
            ),
            cwd=config.repo_root,
            env=dict(env),
            prefix=f"\n[{TEST_LEVEL_L0}] ",
        )


def _run_l1(
    config: AndroidWorkflowConfig,
    env: Mapping[str, str],
) -> None:
    if config.l1_gradle_tasks:
        run_logged_command(
            gradlew_command(
                config.repo_root,
                config.l1_gradle_tasks,
                gradle_wrapper=config.gradle_wrapper,
                platform=config.host_platform,
            ),
            cwd=config.repo_root,
            env=dict(env),
            prefix=f"\n[{TEST_LEVEL_L1}] ",
        )

    extension_root = find_freecm_extension_root(config.repo_root, env)
    if extension_root is None:
        if config.require_freecm_extension:
            raise RuntimeError(
                "FreeCM VS Code extension root was not found for L1 command validation"
            )
        return

    if config.force_validator_rebuild:
        run_logged_command(
            [
                "npm",
                "--prefix",
                str(extension_root),
                "run",
                "compile",
                "--",
                "--pretty",
                "false",
            ],
            cwd=config.repo_root,
            env=dict(env),
            prefix=f"\n[{TEST_LEVEL_L1}] ",
        )
    validator_status = freecm_validator_build_status(extension_root)
    if not validator_status.ready:
        action = (
            "The forced extension compile did not produce a current validator"
            if config.force_validator_rebuild
            else "Rebuild it with `npm --prefix <FreeCM/vscode-extension> run compile` "
            "or set AndroidWorkflowConfig(force_validator_rebuild=True)"
        )
        raise RuntimeError(
            f"FreeCM command validator is missing or stale: {validator_status.reason}. {action}."
        )
    run_logged_command(
        [
            "node",
            str(extension_root / "out/validateRepoCommands.js"),
            "--preview",
            "--platform",
            config.validator_platform,
            str(config.repo_root),
        ],
        cwd=config.repo_root,
        env=dict(env),
        prefix=f"\n[{TEST_LEVEL_L1}] ",
    )


def _run_scripts(
    config: AndroidWorkflowConfig,
    label: str,
    scripts: Sequence[PathValue],
    env: Mapping[str, str],
) -> None:
    for script in scripts:
        run_logged_command(
            [_repo_path(config.repo_root, script)],
            cwd=config.repo_root,
            env=dict(env),
            prefix=f"\n[{label}] ",
        )


def _repo_path(repo_root: Path, path: PathValue) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        return str(candidate)
    return str(repo_root / candidate)


def _default_android_sdk_root(env: Mapping[str, str], home: Path, platform: str) -> str:
    if _is_windows(platform):
        local_app_data = env.get("LOCALAPPDATA")
        if local_app_data:
            return ntpath.normpath(ntpath.join(local_app_data, "Android", "Sdk"))
        return ntpath.normpath(ntpath.join(str(home), "AppData", "Local", "Android", "Sdk"))
    if platform == "darwin":
        return posixpath.join(str(home), "Library", "Android", "sdk")
    return posixpath.join(str(home), "Android", "Sdk")


def _join_env_path(root: str, relative: str, platform: str) -> str:
    if _is_windows(platform):
        return ntpath.normpath(ntpath.join(root, *relative.split("/")))
    return posixpath.join(root, *relative.split("/"))


def _environment_path_key(env: Mapping[str, str], platform: str) -> str:
    if not _is_windows(platform):
        return "PATH"
    return next((key for key in env if key.lower() == "path"), "Path")


def _path_separator(platform: str) -> str:
    return ";" if _is_windows(platform) else ":"


def _is_windows(platform: str) -> bool:
    return platform.startswith("win")


def _validator_contract_fields(
    extension_root: Path,
    contract: object,
) -> tuple[int, str, Path, tuple[str, ...], tuple[str, ...]]:
    if not isinstance(contract, dict):
        raise ValueError(f"invalid {VALIDATOR_BUILD_CONTRACT_NAME}: expected object")
    schema_version = contract.get("schemaVersion")
    algorithm = contract.get("algorithm")
    stamp_relative = contract.get("stampPath")
    if schema_version != 1 or algorithm != "sha256" or not isinstance(stamp_relative, str):
        raise ValueError(f"unsupported {VALIDATOR_BUILD_CONTRACT_NAME}")
    inputs = _validator_contract_paths(contract.get("inputs"), "inputs")
    outputs = _validator_contract_paths(contract.get("outputs"), "outputs")
    stamp_path = _validator_contract_path(extension_root, stamp_relative)
    for relative_path in (*inputs, *outputs):
        _validator_contract_path(extension_root, relative_path)
    return schema_version, algorithm, stamp_path, inputs, outputs


def _validator_contract_paths(value: object, field_name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(
            f"invalid {VALIDATOR_BUILD_CONTRACT_NAME} {field_name}: expected unique paths"
        )
    return tuple(value)


def _validator_contract_path(extension_root: Path, relative_path: str) -> Path:
    posix_path = PurePosixPath(relative_path.replace("\\", "/"))
    if posix_path.is_absolute() or ".." in posix_path.parts or not posix_path.parts:
        raise ValueError(f"unsafe validator build path: {relative_path}")
    candidate = (extension_root / Path(*posix_path.parts)).resolve()
    try:
        candidate.relative_to(extension_root)
    except ValueError as exc:
        raise ValueError(f"unsafe validator build path: {relative_path}") from exc
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    "FreeCMValidatorBuildStatus",
    "android_environment",
    "find_freecm_extension_root",
    "freecm_validator_build_status",
    "gradlew_command",
    "run_test_level",
)
