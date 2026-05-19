# Usage:
#   PYTHONPATH=/path/to/RepoConfigsMgr python3 -m cpprepomgr.tools.repo_tool ci-targets --build-dir <build> --target <target> [--quick-target <target>]
#   Library: from cpprepomgr.tools.ci_targets import selected_ci_targets, run_cmake_targets

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class CMakeTargetRun:
    target: str
    returncode: int


def selected_ci_targets(
    *,
    regular_targets: Sequence[str],
    quick_targets: Sequence[str],
    pipeline_source: str | None = None,
    quick_sources: Sequence[str] = ("merge_request_event",),
) -> tuple[str, ...]:
    source = pipeline_source if pipeline_source is not None else os.environ.get("CI_PIPELINE_SOURCE", "")
    if source in set(quick_sources) and quick_targets:
        return tuple(quick_targets)
    return tuple(regular_targets)


def run_cmake_targets(
    build_dir: Path,
    targets: Sequence[str],
    *,
    parallel: int | None = None,
) -> list[CMakeTargetRun]:
    if not build_dir.is_dir():
        raise FileNotFoundError(f"build directory not found: {build_dir}")

    results: list[CMakeTargetRun] = []
    for target in targets:
        cmd = ["cmake", "--build", str(build_dir), "--target", target]
        if parallel is not None:
            cmd.extend(["--parallel", str(parallel)])
        completed = subprocess.run(cmd, check=False)
        results.append(CMakeTargetRun(target=target, returncode=completed.returncode))
        if completed.returncode != 0:
            break
    return results
