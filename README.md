# FreeCM

Shared repository configuration and workflow helpers for C++, Swift/Xcode, and
mixed multi-repo workspaces.

FreeCM is intentionally adapter-oriented. It does not replace Git,
CMake, Xcode, NuGet, or other language package managers; it provides a common
source-root lock format, dependency materialization workflow, repo maintenance
helpers, and a small VS Code extension for running standardized workflows.

## What It Provides

- `freecm/`: the shared source-root engine. It reads
  `source_roots.lock.jsonc`, prepares local seed repositories, materializes
  dependency source roots, validates the result, and exposes binding helpers for
  host repositories.
- `repomgrcpp/`: C++/CMake adapter with CMake preset templates, reusable CMake
  modules, packaging helpers, and repo maintenance tools.
- `repomgrswift/`: Swift/Xcode adapter built on the same source-root engine,
  with Swift-specific lock fields such as `SwiftConfigs`.
- `repomgrandroid/`: Android workflow helpers for SDK/JDK environment setup,
  Gradle wrapper commands, layered tests, and FreeCM command validation.
- `repomgrdotnet/`: .NET/C# workflow helpers for repo-local dotnet/NuGet
  environment isolation and solution build/test/run commands.
- `hooks/`: shared Git hooks for commit-message validation, staged formatting,
  text normalization, and large-file blocking.
- `vscode-extension/`: local VS Code extension with dependency workflow buttons,
  source-root lock mode controls, and manifest-driven `Config` / `Build` /
  `Run` / `Test` commands.

## Package Boundaries

FreeCM keeps shared dependency management in `freecm` and keeps language or
build-system behavior in narrow adapters:

- `freecm`: lock/schema handling, seed repositories, materialized source roots,
  asset seeds, path maps, terminal styling, and generic workflow scripts.
- `repomgrcpp`: C++/CMake presets, dependency builds, packaging, CMake modules,
  and C++-oriented repo tools.
- `repomgrswift`: Swift/Xcode configuration and source-root adapter behavior.
- `repomgrandroid`: Android SDK/JDK environment setup, Gradle wrapper helpers,
  layered Android test execution, and FreeCM validator discovery.
- `repomgrdotnet`: repo-local dotnet/NuGet environment isolation, solution
  restore/build/test command helpers, dotnet run helpers, and Windows exit-code
  normalization.

Downstream repositories should import `freecm` core plus the adapter they
actually need. Non-C++ repositories should not import `repomgrcpp` for generic
dependency workflow behavior, and new code should not use the old `depsfixture`
namespace.

## Downstream Repository Setup

Add FreeCM as a submodule named exactly `FreeCM`:

```bash
git submodule add git@github.com:FreeCM/FreeCM.git FreeCM
git submodule update --init --recursive FreeCM
```

Downstream repositories should expose the standard entrypoints under `configs/`:

```text
configs/source_roots.py
configs/source_root_workflow.py
source_roots.lock.jsonc.in
```

The active machine-local lock is `source_roots.lock.jsonc`. It is normally
generated from `source_roots.lock.jsonc.in` and should stay untracked unless the
host repository has a deliberate reason to commit it.

## Installation Modes

FreeCM supports three installation modes with different contents:

- Source checkout or submodule: full repository contents, including hooks,
  docs, templates, Python adapters, and VS Code extension source.
- Python package: importable Python packages and console scripts such as
  `freecm-deps`, `repomgrcpp`, `package-tool`, `regression-tool`, and
  `repo-tool`.
- VSIX: compiled VS Code extension assets and metadata only.

The repository root `VERSION` file is the version source of truth. Keep
`VERSION`, `pyproject.toml`, `vscode-extension/package.json`, and
`vscode-extension/package-lock.json` aligned with:

```bash
python3 scripts/sync-version.py
python3 scripts/check-version-consistency.py
```

## Documentation Map

- [Dependency lock schema](docs/dependency-lock-schema.md): lock fields,
  `dependencyName` / `repoName` semantics, policy files, and JSON diagnostics.
- [Organization adoption guide](docs/org-adoption-guide.md): pilot rollout,
  lock ownership, upgrade order, policy integration, and governance boundaries.
- [Compatibility policy](docs/compatibility.md): CLI, lock schema, JSON report,
  and public error compatibility expectations.
