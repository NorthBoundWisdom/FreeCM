# FreeCM TODO

Last reviewed: 2026-07-11

This file tracks only unfinished repository work. Remove completed items and
fold durable behavior or maintenance rules into the owning documentation.

## VS Code Extension Performance

### Reduce VSIX Size

- [ ] Resize and compress the packaged extension icon; the current 1024x1024
  PNG is about 1.5 MB and dominates the roughly 2.0 MB unpacked VSIX.
  - [ ] Package only runtime dependencies needed by the extension and add
    archive content plus compressed/unpacked size budgets to release smoke
    tests.
