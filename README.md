# FreeCM

Shared source-root and workflow infrastructure for large multi-repository
software workspaces across C++, Swift/Xcode, Android, .NET, and mixed stacks.

FreeCM does not replace Git, CMake, Xcode, Gradle, NuGet, or a downstream
project's own build system. It sits one layer above them: it records source-root
dependencies in a common lock file, prepares local seed repositories, materializes
dependency source roots, exposes reusable adapter helpers, and gives VS Code a
small workflow surface for repeatable project actions.

## Design Goals

- Keep the cross-language dependency engine in `freecm/`.
- Keep build-system and language behavior in narrow adapters such as
  `repomgrcpp/`, `repomgrswift/`, `repomgrandroid/`, and `repomgrdotnet/`.
- Keep downstream repository details explicit. FreeCM should not guess product
  names, CMake options, app targets, solution files, package names, or asset
  catalogs.
- Make `--init` the only dependency workflow step that may use the network.
  The explicit VS Code `Pull Seeds` maintenance action may update existing clean
  Git seeds; `--update`, diagnostics, materialization, VS Code lock-mode
  controls, and command validation remain offline.
- Treat `source_roots.lock.jsonc.in` as the reviewed baseline and
  `source_roots.lock.jsonc` as the machine-local active lock.
- Treat `build/dependency_seed_repos/*` and
  `build/dependency_source_roots/*` as generated dependency inputs owned by the
  parent workflow. Edit dependency code in real manual checkouts instead.
- Provide JSON status, graph, audit, and policy reports so CI and organization
  tooling can consume stable data instead of scraping terminal logs.

## Architecture

FreeCM is a small core plus adapter packages:

```text
FreeCM/
|-- freecm/              lock/schema, seed repos, source-root materialization
|-- repomgrcpp/          CMake presets, C++ packaging, C++ repo tools
|-- repomgrswift/        Swift/Xcode source-root and AppConfigs helpers
|-- repomgrandroid/      Android SDK/JDK, Gradle, test, validator helpers
|-- repomgrdotnet/       .NET/NuGet workflow and command helpers
|-- tools/               shared maintenance tools
|-- hooks/               shared Git hooks
`-- vscode-extension/    workflow UI and repo command manifest runner
```

Downstream repositories normally consume FreeCM as a submodule:

```text
DownstreamRepo/
|-- FreeCM/
|-- configs/source_roots.py
|-- configs/source_root_workflow.py
|-- configs/freecm.commands.jsonc      optional
|-- source_roots.lock.jsonc.in         reviewed template
`-- source_roots.lock.jsonc            local active lock, usually untracked
```

`configs/source_roots.py` binds the host repository's dependency specs to the
FreeCM core or to a narrow adapter. `configs/source_root_workflow.py` is the
stable public workflow entrypoint used by humans, CI, CMake helpers, and the
VS Code extension.

## What It Provides

- Source-root locking with JSONC and `schemaVersion: 5`.
- Dependency modes: `pinned`, `latest`, and `manual`.
- Recursive seed repository preparation under `build/dependency_seed_repos/`.
- Offline dependency materialization under `build/dependency_source_roots/`.
- Dependency graph, audit, policy, and conflict reports.
- Optional asset seed preparation from the same lock file family.
- Workspace-level mutation locking shared by Python workflows and VS Code
  lock-mode controls.
- C++/CMake helpers for presets, dependency builds, packaging, CMake modules,
  and repository maintenance.
- Swift/Xcode helpers for source-root path maps and shared `AppConfigs` lock
  values.
- Android and .NET workflow helper packages for downstream binding code.
- Git hooks for commit-message validation, staged formatting, text
  normalization, and large-file blocking.
- A VS Code extension for workflow buttons, lock-mode controls, conservative
  build cleanup, and manifest-driven project commands.

## Downstream Setup

Add FreeCM as a submodule named exactly `FreeCM`:

```bash
git submodule add git@github.com:FreeCM/FreeCM.git FreeCM
git config -f .gitmodules submodule.FreeCM.branch master
git submodule update --init --recursive FreeCM
```

The parent repository records FreeCM as a gitlink. Teams should choose an
explicit host policy for that gitlink rather than relying on an agent's default
branch or pull-request behavior.

For owner-managed repositories that should follow `FreeCM/master` without pull
requests, run the refresh from a clean host primary branch:

```bash
git submodule update --remote --checkout FreeCM
```

If `git diff --submodule -- FreeCM` is empty, the refresh is a silent no-op:
there is nothing to warn about, commit, or publish. If the gitlink changed,
validate the host against the new FreeCM revision, commit the gitlink on the
existing host primary branch, and push that branch directly. Do not create an
update branch or pull request for a FreeCM-only refresh. Do not use
`git -C FreeCM pull`; submodules normally use detached HEAD, and pulling inside
the submodule obscures whether the parent gitlink was intentionally updated.

The reusable host-agent policy is
`.codex/freecm-wiring/assets/owner-managed-latest.md`. Public or shared hosts
with review requirements can keep a different publication policy; FreeCM core
does not commit or publish host changes itself.

Expose the standard host-owned files:

```text
configs/source_roots.py
configs/source_root_workflow.py
source_roots.lock.jsonc.in
```

For generic dependency-root diagnostics, `configs/source_roots.py` usually binds
`freecm.dependency_roots` and exports its CLI helpers:

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
        repo_display_name="SampleApp",
        dependency_root_specs=(
            DependencyRootSpec(
                dependency_name="LibA",
                repo_name="LibA",
                env_key="LIBA_ROOT",
                required_relative_paths=(),
            ),
        ),
    ),
)
```

Dependency spec names and `env_key` values must be unique. Environment keys use
portable shell identifier syntax (`[A-Za-z_][A-Za-z0-9_]*`), and required or
adapter-provided extra paths must stay relative to the dependency root. FreeCM
rejects absolute paths, lexical parent escapes, and resolved symlink escapes.
When `show` or Swift `status` uses `--format shell`, values are quoted for safe
evaluation by a POSIX shell.

`configs/source_root_workflow.py` should stay thin, but the exact wrapper
depends on the adapter. C++/CMake hosts commonly import the helpers exported by
`configs/source_roots.py`, bind host-specific CMake behavior, and then run the
C++ workflow script:

```python
#!/usr/bin/env python3

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
FREECM_ROOT = REPO_ROOT / "FreeCM"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(FREECM_ROOT) not in sys.path:
    sys.path.insert(0, str(FREECM_ROOT))

from repomgrcpp.cmake_workflow import (
    CMakeDependencyBuildSpec,
    bind_cmake_workflow_script,
)
from configs.source_roots import *  # re-export bound dependency-root helpers

script = bind_cmake_workflow_script(
    globals(),
    repo_root=REPO_ROOT,
    repo_display_name="SampleApp",
    dependency_build_order=(
        CMakeDependencyBuildSpec(
            dependency_name="LibA",
            uses_c_language=True,
            uses_cxx_language=True,
            cmake_options=("-DLIBA_BUILD_TESTS=OFF",),
        ),
    ),
)


if __name__ == "__main__":
    raise SystemExit(script.main())