- [Release process](docs/release-process.md): version, validation, tagging, and
  VSIX release steps.
- [Contributing](CONTRIBUTING.md), [Security](SECURITY.md), and
  [Changelog](CHANGELOG.md): project workflow, security reporting, and release
  notes.

## Multi-Repository Development

For cross-repository work, treat the active lock as the local truth and the
template lock as the committed baseline:

- Inspect the active state with the host wrapper, for example
  `python3 configs/source_roots.py status --format json` and
  `python3 configs/source_roots.py verify`.
- Modify dependency source code only in a real checkout selected by
  `depsMode=manual` and `depsManualPath`, not under generated
  `build/dependency_source_roots/*` materialization output.
- Treat repositories under `build/dependency_seed_repos/*` and
  `build/dependency_source_roots/*` as inputs owned by the parent workflow. They
  must not run their own FreeCM bootstrap, init, update, or dependency
  materialization while being consumed by a parent repository.
- Parent repositories own the full dependency closure for a build. If a
  materialized dependency is used as a CMake subproject, the parent must prepare
  any required install prefixes and `CMAKE_PREFIX_PATH`; the dependency must only
  consume those packages or targets. Nested bootstrap from inside a dependency
  root can build a second dependency graph and produce ABI mismatches.
- Commit and push dependency repositories first. Before writing a dependency SHA
  into `source_roots.lock.jsonc.in`, confirm that SHA exists on the dependency
  remote, for example with `git ls-remote <remote> <sha>`.
- Update committed lock templates in dependency order, from lower-level
  libraries upward, so each repository's own pinned baseline matches the ABI or
  behavior consumed by its parents.
- Use the shared hook format for commits: `[type]: description`, where `type`
  is one of the values documented in `hooks/README.md`.

## Lock File Shape

`source_roots.lock.jsonc.in` uses `schemaVersion: 5` and is JSONC, so comments
and trailing commas are allowed.

```jsonc
{
  "schemaVersion": 5,
  "cmakeEnvironment": {},
  "cmakeCacheVariables": {
    "DEV_MODE": "true",
    "mac": {
      "CMAKE_OSX_DEPLOYMENT_TARGET": "13.0"
    },
    "linux": {
      "USE_SYSTEM_FREETYPE": "true"
    },
    "win": {
      "CMAKE_MSVC_RUNTIME_LIBRARY": "MultiThreadedDLL"
    }
  },
  "terminalPath": {
    "common": ["tools/bin"],
    "mac": ["/opt/homebrew/bin"]
  },
  "depsMode": "pinned",
  "depsManualPath": {
    "Geo2dCore": ""
  },
  "dependencies": {
    "Geo2dCore": {
      "repoName": "Geo2dCore",
      "remote": "git@github.com:FreeCM/Geo2dCore.git",
      "commit": "<pinned-commit>",
      "abiGroup": "geometry2d"
    }
  }
}
```

Supported dependency modes:

- `pinned`: materialize the exact commits listed in the lock.
- `latest`: resolve each dependency to the latest locally available seed commit.
- `manual`: use paths from `depsManualPath`.

The dependency map key is the logical `dependencyName`; it is the stable name
used by lock modes, manual-path overrides, environment maps, and conflict
diagnostics. `repoName` is optional and names the local seed/materialized
repository directory under `build/dependency_seed_repos/` and
`build/dependency_source_roots/`. Omit `repoName` when it matches the dependency
name. Use it when a logical dependency name differs from the repository
checkout name, for example `dependencyName=LibA` and `repoName=RepoA`.

`cmakeCacheVariables` accepts common string values plus optional `linux`, `mac`,
and `win` maps. When generating `CMakePresets.json`, FreeCM applies common
values first and then overlays the current platform map.

`terminalPath` accepts optional `common`, `linux`, `mac`, and `win` string
arrays. The VS Code extension prepends `common` plus the current platform paths
to `PATH` for `Run` and `Test` commands only; relative paths are resolved from
the downstream repository root.

`--init` is the only networked step. It creates the active lock when missing,
prepares the recursive seed repository closure, and may clone, fetch, download,
or prepare remote assets:

```bash
python3 configs/source_root_workflow.py --init
```

`--update` is offline. It materializes dependency roots from local seed
repositories, writes generated project configuration such as CMake presets, and
runs the host adapter update callback:

