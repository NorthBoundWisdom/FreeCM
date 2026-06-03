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