```

Binding captures the dependency manager, workspace root, build specs, and
service callbacks once in an immutable workflow context. Multiple configured
hosts can therefore coexist in one Python process without rewriting shared
`repomgrcpp.cmake_workflow` globals.

`repomgrcpp/cmake/CppKitRust.cmake` tracks a Rust library's `Cargo.toml`,
optional lock/build/config files, and `src/**/*.rs` inputs without running Cargo
during CMake configure. Use `DEPENDS` for workspace manifests, generated source,
or other inputs outside that default set:

```cmake
cppkit_build_rust_library(
    NAME RustCore
    ROOT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/rust-core"
    DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/ffi/contract.json"
)
```

Rust builds publish a generator-stable witness only after Cargo produces the
expected library. No-op builds do not rerun Cargo, changed inputs rebuild once,
and a missing library is restored before the CMake target can succeed.

`CppKitCompilerFlags.cmake` computes definitions, compile options, and link
options once through `cppkit_common_compile_flags_values`. New integrations
should apply that model with the target-scoped
`cppkit_apply_common_compile_flags_to_target`; the directory-scoped
`cppkit_apply_common_compile_flags` entry point remains for compatibility. On
Linux, enabling coverage and declaring `cppkit_add_executable(... IS_TEST)`
instruments the test executable. Building `Coverage_<target>` first builds the
instrumented executable, runs it, and writes the HTML report.

Swift/Xcode hosts use `repomgrswift.source_roots.DependencyRootWorkflow`, which
implements the protocol expected by
`freecm.source_root_workflow.SourceRootWorkflowScript`. Other adapters can use
the same script wrapper if they provide `init_seed_repositories`,
`materialize_source_roots`, `verify_source_roots`, and
`dependency_resolutions`.

The Swift workflow is a narrow adapter over
`freecm.dependency_workflow.DependencyRootWorkflowFacade`. Generic seed,
resolve, materialize, verify, require, pin, and asset orchestration stays in the
core facade; Swift keeps AppConfigs, extra-path mapping, Xcode-facing
presentation, and its existing public imports. Direct dependency specs define
the root lock and pin choices. Optional known specs add metadata for recognized
transitive dependencies without making them direct.

## Daily Workflow

Run `--init` first. It creates the active lock from the template when needed and
prepares the recursive seed repository closure. This is the only dependency
workflow step that may clone repositories, download files, or prepare remote
assets:

```bash
python3 configs/source_root_workflow.py --init
```

Run `--update` to materialize dependency source roots from local seed
repositories and run the host update callback. Network access is disabled:

```bash
python3 configs/source_root_workflow.py --update
```

FreeCM serializes workspace mutations with `.freecm.workspace.lock`. `--init`,
`--update`, source-root materialization, dependency pinning, and VS Code
lock-mode writes use the same lock so local tools do not rewrite the active
lock or generated source roots at the same time. The lock is a short-lived
directory and is removed after the mutation finishes.

Inspect and validate the active state with the host wrapper. Generic
`freecm.dependency_roots` bindings expose `show`; Swift/Xcode bindings expose
`status` for the same "final roots" purpose:

```bash
python3 configs/source_roots.py show --format json
python3 configs/source_roots.py verify
python3 configs/source_roots.py resolve --format json
```

For Swift/Xcode-bound hosts:

```bash
python3 configs/source_roots.py status --format json
python3 configs/source_roots.py verify
```

Generic `freecm.dependency_roots` bindings also expose graph, audit, policy,
and conflict diagnostics. These commands are offline when they can be resolved
from local seed repositories:

```bash
python3 configs/source_roots.py graph --format json
python3 configs/source_roots.py graph --format dot
python3 configs/source_roots.py audit --format json
python3 configs/source_roots.py policy-check --format json
python3 configs/source_roots.py explain-conflict LibA --format json
```

When changing a dependency ABI or behavior, publish lower-level dependency
commits first, confirm each SHA exists on its remote, then update parent lock
templates in dependency order:

```bash
git ls-remote <remote> <sha>
```

## Lock File

`source_roots.lock.jsonc.in` is JSONC, so comments and trailing commas are
allowed. The active `source_roots.lock.jsonc` is normally generated from it and
kept machine-local.

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
    "LibA": ""
  },
  "dependencies": {
    "LibA": {
      "repoName": "LibA",
      "remote": "git@github.com:my-org/LibA.git",
      "commit": "<pinned-commit>"
    }
  }
}
```

Dependency modes:

- `pinned`: materialize the exact commits listed in the lock.
- `latest`: resolve each dependency to the latest locally available seed
  commit.