```bash
python3 configs/source_root_workflow.py --update
```

All other workflow and diagnostic commands are offline as well, including
`materialize`, `verify`, `status`, VS Code lock-mode controls, and repo command
validation. If a required local seed commit or asset is missing, offline commands
fail and should ask the user to run `--init`.

Dependency diagnostics expose machine-readable outputs for CI and organization
tooling:

```bash
python3 configs/source_roots.py status --format json
python3 configs/source_roots.py graph --format json
python3 configs/source_roots.py graph --format dot
python3 configs/source_roots.py audit --format json
python3 configs/source_roots.py policy-check --format json
python3 configs/source_roots.py explain-conflict LibA --format json
```

`policy-check` reads only the active direct lock entries and
`configs/freecm_policy.jsonc`, so it does not need seed repositories or
materialized roots. `graph` and `audit` resolve the local dependency closure from
existing seed repositories and remain offline.
When lock templates declare conflicting dependency remotes or commits, `audit`
and `explain-conflict` include the conflicting sources, parent dependency names,
field, values, and suggested remediation actions in machine-readable JSON.

An optional policy file can constrain approved remotes and dependency modes:

```jsonc
{
  "schemaVersion": 1,
  "allowedRemotes": [
    "https://github.com/my-org/*",
    "ssh://git@github.com/my-org/*"
  ],
  "dependencyCatalog": {
    "Geo2dCore": {
      "owner": "Geometry Platform",
      "tier": "production",
      "license": "MIT",
      "approvalRequired": true
    }
  },
  "dependencyPolicies": {
    "Geo2dCore": {
      "pinRequired": true,
      "manualAllowed": false,
      "latestAllowed": false,
      "abiGroup": "geometry2d",
      "licenseAllowlist": ["MIT", "Apache-2.0", "BSD-3-Clause"]
    }
  },
  "conflictPolicy": {
    "default": "fail"
  }
}
```

`dependencyCatalog` metadata is preserved in JSON policy and audit reports so CI
can join dependency rows with owner, tier, license, and approval data. Policies
can also require a specific `abiGroup` or enforce a catalog license allowlist.

## Minimal C++ Host Binding

For C++/CMake hosts, `configs/source_roots.py` normally binds the FreeCM core
dependency-root manager and delegates CMake-specific work to the C++ adapter:

```python
from pathlib import Path

from freecm.dependency_roots import DependencyRootConfig, bind_dependency_root_workflow

REPO_ROOT = Path(__file__).resolve().parents[1]

workflow = bind_dependency_root_workflow(
    globals(),
    DependencyRootConfig(
        repo_root=REPO_ROOT,
        dependency_root_specs=(),
        repo_display_name="MyRepo",
    )
)
```

`configs/source_root_workflow.py` should be a thin host wrapper that imports the
bound workflow from `configs/source_roots.py` and calls the shared script
adapter. Keep the public entrypoint path stable; the VS Code extension only uses
`configs/source_root_workflow.py`.

## Project Commands Manifest

The VS Code extension can expose project commands from
`configs/freecm.commands.jsonc`. The manifest is explicit by design; the
extension does not guess CMake presets, Xcode schemes, `.sln` files, or shell
snippets.

```jsonc
{
  "version": 1,
  "commands": {
    "config": [
      {
        "id": "mac-clang-debug",
        "label": "Mac Clang Debug",
        "command": "cmake",
        "args": ["--preset", "mac_clang_debug"],
        "platforms": ["darwin"],
        "default": true
      }
    ],
    "build": [
      {
        "id": "mac-clang-debug",
        "label": "Mac Clang Debug",
        "command": "cmake",
        "args": ["--build", "--preset", "mac_clang_debug"],
        "platforms": ["darwin"]
      }
    ],
    "run": [],
    "test": []
  }
}
```

Each variant must use either `command` + `args` or `steps`. Commands are argv
arrays, not shell strings. All commands run with the downstream repository root
as `cwd`.

Validate and preview downstream manifests without opening VS Code:

```bash
cd /path/to/downstream
node FreeCM/vscode-extension/out/validateRepoCommands.js .
node FreeCM/vscode-extension/out/validateRepoCommands.js --preview .
```

