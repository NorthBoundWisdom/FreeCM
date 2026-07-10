# FreeCM TODO

Last reviewed: 2026-07-10

This file tracks only unfinished repository work. Remove completed items and
fold durable behavior or maintenance rules into the owning documentation.

## Correctness And Resilience

### Expand Release Artifact Smoke Tests

- [ ] Smoke every installed console script from the built wheel, including
  `package-tool`, `regression-tool`, and `repo-tool`, and verify packaged CMake
  resources can be loaded.
  - [ ] Inspect and activate the packaged VSIX rather than validating only its
    filename and presence.

### Reject Ambiguous Flat Header Exports

- [ ] Detect when multiple source headers passed to
  `cppkit_export_headers_flat` map to the same output basename and fail during
  CMake configuration with the conflicting source paths.
  - [ ] Add a focused CMake test for duplicate basenames while preserving
    valid flat and tree exports.

### Make Qt Deployment Tool Requirements Explicit

- [ ] Make a missing `linuxdeployqt` fail consistently with `windeployqt` and
  `macdeployqt`, or require callers to opt in explicitly to skipping deployment.
  - [ ] Cover required and optional tool discovery on each supported platform
    without masking deployment command failures.

## Architecture And Module Boundaries

### Replace Mutable CMake Workflow Globals

- [ ] Refactor `repomgrcpp/cmake_workflow.py` around an explicit bound context
  object instead of synchronizing module globals through generated wrappers.
  - [ ] Split preset/context inspection, dependency SDK building, and CLI
    binding into focused modules while keeping the downstream binding API thin.
  - [ ] Prove that two independently configured host contexts can coexist in
    one Python process without leaking repository roots, build specs, or helper
    overrides into each other.

### Narrow The Swift Adapter

- [ ] Reduce `repomgrswift/source_roots.py` to Swift/Xcode-specific AppConfigs,
  extra-path mapping, and presentation over the `freecm` core.
  - [ ] Reuse core command/error handling instead of maintaining parallel
    resolve/materialize/verify/pin wrappers and CLI exception lists.
  - [ ] Preserve the existing importable Swift API and keep the adapter free of
    `repomgrcpp` dependencies.

### Consolidate CMake Compiler Flag Logic

- [ ] Make the target-scoped and legacy directory-scoped compiler flag entry
  points consume one computed definitions/options model so compiler branches
  cannot drift between the two implementations.
  - [ ] Keep new behavior target-scoped by default and add parity tests for
    Clang, clang-cl, GCC, IntelLLVM, and MSVC option generation.

### Repair CMake Coverage Wiring

- [ ] Apply coverage compile options to `cppkit_add_executable(IS_TEST)` targets
  and make report targets depend on the instrumented test target.
  - [ ] Generate valid GCC commands for multiple inputs instead of passing a
    single `-a`, and preserve the Clang flow.
  - [ ] Add small real GCC and Clang CMake integration projects that build,
    execute, and produce a coverage report.

### Split The Regression Runner By Responsibility

- [ ] Separate regression case schema/selection, process execution, report
  assertions, and JUnit/summary rendering from `tools/regression/runner.py`.
  - [ ] Preserve the importable functions and keep `tools.regression.cli` as a
    thin command wrapper.

## Core And Adapter Performance

### Reduce Repeated Git And Filesystem Work

- [ ] Add I/O-aware benchmarks for seed preflight, closure discovery,
  materialization, and verification; the current benchmark covers only
  in-memory parsing and synthetic resolution.
  - [ ] Use the measurements to remove duplicate `git status`, `rev-parse`, and
    worktree probes within one operation without weakening dirty-worktree or
    offline guarantees.
  - [ ] Keep all non-`--init` benchmark and optimized paths network-disabled.

### Improve Packaging Scans

- [ ] Index macOS library search roots once instead of running a recursive
  search for every requested library, and traverse an app bundle once when
  collecting Mach-O candidates.
  - [ ] Reduce per-binary `otool`/`install_name_tool` process overhead where
    batching preserves actionable diagnostics.
  - [ ] Add representative large-bundle fixtures and native-platform smoke
    coverage for macOS, Windows, and Linux deployment helpers.

