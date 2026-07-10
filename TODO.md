# FreeCM TODO

Last reviewed: 2026-07-10

This file tracks only unfinished repository work. Remove completed items and
fold durable behavior or maintenance rules into the owning documentation.

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
