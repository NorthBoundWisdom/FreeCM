#!/usr/bin/env python3
# Usage:
#   cd /path/to/FreeCM/hooks
#   cp ./path.ini.sample ./path.ini
#   python3 install.py
#   python3 install.py --existing backup  # preserve conflicting hooks before replacing them

"""Install Git hooks script."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from freecm.git_repositories import git_toplevel

RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GRAY = "\033[90m"

HOOK_FILES = ("pre-commit", "commit-msg", "prepare-commit-msg", "commit_msg.py")
EXISTING_HOOK_POLICIES = ("abort", "backup", "replace")


class HookInstallError(RuntimeError):
    """Raised when hooks cannot be installed without losing existing files."""


@dataclass(frozen=True)
class _StagedHook:
    name: str
    temporary_path: Path
    target_path: Path


@dataclass(frozen=True)
class _PublishedHook:
    target_path: Path
    backup_path: Path | None


def _enable_windows_vt_mode() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        kernel32 = cast(Any, vars(ctypes)["windll"].kernel32)
        get_std_handle = cast(Callable[[int], int], kernel32.GetStdHandle)
        get_console_mode = cast(Callable[[int, object], int], kernel32.GetConsoleMode)
        set_console_mode = cast(Callable[[int, int], int], kernel32.SetConsoleMode)
        handle = get_std_handle(-11)  # STD_OUTPUT_HANDLE
        if handle in (0, -1):
            return False
        mode = ctypes.c_uint()
        if get_console_mode(handle, ctypes.byref(mode)) == 0:
            return False
        return set_console_mode(handle, mode.value | 0x0004) != 0
    except Exception:
        return False


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    return _enable_windows_vt_mode()


COLOR_ENABLED = _supports_color()


def colorize(text: str, color: str) -> str:
    return f"{color}{text}{RESET}" if COLOR_ENABLED else text


def print_ok(message: str) -> None:
    print(f"{colorize('[OK]', GREEN)} {message}")


def print_error(message: str) -> None:
    print(f"{colorize('[ERROR]', RED)} {message}")


def print_warn(message: str) -> None:
    print(f"{colorize('[WARN]', YELLOW)} {message}")


def print_info(message: str) -> None:
    print(message)


def print_header(message: str) -> None:
    print(colorize(message, CYAN))


def check_git_repo() -> bool:
    """Check if current directory is a git repository"""
    try:
        subprocess.run(  # nosec B603 B607
            ["git", "rev-parse", "--git-dir"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_hooks_dir(repo_root: Path | None = None) -> Path:
    """Resolve Git's effective hooks directory, including core.hooksPath."""
    result = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    raw_path = result.stdout.strip()
    if not raw_path:
        raise HookInstallError("Git returned an empty hooks path")
    hooks_dir = Path(raw_path)
    if not hooks_dir.is_absolute():
        raise HookInstallError(f"Git returned a non-absolute hooks path: {raw_path}")
    hooks_dir = hooks_dir.resolve()
    if hooks_dir.exists() and not hooks_dir.is_dir():
        raise HookInstallError(f"Configured hooks path is not a directory: {hooks_dir}")
    return hooks_dir


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _matches_hook_content(source: Path, target: Path) -> bool:
    if target.is_symlink() or not target.is_file():
        return False
    try:
        return source.read_bytes() == target.read_bytes()
    except OSError:
        return False


def _reserve_backup_path(target: Path, *, persistent: bool) -> Path:
    prefix = f"{target.name}.freecm-backup-" if persistent else f".{target.name}.freecm-backup-"
    with tempfile.NamedTemporaryFile(
        dir=target.parent,
        prefix=prefix,
        delete=False,
    ) as backup_file:
        return Path(backup_file.name)


def _stage_hook(source: Path, target: Path, name: str) -> _StagedHook:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=f".{name}.freecm-install-",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        shutil.copy2(source, temporary_path)
        os.chmod(temporary_path, 0o755)  # nosec B103
        return _StagedHook(name=name, temporary_path=temporary_path, target_path=target)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _cleanup_staged_hooks(staged_hooks: list[_StagedHook]) -> None:
    for staged in staged_hooks:
        staged.temporary_path.unlink(missing_ok=True)


