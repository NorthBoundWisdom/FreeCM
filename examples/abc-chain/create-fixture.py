#!/usr/bin/env python3
# Usage:
#   python3 examples/abc-chain/create-fixture.py /tmp/freecm-abc-chain
#   python3 examples/abc-chain/create-fixture.py /tmp/freecm-abc-chain --force

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FREECM_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RepoInfo:
    name: str
    root: Path
    commit: str


def run(cmd: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def git(repo_root: Path, *args: str) -> str:
    return run(["git", *args], cwd=repo_root)


def init_git_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    git(repo_root, "init")
    git(repo_root, "config", "user.name", "FreeCM Example")
    git(repo_root, "config", "user.email", "freecm@example.invalid")


def commit(repo_root: Path, message: str) -> str:
    git(repo_root, "add", ".")
    git(repo_root, "commit", "-m", message)
    return git(repo_root, "rev-parse", "HEAD")


def dependency_lock_data(dependencies: Iterable[RepoInfo]) -> dict[str, object]:
    dependency_list = tuple(dependencies)
    return {
        "schemaVersion": 5,
        "depsMode": "pinned",
        "cmakeEnvironment": {},
        "cmakeCacheVariables": {},
        "depsManualPath": {
            dependency.name: ""
            for dependency in dependency_list
        },
        "dependencies": {
            dependency.name: {
                "remote": str(dependency.root),
                "commit": dependency.commit,
            }
            for dependency in dependency_list
        },
    }


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_dependency_sources(repo_root: Path, name: str) -> None:
    include_dir = repo_root / "include" / name
    source_dir = repo_root / "src"
    include_dir.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)
    (include_dir / f"{name}.h").write_text(
        f"#pragma once\n\nint {name.lower()}_value();\n",
        encoding="utf-8",
    )
    (source_dir / f"{name}.cpp").write_text(
        f'#include "{name}/{name}.h"\n\nint {name.lower()}_value() {{ return 1; }}\n',
        encoding="utf-8",
    )
    (repo_root / "CMakeLists.txt").write_text(
        "\n".join(
            [
                "cmake_minimum_required(VERSION 3.16)",
                f"project({name} LANGUAGES CXX)",
                f"add_library({name} STATIC src/{name}.cpp)",
                f"target_include_directories({name} PUBLIC",
                "    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>",
                "    $<INSTALL_INTERFACE:include>",
                ")",
                f"install(TARGETS {name}",
                f"    EXPORT {name}Targets",
                "    ARCHIVE DESTINATION lib",
                "    LIBRARY DESTINATION lib",
                "    RUNTIME DESTINATION bin",
                ")",
                "install(DIRECTORY include/ DESTINATION include)",
                f"install(EXPORT {name}Targets",
                f"    NAMESPACE {name}::",
                f"    DESTINATION lib/cmake/{name}",
                ")",
                "",
            ]
        ),
        encoding="utf-8",
    )


def create_dependency_repo(
    remotes_root: Path,
    name: str,
    *,
    dependencies: Iterable[RepoInfo] = (),
) -> RepoInfo:
    repo_root = remotes_root / name
    init_git_repo(repo_root)
    write_dependency_sources(repo_root, name)
    dependency_list = tuple(dependencies)
    if dependency_list:
        write_json(
            repo_root / "source_roots.lock.jsonc.in",
            dependency_lock_data(dependency_list),
        )
    return RepoInfo(name=name, root=repo_root, commit=commit(repo_root, f"create {name}"))