- `manual`: use paths from `depsManualPath`.

The dependency map key is the logical `dependencyName`. It is used by lock
modes, manual-path overrides, environment maps, policy data, and conflict
diagnostics. `repoName` is optional and controls the local seed/materialized
checkout directory under `build/dependency_seed_repos/` and
`build/dependency_source_roots/`.

`cmakeCacheVariables` accepts common string values plus optional `linux`, `mac`,
and `win` maps. When generating `CMakePresets.json`, FreeCM applies common
values first and overlays the current platform map.

During `--update`, every resolved dependency source root is also injected into
each generated configure preset under its `DependencyRootSpec.env_key`. These
resolved values override same-named `cmakeEnvironment` entries without changing
the active lock, so pinned and manual roots are consumed consistently.

`terminalPath` accepts optional `common`, `linux`, `mac`, and `win` string
arrays. The VS Code extension prepends those paths to `PATH` for `Run` and
`Test` commands. Relative paths are resolved from the downstream repository
root.

FreeCM core and the VS Code extension share a small lock-schema contract:
schema version, dependency modes, active/template lock filenames, core field
names, and the workspace lock name. Keep that contract in sync when changing
lock validation or lock-mode behavior.

See [docs/dependency-lock-schema.md](docs/dependency-lock-schema.md) for the
full lock and policy schema.

## Policy and Reports

Hosts may add `configs/freecm_policy.jsonc` to constrain approved remotes,
dependency modes, catalog metadata, and conflict behavior:

```jsonc
{
  "schemaVersion": 1,
  "allowedRemotes": ["https://github.com/my-org/*"],
  "dependencyCatalog": {
    "LibA": {
      "owner": "Core Platform",
      "tier": "production",
      "license": "MIT",
      "approvalRequired": true
    }
  },
  "dependencyPolicies": {
    "LibA": {
      "pinRequired": true,
      "manualAllowed": false,
      "latestAllowed": false,
      "licenseAllowlist": ["MIT", "Apache-2.0", "BSD-3-Clause"]
    }
  },
  "violationSeverities": {
    "remote-not-allowed": "warning"
  },
  "conflictPolicy": {
    "default": "fail"
  }
}
```

`policy-check` validates direct lock entries without requiring seed
repositories. `audit` resolves the local dependency closure, reports conflicts,
and preserves catalog metadata so CI can join report rows with ownership,
tier, license, and approval data. Policy violations default to error severity;
`violationSeverities` can downgrade selected violation codes to warnings while
leaving them visible in JSON reports.

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

The extension discovers workspace capabilities independently:

- `build/dependency_seed_repos/` enables the `Pull Seeds` action.
- `configs/source_root_workflow.py` enables `Init` and `Update`.
- `source_roots.lock.jsonc` or `source_roots.lock.jsonc.in` enables lock-mode
  controls.
- `configs/freecm.commands.jsonc` enables project command actions.

Main actions:

- `Pull`: run `git pull --rebase` for the target workspace when clean.
- `Pull Seeds`: run `git pull --rebase` for each existing clean Git repository
  directly under `build/dependency_seed_repos/`. Dirty repositories are skipped,
  failures do not stop the remaining pulls, and non-Git asset directories are
  ignored.
- `Init`: run `configs/source_root_workflow.py --init`.
- `Update`: run `configs/source_root_workflow.py --update`.
- `Use pinned`, `Pin latest`, `Manual all`, `Update used`: edit lock modes
  without hidden network operations.
- `Clean build`: remove direct children under `build/` while preserving
  `build/dependency_seed_repos` and `build/dependency_source_roots`.
- `Config`, `Build`, `Run`, `Test`, `Package`: run variants from the repo
  command manifest in that recommended order.

`Config` is explicit and separate from `Build`; build actions do not silently
configure first.

`Pin latest` temporarily switches the active lock to `latest`, runs the normal
offline `--update`, and then pins the active lock back to the locally resolved
commits. The extension releases the workspace lock while `--update` runs so the
Python workflow can acquire the same lock in its own process; failure restores
the previous active lock.