def _rollback_published_hooks(published_hooks: list[_PublishedHook]) -> list[str]:
    errors: list[str] = []
    for published in reversed(published_hooks):
        try:
            if _path_exists(published.target_path):
                published.target_path.unlink()
            if published.backup_path is not None:
                if not _path_exists(published.backup_path):
                    raise FileNotFoundError(f"missing backup {published.backup_path}")
                published.backup_path.replace(published.target_path)
        except Exception as error:
            errors.append(f"{published.target_path}: {error}")
    return errors


def install_hooks(
    script_dir: Path,
    hooks_dir: Path,
    hook_names: tuple[str, ...] = HOOK_FILES,
    *,
    existing_policy: str = "abort",
) -> tuple[str, ...]:
    """Install a complete hook set transactionally."""
    if existing_policy not in EXISTING_HOOK_POLICIES:
        raise ValueError(f"Unsupported existing hook policy: {existing_policy}")
    hooks_dir.mkdir(parents=True, exist_ok=True)

    pending: list[tuple[str, Path, Path]] = []
    conflicts: list[Path] = []
    for name in hook_names:
        source = script_dir / name
        target = hooks_dir / name
        if source.is_symlink() or not source.is_file():
            raise HookInstallError(f"Cannot find regular {name} hook file: {source}")
        content_matches = _matches_hook_content(source, target)
        if content_matches and os.access(target, os.X_OK):
            continue
        if _path_exists(target):
            if not target.is_symlink() and not target.is_file():
                raise HookInstallError(f"Refusing to replace non-file hook path: {target}")
            if existing_policy == "abort" and not content_matches:
                conflicts.append(target)
        pending.append((name, source, target))

    if conflicts:
        paths = "\n".join(f"- {path}" for path in conflicts)
        raise HookInstallError(
            "Existing hook files differ from FreeCM; no files were changed:\n"
            f"{paths}\n"
            "Rerun with `--existing backup` to preserve them or "
            "`--existing replace` to replace them explicitly."
        )

    staged_hooks: list[_StagedHook] = []
    try:
        for name, source, target in pending:
            staged_hooks.append(_stage_hook(source, target, name))
    except Exception as error:
        _cleanup_staged_hooks(staged_hooks)
        raise HookInstallError(f"Failed to stage hook files: {error}") from error

    published_hooks: list[_PublishedHook] = []
    try:
        for staged in staged_hooks:
            backup_path: Path | None = None
            if _path_exists(staged.target_path):
                backup_path = _reserve_backup_path(
                    staged.target_path,
                    persistent=existing_policy == "backup",
                )
                try:
                    staged.target_path.replace(backup_path)
                except Exception:
                    backup_path.unlink(missing_ok=True)
                    raise
            published_hooks.append(
                _PublishedHook(target_path=staged.target_path, backup_path=backup_path)
            )
            staged.temporary_path.replace(staged.target_path)
    except Exception as error:
        rollback_errors = _rollback_published_hooks(published_hooks)
        retained_backups = [
            str(published.backup_path)
            for published in published_hooks
            if published.backup_path is not None and _path_exists(published.backup_path)
        ]
        _cleanup_staged_hooks(staged_hooks)
        if rollback_errors:
            raise HookInstallError(
                "Hook installation failed and rollback was incomplete; retained backups: "
                f"{', '.join(retained_backups) or 'none'}; rollback errors: "
                + "; ".join(rollback_errors)
            ) from error
        raise HookInstallError(
            f"Hook installation failed; previous hooks were restored: {error}"
        ) from error
    else:
        _cleanup_staged_hooks(staged_hooks)
        for published in published_hooks:
            backup_path = published.backup_path
            if backup_path is None:
                continue
            if existing_policy == "backup":
                print_info(f"Preserved existing hook at {backup_path}")
            else:
                try:
                    backup_path.unlink(missing_ok=True)
                except OSError as error:
                    print_warn(f"Could not remove temporary hook backup {backup_path}: {error}")

    for staged in staged_hooks:
        print_ok(f"Installed {staged.name} hook")
    return hook_names


