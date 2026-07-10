# FreeCM TODO

Last reviewed: 2026-07-10

This file tracks only unfinished repository work. Remove completed items and
fold durable behavior or maintenance rules into the owning documentation.

## Correctness And Resilience

### Fail Required Packaging Steps Closed

- [ ] Make required deployment commands fail on non-zero exit instead of
  printing a successful completion message after `macdeployqt`, `windeployqt`,
  `install_name_tool`, signing, or AppImage tooling fails.
  - [ ] Distinguish explicitly optional fixups from required package inputs and
    reject missing configured `requiredDlls`.
  - [ ] Validate resource entries strictly so malformed sources, destinations,
    and remove lists cannot be silently skipped.

### Preserve Partial Staging In Commit Hooks

- [ ] Format and normalize the staged blob without adding unrelated worktree
  hunks from the same file to the index.
  - [ ] Run text/binary and large-file checks against index contents rather
    than mixing staged paths with worktree metadata.
  - [ ] Add partial-stage, deleted/renamed, symlink, binary, and formatter
    failure integration tests.

## Python Type Checking

- [ ] Remove the adoption-period `disable_error_code` exemptions from
  `[tool.mypy]` in `pyproject.toml` while keeping `python3 -m mypy` green.
  - [ ] Replace untyped dependency-workflow mixin composition with typed base
    interfaces or protocols so `attr-defined` and `no-any-return` can be
    enabled.
  - [ ] Type the direct-script import fallbacks without duplicate symbol
    definitions so `no-redef` can be enabled.
  - [ ] Correct the remaining concrete signature and platform-narrowing errors,
    then enable `arg-type`, `assignment`, `misc`, `operator`, `type-var`, and
    `union-attr`.

Baseline from this review: enabling all currently disabled error codes reports
236 errors in 27 files. The largest groups are `no-redef` (118),
`attr-defined` (86), and `no-any-return` (22).

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

### Split The Regression Runner By Responsibility

- [ ] Separate regression case schema/selection, process execution, report
  assertions, and JUnit/summary rendering from `tools/regression/runner.py`.
  - [ ] Preserve the importable functions and keep `tools.regression.cli` as a
    thin command wrapper.

## Core And Adapter Performance

### Make Android Defaults Platform-Aware

- [ ] Select conventional SDK defaults for macOS, Linux, and Windows when
  neither `ANDROID_SDK_ROOT` nor `ANDROID_HOME` is set.
  - [ ] Use `gradlew.bat` on Windows and `gradlew` elsewhere while preserving an
    explicit downstream wrapper override.
  - [ ] Cover path separators, executable invocation, and environment assembly
    for all supported host platforms.

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

### Avoid Recompiling The Extension In Every Android L1 Run

- [ ] Let `repomgrandroid` reuse an up-to-date
  `out/validateRepoCommands.js`, with an explicit force/rebuild option and a
  clear failure when generated output is missing or stale.
  - [ ] Test timestamp/content invalidation without adding a network step.

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

## Maintenance Tool Performance

- [ ] Stream regression stdout/stderr directly to case log files with bounded
  diagnostic tails instead of retaining both complete streams in memory for
  every parallel case.
- [ ] Batch staged-file binary detection, formatter invocations, and `git add`
  operations in `hooks/pre_commit.py` where tool semantics permit it.
- [ ] Let `tools/host_clang_format.py` invoke clang-format for batches of files
  while retaining per-file failure reporting.
- [ ] Stream large `git log --numstat` histories in `tools/git_summary.py`
  instead of capturing the complete history before parsing.