## Project Commands

The VS Code extension can expose downstream commands from
`configs/freecm.commands.jsonc`. The manifest is explicit by design: command
variants are structured argv arrays, not shell strings, and FreeCM does not
guess CMake presets, Xcode schemes, `.sln` files, or run targets.

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
    "test": [],
    "package": []
  }
}
```

Each variant must use either `command` + `args` or `steps`. All commands run
with the downstream repository root as `cwd`.

Validate and preview manifests without opening VS Code:

```bash
cd /path/to/downstream
node FreeCM/vscode-extension/out/validateRepoCommands.js .
node FreeCM/vscode-extension/out/validateRepoCommands.js --preview .
```

On macOS, prefer launching an app executable under
`.app/Contents/MacOS/<ExecutableName>` for normal `Run` variants instead of
`open path/to/App.app`, so logs stay attached to the FreeCM terminal and
`Ctrl+C` can stop the process.

## Tools, Hooks, and Packaging

FreeCM can be consumed in three forms:

- Source checkout or submodule: full repository contents, hooks, docs,
  templates, adapters, and VS Code extension source.
- Python package: importable packages plus console scripts such as
  `freecm-deps`, `repomgrcpp`, `package-tool`, `regression-tool`, and
  `repo-tool`.
- VSIX: compiled VS Code extension assets and metadata.

The C++ repo tool can be run as a module or installed console script:

```bash
PYTHONPATH=. python3 -m repomgrcpp.tools.repo_tool --help
repo-tool --help
```

Run a config-driven regression suite with the installed runner:

```bash
regression-tool \
  --app /path/to/app \
  --suite-root /path/to/cases \
  --out build/regression
```

Each case writes `stdout.log`, `stderr.log`, and, when produced by the app,
`report.json` under its artifact directory. The suite writes `summary.json` and
`junit.xml`. Exit code `0` means all selected cases passed or no cases were
selected; `1` means a case failed or the CLI caught a configuration/I/O error;
`2` means the app or suite is missing, or a selected case has invalid schema.
Case processes stream stdout and stderr directly to their log files so parallel
runs do not retain complete output buffers; in-memory diagnostics are limited
to the final 64 KiB of each stream.

The host formatter batches files into one clang-format process by default and
retries only failed batches per file so errors still name the affected path.
Use `--batch-size` to tune the process/file tradeoff. `repo-tool git-summary`
also parses `git log --numstat` incrementally, including for large histories.

The macOS deploy helper indexes configured library search roots once, batches
Mach-O inspection, and combines compatible fixups per binary. Native smoke
coverage for macOS, Windows, and Linux runs on the corresponding CI workers.

Measure dependency workflow I/O with real local Git fixtures:

```bash
repo-tool performance-baseline \
  --dependencies 50 --iterations 25 \
  --io --io-dependencies 8 --io-iterations 1
