#!/usr/bin/env python3
# Usage:
#   cd /path/to/FreeCM/hooks
#   cp ./path.ini.sample ./path.ini
#   python3 install.py

"""Install Git hooks script."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

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


def _enable_windows_vt_mode() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if handle in (0, -1):
            return False
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return False
        return kernel32.SetConsoleMode(handle, mode.value | 0x0004) != 0
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


def check_git_repo():
    """Check if current directory is a git repository"""
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_hooks_dir():
    """Get the git hooks directory path"""
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"], capture_output=True, text=True, check=True
    )
    git_dir = Path(result.stdout.strip())
    return git_dir / "hooks"


def install_hook(script_dir, hooks_dir, hook_name):
    """Install a single hook file"""
    source_file = script_dir / hook_name
    target_file = hooks_dir / hook_name

    if not source_file.exists():
        print_error(f"Cannot find {hook_name} hook file")
        return False

    try:
        shutil.copy2(source_file, target_file)
        # Make executable
        os.chmod(target_file, 0o755)
        print_ok(f"Installed {hook_name} hook")
        return True
    except Exception as e:
        print_error(f"Error installing {hook_name}: {e}")
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
            subprocess.run(
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
            print_info(
                f"Please create {PATH_INI_FILENAME} from {PATH_INI_SAMPLE_FILENAME}."
            )
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
        subprocess.run(
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
        subprocess.run(
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
    return git_toplevel(Path.cwd())


def main():
    """Main installation function"""
    if len(sys.argv) > 1:
        print_error("install.py does not accept command-line arguments.")
        print_info("Use: python3 install.py")
        sys.exit(1)

    print_header("Installing Git hooks...")
    print()

    # Check if in git repository
    if not check_git_repo():
        print_error("current directory is not a git repository")
        sys.exit(1)

    repo_root = get_repo_root()
    script_dir = Path(__file__).parent.resolve()
    hooks_dir = get_hooks_dir()

    cfg = load_path_config(script_dir)
    if cfg is None:
        sys.exit(1)
    print_info(f"{colorize('Using config:', GRAY)} {script_dir / PATH_INI_FILENAME}")
    if not apply_tool_paths_from_ini(repo_root, cfg):
        sys.exit(1)
    print()

    # Create hooks directory if it doesn't exist
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # Copy hooks files
    print_info(f"{colorize('Copying hooks files to', GRAY)} {hooks_dir}")

    hooks_installed = []
    for hook_name in ["pre-commit", "commit-msg", "prepare-commit-msg", "commit_msg.py"]:
        if install_hook(script_dir, hooks_dir, hook_name):
            hooks_installed.append(hook_name)

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
    print("2. QML/JS files under configured source roots will be formatted when qmlformat is configured")
    print("3. Text files will be normalized to LF without trailing whitespace")
    print("4. Files larger than 15MB will be blocked from committing")
    print("5. Commit message template will be displayed automatically")
    print(
        "6. Commit message format will be validated against [type]: description format"
    )
    print()
    print(
        "Supported types: feat, fix, refactor, style, docs, test, chore, perf, ci, build, enhancement"
    )
    print()
    print(f"Requirements: python3/python and a valid {PATH_INI_FILENAME} in hooks/.")
    print(f"Edit {PATH_INI_FILENAME} to change tool paths and rerun install.py.")


if __name__ == "__main__":
    main()
