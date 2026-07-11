# FreeCM TODO

Last reviewed: 2026-07-11

This file tracks only unfinished repository work. Remove completed items and
fold durable behavior or maintenance rules into the owning documentation.

## VS Code Extension Performance

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

### Add Extension Performance Baselines

- [ ] Extend the filesystem/Git/concurrency/duration baselines for cold, cached,
  watched-file, and 50-manual-dependency refreshes to representative code-count
  trees when the counting engine refactor lands.

### Reduce VSIX Size

- [ ] Resize and compress the packaged extension icon; the current 1024x1024
  PNG is about 1.5 MB and dominates the roughly 2.0 MB unpacked VSIX.
  - [ ] Package only runtime dependencies needed by the extension and add
    archive content plus compressed/unpacked size budgets to release smoke
    tests.