The validator uses the same parser and terminal quoting as the extension. It
exits non-zero for invalid manifests and prints warnings for common terminal
ownership mistakes, such as macOS `Run` variants that use `open path/App.app`.
Prefer launching `.app/Contents/MacOS/<ExecutableName>` so logs stay attached to
the FreeCM terminal and `Ctrl+C` can stop the process. Downstream repositories
can use the non-preview command in CI and the preview command during review to
inspect the exact terminal lines FreeCM will send.

Recommended order for users:

```text
Init -> Update -> Config -> Build -> Run/Test
```

`Config` is explicit and separate from `Build`, matching CMake Tools style.

## VS Code Extension

The extension lives in `vscode-extension/`.

```bash
cd vscode-extension
npm ci
npm test
npm audit --omit=optional
npm run package
```

`npm run package` writes a VSIX into the repository root `plugin/` directory:

```text
plugin/FreeCM_<platform>_v<version>.vsix
```

The extension shows workflow actions only for eligible workspaces:

- `FreeCM/`
- `configs/source_root_workflow.py`
- `source_roots.lock.jsonc` or `source_roots.lock.jsonc.in`

Dependency controls:

- `Pull`: `git pull --rebase` for the target workspace if clean.
- `Pull Submodule`: `git pull --rebase` for the `FreeCM` submodule if
  present and clean.
- `Init`: runs `configs/source_root_workflow.py --init`.
- `Update`: runs `configs/source_root_workflow.py --update`.
- `Use pinned`, `Pin latest`, `Manual all`, `Update used`: edit lock modes
  without hidden network operations.
- `Clean build`: conservatively removes direct children under `build/` while
  preserving dependency seed/source-root directories.

## Repo Tools and Hooks

The C++ repo tool can be run as a module or installed console script:

```bash
PYTHONPATH=. python3 -m repomgrcpp.tools.repo_tool --help
repo-tool --help
```

It includes file-list generation, QRC entries, empty-directory cleanup,
conservative build cleanup, staged formatting helpers, Git summary, JSON helper
commands, Markdown catalog generation, and CI target selection.

Install hooks from a host repository after creating `hooks/path.ini` from the
sample:

```bash
cd hooks
cp path.ini.sample path.ini
python3 install.py
```

## CI and Release

GitHub Actions runs Python compile/tests plus VS Code extension compile/tests on
push and pull request across Ubuntu, macOS, and Windows. The CI also checks
version metadata consistency, audits VS Code dependencies, runs package smoke
tests for the Python wheel and VSIX, and checks whitespace. Tags matching `v*`
build VSIX packages on Linux, macOS, and Windows, then upload them to a GitHub
Release.

## Troubleshooting

- Seed repository missing: run `python3 configs/source_root_workflow.py --init`.
  Offline commands intentionally do not clone or fetch.
- Dirty seed repository: inspect `build/dependency_seed_repos/<repoName>` with
  `git status --short`. FreeCM refuses to overwrite unmanaged local changes.
- Manual dependency path wrong: check `depsMode=manual` and
  `depsManualPath.<dependencyName>` in the active `source_roots.lock.jsonc`.
  Generated `build/dependency_source_roots/*` paths are not editable checkouts.
- Remote or commit mismatch: run
  `python3 configs/source_roots.py audit --format json` and confirm pinned SHAs
  exist on the dependency remote with `git ls-remote <remote> <sha>`.
- Nested dependency conflict: run
  `python3 configs/source_roots.py explain-conflict <dependencyName> --format json`
  to see the conflicting parent dependency and suggested remediation.
- CMake presets not generated: run `--update` after `--init`; `Build` commands
  do not silently run configuration first.
- VS Code workflow not shown: ensure the workspace contains `FreeCM/`,
  `configs/source_root_workflow.py`, and a source-root lock or lock template.
  The extension does not use legacy `scripts/source_root_workflow.py`.
- Windows path or quoting failure: keep repo command manifests as structured
  `command` + `args` or `steps` arrays, then preview with
  `node FreeCM/vscode-extension/out/validateRepoCommands.js --preview .`.

## Verification

Use these commands before publishing shared changes:

```bash
python3 -m compileall -q freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
python3 -m unittest discover -s tests -v
python3 scripts/check-version-consistency.py
cd vscode-extension
npm test
npm audit --omit=optional
npm run package
cd ..
git diff --check
```