## VS Code Extension Performance

### Coalesce Refreshes Without Losing Changes

- [ ] Replace the current single in-flight refresh guard with a generation or
  dirty-flag coordinator that runs one trailing refresh when watched state
  changes during an active refresh.
  - [ ] Invalidate cache fields by changed file instead of dropping capability,
    lock, dependency, and command-manifest data together.
  - [ ] Read and parse the active lock once per refresh, then derive both lock
    status and dependency comparison from that snapshot.
  - [ ] Add tests that count filesystem reads and prove the final rendered state
    includes an event received during an in-flight refresh.

### Bound And Refresh Manual Dependency Status

- [ ] Replace unbounded `Promise.all` Git status launches with a small worker
  pool shared by dependency comparison and dirty checks.
  - [ ] Give manual-path status a short TTL or targeted watcher invalidation;
    the current folder cache can keep external manual worktree status stale
    until a lock/config file changes.
  - [ ] Deduplicate `runGitStatus` and `runGit`, cap captured output, and cover
    workspaces with many manual dependencies.

### Optimize Code Counting

- [ ] Split `codeCounter.ts` into language discovery, file/ignore discovery,
  line counting, and report rendering modules.
  - [ ] Keep lightweight settings/view helpers out of the counting engine and
    dynamically import that engine only when `freecm.countCode` first runs.
  - [ ] Cache the installed-extension language table and invalidate it only
    when extensions or `files.associations` change.
  - [ ] Enumerate supported source candidates directly instead of collecting up
    to 100,000 arbitrary files first.
  - [ ] Replace the partial directory-only `.gitignore` parser with a proven
    Git-ignore implementation, scope ignore-file discovery to the target, and
    avoid `files x rules` path normalization.
  - [ ] Pre-index known extensions instead of scanning every language suffix for
    every candidate file.
  - [ ] Add cancellation, a configurable large-file limit, adaptive read
    concurrency, and line scanning that does not create a trimmed copy of every
    line while dozens of complete file buffers are resident.
  - [ ] Cache unchanged file counts by path, size/mtime, and language-table
    version so repeated reports only recount changed files.
  - [ ] Retain only a configured number of `.freecm/counts` reports and clean
    older timestamped output safely.
  - [ ] Report unreadable or skipped files, and surface the `maxFiles` limit as
    an explicit warning or failure instead of silently truncating results.

### Avoid Terminal And Webview Churn

- [ ] Stop `TerminalSessionManager.logToTerminal` from creating a command
  terminal merely to write to the separate log terminal. Runtime-path commands
  currently can create a default terminal and immediately replace it with a
  runtime-profile terminal.
- [ ] Send state updates to the existing workflow Webview and patch changed DOM
  regions instead of regenerating and replacing the complete HTML document on
  every launch/status transition.
  - [ ] Preserve unsaved editor state and focus while applying background
    refreshes.

### Add Extension Performance Baselines

- [ ] Add repeatable fixtures and timing/call-count reports for cold activation,
  cached refresh, watched-file refresh, 50 manual dependencies, and code-count
  trees at representative sizes.
  - [ ] Track filesystem reads, spawned Git processes, peak concurrent reads,
    and total duration; use generous regression budgets rather than flaky
    wall-clock microbenchmarks in CI.

### Reduce VSIX Size

- [ ] Resize and compress the packaged extension icon; the current 1024x1024
  PNG is about 1.5 MB and dominates the roughly 2.0 MB unpacked VSIX.
  - [ ] Package only runtime dependencies needed by the extension and add
    archive content plus compressed/unpacked size budgets to release smoke
    tests.

## Maintenance Tool Performance

- [ ] Stream regression stdout/stderr directly to case log files with bounded
  diagnostic tails instead of retaining both complete streams in memory for
  every parallel case.
- [ ] Let `tools/host_clang_format.py` invoke clang-format for batches of files
  while retaining per-file failure reporting.
- [ ] Stream large `git log --numstat` histories in `tools/git_summary.py`
  instead of capturing the complete history before parsing.