def create_app_repo(output_root: Path, lib_b: RepoInfo, lib_c: RepoInfo) -> Path:
    app_root = output_root / "AppA"
    init_git_repo(app_root)
    (app_root / "configs").mkdir(parents=True, exist_ok=True)
    (app_root / "src").mkdir(parents=True, exist_ok=True)
    (app_root / "configs" / "__init__.py").write_text("", encoding="utf-8")
    (app_root / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (app_root / "CMakeLists.txt").write_text(
        "\n".join(
            [
                "cmake_minimum_required(VERSION 3.16)",
                "project(AppA LANGUAGES CXX)",
                'set(CPPKIT_DEPSMGR_WORKFLOW_SCRIPT "${CMAKE_SOURCE_DIR}/configs/source_root_workflow.py")',
                f'include("{(FREECM_ROOT / "repomgrcpp" / "cmake" / "DependencyBootstrap.cmake").as_posix()}")',
                "cppkit_ensure_dependency_installs_for_current_preset()",
                "add_executable(AppA src/main.cpp)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_json(app_root / "source_roots.lock.jsonc.in", dependency_lock_data((lib_b, lib_c)))
    write_source_roots_config(app_root)
    write_workflow_config(app_root)
    commit(app_root, "create AppA")
    return app_root


def write_source_roots_config(app_root: Path) -> None:
    (app_root / "configs" / "source_roots.py").write_text(
        f'''# Usage:
#   python3 configs/source_roots.py --help
#   python3 configs/source_roots.py graph --format dot

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
FREECM_ROOT = Path({json.dumps(str(FREECM_ROOT))})
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(FREECM_ROOT) not in sys.path:
    sys.path.insert(0, str(FREECM_ROOT))

from freecm.dependency_roots import (  # noqa: E402
    DependencyRootConfig,
    DependencyRootSpec,
    bind_dependency_root_workflow,
)


workflow = bind_dependency_root_workflow(
    globals(),
    DependencyRootConfig(
        repo_root=REPO_ROOT,
        dependency_root_specs=(
            DependencyRootSpec(
                dependency_name="LibB",
                repo_name="LibB",
                env_key="LIBB_SOURCE_ROOT",
                required_relative_paths=("CMakeLists.txt", "include/LibB"),
            ),
            DependencyRootSpec(
                dependency_name="LibC",
                repo_name="LibC",
                env_key="LIBC_SOURCE_ROOT",
                required_relative_paths=("CMakeLists.txt", "include/LibC"),
            ),
        ),
        repo_display_name="AppA",
    ),
)


if __name__ == "__main__":
    raise SystemExit(main())
''',
        encoding="utf-8",
    )


def write_workflow_config(app_root: Path) -> None:
    (app_root / "configs" / "source_root_workflow.py").write_text(
        f'''# Usage:
#   python3 configs/source_root_workflow.py --init
#   python3 configs/source_root_workflow.py --update

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
FREECM_ROOT = Path({json.dumps(str(FREECM_ROOT))})
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(FREECM_ROOT) not in sys.path:
    sys.path.insert(0, str(FREECM_ROOT))

from configs.source_roots import *  # noqa: F401,F403,E402
from repomgrcpp.cmake_workflow import (  # noqa: E402
    CMakeDependencyBuildSpec,
    bind_cmake_workflow_script,
)


script = bind_cmake_workflow_script(
    globals(),
    repo_root=REPO_ROOT,
    repo_display_name="AppA",
    dependency_build_order=(
        CMakeDependencyBuildSpec(
            dependency_name="LibD",
            uses_c_language=False,
            cmake_options=(),
        ),
        CMakeDependencyBuildSpec(
            dependency_name="LibC",
            uses_c_language=False,
            cmake_options=(),
        ),
        CMakeDependencyBuildSpec(
            dependency_name="LibB",
            uses_c_language=False,
            cmake_options=(),
        ),
    ),
)


if __name__ == "__main__":
    raise SystemExit(script.main())
''',
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a local AppA -> LibB/LibC -> LibD FreeCM fixture."
    )
    parser.add_argument("output", type=Path, help="Directory to create.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Remove the output directory first when it already exists.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_root = args.output.resolve()
    if output_root.exists():
        if not args.force:
            raise SystemExit(f"Output directory already exists: {output_root}")
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)
    remotes_root = output_root / "remotes"
    remotes_root.mkdir()

    lib_d = create_dependency_repo(remotes_root, "LibD")
    lib_c = create_dependency_repo(remotes_root, "LibC", dependencies=(lib_d,))
    lib_b = create_dependency_repo(remotes_root, "LibB", dependencies=(lib_c, lib_d))
    app_root = create_app_repo(output_root, lib_b, lib_c)

    print(f"Created ABC chain fixture: {output_root}")
    print("Run:")
    print(f"  cd {app_root}")
    print("  python3 configs/source_root_workflow.py --init")
    print("  python3 configs/source_root_workflow.py --update")
    print("  python3 configs/source_roots.py graph --format dot")
    print("Optional CMake build-order demo:")
    print("  cmake -S . -B build/abc-chain-demo -G Ninja")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