```

The existing in-memory benchmarks remain under `benchmarks`; the optional
`ioBenchmarkSuite` reports five init/offline/materialize/verify scenarios.
Git call counts and command categories are the regression signal. Timings are
informational only. Fixture setup is excluded from measurements, and every
non-init scenario reports zero network-capable Git commands.

`repo-tool generate-json-keys` validates namespace components, header guards,
and configured constant names before writing output. Distinct JSON keys must
not normalize to the same generated C++ constant name; use non-conflicting
`--special-name key=ConstantName` mappings when normalization is ambiguous.

`repomgrandroid.AndroidWorkflowConfig` selects conventional Android SDK and
Gradle wrapper defaults from `host_platform`: macOS uses
`~/Library/Android/sdk`, Linux uses `~/Android/Sdk`, and Windows uses
`%LOCALAPPDATA%\Android\Sdk` (or `~/AppData/Local/Android/Sdk`) with
`gradlew.bat`. Explicit SDK environment variables and `gradle_wrapper` values
take precedence.

Android L1 validation reuses the compiled FreeCM command validator only when
its shared SHA-256 build stamp matches both source inputs and generated
outputs. A missing or stale validator fails with a rebuild instruction; set
`force_validator_rebuild=True` to run the repo-local, offline `npm run compile`
step explicitly and verify the new stamp before validation. No L1 path installs
or downloads Node packages.

`cppkit_export_headers_flat` rejects header sets whose source paths collapse to
the same output basename and reports every conflicting source during CMake
configuration. `cppkit_deploy_qt_dependencies` requires the platform deployment
tool (`windeployqt`, `macdeployqt`, or `linuxdeployqt`) by default. Pass
`OPTIONAL_TOOL` only when skipping Qt deployment is an explicit downstream
choice; deployment command failures remain fatal when a tool is found.

The C++ packaging adapters fail closed for deployment tools and configured
package inputs. macOS configurations select `mac.deploymentTool` explicitly:
`qt` runs `macdeployqt`, while `native` packages an existing `.app` without a
Qt runtime. Both modes can collect permitted dynamic-library dependencies from
`mac.librarySearchPaths`; an optional `mac.dmgOutputPath` plus
`mac.dmgVolumeName` creates a drag-to-Applications DMG. Resource `copyTrees`
and `copyFiles` entries are required by
default; set an entry's `required` field to `false` only for an explicitly
optional resource. Configured translation, font, icon, background, extra
library, and required DLL inputs must exist. macOS and Linux library inputs
that may be absent belong in `optionalExtraLibraries`, with macOS name and glob
variants in `optionalLibraryNames` and `optionalLibraryGlobs`.

Install hooks from a host repository after creating `hooks/path.ini` from the
sample:

```bash
cd hooks
cp path.ini.sample path.ini
python3 install.py
```

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
- [Release process](docs/release-process.md): version, validation, tagging, and
  VSIX release steps.
- [Contributing](CONTRIBUTING.md), [Security](SECURITY.md), and
  [Code of Conduct](CODE_OF_CONDUCT.md): project workflow, security reporting,
  and community expectations.
- [Agent notes](AGENTS.md) and
  [FreeCM wiring skill](.codex/freecm-wiring/SKILL.md): maintainer and
  automation rules for keeping downstream wiring consistent.

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
- VS Code workflow not shown: ensure the workspace contains the file required
  by the action you want, such as `configs/source_root_workflow.py` for
  `Init` / `Update` or `configs/freecm.commands.jsonc` for project commands.
- Windows path or quoting failure: keep repo command manifests as structured
  `command` + `args` or `steps` arrays, then preview with
  `node FreeCM/vscode-extension/out/validateRepoCommands.js --preview .`.

## Validation

Use these commands before publishing shared FreeCM changes:

```bash
python3 -m pip install -e ".[dev]"
python3 -m compileall -q freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
python3 scripts/check-version-consistency.py
python3 -m mypy
python3 -m ruff check freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
python3 -m black --check freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts tests
python3 -m coverage run -m unittest discover -s tests -v
python3 -m coverage report
python3 -m bandit -q -r freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks scripts
python3 -m pip_audit . --progress-spinner off
cd vscode-extension
npm test
npm audit --omit=optional
npm run package
cd ..
git diff --check
```

For quick local iteration, use:

```bash
python3 scripts/test-fast.py
```

The fast profile skips integration-heavy dependency materialization suites that
create repeated git repositories. CI and release validation still run full
`python3 -m unittest discover -s tests -v`.

Linux validation also runs the native GCC and Clang coverage integrations. With
`g++`, `lcov`, `genhtml`, `clang++`, `llvm-cov`, and `llvm-profdata` installed,
run the same enforced gate locally with:

```bash
FREECM_RUN_NATIVE_COVERAGE_TESTS=1 \
  python3 -m unittest discover -s tests -p test_cmake_tools.py -v
```
