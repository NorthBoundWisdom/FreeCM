---
name: freecm-wiring
description: Use this skill when wiring a downstream repository to FreeCM, migrating legacy dependency-root/source-root imports, connecting C++/CMake, Swift/Xcode, Android, or .NET adapters, or updating source_roots.lock.jsonc.in to the current FreeCM workflow.
---

# FreeCM Wiring

Use this SOP when connecting a downstream repository to a public FreeCM
submodule. Keep instructions generic: do not hardcode local machine paths,
private workspace names, or host-specific absolute directories.

## Core Rules

- Downstream repositories should use a submodule named `FreeCM/`.
- `configs/source_roots.py`, `configs/source_root_workflow.py`, and
  `source_roots.lock.jsonc.in` are the standard host-owned entrypoints.
- `freecm` is the generic dependency-management core.
- Adapter packages are narrow:
  - `repomgrcpp`: C++/CMake/package/repo-tool helpers.
  - `repomgrswift`: Swift/Xcode source-root adapter helpers.
  - `repomgrandroid`: Android SDK/JDK, Gradle, layered test, and validator helpers.
  - `repomgrdotnet`: .NET/C# dotnet/NuGet environment and command helpers.
- Do not reintroduce legacy package names or compatibility bridges for
  `depsfixture`, `cpprepomgr`, or `swiftrepomgr`.
- `--init` is the only command allowed to use the network. `--update`,
  `materialize`, `verify`, `status`, lock-mode actions, command validation, and
  diagnostics must stay offline.

## Inspection

Start by reading the downstream repository instead of guessing its shape.

```bash
git status --short --branch
git submodule status
rg -n "depsfixture|cpprepomgr|swiftrepomgr|source_root_workflow|source_roots|repoName|defaultMode|manualRoots" . --glob '!build/**' --glob '!**/.git/**'
find configs -maxdepth 2 -type f | sort
```

Classify the host before editing:

- C++/CMake: CMake presets/modules or native dependency builds are present.
- Swift/Xcode: Swift config fields, Xcode setup callbacks, or extra source-root
  paths are present.
- Android: Gradle wrapper, Android SDK/JDK setup, emulator/device smoke tests,
  or Android command validator workflows are present.
- .NET/C#: `.sln`, `.slnx`, `.csproj`, dotnet build/test/run, or repo-local
  NuGet/dotnet cache setup is present.
- Mixed repositories should import `freecm` core plus only the adapter packages
  they actually use.

## Submodule

Prefer a single shared submodule:

```bash
git submodule add <freecm-remote-url> FreeCM
git submodule update --init --recursive FreeCM
```

If a repository already has a legacy shared-tool submodule, migrate it to
`FreeCM/` and update `.gitmodules`. Do not leave fallback code that searches
old submodule names unless the user explicitly requests a short migration shim.

## Source-Root Binding

`configs/source_roots.py` should insert `REPO_ROOT / "FreeCM"` into `sys.path`
and bind the dependency manager from `freecm.dependency_roots`.

```python
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
FREECM_ROOT = REPO_ROOT / "FreeCM"
if str(FREECM_ROOT) not in sys.path:
    sys.path.insert(0, str(FREECM_ROOT))

from freecm.dependency_roots import (
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
                dependency_name="DependencyName",
                repo_name="DependencyRepo",
                env_key="DEPENDENCY_SOURCE_ROOT",
                required_relative_paths=(),
            ),
        ),
        repo_display_name="HostRepo",
    ),
)
```

`configs/source_root_workflow.py` should be a thin wrapper around
`freecm.source_root_workflow.SourceRootWorkflowScript`. Add adapter-specific
callbacks only when the host actually needs them.

```python
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
FREECM_ROOT = REPO_ROOT / "FreeCM"
if str(FREECM_ROOT) not in sys.path:
    sys.path.insert(0, str(FREECM_ROOT))

from freecm.source_root_workflow import SourceRootWorkflowScript
from configs.source_roots import workflow


def update_callback() -> int:
    return 0


script = SourceRootWorkflowScript(
    workflow,
    repo_display_name="HostRepo",
    update_callback=update_callback,
)


def main(argv: list[str] | None = None) -> int:
    return script.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
```

For C++ dependency SDK builds, import CMake-specific helpers from
`repomgrcpp.cmake_workflow`. For Swift, Android, or .NET workflows, import from
`repomgrswift`, `repomgrandroid`, or `repomgrdotnet` respectively rather than
from `repomgrcpp`.

## Lock Template

The committed template is `source_roots.lock.jsonc.in`; the active
`source_roots.lock.jsonc` is usually machine-local. Use schema version 5.

```jsonc
{
  "schemaVersion": 5,
  "cmakeEnvironment": {},
  "cmakeCacheVariables": {},
  "terminalPath": {},
  "depsMode": "pinned",
  "depsManualPath": {
    "DependencyName": ""
  },
  "dependencies": {
    "DependencyName": {
      "remote": "<dependency-remote-url>",
      "commit": "<exact-commit>",
      "abiGroup": "optional-group"
    }
  }
}
```

Dependency entries allow `remote`, `commit`, and optional `abiGroup`. Do not
restore removed fields such as `repoName`, `defaultMode`, or `manualRoots`.
Avoid lock churn unrelated to the migration.

## Adapter Notes

- C++/CMake:
  - Use `repomgrcpp.cmake_workflow` for CMake presets, dependency build specs,
    and CMake package data.
  - CMake includes should reference `FreeCM/repomgrcpp/cmake/...`.
- Swift/Xcode:
  - Use `repomgrswift.source_roots` only for Swift/Xcode-specific config and
    extra source-root path behavior.
  - Generic workflow scripting should come from `freecm.source_root_workflow`.
- Android:
  - Use `repomgrandroid.workflow` for SDK/JDK environment setup, repo-local
    Gradle wrapper commands, layered tests, and validator discovery.
- .NET/C#:
  - Use `repomgrdotnet.workflow` for repo-local dotnet/NuGet cache isolation,
    solution restore/build/test commands, `dotnet run` command construction,
    and Windows exit-code normalization.

## Validation

Run the smallest meaningful downstream checks first, then broaden.

```bash
python3 -m compileall -q configs
python3 configs/source_root_workflow.py --help
python3 configs/source_roots.py --help
python3 configs/source_roots.py status --format json
python3 configs/source_roots.py verify
python3 configs/source_root_workflow.py --init
python3 configs/source_root_workflow.py --update
node FreeCM/vscode-extension/out/validateRepoCommands.js --preview .
git diff --check
```

Adapter-specific checks depend on the host:

- C++/CMake: run the host configure/build presets or CMake workflow command.
- Swift/Xcode: run the host Xcode setup/update callback checks.
- Android: run the host Gradle or layered Android workflow checks.
- .NET/C#: run the host dotnet restore/build/test workflow checks.

If `--update`, `verify`, or `status` needs the network, stop and fix the wiring.
Do not paper over missing local seeds/assets with fallback downloads.

## Cleanup

After edits, search for stale wiring:

```bash
rg -n "depsfixture|cpprepomgr|swiftrepomgr|repoName|defaultMode|manualRoots" . --glob '!build/**' --glob '!**/.git/**'
```

Generated/local files such as `source_roots.lock.jsonc`, `CMakePresets.json`,
build outputs, IDE caches, and dependency materialization directories should
remain untracked unless the host repository intentionally tracks them.

## Final Report

Report:

- FreeCM submodule path and commit.
- Which adapters were wired.
- Legacy imports/paths removed.
- Lock template changes, if any.
- Validation commands and results.
- Any remaining dirty files that were intentionally left alone.
