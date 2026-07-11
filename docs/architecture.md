# FreeCM Architecture

FreeCM is a source-root workflow layer for repositories that need to consume
other source repositories while keeping the parent repository in charge of the
dependency closure. It does not replace Git, CMake, Xcode, Gradle, NuGet, or a
host build system. It gives those systems a consistent dependency-root contract.

## Dependency Flow

```text
source_roots.lock.jsonc.in
        |
        | --init, network allowed
        v
build/dependency_seed_repos
        |
        | --update / materialize, offline only
        v
build/dependency_source_roots
        |
        | adapter layer
        v
CMake / Xcode / Gradle / dotnet / VS Code commands
```

The committed `source_roots.lock.jsonc.in` is the reviewed template. The active
`source_roots.lock.jsonc` is normally generated from that template and kept
machine-local unless a host repository deliberately tracks it.

`--init` is the only dependency workflow phase that may use the network. It can
clone missing seed repositories, fetch remote updates, and prepare remote asset
seeds. It also discovers nested dependency templates from the seed closure.

`--update`, `materialize`, `verify`, `status`, `show`, `graph`, `audit`,
VS Code lock-mode controls, and repo command validation are offline operations.
They must work from existing local seed repositories and local lock data. This
keeps repeated local work and CI diagnostics deterministic after initialization.

## Component Boundaries

`freecm` is the cross-language core. It owns lock/schema validation, seed
repository handling, dependency closure resolution, source-root materialization,
asset seeds, path maps, terminal output helpers, and generic workflow scripting.

Adapter packages keep host technology behavior narrow:

- `repomgrcpp`: C++/CMake presets, dependency build specs, CMake modules,
  packaging helpers, and repo-tool commands.
- `repomgrswift`: Swift/Xcode source-root and AppConfigs behavior.
- `repomgrandroid`: Android SDK/JDK, Gradle wrapper, layered tests, and command
  validator discovery.
- `repomgrdotnet`: .NET solution workflow, dotnet/NuGet environment isolation,
  and Windows exit-code normalization.

Generic dependency commands and their explicit user-error execution boundary
belong to `freecm`. Core and adapter CLIs bind their own root operations and
presentation to that shared command layer, preserving command names and output
styles without maintaining parallel exception dispatch. Adapter-specific data
models and renderers remain in the adapter packages.

`freecm.dependency_workflow.DependencyRootWorkflowFacade` owns generic seed,
asset, resolve, materialize, verify, require, and pin orchestration. The Swift
workflow subclasses that facade and adds only AppConfigs validation, extra-path
mapping, Xcode hints, and Swift presentation. Its compatibility types and
imports remain in `repomgrswift`.

Dependency manager configuration distinguishes direct specs from known specs.
Direct specs are the dependencies required in the root lock, exposed in root
summaries, and eligible for pinning. Known specs include every direct spec and
may additionally describe recognized transitive dependencies so closure
resolution can use stable repository names, environment keys, and required
paths. Known specs never add root-lock requirements or pin choices. Bindings
must pass both sets through `DependencyRootConfig`; they must not mutate a
manager's spec maps after construction.

Downstream repositories bind these pieces through host-owned files:

```text
configs/source_roots.py
configs/source_root_workflow.py
source_roots.lock.jsonc.in
configs/freecm.commands.jsonc       optional VS Code command manifest
configs/freecm_policy.jsonc         optional organization policy
```

The VS Code extension does not invent another dependency model. It targets the
same `configs/source_root_workflow.py`, lock files, and command manifests that
humans and CI can run directly.

## Workspace Mutation Boundary

Workspace mutations are serialized with `.freecm.workspace.lock` at the
downstream repository root. Python workflows and the VS Code extension use this
same directory lock name so lock-mode changes, seed synchronization,
materialization, nested active-lock generation, and generated CMake preset
writes do not race each other.

Command wrappers should hold the workspace lock for their full mutation surface.
For C++/CMake hosts, that means `--init` covers active-lock creation, `.clangd`
creation, seed preparation, and asset seed preparation; `--update` covers
offline materialization, asset verification, nested dependency workflow
preparation, and generated `CMakePresets.json`.

CMake host binding is instance-scoped. `bind_cmake_workflow_script(...)`
returns a `CMakeWorkflowScript` backed by an immutable context containing the
resolved host root, display name, dependency-root callbacks, build order, and
state filename. The shared façade remains unbound and is never rewritten by a
host binding, so independently configured hosts can run interleaved or in
parallel without leaking paths, helpers, or build specs. `--update` calls only
the captured unlocked materializer with `allow_network=False` while the host's
workspace lock is held.