def install_hook(script_dir: Path, hooks_dir: Path, hook_name: str) -> bool:
    """Install one hook with the same safe default used by the batch installer."""
    try:
        install_hooks(script_dir, hooks_dir, (hook_name,))
        return True
    except (HookInstallError, OSError, ValueError) as error:
        print_error(f"Error installing {hook_name}: {error}")
        return False


def configure_git_local():
    """Configure git local settings"""
    configs = [
        ("core.editor", "vim"),
        ("core.pager", "less -r"),
        ("core.filemode", "false"),
        ("color.ui", "true"),
        ("pull.rebase", "true"),
        (
            "alias.lg",
            "log --color --graph --pretty=format:'%Cred%h%Creset -%C(yellow)%d%Creset %s %Cgreen(%cr) %C(bold blue)<%an>%Creset' --abbrev-commit --all",
        ),
    ]

    print_header("Configuring git local settings...")
    success_count = 0
    for key, value in configs:
        try:
            subprocess.run(  # nosec B603 B607
                ["git", "config", "--local", key, value],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            print_ok(f"Configured {key}")
            success_count += 1
        except subprocess.CalledProcessError as e:
            print_error(f"Error configuring {key}: {e}")
        except FileNotFoundError:
            print_error("git command not found")
            return False

    return success_count == len(configs)


CLANG_FORMAT_CONFIG_KEY = "freecm.clangFormatPath"
QMLFORMAT_CONFIG_KEY = "freecm.qmlformatPath"
SOURCE_ROOTS_CONFIG_KEY = "freecm.hooks.sourceRoots"
EXCLUDED_DIRS_CONFIG_KEY = "freecm.hooks.excludeDirs"

PATH_INI_FILENAME = "path.ini"
PATH_INI_SAMPLE_FILENAME = "path.ini.sample"


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_path_ini(path_ini: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw_line in path_ini.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def validate_executable_path(path_value: str, field_name: str) -> Path | None:
    value = str(path_value).strip()
    if not value:
        print_error(f"{field_name} is empty in {PATH_INI_FILENAME}")
        return None
    resolved = Path(value).expanduser().resolve()
    if not resolved.is_file():
        print_error(f"{field_name} not found: {resolved}")
        return None
    if not os.access(resolved, os.X_OK):
        print_error(f"{field_name} is not executable: {resolved}")
        return None
    return resolved


def validate_optional_executable_path(path_value: str, field_name: str) -> Path | None:
    value = str(path_value).strip()
    if not value:
        return None
    return validate_executable_path(value, field_name)


def load_path_config(script_dir: Path) -> dict[str, str] | None:
    path_ini = script_dir / PATH_INI_FILENAME
    sample_ini = script_dir / PATH_INI_SAMPLE_FILENAME
    if not path_ini.exists():
        print_error(f"Missing {path_ini}")
        if sample_ini.exists():
            print_info(f"Please create {PATH_INI_FILENAME} from {PATH_INI_SAMPLE_FILENAME}.")
        else:
            print_info(f"Please create {PATH_INI_FILENAME} in {script_dir}.")
        return None
    return parse_path_ini(path_ini)


def apply_tool_paths_from_ini(repo_root: Path, cfg: dict[str, str]) -> bool:
    required_paths = [
        ("CLANG_FORMAT_PATH", CLANG_FORMAT_CONFIG_KEY, "clang-format"),
    ]

    for ini_key, git_key, label in required_paths:
        resolved = validate_executable_path(cfg.get(ini_key, ""), ini_key)
        if resolved is None:
            return False
        if not set_tool_path(repo_root, git_key, str(resolved), label):
            return False

    qmlformat = validate_optional_executable_path(cfg.get("QMLFORMAT_PATH", ""), "QMLFORMAT_PATH")
    if qmlformat is None:
        if not unset_tool_path(repo_root, QMLFORMAT_CONFIG_KEY, "qmlformat"):
            return False
        print_warn("qmlformat path not configured; QML/JS formatting will be skipped.")
    elif not set_tool_path(repo_root, QMLFORMAT_CONFIG_KEY, str(qmlformat), "qmlformat"):
        return False

    path_settings = [
        ("SOURCE_ROOTS", SOURCE_ROOTS_CONFIG_KEY, "source roots"),
        ("EXCLUDE_DIRS", EXCLUDED_DIRS_CONFIG_KEY, "excluded directories"),
    ]
    for ini_key, git_key, label in path_settings:
        raw = cfg.get(ini_key, "").strip()
        if raw and not set_tool_path(repo_root, git_key, raw, label):
            return False
    return True


def should_configure_git_from_ini(cfg: dict[str, str]) -> bool:
    return parse_bool(cfg.get("USE_GIT_CONFIG", "false"))


def set_tool_path(repo_root: Path, config_key: str, path: str, label: str) -> bool:
    """Save tool path to git local config."""
    try:
        subprocess.run(  # nosec B603 B607
            ["git", "config", "--local", config_key, path],
            cwd=repo_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        print_ok(f"Saved {label} path: {path}")
        return True
    except subprocess.CalledProcessError as e:
        print_error(f"Failed to save config: {e}")
        return False


def unset_tool_path(repo_root: Path, config_key: str, label: str) -> bool:
    """Remove optional tool path from git local config."""
    try:
        subprocess.run(  # nosec B603 B607
            ["git", "config", "--local", "--unset", config_key],
            cwd=repo_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        print_ok(f"Cleared optional {label} path")
        return True
    except OSError as e:
        print_error(f"Failed to clear config: {e}")
        return False


def get_repo_root() -> Path:
    """Get the repository root directory."""
    return cast(Path, git_toplevel(Path.cwd()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install the shared FreeCM Git hooks.")
    parser.add_argument(
        "--existing",
        choices=EXISTING_HOOK_POLICIES,
        default="abort",
        help=(
            "How to handle existing files that differ from FreeCM: abort (default), "
            "backup, or replace."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Main installation function"""
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    print_header("Installing Git hooks...")
    print()

    # Check if in git repository
    if not check_git_repo():
        print_error("current directory is not a git repository")
        return 1

    repo_root = get_repo_root()
    script_dir = Path(__file__).parent.resolve()
    try:
        hooks_dir = get_hooks_dir(repo_root)
    except (HookInstallError, OSError, subprocess.CalledProcessError) as error:
        print_error(f"Cannot resolve Git hooks directory: {error}")
        return 1

    cfg = load_path_config(script_dir)
    if cfg is None:
        return 1
    print_info(f"{colorize('Using config:', GRAY)} {script_dir / PATH_INI_FILENAME}")
    if not apply_tool_paths_from_ini(repo_root, cfg):
        return 1
    print()

    print_info(f"{colorize('Copying hooks files to', GRAY)} {hooks_dir}")
    try:
        install_hooks(script_dir, hooks_dir, existing_policy=args.existing)
    except (HookInstallError, OSError, ValueError) as error:
        print_error(str(error))
        return 1

    print()
    # Configure git local settings
    if should_configure_git_from_ini(cfg):
        configure_git_local()
    else:
        print_warn(
            f"Skipping git local config (set USE_GIT_CONFIG=true in {PATH_INI_FILENAME} to enable)."
        )

    print()
    print_ok("Git hooks installation completed!")
    print()
    print("Now when you use 'git commit':")
    print("1. C/C++ files under configured source roots will be formatted with clang-format")
    print(
        "2. QML/JS files under configured source roots will be formatted when qmlformat is configured"
    )
    print("3. Text files will be normalized to LF without trailing whitespace")
    print("4. Files larger than 15MB will be blocked from committing")
    print("5. Commit message template will be displayed automatically")
    print("6. Commit message format will be validated against [type]: description format")
    print()
    print(
        "Supported types: feat, fix, refactor, style, docs, test, chore, perf, ci, build, enhancement"
    )
    print()
    print(f"Requirements: python3/python and a valid {PATH_INI_FILENAME} in hooks/.")
    print(f"Edit {PATH_INI_FILENAME} to change tool paths and rerun install.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