When one tool invokes another process that also owns workspace mutations, the
outer tool must not keep holding the lock. The VS Code `Pin latest` command is
the reference shape: it locks to switch the active lock to `latest`, releases
the lock while running `configs/source_root_workflow.py --update`, then locks
again to pin the resolved active lock or restore the original content after a
failure.

## Closure Model

Consider a four-repository chain:

```text
AppA
|-- LibB
|   `-- LibD
`-- LibC
    `-- LibD
```

`AppA` owns the dependency closure for its build. When `AppA` runs `--init`,
FreeCM prepares seed repositories for direct dependencies and then reads nested
`source_roots.lock.jsonc.in` templates from those seeds to discover transitive
dependencies. In this example, the closure contains `LibB`, `LibC`, and `LibD`.

When `AppA` runs `--update`, FreeCM materializes concrete source roots under
`build/dependency_source_roots`. If `LibB` or `LibC` is added with
`add_subdirectory(...)`, its build files must consume the already-prepared
packages, targets, and source roots supplied by `AppA`. A materialized
dependency must not initialize its own nested `FreeCM/` submodule or build a
second dependency graph.

## ABI Risk Model

The closure has one effective source root for each logical dependency name. That
is what prevents a parent build from accidentally compiling `LibB` against one
copy of `LibD` and `LibC` against another.

The same rule also exposes ABI risk. If `LibB` was validated against
`LibD@commit1`, `LibC` was validated against `LibD@commit2`, and `AppA` unifies
the closure at `LibD@commit3`, the build may compile while still carrying ABI,
layout, enum, behavior, or data-file compatibility risk. FreeCM can report the
declared sources and commit choices, but the dependency owners still need to
publish compatible lower-level changes first and update parent lock templates in
topological order.

For multi-repository changes, publish lower-level dependency commits first,
confirm each SHA exists on its remote with `git ls-remote <remote> <sha>`, then
update intermediate repositories, and only then update the final app or product
repository. Do not leave a parent lock template pointing at an older dependency
commit after downstream code starts relying on a changed ABI or behavior.

## Generated Roots

`build/dependency_seed_repos/*` and `build/dependency_source_roots/*` are
generated inputs owned by the parent workflow. They may be replaced by
`--init`, `--update`, materialization, or cleanup commands.

Dependency code edits should happen in a real checkout selected through
`depsMode=manual` and `depsManualPath`, or in another developer-provided source
checkout. Generated roots are diagnostics and build inputs, not durable editing
locations.

## CMake Build Metadata Boundary

FreeCM's C++ adapter intentionally keeps dependency build behavior parent-owned
by default. The parent repository supplies `CMakeDependencyBuildSpec` entries
with the dependency build order, language filtering, source subdirectory, and
host-specific CMake options. This is the only authority used to build dependency
SDKs today.

Each generated dependency SDK has a versioned receipt in the preset's dependency
state manifest. The receipt fingerprints the materialized root and commit, every
`CMakeDependencyBuildSpec` field, effective language-filtered CMake context,
source directory, dependency edges, and upstream SDK fingerprints. Canonical
JSON plus SHA-256 makes dictionary order irrelevant while preserving ordered
inputs such as CMake options and build configurations.

When an input changes, FreeCM invalidates that dependency's receipt and the
receipts of its transitive consumers before deleting generated output. It then
writes each receipt immediately after that dependency installs successfully, so
a later failure can resume without rebuilding completed lower-level SDKs. A
manual checkout is rebuilt on every invocation because FreeCM does not hash its
mutable source tree; unrelated pinned siblings remain reusable.

When a dependency leaves the closure, its receipt is pruned immediately. Its
now-unreferenced build and install directories are left for the explicit build
cleanup workflow rather than deleting paths named only by stale manifest data.

Dependency configure commands receive install prefixes only for their declared
transitive dependencies, in closure order. Unrelated siblings must not become
implicit `find_package` inputs. SDK receipt changes, build-directory cleanup,
configure, build, install, and receipt writes all run under the shared workspace
mutation lock.

If FreeCM later accepts self-describing CMake metadata from dependency
repositories, keep that metadata declarative and minimal. Acceptable candidates
are facts such as whether the dependency supports install, default CMake options
that a parent may override, and required package names the parent must already
provide. Do not let dependency metadata choose remotes, fetch refs, mutate lock
files, set install prefixes, own `CMAKE_PREFIX_PATH`, start nested FreeCM
bootstrap, or determine the parent's dependency build order.

Materialized dependencies may contain their own `source_roots.lock.jsonc.in` for
closure discovery, but while they are consumed by a parent build they must use
the parent-prepared dependency roots and install prefixes. A child repository's
metadata can describe needs; it must not launch a second dependency graph.
